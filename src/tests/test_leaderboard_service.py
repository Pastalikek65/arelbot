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
