from __future__ import annotations

from datetime import datetime, timezone

import pytest
from uuid import uuid4

from src.services.leaderboard_service import LeaderboardService


class FakeDatabase:
    def __init__(self):
        self.events = set()
        self.stats_updates = 0
        self.progress = []

    async def save_game_score(self, **kwargs):
        event_id = kwargs.get("event_id")
        if event_id and event_id in self.events:
            return False
        if event_id:
            self.events.add(event_id)
        return True

    async def update_player_stats(self, **kwargs):
        self.stats_updates += 1

    async def record_game_progress(self, **kwargs):
        if any(row["event_id"] == kwargs["event_id"] for row in self.progress):
            return False
        self.progress.append(kwargs)
        return True

    async def get_game_achievements(self, user_id, chat_id, limit=50):
        return [{"key": "first_game", "label": "Ä°lk oyun"}]

    async def get_season_leaderboard(self, chat_id, season_id, limit=10):
        return [{"user_id": 7, "points": 25, "games_played": 2, "user_name": "Arel"}]


@pytest.mark.asyncio
async def test_score_progression_is_event_idempotent_and_season_aware():
    db = FakeDatabase()
    service = LeaderboardService(db)

    assert await service.record_score(7, -100, "quiz", 10, user_name="Arel", event_id="quiz:1") is True
    assert await service.record_score(7, -100, "quiz", 10, user_name="Arel", event_id="quiz:1") is False

    assert len(db.progress) == 1
    assert db.progress[0]["season_id"] == service.current_season()
    assert db.progress[0]["event_id"] == "quiz:1"
    assert "first_game" in db.progress[0]["achievements"]
    assert db.stats_updates == 1


@pytest.mark.asyncio
async def test_progression_views_are_scoped_and_rendered():
    db = FakeDatabase()
    service = LeaderboardService(db)
    text = await service.render_achievements(7, -100)
    assert "Ä°lk oyun" in text
    season = await service.render_season_leaderboard(-100, now=datetime(2026, 10, 5, tzinfo=timezone.utc))
    assert "Arel" in season
    assert "2026-Q4" in season
