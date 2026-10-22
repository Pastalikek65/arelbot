"""Versioned, idempotent Mongo migration runner.

Migrations are deliberately small and explicit.  A migration is marked as
applied only after its ``up`` function succeeds.  Rollback is opt-in per
migration so an operator cannot accidentally infer a destructive reverse
operation from an ``up`` function.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable


MigrationAction = Callable[[Any], Awaitable[None]]


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    up: MigrationAction
    down: MigrationAction | None = None

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("migration_version_must_be_positive")
        if not self.name.strip():
            raise ValueError("migration_name_required")


class MigrationRunner:
    """Apply ordered migrations against a Motor-compatible database object."""

    def __init__(self, database: Any, migrations: list[Migration], *, collection_name: str = "schema_migrations") -> None:
        versions = [migration.version for migration in migrations]
        if versions != sorted(set(versions)):
            raise ValueError("migration_versions_must_be_unique_and_sorted")
        self.database = database
        self.migrations = tuple(migrations)
        self.collection = database[collection_name]
        self._lock = asyncio.Lock()

    async def _prepare(self) -> None:
        await self.collection.create_index("version", unique=True)

    async def applied(self) -> dict[int, dict[str, Any]]:
        cursor = self.collection.find({}, {"_id": 0})
        rows = await cursor.to_list(length=None)
        return {int(row["version"]): dict(row) for row in rows if "version" in row}

    async def migrate(self, target_version: int | None = None) -> int:
        target = self.migrations[-1].version if target_version is None and self.migrations else (target_version or 0)
        if target < 0:
            raise ValueError("migration_target_invalid")
        async with self._lock:
            await self._prepare()
            applied = await self.applied()
            current = max(applied, default=0)
            for migration in self.migrations:
                if migration.version > target or migration.version <= current:
                    continue
                await migration.up(self.database)
                await self.collection.insert_one(
                    {
                        "version": migration.version,
                        "name": migration.name,
                        "applied_at": datetime.now(timezone.utc),
                    }
                )
                current = migration.version
            return current

    async def rollback(self, target_version: int) -> int:
        if target_version < 0:
            raise ValueError("migration_target_invalid")
        async with self._lock:
            await self._prepare()
            applied = await self.applied()
            for migration in sorted(self.migrations, key=lambda item: item.version, reverse=True):
                if migration.version <= target_version or migration.version not in applied:
                    continue
                if migration.down is None:
                    raise RuntimeError(f"migration_not_reversible:{migration.version}")
                await migration.down(self.database)
                await self.collection.delete_one({"version": migration.version})
            remaining = await self.applied()
            return max(remaining, default=0)
