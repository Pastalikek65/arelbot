from __future__ import annotations

import pytest

from src.services.leaderboard_service import LeaderboardService


class FakeDatabase:
    def __init__(self):
        self.events = set()
        self.stats_updates = 0

    async def save_game_score(self, **kwargs):
        event_id = kwargs.get("event_id")
        if event_id and event_id in self.events:
            return False
        if event_id:
            self.events.add(event_id)
        return True

    async def update_player_stats(self, **kwargs):
        self.stats_updates += 1


@pytest.mark.asyncio
async def test_score_event_is_idempotent():
    database = FakeDatabase()
    service = LeaderboardService(database)
    assert await service.record_score(1, -100, "quiz", 10, event_id="quiz:event") is True
    assert await service.record_score(1, -100, "quiz", 10, event_id="quiz:event") is False
    assert database.stats_updates == 1


@pytest.mark.asyncio
async def test_retry_repairs_stats_after_score_insert_without_counting_twice():
    class InterruptedDatabase(FakeDatabase):
        fail_stats_once = True

        async def score_stats_retryable(self, *, event_id, user_id, chat_id, game_type, score):
            return event_id in self.events

        async def update_player_stats(self, *, user_id, score_delta, user_name=None, event_id=None):
            if self.fail_stats_once:
                self.fail_stats_once = False
                raise RuntimeError("stats_write_failed")
            if not hasattr(self, "applied_events"):
                self.applied_events = set()
            if event_id not in self.applied_events:
                self.applied_events.add(event_id)
                self.stats_updates += 1

    database = InterruptedDatabase()
    service = LeaderboardService(database)
    with pytest.raises(RuntimeError, match="stats_write_failed"):
        await service.record_score(1, -100, "quiz", 10, event_id="quiz:recovery")
    assert await service.record_score(1, -100, "quiz", 10, event_id="quiz:recovery") is False
    assert await service.record_score(1, -100, "quiz", 10, event_id="quiz:recovery") is False
    assert database.stats_updates == 1


@pytest.mark.asyncio
async def test_production_stats_increment_and_event_stamp_are_one_write(monkeypatch):
    from src.database import Database

    class DuplicateKey(Exception):
        code = 11000

    class Stats:
        def __init__(self):
            self.row = None

        async def update_one(self, query, update, *, upsert):
            assert upsert is True
            assert query["applied_score_events"] == {"$ne": "match:1"}
            assert update["$addToSet"] == {"applied_score_events": "match:1"}
            if self.row is not None:
                raise DuplicateKey()
            self.row = {
                "_id": query["_id"], "total_score": update["$inc"]["total_score"],
                "games_played": update["$inc"]["games_played"],
                "applied_score_events": ["match:1"],
            }
            return type("Result", (), {"modified_count": 0, "upserted_id": query["_id"]})()

        async def find_one(self, query, projection):
            assert query == {"_id": 1, "applied_score_events": "match:1"}
            return self.row

    stats = Stats()
    monkeypatch.setattr(Database, "_db", {"player_stats": stats})
    database = Database.__new__(Database)
    assert await database.update_player_stats(1, 12, event_id="match:1") is True
    assert await database.update_player_stats(1, 12, event_id="match:1") is False
    assert stats.row["total_score"] == 12
    assert stats.row["games_played"] == 1


@pytest.mark.asyncio
async def test_only_new_matching_scores_can_repair_stats(monkeypatch):
    from src.database import Database

    class Scores:
        def __init__(self):
            self.rows = {}

        async def insert_one(self, row):
            self.rows[row["event_id"]] = row

        async def find_one(self, query, projection):
            return self.rows.get(query["event_id"])

    scores = Scores()
    monkeypatch.setattr(Database, "_db", {"game_scores": scores})
    database = Database.__new__(Database)
    assert await database.save_game_score(1, -100, "quiz", 12, event_id="new:1") is True
    assert scores.rows["new:1"]["stats_protocol_version"] == 2
    assert await database.score_stats_retryable(
        event_id="new:1", user_id=1, chat_id=-100, game_type="quiz", score=12,
    ) is True
    with pytest.raises(ValueError, match="game_score_event_conflict"):
        await database.score_stats_retryable(
            event_id="new:1", user_id=2, chat_id=-100, game_type="quiz", score=12,
        )
    scores.rows["legacy:1"] = {"user_id": 1, "chat_id": -100, "game_type": "quiz", "score": 12}
    assert await database.score_stats_retryable(
        event_id="legacy:1", user_id=1, chat_id=-100, game_type="quiz", score=12,
    ) is False


@pytest.mark.asyncio
async def test_concurrent_other_score_upsert_retries_profile_increment(monkeypatch):
    from src.database import Database

    class DuplicateKey(Exception):
        code = 11000

    class Stats:
        def __init__(self):
            self.attempts = 0

        async def update_one(self, query, update, *, upsert):
            self.attempts += 1
            if self.attempts == 1:
                raise DuplicateKey()
            return type("Result", (), {"modified_count": 1, "upserted_id": None})()

        async def find_one(self, query, projection):
            return None  # The conflicting insert belongs to another event.

    stats = Stats()
    monkeypatch.setattr(Database, "_db", {"player_stats": stats})
    database = Database.__new__(Database)
    assert await database.update_player_stats(1, 8, event_id="second:1") is True
    assert stats.attempts == 2


@pytest.mark.asyncio
async def test_score_range_is_bounded_before_persistence():
    database = FakeDatabase()
    service = LeaderboardService(database)
    with pytest.raises(ValueError, match="score_out_of_range"):
        await service.record_score(1, -100, "quiz", 101, event_id="bad")
    assert database.stats_updates == 0


@pytest.mark.asyncio
async def test_score_without_event_id_gets_a_stable_scoped_event():
    database = FakeDatabase()
    service = LeaderboardService(database)
    assert await service.record_score(7, -100, "XOX", 30) is True
    assert len(database.events) == 1
    event_id = next(iter(database.events))
    assert event_id.startswith("xox:-100:7:")


@pytest.mark.asyncio
async def test_invalid_game_identity_is_rejected_before_persistence():
    database = FakeDatabase()
    service = LeaderboardService(database)
    with pytest.raises(ValueError, match="invalid_game_type"):
        await service.record_score(7, -100, "xox game", 10, event_id="bad")
