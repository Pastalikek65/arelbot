"""Financial game state retention and durable owner lease contracts."""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from src.database import Database


class CaptureCollection:
    def __init__(self):
        self.rows = {}
        self.writes = []

    async def update_one(self, query, update, upsert=False):
        self.writes.append((deepcopy(query), deepcopy(update), upsert))
        key = (query["chat_id"], query["game_type"])
        row = self.rows.get(key)
        if row is None and not upsert:
            return SimpleNamespace(modified_count=0, upserted_id=None)
        if row is None:
            row = {"chat_id": key[0], "game_type": key[1]}
            self.rows[key] = row
        for name, value in update.get("$set", {}).items():
            row[name] = deepcopy(value)
        for name in update.get("$unset", {}):
            row.pop(name, None)
        return SimpleNamespace(modified_count=1, upserted_id=None)


@pytest.fixture
def scoped_game_db():
    previous = Database._db
    games = CaptureCollection()

    class Scope:
        def __getitem__(self, name):
            assert name == "game_states"
            return games

    Database._db = Scope()
    try:
        yield Database.__new__(Database), games
    finally:
        Database._db = previous


@pytest.mark.parametrize(("game_type", "state"), [
    ("blackjack:7", {"status": "playing", "economy_required": True}),
    ("blackjack:7", {"status": "pending_bet"}),
    ("blackjack:7", {"status": "finished", "refund_pending": True}),
    ("roulette", {"phase": "finished", "score_pending": True}),
    ("roulette", {"phase": "finished", "panel_dirty": True}),
    ("word_game", {"phase": "finished", "score_pending": True}),
])
@pytest.mark.asyncio
async def test_financial_obligation_removes_ttl_on_save(scoped_game_db, game_type, state):
    database, games = scoped_game_db
    key = (-100, game_type)
    games.rows[key] = {"chat_id": -100, "game_type": game_type,
                       "expires_at": datetime.now(UTC) - timedelta(days=1)}
    await database.save_game_state(-100, game_type, state)
    assert games.rows[key]["state"] == state
    assert "expires_at" not in games.rows[key]
    assert games.writes[-1][1]["$unset"] == {"expires_at": ""}


@pytest.mark.asyncio
async def test_unpaid_game_keeps_bounded_expiry(scoped_game_db):
    database, games = scoped_game_db
    await database.save_game_state(-100, "blackjack:7",
                                   {"status": "cancelled", "economy_required": False})
    assert datetime.now(UTC) < games.rows[(-100, "blackjack:7")]["expires_at"]


@pytest.mark.asyncio
async def test_financial_obligation_survives_cas_transition(scoped_game_db):
    database, games = scoped_game_db
    state = {"version": 2, "status": "finished", "settlement_pending": True}
    games.rows[(-100, "blackjack:7")] = {"chat_id": -100, "game_type": "blackjack:7",
        "state": {"version": 1}, "expires_at": datetime.now(UTC) + timedelta(hours=24)}
    assert await database.compare_and_save_game_state(
        -100, "blackjack:7", state, expected_version=1)
    assert "expires_at" not in games.rows[(-100, "blackjack:7")]


def test_owner_lease_defaults_to_one_minute_and_is_bounded():
    from src.games.action_ledger import GameActionLedger

    assert GameActionLedger.LEASE_SECONDS == 60
    assert GameActionLedger.LEASE_HEARTBEAT_SECONDS < GameActionLedger.LEASE_SECONDS
