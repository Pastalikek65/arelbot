from __future__ import annotations

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
