"""Migration 22 preserves legacy board payloads and never mints old rewards."""

import re
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.database import Database
from src.services.board_panel_service import BoardPanelService


def _field(document, path):
    value = document
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return False, None
        value = value[part]
    return True, value


def _matches(document, query):
    for field, condition in query.items():
        if field == "$or":
            if not any(_matches(document, clause) for clause in condition):
                return False
            continue
        if field == "$and":
            if not all(_matches(document, clause) for clause in condition):
                return False
            continue
        exists, value = _field(document, field)
        if isinstance(condition, dict):
            for operator, target in condition.items():
                if operator == "$exists" and exists != bool(target):
                    return False
                if operator == "$in" and value not in target:
                    return False
                if operator == "$nin" and value in target:
                    return False
                if operator == "$ne" and value == target:
                    return False
                if operator == "$lte" and (not exists or value is None or value > target):
                    return False
                if operator == "$gt" and (not exists or value is None or value <= target):
                    return False
                if operator == "$regex" and (not isinstance(value, str) or re.search(target, value) is None):
                    return False
        elif value != condition:
            return False
    return True


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def __aiter__(self):
        async def iterate():
            for row in self.rows:
                yield deepcopy(row)
        return iterate()

    def sort(self, field, direction):
        self.rows.sort(key=lambda row: _field(row, field)[1] or datetime.min.replace(tzinfo=UTC),
                       reverse=direction < 0)
        return self

    def limit(self, count):
        self.rows = self.rows[:count]
        return self

    async def to_list(self, length):
        return deepcopy(self.rows if length is None else self.rows[:length])


class Collection:
    def __init__(self, rows=()):
        self.rows = [deepcopy(row) for row in rows]
        self.indexes = []
        self.updates = 0
        self.last_query = None

    def find(self, query, projection=None):
        self.last_query = query
        return Cursor([deepcopy(row) for row in self.rows if _matches(row, query)])

    async def find_one(self, query):
        return next((deepcopy(row) for row in self.rows if _matches(row, query)), None)

    async def update_one(self, query, update, upsert=False):
        for row in self.rows:
            if not _matches(row, query):
                continue
            for path, value in update.get("$set", {}).items():
                target = row
                parts = path.split(".")
                for part in parts[:-1]:
                    target = target.setdefault(part, {})
                target[parts[-1]] = deepcopy(value)
            for path in update.get("$unset", {}):
                target = row
                parts = path.split(".")
                for part in parts[:-1]:
                    target = target.get(part, {})
                target.pop(parts[-1], None)
            self.updates += 1
            return SimpleNamespace(modified_count=1)
        return SimpleNamespace(modified_count=0)

    async def create_index(self, fields, **kwargs):
        self.indexes.append((fields, kwargs))

    async def drop_index(self, name):
        self.indexes = [entry for entry in self.indexes if entry[1].get("name") != name]


class FakeDatabase(dict):
    def __missing__(self, name):
        collection = self[name] = Collection()
        return collection


def _setup(monkeypatch, rows):
    fake = FakeDatabase(game_states=Collection(rows))
    monkeypatch.setattr(Database, "_db", fake)
    repository = object.__new__(Database)
    migration = next(item for item in repository.migration_runner().migrations if item.version == 22)
    return repository, fake["game_states"], migration, fake


@pytest.mark.asyncio
async def test_migration_backfills_once_without_rewriting_board_payloads(monkeypatch):
    legacy = {"chat_id": -100, "game_type": "connect4", "expires_at": datetime.now(UTC),
              "state": {"round_id": "round-a", "status": "playing", "prompt_message_id": 77,
                        "thread_id": 42, "players": [{"id": 1}], "board": [[0] * 7 for _ in range(6)],
                        "moves": 3, "custom_legacy_value": {"keep": True}}}
    modern = {"chat_id": -101, "game_type": "gomoku", "state": {
        "round_id": "new", "version": 9, "board_panel_revision": 4,
        "board_panel_dirty": False, "board_score_pending": True, "board": [1, 2]}}
    unrelated = {"chat_id": -102, "game_type": "uno", "state": {"round_id": "other"}}
    _, games, migration, fake = _setup(monkeypatch, [legacy, modern, unrelated])

    await migration.up(fake)
    first = deepcopy(games.rows)
    await migration.up(fake)
    assert games.rows == first
    assert games.updates == 1
    migrated = games.rows[0]
    assert "expires_at" not in migrated
    assert migrated["state"]["version"] == 0
    assert migrated["state"]["board_panel_revision"] == 0
    assert migrated["state"]["board_panel_dirty"] is True
    assert migrated["state"]["board_panel_delivery_unknown"] is False
    assert migrated["state"]["board_score_pending"] is False
    for field in ("round_id", "status", "prompt_message_id", "thread_id", "players",
                  "board", "moves", "custom_legacy_value"):
        assert migrated["state"][field] == legacy["state"][field]
    assert games.rows[1:] == [modern, unrelated]
    assert {entry[1]["name"] for entry in games.indexes} == {
        "board_panel_due", "board_score_due", "board_clock_due"}


@pytest.mark.asyncio
async def test_migration_preserves_finished_winner_without_reawarding(monkeypatch):
    row = {"chat_id": -200, "game_type": "chess", "state": {
        "round_id": "old-finished", "status": "finished", "winner_id": 1,
        "players": [{"id": 1, "name": "Ada"}, {"id": 2, "name": "Bora"}],
        "prompt_message_id": 91, "thread_id": None, "fen": "legacy-fen"}}
    repository, games, migration, fake = _setup(monkeypatch, [row])
    await migration.up(fake)
    migrated = games.rows[0]["state"]
    assert migrated["winner_id"] == 1 and migrated["fen"] == "legacy-fen"
    assert migrated["board_score_round_id"] == "old-finished"
    assert migrated["board_score_pending"] is False

    class Lease:
        @asynccontextmanager
        async def lease(self, *args):
            yield

    class Rewards:
        def __init__(self):
            self.calls = []

        async def record_score(self, **kwargs):
            self.calls.append(kwargs)

        async def credit(self, *args, **kwargs):
            self.calls.append((args, kwargs))

    class Bot:
        async def edit_message_text(self, **kwargs):
            return None

    rewards = Rewards()
    service = BoardPanelService(repository, leaderboard=rewards, economy=rewards, actions=Lease())
    await service.process(Bot(), -200, "chess")
    assert rewards.calls == []
    assert (await repository.get_game_state(-200, "chess"))["board_score_pending"] is False


@pytest.mark.asyncio
async def test_missing_legacy_panel_is_marked_unknown_and_rollback_preserves_state(monkeypatch):
    row = {"chat_id": -300, "game_type": "tictactoe", "state": {
        "round_id": "lost-reply", "status": "lobby", "players": [{"id": 3}], "board": [0] * 9}}
    _, games, migration, fake = _setup(monkeypatch, [row])
    await migration.up(fake)
    migrated = deepcopy(games.rows[0])
    assert migrated["state"]["board_panel_delivery_unknown"] is True
    assert migrated["state"]["board_panel_dirty"] is True
    await migration.down(fake)
    assert games.rows[0] == migrated
    assert games.indexes == []


@pytest.mark.asyncio
async def test_board_recovery_backoff_defers_one_chat_without_starving_another(monkeypatch):
    rows = [
        {"chat_id": chat, "game_type": "connect4", "updated_at": datetime.now(UTC),
         "state": {"board_panel_dirty": True, "board_panel_delivery_unknown": False,
                   "board_panel_retry_at": 0}}
        for chat in (-400, -401)
    ]
    repository, games, _, _ = _setup(monkeypatch, rows)
    await repository.defer_board_recovery(-400, "connect4", 120)
    assert games.rows[0]["board_recovery_after"] == 120.0
    due_now = await repository.list_due_game_states(now=100)
    assert [row["chat_id"] for row in due_now] == [-401]
    due_later = await repository.list_due_game_states(now=121)
    assert {row["chat_id"] for row in due_later} == {-400, -401}
