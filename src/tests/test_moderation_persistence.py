from __future__ import annotations

import pytest


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    async def to_list(self, length=None):
        return list(self.rows)


class _Collection:
    def __init__(self):
        self.indexes: list[dict] = []

    async def create_index(self, key, **kwargs):
        self.indexes.append({"key": key, **kwargs})

    async def drop_index(self, name):
        self.indexes[:] = [item for item in self.indexes if item.get("name") != name]

    def find(self, *_args):
        return _Cursor([])


class _Database:
    def __init__(self):
        self.collections: dict[str, _Collection] = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, _Collection())


def test_database_registers_reversible_moderation_action_migration_v16():
    from src.database import Database

    previous = Database._db
    Database._db = _Database()
    try:
        runner = Database.__new__(Database).migration_runner()
        migration = next(item for item in runner.migrations if item.version == 16)
        assert migration.version == 16
        assert migration.name == "moderation-action-ledger"
        assert migration.down is not None
    finally:
        Database._db = previous


@pytest.mark.asyncio
async def test_moderation_action_migration_creates_idempotent_and_ttl_indexes():
    from src.database import Database

    previous = Database._db
    database = _Database()
    Database._db = database
    try:
        migration = next(item for item in Database.__new__(Database).migration_runner().migrations if item.version == 16)
        await migration.up(database)
        action_indexes = database["moderation_actions"].indexes
        provider_indexes = database["moderation_provider_events"].indexes
        assert any(item.get("name") == "moderation_action_idempotency_unique" and item.get("unique") for item in action_indexes)
        assert any(item.get("name") == "moderation_action_created_ttl" and item.get("expireAfterSeconds") for item in action_indexes)
        assert any(item.get("name") == "moderation_provider_request_unique" and item.get("unique") for item in provider_indexes)
    finally:
        Database._db = previous

