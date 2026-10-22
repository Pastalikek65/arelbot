"""Persistent economy receipts, daily progression and atomic shop transitions."""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from bson import BSON

from src.storage.economy_store import MAX_RECEIPT_BYTES, EconomyStore
from src.tests.test_economy_service import (
    _DuplicateKeyError,
    _FakeDatabase,
    _read,
    _unset,
    _UpdateResult,
    _write,
)

MISSING = object()
NOW = datetime(2026, 10, 21, tzinfo=UTC)


def matches(document, query):
    for field, condition in query.items():
        value = _read(document, field)
        absent = value.__class__ is object
        if isinstance(condition, dict):
            for op, expected in condition.items():
                if op == "$exists" and (not absent) != expected:
                    return False
                if op == "$gte" and (absent or value < expected):
                    return False
                if op == "$ne" and not absent and (
                        value == expected or isinstance(value, list) and expected in value):
                    return False
        elif absent or value != condition and not (isinstance(value, list) and condition in value):
            return False
    return True


class BSONCollection:
    def __init__(self):
        self.documents = {}
        self.lock = asyncio.Lock()
        self.updates = []

    async def create_index(self, *_args, **_kwargs):
        return None

    async def find_one(self, query, _projection=None):
        async with self.lock:
            return next((BSON(raw).decode() for raw in self.documents.values()
                         if matches(BSON(raw).decode(), query)), None)

    async def update_one(self, query, update, upsert=False):
        async with self.lock:
            self.updates.append((deepcopy(query), deepcopy(update), upsert))
            existing_key = next((key for key, raw in self.documents.items()
                                 if matches(BSON(raw).decode(), query)), None)
            inserted = False
            if existing_key is None:
                if not upsert:
                    return _UpdateResult()
                account = (query["user_id"], query["chat_id"])
                if account in self.documents:
                    raise _DuplicateKeyError("E11000 duplicate key")
                document = {"user_id": account[0], "chat_id": account[1]}
                inserted = True
                existing_key = account
            else:
                document = BSON(self.documents[existing_key]).decode()
            for field, value in update.get("$set", {}).items():
                _write(document, field, deepcopy(value))
            if inserted:
                for field, value in update.get("$setOnInsert", {}).items():
                    _write(document, field, deepcopy(value))
            for field, amount in update.get("$inc", {}).items():
                old = _read(document, field)
                _write(document, field, (0 if old.__class__ is object else old) + amount)
            for field, value in update.get("$addToSet", {}).items():
                old = _read(document, field)
                values = [] if old.__class__ is object else list(old)
                if value not in values:
                    values.append(value)
                _write(document, field, values)
            for field in update.get("$unset", {}):
                _unset(document, field)
            self.documents[existing_key] = BSON.encode(document)
            return _UpdateResult(0 if inserted else 1, object() if inserted else None)

    def account(self, uid=4, cid=-100):
        return BSON(self.documents[(uid, cid)]).decode()


def credit_kwargs(event, amount=1, at=NOW):
    return {"user_id": 4, "chat_id": -100, "event_key": event,
            "delta": amount, "direction": "credit", "amount": amount,
            "reason": "quiz_reward", "created_at": at}


@pytest.mark.asyncio
async def test_legacy_and_new_event_replays_after_505_entries_and_restart():
    collection = BSONCollection()
    first = EconomyStore(_FakeDatabase(collection))
    for number in range(505):
        assert await first.apply_event(**credit_kwargs(f"4:-100:event:{number}",
            at=NOW + timedelta(seconds=number)))
    account = collection.account()
    assert len(account["applied_events"]) == 500
    assert len(account["receipts"]) == 505
    assert len(collection.documents[(4, -100)]) < 16 * 1024 * 1024
    restarted = EconomyStore(_FakeDatabase(collection))
    assert not await restarted.apply_event(**credit_kwargs("4:-100:event:0"))
    assert await restarted.balance(4, -100) == 505


@pytest.mark.asyncio
async def test_event_applied_reads_pruned_receipt_only_for_exact_account():
    class ProjectionCollection(BSONCollection):
        last_query = None
        last_projection = None

        async def find_one(self, query, projection=None):
            self.last_query = deepcopy(query)
            self.last_projection = deepcopy(projection)
            return await super().find_one(query, projection)

    collection = ProjectionCollection()
    first = EconomyStore(_FakeDatabase(collection))
    for number in range(502):
        assert await first.apply_event(**credit_kwargs(f"4:-100:bet:{number}",
            at=NOW + timedelta(seconds=number)))
    assert "4:-100:bet:0" not in collection.account()["applied_events"]
    restarted = EconomyStore(_FakeDatabase(collection))
    assert await restarted.event_applied(4, -100, "4:-100:bet:0")
    assert collection.last_query == {"user_id": 4, "chat_id": -100}
    assert collection.last_projection["_id"] == 0
    assert len(collection.last_projection) == 3
    assert not await restarted.event_applied(5, -100, "4:-100:bet:0")
    assert not await restarted.event_applied(4, -101, "4:-100:bet:0")
    assert not await restarted.event_applied(4, -100, "4:-100:bet:missing")


@pytest.mark.asyncio
async def test_event_applied_recognizes_legacy_and_fails_closed_on_collision_or_corruption(monkeypatch):
    from src.storage import economy_store as module

    collection = BSONCollection()
    old_key = "4:-100:old.bet"
    safe_key = EconomyStore._legacy_safe_event_key(old_key)
    collection.documents[(4, -100)] = BSON.encode({"user_id": 4, "chat_id": -100,
        "balance": 1, "applied_events": {safe_key: {"event_key": old_key}}})
    store = EconomyStore(_FakeDatabase(collection))
    assert await store.event_applied(4, -100, old_key)
    assert not await store.event_applied(4, -100, "4:-100:absent")
    monkeypatch.setattr(EconomyStore, "_legacy_safe_event_key", staticmethod(lambda _key: safe_key))
    with pytest.raises(RuntimeError, match="economy_legacy_key_collision"):
        await store.event_applied(4, -100, "different")
    monkeypatch.undo()
    receipt_key = module._receipt_key(old_key)
    document = collection.account()
    document["receipts"] = {receipt_key: b"corrupt"}
    collection.documents[(4, -100)] = BSON.encode(document)
    with pytest.raises(RuntimeError, match="economy_receipt_corrupt"):
        await store.event_applied(4, -100, old_key)


@pytest.mark.asyncio
async def test_prune_backfills_legacy_receipts_atomically_and_detects_payload_reuse():
    collection = BSONCollection()
    legacy = {f"4:-100:old:{number}": {"event_key": f"4:-100:old:{number}",
        "amount": 1, "delta": 1, "direction": "credit", "reason": "quiz_reward",
        "at": NOW + timedelta(seconds=number)} for number in range(503)}
    collection.documents[(4, -100)] = BSON.encode({"user_id": 4, "chat_id": -100,
        "balance": 503, "applied_events": legacy})
    assert await EconomyStore.prune_account(collection, 4, -100) == 3
    account = collection.account()
    assert len(account["applied_events"]) == 500 and len(account["receipts"]) == 3
    store = EconomyStore(_FakeDatabase(collection))
    assert not await store.apply_event(**credit_kwargs("4:-100:old:0"))
    with pytest.raises(ValueError, match="economy_event_payload_mismatch"):
        await store.apply_event(**credit_kwargs("4:-100:old:0", amount=9))
    assert await store.balance(4, -100) == 503


@pytest.mark.asyncio
async def test_daily_legacy_local_day_and_streak_after_restart():
    collection = BSONCollection()
    at = datetime(2026, 10, 21, 22, 30, tzinfo=UTC)  # Istanbul: 22 Oct
    old_key = "4:-100:daily:2026-10-21"
    collection.documents[(4, -100)] = BSON.encode({"user_id": 4, "chat_id": -100,
        "balance": 25, "applied_events": {old_key: {"event_key": old_key,
            "amount": 25, "delta": 25, "direction": "credit", "reason": "daily_reward", "at": at}}})
    store = EconomyStore(_FakeDatabase(collection))
    assert (await store.snapshot(4, -100))["daily_day"] == "2026-10-22"
    denied = await store.daily(4, -100, "2026-10-22", (25, 30, 35), at)
    assert denied["accepted"] is False and denied["balance"] == 25
    granted = await EconomyStore(_FakeDatabase(collection)).daily(
        4, -100, "2026-10-23", (25, 30, 35), at + timedelta(days=1))
    assert granted["accepted"] and granted["amount"] == 30 and granted["streak"] == 2
    assert not (await store.daily(4, -100, "2026-10-23", (25, 30, 35), at + timedelta(days=1)))["accepted"]
    assert await store.balance(4, -100) == 55


@pytest.mark.asyncio
async def test_two_store_purchase_single_debit_ownership_and_equip():
    collection = BSONCollection()
    first, second = EconomyStore(_FakeDatabase(collection)), EconomyStore(_FakeDatabase(collection))
    assert await first.apply_event(**credit_kwargs("fund", amount=100))
    outcomes = await asyncio.gather(
        first.purchase(4, -100, "gold_frame", 70, "buy:1", NOW),
        second.purchase(4, -100, "gold_frame", 70, "buy:2", NOW),
    )
    assert sorted(outcomes) == [False, True]
    assert await first.balance(4, -100) == 30
    assert not await first.purchase(4, -100, "gold_frame", 70, "buy:1", NOW)
    assert not await first.purchase(4, -100, "other", 70, "buy:3", NOW)
    assert not await second.equip(4, -100, "other", "frame")
    assert await second.equip(4, -100, "gold_frame", "frame")
    assert (await first.snapshot(4, -100))["equipped"] == {"frame": "gold_frame"}


@pytest.mark.asyncio
async def test_reward_day_cap_stamps_zero_remaining_event_and_replay():
    collection = BSONCollection()
    store = EconomyStore(_FakeDatabase(collection))
    for event, amount in (("game:1", 90), ("game:2", 90), ("game:3", 10)):
        assert await store.apply_event(**credit_kwargs(event, amount=amount),
            reward_day="2026-10-22", reward_limit=150)
    assert await store.balance(4, -100) == 150
    assert not await EconomyStore(_FakeDatabase(collection)).apply_event(
        **credit_kwargs("game:3", amount=10), reward_day="2026-10-22", reward_limit=150)
    account = collection.account()
    assert account["applied_events"]["game:3"]["amount"] == 0
    assert account["reward_earned"] == 150


@pytest.mark.asyncio
async def test_game_reward_replay_after_midnight_and_limit_change_keeps_original_bucket():
    collection = BSONCollection()
    first = EconomyStore(_FakeDatabase(collection))
    assert await first.apply_event(**credit_kwargs("game:overnight", amount=80),
        reward_day="2026-10-22", reward_limit=150)
    before = collection.account()
    assert before["reward_day"] == "2026-10-22"
    assert before["reward_earned"] == 80
    restarted = EconomyStore(_FakeDatabase(collection))
    assert not await restarted.apply_event(**credit_kwargs("game:overnight", amount=80,
        at=NOW + timedelta(days=1)), reward_day="2026-10-23", reward_limit=100)
    after = collection.account()
    assert after["balance"] == 80
    assert after["reward_day"] == "2026-10-22"
    assert after["reward_earned"] == 80


@pytest.mark.asyncio
async def test_capped_event_prune_preserves_requested_payload_receipt_across_retry():
    collection = BSONCollection()
    store = EconomyStore(_FakeDatabase(collection))
    assert await store.apply_event(**credit_kwargs("game:budget-fill", amount=100),
        reward_day="2026-10-22", reward_limit=100)
    assert await store.apply_event(**credit_kwargs("game:capped", amount=80,
        at=NOW + timedelta(seconds=1)), reward_day="2026-10-22", reward_limit=100)
    assert collection.account()["applied_events"]["game:capped"]["amount"] == 0
    for number in range(500):
        assert await store.apply_event(**credit_kwargs(f"event:next:{number}",
            at=NOW + timedelta(seconds=number + 2)))
    assert "game:capped" not in collection.account()["applied_events"]
    restarted = EconomyStore(_FakeDatabase(collection))
    assert not await restarted.apply_event(**credit_kwargs("game:capped", amount=80),
        reward_day="2026-10-23", reward_limit=200)
    assert await restarted.balance(4, -100) == 600


@pytest.mark.asyncio
async def test_daily_same_event_replay_after_policy_amount_change():
    collection = BSONCollection()
    store = EconomyStore(_FakeDatabase(collection))
    first = await store.daily(4, -100, "2026-10-22", (25, 30), NOW)
    assert first["accepted"] and first["amount"] == 25
    second = await EconomyStore(_FakeDatabase(collection)).daily(
        4, -100, "2026-10-22", (50, 60), NOW + timedelta(hours=1))
    assert not second["accepted"]
    assert second["balance"] == 25


@pytest.mark.asyncio
async def test_daily_two_instances_only_one_claim_and_purchase_event_mismatch():
    collection = BSONCollection()
    first, second = EconomyStore(_FakeDatabase(collection)), EconomyStore(_FakeDatabase(collection))
    claims = await asyncio.gather(
        first.daily(4, -100, "2026-10-21", (25, 30), NOW),
        second.daily(4, -100, "2026-10-21", (25, 30), NOW),
    )
    assert sorted(item["accepted"] for item in claims) == [False, True]
    assert await first.balance(4, -100) == 25
    assert await first.purchase(4, -100, "badge", 20, "buy:badge", NOW)
    with pytest.raises(ValueError, match="economy_event_payload_mismatch"):
        await second.purchase(4, -100, "other", 10, "buy:badge", NOW)
    assert await second.balance(4, -100) == 5


@pytest.mark.asyncio
async def test_forced_receipt_hash_collision_and_capacity_fail_closed(monkeypatch):
    from src.storage import economy_store as module

    collection = BSONCollection()
    store = EconomyStore(_FakeDatabase(collection))
    assert await store.apply_event(**credit_kwargs("first"))
    original = module._receipt_key
    monkeypatch.setattr(module, "_receipt_key", lambda _key: original("first"))
    with pytest.raises(RuntimeError, match="economy_receipt_collision"):
        await store.apply_event(**credit_kwargs("second"))
    monkeypatch.setattr(module, "_receipt_key", original)
    monkeypatch.setattr(module, "MAX_RECEIPT_BYTES", collection.account()["receipt_bytes"])
    with pytest.raises(RuntimeError, match="economy_receipt_capacity_reached"):
        await store.apply_event(**credit_kwargs("second"))
    assert await store.balance(4, -100) == 1


@pytest.mark.asyncio
async def test_legacy_unsafe_event_key_replay_preserved():
    from src.storage import economy_store as module

    raw = "4:-100:old.name"
    old_safe = module.EconomyStore._legacy_safe_event_key(raw)
    collection = BSONCollection()
    collection.documents[(4, -100)] = BSON.encode({"user_id": 4, "chat_id": -100,
        "balance": 9, "applied_events": {old_safe: {"event_key": raw,
            "amount": 9, "delta": 9, "direction": "credit",
            "reason": "quiz_reward", "at": NOW}}})
    store = EconomyStore(_FakeDatabase(collection))
    assert not await store.apply_event(**credit_kwargs(raw, amount=9))
    assert await store.balance(4, -100) == 9


def test_compact_receipt_budget_handles_100k_short_events_below_bson_limit():
    from src.storage.economy_store import _receipt_key, _receipt_size, _receipt_value

    receipts = {_receipt_key(f"u:c:g:{number}"): _receipt_value(f"u:c:g:{number}", b"x" * 32)
                for number in range(100_000)}
    size = _receipt_size(receipts)
    assert size <= MAX_RECEIPT_BYTES
    assert size < 16 * 1024 * 1024


@pytest.mark.asyncio
async def test_store_rejects_positive_debit_and_negative_credit_without_write():
    collection = BSONCollection()
    store = EconomyStore(_FakeDatabase(collection))
    with pytest.raises(ValueError, match="economy_delta_invalid"):
        await store.apply_event(user_id=4, chat_id=-100, event_key="bad:debit",
            delta=10, direction="debit", amount=10, reason="bad", created_at=NOW)
    with pytest.raises(ValueError, match="economy_delta_invalid"):
        await store.apply_event(user_id=4, chat_id=-100, event_key="bad:credit",
            delta=-10, direction="credit", amount=10, reason="bad", created_at=NOW)
    assert not collection.documents
