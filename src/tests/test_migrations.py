from __future__ import annotations

import logging
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from src.storage.migrations import Migration, MigrationRunner


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    async def to_list(self, length=None):
        return list(self.rows)


class _Collection:
    def __init__(self):
        self.rows = []
        self.indexes = []

    async def create_index(self, field, unique=False):
        self.indexes.append((field, unique))

    def find(self, *_args):
        return _Cursor(self.rows)

    async def insert_one(self, row):
        if any(item["version"] == row["version"] for item in self.rows):
            raise RuntimeError("duplicate_version")
        self.rows.append(dict(row))

    async def delete_one(self, query):
        self.rows[:] = [row for row in self.rows if row.get("version") != query.get("version")]


class _Database:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, _Collection())


@pytest.mark.asyncio
async def test_migrations_apply_once_and_record_version():
    database = _Database()
    applied = []

    async def up_one(_db):
        applied.append("one")

    async def up_two(_db):
        applied.append("two")

    runner = MigrationRunner(database, [
        Migration(1, "base", up_one),
        Migration(2, "notification-ledger", up_two),
    ])
    assert await runner.migrate() == 2
    assert await runner.migrate() == 2
    assert applied == ["one", "two"]
    assert [row["version"] for row in database["schema_migrations"].rows] == [1, 2]


@pytest.mark.asyncio
async def test_migration_rollback_requires_explicit_down_action():
    database = _Database()

    async def up(_db):
        return None

    runner = MigrationRunner(database, [Migration(1, "irreversible", up)])
    await runner.migrate()
    with pytest.raises(RuntimeError, match="migration_not_reversible:1"):
        await runner.rollback(0)
    assert await runner.applied()


@pytest.mark.asyncio
async def test_reversible_migration_rolls_back_in_reverse_order():
    database = _Database()
    events = []

    async def up_one(_db):
        events.append("up1")

    async def down_one(_db):
        events.append("down1")

    async def up_two(_db):
        events.append("up2")

    async def down_two(_db):
        events.append("down2")

    runner = MigrationRunner(database, [
        Migration(1, "one", up_one, down_one),
        Migration(2, "two", up_two, down_two),
    ])
    await runner.migrate()
    assert await runner.rollback(0) == 0
    assert events == ["up1", "up2", "down2", "down1"]


_MISSING = object()


def _mongo_type(value):
    if value is _MISSING or value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return "unknown"


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


def _evaluate(expression, document):
    """Evaluate the aggregation expressions the economy migration uses."""
    if isinstance(expression, dict) and expression:
        if set(expression) == {"$convert"}:
            spec = expression["$convert"]
            value = _evaluate(spec.get("input"), document)
            target = spec.get("to")
            try:
                if target in {"int", "long"}:
                    return int(value)
                if target == "double":
                    return float(value)
                if target == "string":
                    return str(value)
            except (TypeError, ValueError):
                return spec.get("onError", 0)
            return spec.get("onError", 0)
        if set(expression) == {"$ifNull"}:
            for alternative in expression["$ifNull"]:
                value = _evaluate(alternative, document)
                if value is not None:
                    return value
            return None
    if isinstance(expression, str) and expression.startswith("$"):
        return document.get(expression[1:], None)
    return expression


def _matches(document, query):
    for field, condition in query.items():
        value = _read(document, field)
        if isinstance(condition, dict) and condition:
            for operator, operand in condition.items():
                if operator == "$not":
                    inner = operand
                    assert set(inner) == {"$type"}, f"unsupported query operator: {inner}"
                    if _mongo_type(value) == inner["$type"]:
                        return False
                    continue
                if operator == "$exists":
                    if (value is not _MISSING) != bool(operand):
                        return False
                    continue
                if operator == "$gte":
                    if value is _MISSING or value < operand:
                        return False
                    continue
                raise AssertionError(f"unsupported query operator: {operator}")
        elif value is _MISSING or value != condition:
            return False
    return True


def _sort_key(value):
    return (0, "") if value is _MISSING else (1, value)


class _MongoResult:
    def __init__(self, modified_count=0, upserted_id=None):
        self.modified_count = modified_count
        self.upserted_id = upserted_id
        self.matched_count = modified_count


class _MongoCursor:
    def __init__(self, rows):
        self._rows = list(rows)
        self._sort = None
        self._limit = None
        self.materialized = False
        self.streamed = False

    def sort(self, field, direction=1):
        self._sort = (field, direction)
        return self

    def limit(self, count):
        self._limit = count
        return self

    def _resolved(self):
        rows = self._rows
        if self._sort is not None:
            field, direction = self._sort
            rows = sorted(rows, key=lambda row: _sort_key(_read(row, field)), reverse=direction < 0)
        if self._limit is not None:
            rows = rows[: self._limit]
        return rows

    async def to_list(self, length=None):
        self.materialized = True
        rows = self._resolved()
        if length is not None:
            rows = rows[:length]
        return [deepcopy(row) for row in rows]

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        self.streamed = True
        for row in self._resolved():
            yield deepcopy(row)


class _MongoCollection:
    """A small Mongo-like collection for the economy ledger migration."""

    def __init__(self):
        self.documents: list[dict] = []
        self.indexes: list[dict] = []
        self.aggregations: list[dict] = []
        self.cursors: list[_MongoCursor] = []
        self.updates: list[tuple[dict, dict, bool]] = []

    async def create_index(self, key, unique=False, **kwargs):
        self.indexes.append({"key": key, "unique": unique, **kwargs})

    async def drop_index(self, index):
        self.indexes[:] = [
            item for item in self.indexes
            if item.get("key") != index and item.get("name") != index
        ]

    def find(self, query=None, projection=None):
        rows = [row for row in self.documents if _matches(row, query or {})]
        cursor = _MongoCursor(rows)
        self.cursors.append(cursor)
        return cursor

    def aggregate(self, pipeline, **kwargs):
        self.aggregations.append({"pipeline": pipeline, "options": kwargs})
        rows = list(self.documents)
        for stage in pipeline:
            assert set(stage) == {"$group"}, f"unsupported aggregation stage: {stage}"
            identity = stage["$group"]["_id"]
            grouped: dict[tuple, list[dict]] = {}
            for row in rows:
                key = tuple(_evaluate(part, row) for part in identity.values())
                grouped.setdefault(key, []).append(row)
            rows = [{"_id": dict(zip(identity.keys(), key))} for key in grouped]
        cursor = _MongoCursor(rows)
        self.cursors.append(cursor)
        return cursor

    async def find_one(self, query, projection=None):
        document = next((row for row in self.documents if _matches(row, query)), None)
        return deepcopy(document) if document is not None else None

    async def insert_one(self, document):
        self.documents.append(deepcopy(document))

    async def update_one(self, query, update, upsert=False):
        self.updates.append((deepcopy(query), deepcopy(update), upsert))
        document = next((row for row in self.documents if _matches(row, query)), None)
        inserted = False
        if document is None:
            if not upsert:
                return _MongoResult()
            document = {
                field: value
                for field, value in query.items()
                if not isinstance(value, dict)
            }
            self.documents.append(document)
            inserted = True
        self._apply(document, update, inserted)
        return _MongoResult(
            modified_count=0 if inserted else 1,
            upserted_id="upserted" if inserted else None,
        )

    async def update_many(self, query, update):
        matched = 0
        for document in self.documents:
            if not _matches(document, query):
                continue
            self._apply(document, update, False)
            matched += 1
        return _MongoResult(modified_count=matched)

    def _apply(self, document, update, inserted):
        for operator in ("$set", "$setOnInsert", "$inc", "$unset"):
            if operator in update:
                assert update[operator], f"empty update operator: {operator}"
        for field, value in update.get("$set", {}).items():
            _write(document, field, deepcopy(_evaluate(value, document)))
        if inserted:
            for field, value in update.get("$setOnInsert", {}).items():
                _write(document, field, deepcopy(value))
        for field, delta in update.get("$inc", {}).items():
            current = _read(document, field)
            _write(document, field, (0 if current is _MISSING else current) + delta)
        for field in update.get("$unset", {}):
            _unset(document, field)


class _MongoDatabase:
    def __init__(self):
        self.collections: dict[str, _MongoCollection] = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, _MongoCollection())


class _StoreDatabase:
    """A database facade that publishes the migrated account collection."""

    available = True

    def __init__(self, database: _MongoDatabase):
        self.economy_accounts = database["economy_accounts"]


def _economy_migration():
    from src.database import Database

    return next(
        item for item in Database.__new__(Database).migration_runner().migrations
        if item.version == 20
    )


def _daily_transaction(user_id=4, chat_id=-100, day="2026-10-21"):
    return {
        "event_key": f"{user_id}:{chat_id}:daily:{day}",
        "user_id": user_id,
        "chat_id": chat_id,
        "amount": 25,
        "direction": "credit",
        "delta": 25,
        "reason": "daily_reward",
        "created_at": datetime(2026, 10, 21, 8, 0, tzinfo=timezone.utc),
    }


def test_database_registers_reversible_economy_ledger_migration_v20():
    from src.database import Database

    class Mongo:
        def __getitem__(self, name):
            return object()

    previous = Database._db
    Database._db = Mongo()
    try:
        runner = Database.__new__(Database).migration_runner()
        migration = next(item for item in runner.migrations if item.version == 20)
        assert migration.name == "economy-atomic-account-ledger"
        assert migration.down is not None
        assert max(item.version for item in runner.migrations) == 22
    finally:
        Database._db = previous


@pytest.mark.asyncio
async def test_economy_ledger_migration_normalizes_accounts_and_is_reversible():
    from src.database import Database

    previous = Database._db
    database = _MongoDatabase()
    Database._db = database
    try:
        migration = _economy_migration()
        accounts = database["economy_accounts"]
        accounts.documents.append({"user_id": 4, "chat_id": -100, "balance": "12"})
        accounts.documents.append({"user_id": 5, "chat_id": -100, "balance": 7})

        await migration.up(database)

        # The account identity is pinned to the base schema declaration: a
        # named index would raise IndexOptionsConflict against v1.
        assert accounts.indexes == [{"key": [("user_id", 1), ("chat_id", 1)], "unique": True}]
        # A numeric string keeps its value, only an unreadable value falls
        # back to zero.
        assert accounts.documents[0]["balance"] == 12
        assert accounts.documents[1]["balance"] == 7
        assert accounts.documents[0]["applied_events"] == {}
        assert accounts.documents[1]["applied_events"] == {}

        await migration.down(database)

        assert accounts.indexes == []
        assert "applied_events" not in accounts.documents[0]
        assert "applied_events" not in accounts.documents[1]
    finally:
        Database._db = previous


@pytest.mark.asyncio
async def test_economy_ledger_migration_seeds_pre_migration_events():
    from src.database import Database
    from src.services.economy_service import EconomyService

    previous = Database._db
    database = _MongoDatabase()
    Database._db = database
    try:
        migration = _economy_migration()
        accounts = database["economy_accounts"]
        transactions = database["economy_transactions"]
        accounts.documents.append({"user_id": 4, "chat_id": -100, "balance": 25})
        transactions.documents.append(_daily_transaction())

        await migration.up(database)

        account = accounts.documents[0]
        assert "4:-100:daily:2026-10-21" in account["applied_events"]
        seeded = account["applied_events"]["4:-100:daily:2026-10-21"]
        assert seeded["delta"] == 25
        assert seeded["amount"] == 25
        assert seeded["direction"] == "credit"
        assert seeded["reason"] == "daily_reward"
        assert seeded["event_key"] == "4:-100:daily:2026-10-21"

        # The already paid daily reward is refused after the migration, so
        # the same-day claim cannot be paid twice.
        service = EconomyService(_StoreDatabase(database))
        result = await service.claim_daily(4, -100, now=datetime(2026, 10, 21, tzinfo=timezone.utc))
        assert result["accepted"] is False
        assert result["balance"] == 25
    finally:
        Database._db = previous


@pytest.mark.asyncio
async def test_economy_ledger_migration_seed_is_idempotent():
    from src.database import Database

    previous = Database._db
    database = _MongoDatabase()
    Database._db = database
    try:
        migration = _economy_migration()
        accounts = database["economy_accounts"]
        transactions = database["economy_transactions"]
        accounts.documents.append({"user_id": 4, "chat_id": -100, "balance": 25})
        transactions.documents.append(_daily_transaction())
        transactions.documents.append(
            {
                "event_key": "4:-100:quiz:1",
                "user_id": 4,
                "chat_id": -100,
                "amount": 5,
                "direction": "credit",
                "delta": 5,
                "reason": "quiz_reward",
                "created_at": datetime(2026, 10, 20, 8, 0, tzinfo=timezone.utc),
            }
        )

        await migration.up(database)
        first = deepcopy(accounts.documents[0]["applied_events"])
        await migration.up(database)

        assert accounts.documents[0]["applied_events"] == first
        assert len(first) == 2
    finally:
        Database._db = previous


@pytest.mark.asyncio
async def test_economy_ledger_migration_reports_a_group_without_an_account(caplog):
    from src.database import Database

    previous = Database._db
    database = _MongoDatabase()
    Database._db = database
    try:
        migration = _economy_migration()
        transactions = database["economy_transactions"]
        # A transaction group without an account document cannot be seeded.
        transactions.documents.append(_daily_transaction(user_id=5, chat_id=-101))

        with caplog.at_level(logging.WARNING):
            await migration.up(database)

        assert any("Economy ledger seed skipped" in record.message for record in caplog.records)
    finally:
        Database._db = previous


@pytest.mark.asyncio
async def test_economy_ledger_migration_seed_prunes_the_account_in_one_update():
    from src.database import Database
    from src.storage.economy_store import MAX_APPLIED_EVENTS

    previous = Database._db
    database = _MongoDatabase()
    Database._db = database
    try:
        migration = _economy_migration()
        accounts = database["economy_accounts"]
        transactions = database["economy_transactions"]
        account = {"user_id": 4, "chat_id": -100, "balance": 0, "applied_events": {}}
        for index in range(MAX_APPLIED_EVENTS):
            account["applied_events"][f"4:-100:old:{index}"] = {
                "event_key": f"4:-100:old:{index}",
                "amount": 1,
                "direction": "credit",
                "delta": 1,
                "reason": "quiz",
                "at": datetime(2026, 10, 1, tzinfo=timezone.utc),
            }
        accounts.documents.append(account)
        transactions.documents.append(_daily_transaction())

        await migration.up(database)

        applied = accounts.documents[0]["applied_events"]
        assert len(applied) == MAX_APPLIED_EVENTS
        assert "4:-100:daily:2026-10-21" in applied
        assert "4:-100:old:0" not in applied
        # The seed and the prune are one document update per account, so the
        # account is never seen half-seeded.
        assert len(accounts.updates) == 1
        _query, document_update, _upsert = accounts.updates[0]
        assert "applied_events.4:-100:daily:2026-10-21" in document_update["$set"]
        assert "applied_events.4:-100:old:0" in document_update["$unset"]
    finally:
        Database._db = previous


@pytest.mark.asyncio
async def test_economy_ledger_migration_seed_prunes_an_over_cap_account():
    from src.database import Database
    from src.storage.economy_store import MAX_APPLIED_EVENTS

    previous = Database._db
    database = _MongoDatabase()
    Database._db = database
    try:
        migration = _economy_migration()
        accounts = database["economy_accounts"]
        transactions = database["economy_transactions"]
        account = {"user_id": 4, "chat_id": -100, "balance": 0, "applied_events": {}}
        for index in range(MAX_APPLIED_EVENTS + 5):
            account["applied_events"][f"4:-100:old:{index}"] = {
                "event_key": f"4:-100:old:{index}",
                "amount": 1,
                "direction": "credit",
                "delta": 1,
                "reason": "quiz",
                "at": datetime(2026, 10, 1, tzinfo=timezone.utc),
            }
        # The only transaction of this group was already seeded, so the
        # update carries the prune alone.
        account["applied_events"]["4:-100:daily:2026-10-21"] = {
            "event_key": "4:-100:daily:2026-10-21",
            "amount": 25,
            "direction": "credit",
            "delta": 25,
            "reason": "daily_reward",
            "at": datetime(2026, 10, 21, 8, 0, tzinfo=timezone.utc),
        }
        accounts.documents.append(account)
        transactions.documents.append(_daily_transaction())

        await migration.up(database)

        applied = accounts.documents[0]["applied_events"]
        assert len(applied) == MAX_APPLIED_EVENTS
        assert "4:-100:daily:2026-10-21" in applied
        assert "4:-100:old:0" not in applied
        # The prune travels without an empty $set, which MongoDB rejects.
        assert len(accounts.updates) == 1
        _query, document_update, _upsert = accounts.updates[0]
        assert "$set" not in document_update
        assert "applied_events.4:-100:old:0" in document_update["$unset"]
    finally:
        Database._db = previous


@pytest.mark.asyncio
async def test_economy_ledger_migration_streams_the_seed_and_the_rollback():
    from src.database import Database

    previous = Database._db
    database = _MongoDatabase()
    Database._db = database
    try:
        migration = _economy_migration()
        accounts = database["economy_accounts"]
        transactions = database["economy_transactions"]
        accounts.documents.append({"user_id": 4, "chat_id": -100, "balance": 25})
        transactions.documents.append(_daily_transaction())

        await migration.up(database)

        # The grouping runs on disk and is streamed: a mature database must
        # not have to fit its groups in memory.
        assert transactions.aggregations[0]["options"].get("allowDiskUse") is True
        aggregate_cursor = transactions.cursors[0]
        assert aggregate_cursor.streamed is True
        assert aggregate_cursor.materialized is False

        await migration.down(database)

        # The rollback streams the account documents too: every account
        # carries up to 500 events that must not be materialized.
        assert any(cursor.streamed for cursor in accounts.cursors)
        assert not any(cursor.materialized for cursor in accounts.cursors)
    finally:
        Database._db = previous


@pytest.mark.asyncio
async def test_economy_ledger_migration_restores_the_history_on_rollback():
    from src.database import Database

    previous = Database._db
    database = _MongoDatabase()
    Database._db = database
    try:
        migration = _economy_migration()
        accounts = database["economy_accounts"]
        transactions = database["economy_transactions"]
        accounts.documents.append({"user_id": 4, "chat_id": -100, "balance": 25})
        transactions.documents.append(_daily_transaction())

        await migration.up(database)
        await migration.down(database)

        # The ledger history only existed in the account document, so the
        # rollback writes it back to the legacy collection.
        restored = {row["event_key"]: row for row in transactions.documents}
        assert "4:-100:daily:2026-10-21" in restored
        assert restored["4:-100:daily:2026-10-21"]["delta"] == 25
        assert restored["4:-100:daily:2026-10-21"]["reason"] == "daily_reward"
        assert "applied_events" not in accounts.documents[0]
        assert accounts.indexes == []
    finally:
        Database._db = previous
