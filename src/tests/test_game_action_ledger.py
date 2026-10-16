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
