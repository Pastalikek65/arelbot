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
