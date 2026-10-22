"""Cross-instance age game CAS tests."""

import asyncio
from copy import deepcopy

import pytest

from src.services.guess_age_service import GuessAgeService, GuessAgeServiceError


class Repository:
    def __init__(self):
        self.states = {}
        self.barrier = False
        self.readers = 0
        self.ready = asyncio.Event()

    async def get_game_state(self, chat_id, game_type):
        value = deepcopy(self.states.get((chat_id, game_type)))
        if self.barrier and value is not None:
            self.readers += 1
            if self.readers >= 2:
                self.ready.set()
            await self.ready.wait()
        return value

    async def create_game_state_if_absent(self, chat_id, game_type, state):
        key = (chat_id, game_type)
        if key in self.states:
            return False
        self.states[key] = deepcopy(state)
        return True

    async def initialize_game_state_version_if_missing(self, chat_id, game_type):
        state = self.states.get((chat_id, game_type))
        if state is None or "version" in state:
            return False
        state["version"] = 0
        return True

    async def compare_and_save_game_state(self, chat_id, game_type, state, *, expected_version):
        key = (chat_id, game_type)
        old = self.states.get(key)
        if old is None or old.get("version") != expected_version or state["version"] != expected_version + 1:
            return False
        self.states[key] = deepcopy(state)
        return True


@pytest.mark.asyncio
async def test_two_instances_preserve_both_simultaneous_guesses(tmp_path):
    db = Repository()
    first = GuessAgeService(db, asset_dir=tmp_path)
    second = GuessAgeService(db, asset_dir=tmp_path)
    for item in first.assets:
        (tmp_path / item.filename).write_bytes(b"photo")
    await first.start(chat_id=-100, owner_id=1, owner_name="Bir")
    await first.set_prompt_message(-100, 11)
    db.barrier = True
    one, two = await asyncio.gather(
        first.submit(chat_id=-100, user_id=2, display_name="İki", guess=30),
        second.submit(chat_id=-100, user_id=3, display_name="Üç", guess=40),
    )
    assert one[1].accepted and two[1].accepted
    assert set(db.states[(-100, "guess_age")]["answers"]) == {"2", "3"}


@pytest.mark.asyncio
async def test_two_instances_allow_one_answer_per_player(tmp_path):
    db = Repository()
    first = GuessAgeService(db, asset_dir=tmp_path)
    second = GuessAgeService(db, asset_dir=tmp_path)
    for item in first.assets:
        (tmp_path / item.filename).write_bytes(b"photo")
    await first.start(chat_id=-100, owner_id=1, owner_name="Bir")
    await first.set_prompt_message(-100, 11)
    db.barrier = True
    results = await asyncio.gather(
        first.submit(chat_id=-100, user_id=2, display_name="İki", guess=30),
        second.submit(chat_id=-100, user_id=2, display_name="İki", guess=40),
        return_exceptions=True,
    )
    assert sum(isinstance(item, GuessAgeServiceError) and item.code == "guess_age_already_answered" for item in results) == 1
    assert len(db.states[(-100, "guess_age")]["answers"]) == 1


@pytest.mark.asyncio
async def test_legacy_version_backfill_preserves_existing_payload(tmp_path):
    db = Repository()
    service = GuessAgeService(db, asset_dir=tmp_path)
    for item in service.assets:
        (tmp_path / item.filename).write_bytes(b"photo")
    original = await service.start(chat_id=-100, owner_id=1, owner_name="Bir")
    await service.set_prompt_message(-100, 11)
    legacy = db.states[(-100, "guess_age")]
    legacy.pop("version")
    history = deepcopy(legacy)
    current = await service.current(-100)
    assert current["version"] == 0
    assert {k: v for k, v in current.items() if k != "version"} == history
    assert current["round_id"] == original["round_id"]
