from __future__ import annotations

import asyncio
from pathlib import Path
import random

import pytest

from src.games.guess_age import (
    AgeAsset,
    builtin_assets,
    finish_round,
    new_guess_age_state,
    score_guess,
    submit_guess,
    validate_guess_age_state,
)
from src.services.guess_age_service import GuessAgeService, GuessAgeServiceError
from src.services.guess_age_ui import guess_age_keyboard, guess_age_number_keyboard, render_guess_age_text, render_guess_age_round_result


def asset(age: int = 42) -> AgeAsset:
    return AgeAsset(key=f"portrait_{age}", filename=f"arel_age_{age}.png", target_age=age)


def test_guess_age_state_and_scoring_are_bounded():
    state = new_guess_age_state(owner_id=10, owner_name="Ada", chat_id=-100, asset=asset())
    assert validate_guess_age_state(state)
    assert score_guess(42, 42) == 100
    assert score_guess(42, 62) == 0
    assert submit_guess(state, user_id=11, display_name="Bora", guess="40").score == 90
    assert submit_guess(state, user_id=11, display_name="Bora", guess="41").code == "guess_age_already_answered"


def test_finish_round_reveals_and_advances_without_leaking_in_ui():
    state = new_guess_age_state(owner_id=10, owner_name="Ada", chat_id=-100, rounds=2, asset=asset())
    submit_guess(state, user_id=11, display_name="Bora", guess=40)
    finish_round(state, next_asset=asset(55))
    assert state["round"] == 2
    assert state["answers"] == {}
    assert state["history"][0]["target_age"] == 42
    assert "42" not in render_guess_age_text(state)


def test_final_round_is_finished_and_keyboard_is_round_scoped():
    state = new_guess_age_state(owner_id=10, owner_name="Ada", chat_id=-100, rounds=1, asset=asset())
    finish_round(state, next_asset=asset(55))
    assert state["status"] == "finished"
    callbacks = [button.callback_data for row in guess_age_keyboard(state).inline_keyboard for button in row]
    assert all(callback.startswith(f"guessage:{state['round_id']}") for callback in callbacks)


def test_commons_catalog_has_real_photo_sources_and_exact_age_choices():
    assets = builtin_assets("assets/guess_age")
    assert len(assets) >= 200
    for item in assets:
        photo = Path("assets/guess_age") / item.filename
        assert photo.read_bytes().startswith((b"\xff\xd8", b"\x89PNG"))
        assert item.source_url.startswith("https://commons.wikimedia.org/wiki/File:")
        assert item.person_name and item.photo_date and "CC" in item.credit
    state = new_guess_age_state(owner_id=10, owner_name="Ada", chat_id=-100, asset=assets[0])
    assert "range" not in str(guess_age_keyboard(state).to_dict())
    chooser = guess_age_number_keyboard(state, user_id=11, low=21)
    values = [button.callback_data for row in chooser.inline_keyboard for button in row if ":pick:" in button.callback_data]
    assert values[0].startswith(f"guessage:{state['round_id']}:1:")
    assert values[0].endswith(":pick:11:21")
    assert values[-1].endswith(":pick:11:40")
    assert "28" not in render_guess_age_text(state)
    submit_guess(state, user_id=11, display_name="Bora", guess=28)
    finish_round(state, next_asset=assets[1])
    assert all(f":{state['round']}:" in button.callback_data for row in guess_age_keyboard(state).inline_keyboard for button in row)
    result = render_guess_age_round_result(state, state["history"][-1]["ranked"])
    assert "Fotoğraf ve lisans" in result and assets[0].person_name in result


class FakeDatabase:
    def __init__(self):
        self.states = {}

    async def get_game_state(self, chat_id, game_type):
        return self.states.get((int(chat_id), game_type))

    async def save_game_state(self, chat_id, game_type, state):
        self.states[(int(chat_id), game_type)] = state.copy()

    async def delete_game_state(self, chat_id, game_type):
        self.states.pop((int(chat_id), game_type), None)


class FakeLeaderboard:
    def __init__(self):
        self.rows = []

    async def record_score(self, **kwargs):
        self.rows.append(kwargs)
        return True


@pytest.mark.asyncio
async def test_service_persists_start_submit_and_cancel(tmp_path: Path):
    db = FakeDatabase()
    service = GuessAgeService(db, asset_dir=tmp_path, rng=random.Random(1))
    for item in service.assets:
        (tmp_path / item.filename).write_bytes(b"png-fixture")
    state = await service.start(chat_id=-100, owner_id=10, owner_name="Ada")
    assert await service.current(-100)
    state, outcome = await service.submit(chat_id=-100, user_id=12, display_name="Cem", guess=33)
    assert outcome.accepted and state["answers"]["12"]["guess"] == 33
    assert await service.cancel(-100)
    assert await service.current(-100) is None


@pytest.mark.asyncio
async def test_service_rejects_private_chat_and_duplicate_round():
    db = FakeDatabase()
    service = GuessAgeService(db)
    with pytest.raises(ValueError, match="group_only"):
        await service.start(chat_id=123, owner_id=10, owner_name="Ada")
    state = new_guess_age_state(owner_id=10, owner_name="Ada", chat_id=-100, asset=asset())
    db.states[(-100, "guess_age")] = state
    with pytest.raises(GuessAgeServiceError, match="ACTIVE"):
        await service.start(chat_id=-100, owner_id=11, owner_name="Bora")


@pytest.mark.asyncio
async def test_close_round_records_leaderboard_rows_without_name_error(tmp_path: Path):
    db = FakeDatabase()
    leaderboard = FakeLeaderboard()
    service = GuessAgeService(db, asset_dir=tmp_path, rng=random.Random(2), leaderboard_service=leaderboard)
    for item in service.assets:
        (tmp_path / item.filename).write_bytes(b"photo-fixture")
    state = await service.start(chat_id=-100, owner_id=10, owner_name="Ada")
    await service.submit(chat_id=-100, user_id=12, display_name="Cem", guess=33)
    closed, ranking = await service.close_round(chat_id=-100, expected_round_id=state["round_id"])
    assert closed["history"]
    assert ranking and ranking[0]["user_id"] == 12
    assert leaderboard.rows and leaderboard.rows[0]["user_id"] == 12
    with pytest.raises(GuessAgeServiceError, match="STALE_ROUND"):
        await service.close_round(chat_id=-100, expected_round_id=state["round_id"], expected_round=1)


@pytest.mark.asyncio
async def test_start_does_not_persist_a_round_without_its_photo(tmp_path: Path):
    db = FakeDatabase()
    service = GuessAgeService(db, asset_dir=tmp_path)
    with pytest.raises(GuessAgeServiceError, match="ASSET_MISSING"):
        await service.start(chat_id=-100, owner_id=10, owner_name="Ada")
    assert db.states == {}


@pytest.mark.asyncio
async def test_completed_games_do_not_immediately_reuse_the_same_people(tmp_path):
    db = FakeDatabase()
    service = GuessAgeService(db, asset_dir=tmp_path, rounds=1, rng=random.Random(4))
    for item in service.assets:
        (tmp_path / item.filename).write_bytes(b"photo-fixture")
    used = []
    for _ in range(4):
        state = await service.start(chat_id=-100, owner_id=10, owner_name="Ada")
        used.append(state["asset_key"])
        await service.close_round(chat_id=-100, expected_round_id=state["round_id"])
        # This test concerns selection after the previous result was delivered.
        db.states[(-100, "guess_age")]["delivery_pending"] = False
    assert len(set(used)) == len(used)


@pytest.mark.asyncio
async def test_next_round_waits_for_photo_delivery_before_accepting_answers(tmp_path):
    db = FakeDatabase()
    service = GuessAgeService(db, asset_dir=tmp_path)
    for item in service.assets:
        (tmp_path / item.filename).write_bytes(b"photo-fixture")
    await service.start(chat_id=-100, owner_id=10, owner_name="Ada")
    await service.close_round(chat_id=-100)
    with pytest.raises(GuessAgeServiceError, match="ROUND_PREPARING"):
        await service.submit(chat_id=-100, user_id=11, display_name="Bora", guess=30)


@pytest.mark.asyncio
async def test_old_photo_click_cannot_be_applied_to_the_next_round(tmp_path):
    db = FakeDatabase()
    service = GuessAgeService(db, asset_dir=tmp_path)
    for item in service.assets:
        (tmp_path / item.filename).write_bytes(b"photo-fixture")
    first = await service.start(chat_id=-100, owner_id=10, owner_name="Ada")
    second, _ = await service.close_round(chat_id=-100)
    await service.mark_delivery(-100, second["delivery_id"], 2, 123)
    with pytest.raises(GuessAgeServiceError, match="STALE_ROUND"):
        await service.submit(chat_id=-100, user_id=11, display_name="Bora", guess=30,
                             expected_round_id=first["round_id"], expected_round=1)
    assert not db.states[(-100, "guess_age")]["answers"]

