"""The production database detection must survive the legacy module shim.

``src/database/__init__.py`` loads ``src/database.py`` under the private name
``_arel_legacy_database`` and re-exports the class.  A module-name
comparison against ``src.database`` therefore never matches the real
production class, and every module that asked "is this the production
database?" silently answered "no".  The shared helper is the single place
that answers it.
"""

from __future__ import annotations

import pytest

from config import Config
from src.storage.production import is_production_database


def _database_class():
    from src.database import Database

    return Database


def test_the_legacy_module_name_is_recognised_as_production():
    class Legacy:
        pass

    Legacy.__module__ = "_arel_legacy_database"
    assert is_production_database(Legacy()) is True


def test_the_database_class_is_recognised_as_production(monkeypatch):
    database_class = _database_class()
    monkeypatch.setattr(Config, "APP_ENV", "production")

    database = database_class.__new__(database_class)

    assert database.is_production_database is True
    assert is_production_database(database) is True


def test_a_development_database_is_not_production(monkeypatch):
    database_class = _database_class()
    monkeypatch.setattr(Config, "APP_ENV", "development")
    monkeypatch.setattr(Config, "MONGODB_REQUIRED", False)
    monkeypatch.setattr(Config, "APP_ROLE", "combined")

    database = database_class.__new__(database_class)

    assert database.is_production_database is False
    assert is_production_database(database) is False


def test_a_required_mongo_makes_the_database_production(monkeypatch):
    database_class = _database_class()
    monkeypatch.setattr(Config, "APP_ENV", "development")
    monkeypatch.setattr(Config, "MONGODB_REQUIRED", True)
    monkeypatch.setattr(Config, "APP_ROLE", "combined")

    database = database_class.__new__(database_class)

    assert database.is_production_database is True
    assert is_production_database(database) is True


def test_a_test_double_is_not_production():
    class Fake:
        available = True

    assert is_production_database(Fake()) is False
    assert is_production_database(None) is False


def test_the_object_marker_wins_over_the_module_name():
    class Marked:
        available = True
        is_production_database = True

    assert is_production_database(Marked()) is True


@pytest.mark.asyncio
async def test_the_game_ledger_fails_closed_for_a_production_database():
    from src.games.action_ledger import GameActionLedger
    from src.games.state_store import memory_fallback_allowed

    class Production:
        available = True
        is_production_database = True

    ledger = GameActionLedger(Production())

    assert ledger.allow_memory_fallback is False
    assert memory_fallback_allowed(Production()) is False


@pytest.mark.asyncio
async def test_the_game_ledger_keeps_the_memory_fallback_for_a_test_double():
    from src.games.action_ledger import GameActionLedger
    from src.games.state_store import memory_fallback_allowed

    class Fake:
        available = True

    ledger = GameActionLedger(Fake())

    assert ledger.allow_memory_fallback is True
    assert memory_fallback_allowed(Fake()) is True


@pytest.mark.asyncio
async def test_game_state_operations_fail_when_mongo_is_missing(monkeypatch):
    database_class = _database_class()
    monkeypatch.setattr(database_class, "_db", None)
    database = database_class.__new__(database_class)

    with pytest.raises(RuntimeError, match="game_persistence_unavailable"):
        await database.save_game_state(-100, "chess", {"status": "active"})
    with pytest.raises(RuntimeError, match="game_persistence_unavailable"):
        await database.get_game_state(-100, "chess")
    with pytest.raises(RuntimeError, match="game_persistence_unavailable"):
        await database.delete_game_state(-100, "chess")


@pytest.mark.asyncio
async def test_game_state_compare_and_save_checks_the_expected_version(monkeypatch):
    database_class = _database_class()

    class Collection:
        def __init__(self):
            self.query = None
            self.update = None

        async def update_one(self, query, update, *, upsert=False):
            self.query, self.update = query, update
            return type("Result", (), {"modified_count": 1, "upserted_id": None})()

    collection = Collection()
    monkeypatch.setattr(database_class, "_db", {"game_states": collection})
    database = database_class.__new__(database_class)

    saved = await database.compare_and_save_game_state(
        -100, "kelime_zinciri", {"version": 8}, expected_version=7,
    )
    assert saved is True
    assert collection.query == {
        "chat_id": -100, "game_type": "kelime_zinciri", "state.version": 7,
    }
    assert collection.update["$set"]["state"] == {"version": 8}

    with pytest.raises(ValueError, match="game_state_version_invalid"):
        await database.compare_and_save_game_state(
            -100, "kelime_zinciri", {"version": 8}, expected_version=6,
        )


@pytest.mark.asyncio
async def test_age_legacy_version_initializer_only_sets_the_missing_field(monkeypatch):
    database_class = _database_class()

    class Collection:
        async def update_one(self, query, update):
            self.query, self.update = query, update
            return type("Result", (), {"modified_count": 1})()

    collection = Collection()
    monkeypatch.setattr(database_class, "_db", {"game_states": collection})
    database = database_class.__new__(database_class)
    assert await database.initialize_game_state_version_if_missing(-100, "guess_age") is True
    assert collection.query == {
        "chat_id": -100, "game_type": "guess_age", "state.version": {"$exists": False},
    }
    assert collection.update == {"$set": {"state.version": 0}}
