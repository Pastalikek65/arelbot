from __future__ import annotations

import asyncio
from pathlib import Path
import random

import pytest

from src.games.guess_age import (
    AgeAsset,
    builtin_assets,
    final_ranking,
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
    state["initial_delivery_pending"] = False
    assert validate_guess_age_state(state)
    assert score_guess(42, 42) == 100
    assert score_guess(42, 62) == 0
    assert submit_guess(state, user_id=11, display_name="Bora", guess="40").score == 90
    assert submit_guess(state, user_id=11, display_name="Bora", guess="41").code == "guess_age_already_answered"


def test_one_hundred_distinct_players_can_score_but_next_new_player_cannot():
    state = new_guess_age_state(owner_id=1, owner_name="Bir", chat_id=-100, rounds=2, asset=asset())
    state["initial_delivery_pending"] = False
    for user_id in range(1, 101):
        assert submit_guess(state, user_id=user_id, display_name=str(user_id), guess=42).accepted
    assert submit_guess(state, user_id=101, display_name="Yeni", guess=42).code == "guess_age_full"
    finish_round(state, next_asset=asset(55))
    assert len(state["history"][0]["ranked"]) == 100
    assert len(state["players"]) == 100
    assert len(state["answers"]) == 0
    assert submit_guess(state, user_id=100, display_name="Mevcut", guess=55).accepted
    assert submit_guess(state, user_id=101, display_name="Yeni", guess=55).code == "guess_age_full"
    assert len(final_ranking(state)) == 100


def test_legacy_match_over_capacity_preserves_old_players_and_their_guesses():
    state = new_guess_age_state(owner_id=1, owner_name="Bir", chat_id=-100, rounds=2, asset=asset())
    state["initial_delivery_pending"] = False
    state["players"] = {str(user_id): {"display_name": str(user_id), "score": 0} for user_id in range(1, 102)}
    assert submit_guess(state, user_id=101, display_name="Eski", guess=42).accepted
    assert submit_guess(state, user_id=102, display_name="Yeni", guess=42).code == "guess_age_full"
    finish_round(state, next_asset=asset(55))
    assert len(state["players"]) == 101
    assert state["history"][0]["ranked"][0]["user_id"] == 101


def test_finish_round_reveals_and_advances_without_leaking_in_ui():
    state = new_guess_age_state(owner_id=10, owner_name="Ada", chat_id=-100, rounds=2, asset=asset())
    state["initial_delivery_pending"] = False
    submit_guess(state, user_id=11, display_name="Bora", guess=40)
    finish_round(state, next_asset=asset(55))
    assert state["round"] == 2
    assert state["answers"] == {}
    assert state["history"][0]["target_age"] == 42
    assert "42" not in render_guess_age_text(state)


def test_final_round_is_finished_and_keyboard_is_round_scoped():
    state = new_guess_age_state(owner_id=10, owner_name="Ada", chat_id=-100, rounds=1, asset=asset())
    state["initial_delivery_pending"] = False
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
    state["initial_delivery_pending"] = False
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
    assert "href" not in result and assets[0].person_name in result


class FakeDatabase:
    def __init__(self):
        self.states = {}

    async def get_game_state(self, chat_id, game_type):
        return self.states.get((int(chat_id), game_type))

    async def save_game_state(self, chat_id, game_type, state):
        self.states[(int(chat_id), game_type)] = state.copy()

    async def create_game_state_if_absent(self, chat_id, game_type, state):
        key = (int(chat_id), game_type)
        if key in self.states:
            return False
        self.states[key] = state.copy()
        return True

    async def initialize_game_state_version_if_missing(self, chat_id, game_type):
        state = self.states.get((int(chat_id), game_type))
        if state is None or "version" in state:
            return False
        state["version"] = 0
        return True

    async def compare_and_save_game_state(self, chat_id, game_type, state, *, expected_version):
        key = (int(chat_id), game_type)
        old = self.states.get(key)
        if old is None or old.get("version") != expected_version or state.get("version") != expected_version + 1:
            return False
        self.states[key] = state.copy()
        return True

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
    await service.set_prompt_message(-100, 101)
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
    await service.set_prompt_message(-100, 101)
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
        await service.set_prompt_message(-100, 101)
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
    await service.set_prompt_message(-100, 101)
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
    await service.set_prompt_message(-100, 101)
    second, _ = await service.close_round(chat_id=-100)
    await service.mark_delivery(-100, second["delivery_id"], 2, 123)
    with pytest.raises(GuessAgeServiceError, match="STALE_ROUND"):
        await service.submit(chat_id=-100, user_id=11, display_name="Bora", guess=30,
                             expected_round_id=first["round_id"], expected_round=1)
    assert not db.states[(-100, "guess_age")]["answers"]


@pytest.mark.asyncio
async def test_initial_photo_and_result_delivery_gate_guesses(tmp_path):
    db = FakeDatabase()
    service = GuessAgeService(db, asset_dir=tmp_path)
    for item in service.assets:
        (tmp_path / item.filename).write_bytes(b"photo")
    first = await service.start(chat_id=-100, owner_id=1, owner_name="Bir")
    assert first["initial_delivery_pending"] is True and first["round_deadline"] is None
    with pytest.raises(GuessAgeServiceError, match="ROUND_PREPARING"):
        await service.submit(chat_id=-100, user_id=2, display_name="İki", guess=42)
    await service.set_prompt_message(-100, 123)
    await service.submit(chat_id=-100, user_id=2, display_name="İki", guess=42)
    second, _ = await service.close_round(chat_id=-100, expected_round_id=first["round_id"])
    with pytest.raises(GuessAgeServiceError, match="ROUND_PREPARING"):
        await service.submit(chat_id=-100, user_id=3, display_name="Üç", guess=42)
    visible = await service.mark_delivery(-100, second["delivery_id"], 1)
    assert visible["result_visible_until"] > 0
    assert visible["delivery_retry_at"] == visible["result_visible_until"]
    duplicate = await service.mark_delivery(-100, second["delivery_id"], 1)
    assert duplicate["version"] == visible["version"]
    assert duplicate["result_visible_until"] == visible["result_visible_until"]


@pytest.mark.asyncio
async def test_pending_rewards_block_new_game(tmp_path):
    class FailingLeaderboard:
        async def record_score(self, **kwargs):
            raise RuntimeError("offline")

    db = FakeDatabase()
    service = GuessAgeService(db, asset_dir=tmp_path, rounds=1, leaderboard_service=FailingLeaderboard())
    for item in service.assets:
        (tmp_path / item.filename).write_bytes(b"photo")
    state = await service.start(chat_id=-100, owner_id=1, owner_name="Bir")
    await service.set_prompt_message(-100, 101)
    await service.submit(chat_id=-100, user_id=2, display_name="İki", guess=42)
    await service.close_round(chat_id=-100, expected_round_id=state["round_id"])
    with pytest.raises(GuessAgeServiceError, match="REWARDS_PENDING"):
        await service.start(chat_id=-100, owner_id=1, owner_name="Bir")
    assert db.states[(-100, "guess_age")]["pending_scores"]


@pytest.mark.asyncio
async def test_old_match_cancel_cannot_close_replacement_match(tmp_path):
    db = FakeDatabase()
    service = GuessAgeService(db, asset_dir=tmp_path)
    for item in service.assets:
        (tmp_path / item.filename).write_bytes(b"photo")
    old = await service.start(chat_id=-100, owner_id=1, owner_name="Bir")
    assert await service.cancel(-100, expected_round_id=old["round_id"], expected_round=1)
    second_service = GuessAgeService(db, asset_dir=tmp_path)
    fresh = await second_service.start(chat_id=-100, owner_id=2, owner_name="İki")
    with pytest.raises(GuessAgeServiceError, match="GUESS_AGE_STALE_ROUND"):
        await service.cancel(-100, expected_round_id=old["round_id"], expected_round=1)
    current = await service.current(-100)
    assert current["round_id"] == fresh["round_id"] and current["status"] == "playing"


@pytest.mark.asyncio
async def test_subject_named_in_credit_is_not_playable_but_catalog_is_preserved(tmp_path):
    db = FakeDatabase()
    service = GuessAgeService(db, asset_dir=tmp_path, rounds=2, rng=random.Random(7))
    catalog = builtin_assets("assets/guess_age")
    leaked = next(item for item in catalog if item.person_name == "Taj Jackson")
    safe = next(item for item in catalog if item.person_name == "Ayla Algan")
    assert leaked.person_name in leaked.credit
    assert safe.person_name not in safe.credit
    assert service._credit_identifies_subject(AgeAsset("x", "x.jpg", 40, "Taj-Jackson", credit="TAJ JACKSON · CC BY-SA"))
    assert not service._credit_identifies_subject(AgeAsset("x", "x.jpg", 40, "A", credit="A · CC BY-SA"))
    assert not service._credit_identifies_subject(safe)
    service.assets = (leaked, safe)
    for item in service.assets:
        (tmp_path / item.filename).write_bytes(b"photo")
    first = await service.start(chat_id=-100, owner_id=1, owner_name="Bir")
    assert first["asset_key"] == safe.key
    assert first["catalog_size"] == 1
    assert leaked in service.assets
    await service.set_prompt_message(-100, 101)
    second, _ = await service.close_round(chat_id=-100, expected_round_id=first["round_id"])
    assert second["asset_key"] == safe.key


@pytest.mark.asyncio
async def test_last_of_twenty_six_players_keeps_pending_and_durable_score(tmp_path):
    class FailingLeaderboard:
        async def record_score(self, **kwargs):
            raise RuntimeError("offline")

    db = FakeDatabase()
    service = GuessAgeService(db, asset_dir=tmp_path, leaderboard_service=FailingLeaderboard())
    for item in service.assets:
        (tmp_path / item.filename).write_bytes(b"photo")
    started = await service.start(chat_id=-100, owner_id=1, owner_name="Bir")
    await service.set_prompt_message(-100, 101)
    for user_id in range(1, 27):
        await service.submit(chat_id=-100, user_id=user_id, display_name=f"Oyuncu {user_id}", guess=42)
    closed, ranked = await service.close_round(chat_id=-100, expected_round_id=started["round_id"])
    assert len(ranked) == 26
    assert len(closed["history"][-1]["ranked"]) == 26
    assert len(closed["pending_scores"]) == 26
    last = next(row for row in closed["pending_scores"] if row["user_id"] == 26)
    assert last["event_id"] == f"guess_age:-100:{started['round_id']}:1:26"
    successful = FakeLeaderboard()
    service.leaderboard_service = successful
    await service.settle_scores(-100)
    assert {row["user_id"] for row in successful.rows} == set(range(1, 27))
    assert not db.states[(-100, "guess_age")]["pending_scores"]

