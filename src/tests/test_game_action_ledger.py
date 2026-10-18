import asyncio

import pytest

from src.games.action_ledger import GameActionLedger
from src.handlers.callback_handler import CallbackHandler


@pytest.mark.asyncio
async def test_memory_ledger_claims_one_action_only_under_concurrency():
    ledger = GameActionLedger()
    results = await asyncio.gather(*[
        ledger.claim(-100, "quiz", "callback-1", user_id=7)
        for _ in range(12)
    ])
    assert sum(results) == 1
    await ledger.release(-100, "quiz", "callback-1")
    assert await ledger.claim(-100, "quiz", "callback-1", user_id=7) is True


@pytest.mark.asyncio
async def test_database_ledger_receives_scoped_key_and_payload():
    class FakeDatabase:
        available = True

        def __init__(self):
            self.calls = []
            self.claimed = set()

        async def claim_game_action(self, **kwargs):
            self.calls.append(kwargs)
            key = kwargs["action_key"]
            if key in self.claimed:
                return False
            self.claimed.add(key)
            return True

        async def release_game_action(self, key):
            self.claimed.discard(key)

    database = FakeDatabase()
    ledger = GameActionLedger(database)
    assert await ledger.claim(-42, "Quiz", "telegram-callback", user_id=9, payload={"answer": 2}) is True
    assert await ledger.claim(-42, "Quiz", "telegram-callback", user_id=9) is False
    assert database.calls[0]["action_key"] == "-42:quiz:telegram-callback"
    assert database.calls[0]["payload"] == {"answer": 2}


@pytest.mark.asyncio
async def test_invalid_action_is_rejected():
    ledger = GameActionLedger()
    with pytest.raises(ValueError, match="invalid_game_action_id"):
        await ledger.claim(1, "quiz", "")
    with pytest.raises(ValueError, match="invalid_game_type"):
        await ledger.claim(1, "quiz!", "id")


@pytest.mark.asyncio
async def test_ledger_rejects_invalid_scope_and_oversized_payload():
    ledger = GameActionLedger()
    with pytest.raises(ValueError, match="invalid_game_chat_id"):
        await ledger.claim(0, "quiz", "id")
    with pytest.raises(ValueError, match="invalid_game_user_id"):
        await ledger.claim(-100, "quiz", "id", user_id=0)
    with pytest.raises(ValueError, match="game_action_payload_too_large"):
        await ledger.claim(-100, "quiz", "id", payload={"blob": "x" * 5000})


@pytest.mark.asyncio
async def test_production_like_database_never_silently_falls_back_to_memory():
    class UnavailableDatabase:
        available = False

    ledger = GameActionLedger(UnavailableDatabase())
    with pytest.raises(RuntimeError, match="game_persistence_unavailable"):
        await ledger.claim(-100, "quiz", "id")


@pytest.mark.asyncio
async def test_run_supports_sync_operations_and_releases_failed_claim():
    ledger = GameActionLedger()
    calls = []

    first = await ledger.run(-100, "quiz", "sync", lambda: calls.append(1) or "ok")
    assert first.executed is True and first.result == "ok"
    second = await ledger.run(-100, "quiz", "sync", lambda: calls.append(2) or "bad")
    assert second.replayed is True and calls == [1]

    async def fail():
        raise RuntimeError("temporary")

    with pytest.raises(RuntimeError, match="temporary"):
        await ledger.run(-100, "quiz", "retry", fail)
    recovered = await ledger.run(-100, "quiz", "retry", lambda: "recovered")
    assert recovered.executed is True and recovered.result == "recovered"


@pytest.mark.asyncio
async def test_batak_join_callback_is_idempotent_for_same_message_and_user():
    class Query:
        data = "game_batak_join"
        message = type("Message", (), {"message_id": 90})()

        async def answer(self, *args, **kwargs):
            return None

        async def edit_message_text(self, *args, **kwargs):
            return None

    class Bot:
        async def get_chat_member(self, chat_id, user_id):
            return type("Member", (), {"user": type("User", (), {"first_name": "Arel"})()})()

    query = Query()
    update = type("Update", (), {
        "effective_user": type("User", (), {"id": 7})(),
        "effective_chat": type("Chat", (), {"id": -100})(),
    })()
    context = type("Context", (), {
        "chat_data": {"batak_players": [], "batak_names": []},
        "bot": Bot(),
    })()
    handler = CallbackHandler(type("Engine", (), {"db": object()})(), object(), None)
    await handler._handle_batak_callback(update, query, context, query.data)
    await handler._handle_batak_callback(update, query, context, query.data)
    assert context.chat_data["batak_players"] == [7]
