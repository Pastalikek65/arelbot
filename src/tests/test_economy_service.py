from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from src.services.economy_service import EconomyService
from src.storage.economy_store import EconomyStore
from src.handlers.command_handler import CommandHandler
from src.handlers.callback_handler import CallbackHandler
from unittest.mock import AsyncMock


class _DuplicateKeyError(Exception):
    """Mirror the MongoDB duplicate key error of the unique account index."""

    code = 11000


class _UpdateResult:
    def __init__(self, modified_count=0, upserted_id=None):
        self.modified_count = modified_count
        self.upserted_id = upserted_id


_MISSING = object()


def _read(document, path):
    current = document
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _write(document, path, value):
    parts = path.split(".")
    current = document
    for part in parts[:-1]:
        nested = current.get(part)
        if not isinstance(nested, dict):
            nested = {}
            current[part] = nested
        current = nested
    current[parts[-1]] = value


def _unset(document, path):
    parts = path.split(".")
    current = document
    for part in parts[:-1]:
        if not isinstance(current, dict) or part not in current:
            return
        current = current[part]
    if isinstance(current, dict):
        current.pop(parts[-1], None)


def _matches(document, query):
    for field, condition in query.items():
        value = _read(document, field)
        if isinstance(condition, dict):
            for operator, operand in condition.items():
                if operator == "$exists":
                    if (value is not _MISSING) != bool(operand):
                        return False
                elif operator == "$gte":
                    if value is _MISSING or value < operand:
                        return False
                else:
                    raise AssertionError(f"unsupported query operator: {operator}")
        elif value is _MISSING or value != condition:
            return False
    return True


class _FakeEconomyCollection:
    """A minimal Mongo-like collection that records every write it receives."""

    def __init__(self):
        self.documents: dict[tuple[int, int], dict] = {}
        self.updates: list[tuple[dict, dict, bool]] = []
        self.results: list[_UpdateResult] = []
        self.indexes: list[tuple[dict, bool]] = []

    async def create_index(self, key, unique=False, **kwargs):
        self.indexes.append((key, unique))

    def _find(self, query):
        for document in self.documents.values():
            if _matches(document, query):
                return document
        return None

    async def find_one(self, query, projection=None):
        document = self._find(query)
        return deepcopy(document) if document is not None else None

    async def update_one(self, query, update, upsert=False):
        self.updates.append((deepcopy(query), deepcopy(update), upsert))
        document = self._find(query)
        inserted = False
        if document is None:
            if not upsert:
                result = _UpdateResult()
                self.results.append(result)
                return result
            document = {
                field: value
                for field, value in query.items()
                if not isinstance(value, dict)
            }
            key = (document.get("user_id"), document.get("chat_id"))
            if key in self.documents:
                raise _DuplicateKeyError("E11000 duplicate key error")
            self.documents[key] = document
            inserted = True
        for field, value in update.get("$set", {}).items():
            _write(document, field, deepcopy(value))
        if inserted:
            for field, value in update.get("$setOnInsert", {}).items():
                _write(document, field, deepcopy(value))
        for field, delta in update.get("$inc", {}).items():
            current = _read(document, field)
            _write(document, field, (0 if current is _MISSING else current) + delta)
        for field in update.get("$unset", {}):
            _unset(document, field)
        # PyMongo reports an upsert that inserted the document with no
        # modified document and an inserted id instead.
        result = _UpdateResult(
            modified_count=0 if inserted else 1,
            upserted_id=object() if inserted else None,
        )
        self.results.append(result)
        return result


class _FakeDatabase:
    """A database facade that publishes only the account collection."""

    available = True

    def __init__(self, collection=None):
        self.economy_accounts = collection if collection is not None else _FakeEconomyCollection()


def _event(event_key="4:-100:quiz:1", *, delta=50, direction="credit", amount=50, reason="quiz_reward", moment=None):
    return {
        "user_id": 4,
        "chat_id": -100,
        "event_key": event_key,
        "delta": delta,
        "direction": direction,
        "amount": amount,
        "reason": reason,
        "created_at": moment or datetime(2026, 10, 21, tzinfo=timezone.utc),
    }


@pytest.mark.asyncio
async def test_credit_is_idempotent_for_the_same_event_id():
    collection = _FakeEconomyCollection()
    store = EconomyStore(_FakeDatabase(collection))

    first = await store.apply_event(**_event())
    second = await store.apply_event(**_event())

    assert first is True
    assert second is False
    assert await store.balance(4, -100) == 50


@pytest.mark.asyncio
async def test_balance_and_event_are_applied_in_one_document_update():
    collection = _FakeEconomyCollection()
    store = EconomyStore(_FakeDatabase(collection))

    assert await store.apply_event(**_event()) is True

    # One write carries the balance change and the applied-event stamp, so a
    # crash can never separate them.
    assert len(collection.updates) == 1
    query, update, upsert = collection.updates[0]
    assert query["user_id"] == 4 and query["chat_id"] == -100
    assert query["applied_events.4:-100:quiz:1"] == {"$exists": False}
    assert update["$inc"] == {"balance": 50}
    stamp = update["$set"]["applied_events.4:-100:quiz:1"]
    assert stamp["delta"] == 50
    assert stamp["amount"] == 50
    assert stamp["direction"] == "credit"
    assert stamp["reason"] == "quiz_reward"
    assert stamp["event_key"] == "4:-100:quiz:1"
    assert upsert is True


@pytest.mark.asyncio
async def test_debit_is_refused_without_changing_the_balance():
    collection = _FakeEconomyCollection()
    store = EconomyStore(_FakeDatabase(collection))
    await store.apply_event(**_event())

    refused = await store.apply_event(
        **_event("4:-100:shop:1", delta=-75, direction="debit", amount=75, reason="shop")
    )

    assert refused is False
    assert await store.balance(4, -100) == 50
    # The refusal is decided by the filter, so no write may touch the account.
    query, _update, upsert = collection.updates[-1]
    assert query["balance"] == {"$gte": 75}
    assert upsert is False
    document = collection.documents[(4, -100)]
    assert "applied_events.4:-100:shop:1" not in document.get("applied_events", {})


@pytest.mark.asyncio
async def test_replay_after_process_restart_does_not_double_credit():
    collection = _FakeEconomyCollection()
    database = _FakeDatabase(collection)

    assert await EconomyStore(database).apply_event(**_event()) is True
    # A restarted process builds a new store over the same database.
    replayed = await EconomyStore(database).apply_event(**_event())

    assert replayed is False
    assert await EconomyStore(database).balance(4, -100) == 50


@pytest.mark.asyncio
async def test_applied_events_stay_bounded_and_keep_the_newest_entries():
    collection = _FakeEconomyCollection()
    store = EconomyStore(_FakeDatabase(collection))
    start = datetime(2026, 10, 21, tzinfo=timezone.utc)

    for index in range(EconomyStore.MAX_APPLIED_EVENTS + 5):
        await store.apply_event(
            **_event(
                f"4:-100:event:{index}",
                delta=1,
                amount=1,
                reason="quiz",
                moment=start + timedelta(seconds=index),
            )
        )

    document = collection.documents[(4, -100)]
    applied = document["applied_events"]
    assert len(applied) == EconomyStore.MAX_APPLIED_EVENTS
    assert "4:-100:event:0" not in applied
    assert "4:-100:event:504" in applied
    assert await store.balance(4, -100) == EconomyStore.MAX_APPLIED_EVENTS + 5


@pytest.mark.asyncio
async def test_event_keys_with_mongo_unsafe_characters_stay_distinct():
    collection = _FakeEconomyCollection()
    store = EconomyStore(_FakeDatabase(collection))

    assert await store.apply_event(**_event("4:-100:a.b", delta=1, amount=1)) is True
    assert await store.apply_event(**_event("4:-100:a_b", delta=1, amount=1)) is True

    assert await store.balance(4, -100) == 2


@pytest.mark.asyncio
async def test_a_failed_ledger_write_leaves_no_orphan_state():
    class _FailingCollection(_FakeEconomyCollection):
        async def update_one(self, query, update, upsert=False):
            raise RuntimeError("ledger_write_failed")

    collection = _FailingCollection()
    store = EconomyStore(_FakeDatabase(collection))

    with pytest.raises(RuntimeError, match="ledger_write_failed"):
        await store.apply_event(**_event())

    # The ledger has a single write, so a failed write leaves neither a
    # balance change nor an event row behind.
    assert collection.documents == {}
    assert await store.balance(4, -100) == 0


@pytest.mark.asyncio
async def test_a_first_credit_to_a_new_account_is_applied_from_the_upsert_result():
    collection = _FakeEconomyCollection()
    store = EconomyStore(_FakeDatabase(collection))

    assert await store.apply_event(**_event()) is True

    # An upsert that inserted the account reports no modified document, so
    # the success signal has to include the inserted id.
    result = collection.results[0]
    assert result.modified_count == 0
    assert result.upserted_id is not None
    assert await store.balance(4, -100) == 50


@pytest.mark.asyncio
async def test_the_production_wiring_ensures_the_account_index_before_the_first_write(monkeypatch):
    from config import Config
    from src.database import Database

    monkeypatch.setattr(Config, "APP_ENV", "production")
    collection = _FakeEconomyCollection()
    # The production facade publishes the account collection and reports the
    # database as available.
    monkeypatch.setattr(Database, "_db", {"economy_accounts": collection}, raising=False)
    monkeypatch.setattr(Database, "_last_ping_ok", True, raising=False)

    database = Database.__new__(Database)
    service = EconomyService(database)

    assert service._store.required is True
    assert service.allow_memory_fallback is False
    assert await service.credit(4, -100, 50, reason="quiz_reward", event_id="quiz:1") is True
    assert collection.indexes == [([("user_id", 1), ("chat_id", 1)], True)]


@pytest.mark.asyncio
async def test_the_production_wiring_fails_loudly_when_the_index_cannot_be_created(monkeypatch):
    from config import Config
    from src.database import Database

    class _FailingCollection(_FakeEconomyCollection):
        async def create_index(self, key, unique=False, **kwargs):
            raise RuntimeError("index_unavailable")

    monkeypatch.setattr(Config, "APP_ENV", "production")
    monkeypatch.setattr(Database, "_db", {"economy_accounts": _FailingCollection()}, raising=False)
    monkeypatch.setattr(Database, "_last_ping_ok", True, raising=False)

    database = Database.__new__(Database)
    service = EconomyService(database)

    # A production ledger must not pay a reward into a database whose
    # account identity cannot be guaranteed.
    with pytest.raises(RuntimeError, match="economy_account_index_unavailable"):
        await service.credit(4, -100, 50, reason="quiz_reward", event_id="quiz:1")


@pytest.mark.asyncio
async def test_the_store_guarantees_the_unique_account_index_in_production():
    collection = _FakeEconomyCollection()
    store = EconomyStore(_FakeDatabase(collection), required=True)

    assert await store.apply_event(**_event()) is True

    # The ledger depends on one account document per (user_id, chat_id); the
    # index must exist even when the migration has not run yet.
    assert collection.indexes == [([("user_id", 1), ("chat_id", 1)], True)]
    # The index is created once, not on every write.
    assert await store.apply_event(**_event("4:-100:quiz:2")) is True
    assert len(collection.indexes) == 1


@pytest.mark.asyncio
async def test_a_concurrent_account_creation_does_not_lose_the_reward():
    class _RacingCollection(_FakeEconomyCollection):
        """Model a concurrent writer that creates the account first."""

        def __init__(self):
            super().__init__()
            self.raced = False

        async def update_one(self, query, update, upsert=False):
            if not self.raced and upsert:
                self.raced = True
                # Another writer created the account with its own event.
                self.documents[(4, -100)] = {
                    "user_id": 4,
                    "chat_id": -100,
                    "balance": 7,
                    "applied_events": {
                        "4:-100:other": {"event_key": "4:-100:other", "delta": 7, "amount": 7, "direction": "credit", "reason": "quiz", "at": datetime(2026, 10, 21, tzinfo=timezone.utc)},
                    },
                }
                raise _DuplicateKeyError("E11000 duplicate key error")
            return await super().update_one(query, update, upsert=upsert)

    collection = _RacingCollection()
    store = EconomyStore(_FakeDatabase(collection))

    # The duplicate key must not swallow the reward: the event is still
    # unapplied, so the ledger retries and applies it.
    assert await store.apply_event(**_event()) is True
    assert await store.balance(4, -100) == 57


@pytest.mark.asyncio
async def test_a_concurrent_duplicate_of_the_same_event_is_refused():
    class _RacingCollection(_FakeEconomyCollection):
        """Model a concurrent writer that applies the same event first."""

        def __init__(self):
            super().__init__()
            self.raced = False

        async def update_one(self, query, update, upsert=False):
            if not self.raced and upsert:
                self.raced = True
                self.documents[(4, -100)] = {
                    "user_id": 4,
                    "chat_id": -100,
                    "balance": 50,
                    "applied_events": {
                        "4:-100:quiz:1": {"event_key": "4:-100:quiz:1", "delta": 50, "amount": 50, "direction": "credit", "reason": "quiz_reward", "at": datetime(2026, 10, 21, tzinfo=timezone.utc)},
                    },
                }
                raise _DuplicateKeyError("E11000 duplicate key error")
            return await super().update_one(query, update, upsert=upsert)

    collection = _RacingCollection()
    store = EconomyStore(_FakeDatabase(collection))

    assert await store.apply_event(**_event()) is False
    assert await store.balance(4, -100) == 50


@pytest.mark.asyncio
async def test_service_credits_without_a_separate_transaction_row():
    collection = _FakeEconomyCollection()
    database = _FakeDatabase(collection)
    # The account document is the only ledger.  There is no transaction
    # collection to write a second, non-atomic row into.
    assert not hasattr(database, "economy_transactions")
    service = EconomyService(database)

    assert await service.credit(4, -100, 50, reason="quiz_reward", event_id="quiz:1") is True
    assert await service.credit(4, -100, 50, reason="quiz_reward", event_id="quiz:1") is False
    assert await service.balance(4, -100) == 50
    assert len(collection.updates) == 1


@pytest.mark.asyncio
async def test_service_history_reports_the_applied_events():
    collection = _FakeEconomyCollection()
    service = EconomyService(_FakeDatabase(collection))

    await service.credit(4, -100, 50, reason="quiz_reward", event_id="quiz:1")
    await service.debit(4, -100, 20, reason="shop", event_id="shop:1")

    history = await service.history(4, -100, limit=5)
    assert len(history) == 2
    assert {row["event_key"] for row in history} == {"4:-100:quiz:1", "4:-100:shop:1"}
    for row in history:
        assert set(row) == {
            "event_key",
            "user_id",
            "chat_id",
            "amount",
            "direction",
            "delta",
            "reason",
            "created_at",
        }


@pytest.mark.asyncio
async def test_economy_credit_is_idempotent_and_debit_is_bounded():
    service = EconomyService(db=None)

    assert await service.credit(1, -100, 50, reason="quiz", event_id="quiz:1") is True
    assert await service.credit(1, -100, 50, reason="quiz", event_id="quiz:1") is False
    assert await service.balance(1, -100) == 50
    assert await service.debit(1, -100, 75, reason="shop", event_id="shop:1") is False
    assert await service.balance(1, -100) == 50


@pytest.mark.asyncio
async def test_economy_daily_reward_has_cooldown_and_group_scope():
    service = EconomyService(db=None)

    first = await service.claim_daily(7, -100, now=datetime(2026, 10, 5, tzinfo=timezone.utc))
    second = await service.claim_daily(7, -100, now=datetime(2026, 10, 5, 12, tzinfo=timezone.utc))
    next_day = await service.claim_daily(7, -100, now=datetime(2026, 10, 6, tzinfo=timezone.utc))

    assert first["accepted"] is True
    assert second["accepted"] is False
    assert next_day["accepted"] is True
    assert await service.balance(7, -100) == first["amount"] + next_day["amount"]
    assert await service.balance(7, -101) == 0


@pytest.mark.asyncio
async def test_economy_rejects_invalid_amounts():
    service = EconomyService(db=None)
    with pytest.raises(ValueError, match="amount_out_of_range"):
        await service.credit(1, 1, 0, reason="bad")
    with pytest.raises(ValueError, match="amount_out_of_range"):
        await service.credit(1, 1, 10001, reason="bad")


@pytest.mark.asyncio
async def test_economy_requires_stable_event_ids_for_mutations():
    service = EconomyService(db=None)
    with pytest.raises(ValueError, match="event_id_required"):
        await service.credit(1, -100, 1, reason="quiz_reward")
    with pytest.raises(ValueError, match="event_id_required"):
        await service.debit(1, -100, 1, reason="shop")


@pytest.mark.asyncio
async def test_regular_game_rewards_have_a_separate_payout_cap():
    service = EconomyService(db=None)
    with pytest.raises(ValueError, match="reward_amount_out_of_range"):
        await service.credit(1, -100, 1001, reason="quiz_reward", event_id="quiz:large")
    assert await service.credit(1, -100, 1001, reason="blackjack_settlement", event_id="blackjack:1") is True


@pytest.mark.asyncio
async def test_economy_rejects_invalid_identity_reason_and_event_values():
    service = EconomyService(db=None)
    with pytest.raises(ValueError, match="invalid_user_id"):
        await service.credit(0, -100, 1, reason="quiz", event_id="quiz:1")
    with pytest.raises(ValueError, match="invalid_chat_id"):
        await service.credit(1, 0, 1, reason="quiz", event_id="quiz:1")
    with pytest.raises(ValueError, match="reason_invalid"):
        await service.credit(1, -100, 1, reason="", event_id="quiz:1")
    with pytest.raises(ValueError, match="event_id_invalid"):
        await service.credit(1, -100, 1, reason="quiz", event_id="\x00bad")


@pytest.mark.asyncio
async def test_production_like_unavailable_database_does_not_award_memory_coins():
    class UnavailableDatabase:
        available = False

    service = EconomyService(UnavailableDatabase())
    with pytest.raises(RuntimeError, match="economy_persistence_unavailable"):
        await service.credit(1, -100, 1, reason="quiz_reward", event_id="quiz:1")


@pytest.mark.asyncio
async def test_economy_commands_render_user_scoped_balance():
    service = EconomyService(db=None)
    await service.credit(3, -100, 40, reason="quiz", event_id="quiz:1")
    sent = []

    class Message:
        async def reply_text(self, text, **kwargs):
            sent.append(text)

    update = type("U", (), {
        "effective_user": type("User", (), {"id": 3})(),
        "effective_chat": type("Chat", (), {"id": -100})(),
        "effective_message": Message(),
    })()
    handler = CommandHandler(None, None, None, None, economy_service=service)
    await handler.handle_economy_command(update, None)

    assert sent and "40" in sent[0]


@pytest.mark.asyncio
async def test_quiz_reward_is_idempotent_with_score_event():
    class DB:
        async def get_game_state(self, chat_id, game_type):
            return None

        async def delete_game_state(self, chat_id, game_type):
            return None

    class Leaderboard:
        async def record_score(self, *args, **kwargs):
            return True

    economy = EconomyService(db=None)
    handler = CallbackHandler(
        type("Engine", (), {"db": DB()})(),
        None,
        None,
        leaderboard_service=Leaderboard(),
        economy_service=economy,
    )
    query = type("Query", (), {
        "message": type("M", (), {"message_id": 44})(),
        "edit_message_text": AsyncMock(),
        "answer": AsyncMock(),
    })()
    update = type("U", (), {
        "effective_user": type("User", (), {"id": 7, "first_name": "Arel"})(),
        "effective_chat": type("Chat", (), {"id": -100})(),
    })()
    context = type("C", (), {"chat_data": {"quiz_answer": "Ankara", "quiz_question": "Başkent?"}})()

    await handler._handle_quiz_answer(update, query, context, "quiz_ans_0_Ankara")
    assert await economy.balance(7, -100) == 5
