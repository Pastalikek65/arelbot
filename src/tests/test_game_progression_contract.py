from datetime import datetime, timedelta, timezone

import pytest

from src.games.progression import (
    ProgressionEngine,
    ProgressionEvent,
    ProgressionPolicy,
    ProgressionStatus,
    calculate_awarded_score,
    project_leaderboard,
    season_id_for,
)

UTC = timezone.utc
BASE = datetime(2026, 10, 17, 10, 0, tzinfo=UTC)


def event(event_id: str, *, user_id: int = 1, game_type: str = "quiz", seconds: int = 0, score: int = 100, won: bool = True, combo: int = 0, name: str = "Oyuncu") -> ProgressionEvent:
    return ProgressionEvent(
        event_id=event_id,
        user_id=user_id,
        chat_id=-100,
        game_type=game_type,
        base_score=score,
        won=won,
        difficulty="normal",
        combo=combo,
        occurred_at=BASE + timedelta(seconds=seconds),
        user_name=name,
    )


def test_score_is_bounded_and_difficulty_combo_hint_are_deterministic():
    assert calculate_awarded_score(100, difficulty="hard", combo=3, hints_used=0) == 162
    assert calculate_awarded_score(100, difficulty="easy", combo=0, hints_used=2) == 76
    assert calculate_awarded_score(9999, difficulty="expert", combo=20, score_cap=1000) == 1000
    assert calculate_awarded_score(100, difficulty="normal", timed_out=True) == 0
    with pytest.raises(ValueError, match="difficulty_invalid"):
        calculate_awarded_score(10, difficulty="nightmare")


def test_duplicate_event_is_idempotent_and_payload_conflict_is_rejected():
    engine = ProgressionEngine(policy=ProgressionPolicy(cooldown_seconds=0))
    first = engine.apply(event("quiz-1"))
    duplicate = engine.apply(event("quiz-1"))
    assert first.accepted is True
    assert duplicate.accepted is False
    assert duplicate.replayed is True
    assert duplicate.status is ProgressionStatus.DUPLICATE
    assert engine.player_snapshot(1, -100)["total_score"] == 100

    conflict = engine.apply(event("quiz-1", score=50))
    assert conflict.status is ProgressionStatus.CONFLICT
    assert conflict.accepted is False
    assert engine.player_snapshot(1, -100)["total_score"] == 100


def test_cooldown_window_and_daily_cap_reject_distinct_farming_events():
    policy = ProgressionPolicy(cooldown_seconds=10, window_seconds=60, max_events_per_window=2, daily_score_cap=150)
    engine = ProgressionEngine(policy=policy)
    assert engine.apply(event("a", seconds=0, score=50)).accepted
    assert engine.apply(event("b", seconds=5, score=50)).status is ProgressionStatus.COOLDOWN
    assert engine.apply(event("c", seconds=11, score=50)).accepted
    assert engine.apply(event("d", seconds=22, score=50)).status is ProgressionStatus.WINDOW_CAP
    assert engine.apply(event("e", seconds=80, score=50)).accepted
    assert engine.apply(event("f", seconds=100, score=50)).status is ProgressionStatus.DAILY_CAP


def test_season_achievements_and_anti_farming_are_replayable():
    engine = ProgressionEngine(policy=ProgressionPolicy(cooldown_seconds=0, max_events_per_window=100))
    for index, seconds in enumerate((0, 61, 122, 183, 244), start=1):
        assert engine.apply(event(f"quiz-{index}", seconds=seconds, score=100, combo=3)).accepted
    assert engine.achievements(1, -100) == (
        "first_game", "first_win", "score_50", "score_500", "streak_3", "quiz_master"
    )
    assert season_id_for(BASE) == "2026-Q4"

    replay = ProgressionEngine.replay(engine.export_records(), policy=engine.policy)
    assert replay.export_records() == engine.export_records()
    assert replay.leaderboard(-100, season_id="2026-Q4") == engine.leaderboard(-100, season_id="2026-Q4")


def test_leaderboard_projection_is_deterministic_and_filtered():
    events = [
        event("b-2", user_id=2, seconds=61, score=40, name="Beta"),
        event("a-1", user_id=1, seconds=0, score=40, name="Alpha"),
        event("a-2", user_id=1, seconds=61, score=60, name="Alpha"),
        event("other-season", user_id=3, seconds=60 * 60 * 24 * 100, score=500, name="Later"),
    ]
    rows = project_leaderboard(events, chat_id=-100, season_id="2026-Q4", policy=ProgressionPolicy(cooldown_seconds=0))
    assert [row.user_id for row in rows] == [1, 2]
    assert rows[0].total_score == 100
    assert rows[0].rank == 1
    assert rows[1].rank == 2
    assert project_leaderboard(list(reversed(events)), chat_id=-100, season_id="2026-Q4", policy=ProgressionPolicy(cooldown_seconds=0)) == rows


def test_event_replay_rejects_malformed_records_and_future_events():
    with pytest.raises(ValueError, match="event_id_invalid"):
        ProgressionEvent(event_id="bad id", user_id=1, chat_id=-100, game_type="quiz", base_score=1)
    engine = ProgressionEngine(policy=ProgressionPolicy(cooldown_seconds=0, future_skew_seconds=5))
    future = event("future", seconds=10)
    receipt = engine.apply(future, now=BASE)
    assert receipt.status is ProgressionStatus.FUTURE_EVENT
    assert engine.export_records() == []
