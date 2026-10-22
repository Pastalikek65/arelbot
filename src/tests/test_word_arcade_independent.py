"""Independent gameplay review: reproducible player-facing word-game gaps."""
import random

import pytest

from src.games import word_arcade as rules
from src.services.word_arcade_service import WordArcadeService
from src.tests.test_word_arcade_service import Repository


async def started(game_key, *, players=2, now=100, **kwargs):
    repo = Repository()
    state = rules.new_lobby(game_key=game_key, chat_id=-100, thread_id=7,
                            owner_id=1, owner_name="A", now=now)
    for uid in range(2, players + 1):
        state = rules.join(state, user_id=uid, user_name=f"Oyuncu {uid}", now=now)
    await repo.create_game_state_if_absent(-100, f"{game_key}_t7", state)
    service = WordArcadeService(repo)
    await service.act(game_key=game_key, chat_id=-100, thread_id=7,
        user_id=1, action_id="start", action="start", round_id=state["round_id"],
        now=now + 1, **kwargs)
    return service, repo


async def panel_ack(service, game_key, *, now, message_id=50):
    state = await service.current(game_key=game_key, chat_id=-100, thread_id=7)
    lease = await service.claim_panel(game_key=game_key, chat_id=-100,
                                      thread_id=7, now=now)
    assert lease is not None
    assert await service.acknowledge_panel(game_key=game_key, chat_id=-100,
        thread_id=7, round_id=state["round_id"],
        expected_version=lease["version"], message_id=message_id, now=now)
    return await service.current(game_key=game_key, chat_id=-100, thread_id=7)


@pytest.mark.asyncio
async def test_bomb_fuse_keeps_global_end_during_next_panel_delay():
    key = "bomba"
    service, _ = await started(key, bigram="ka", fuse_seconds=60)
    state = await panel_ack(service, key, now=102, message_id=50)
    first_fuse_end = state["fuse_deadline"]
    await service.act(game_key=key, chat_id=-100, thread_id=7, user_id=1,
        action_id="word-one", action="text", round_id=state["round_id"],
        phase_seq=state["phase_seq"], text="kalem", message_id=51, now=105)
    state = await panel_ack(service, key, now=110, message_id=50)
    assert state["fuse_deadline"] <= first_fuse_end


@pytest.mark.asyncio
async def test_same_second_answer_after_panel_ack_is_not_stale():
    key = "sozcuk_avi"
    service, _ = await started(key, players=1)
    state = await panel_ack(service, key, now=102.8, message_id=50)
    status, _ = await service.act(game_key=key, chat_id=-100, thread_id=7,
        user_id=1, action_id="first-valid-word", action="text",
        round_id=state["round_id"], phase_seq=state["phase_seq"],
        text="kedi", message_id=51, submitted_at=102, now=103)
    assert status == "applied"


def test_hunt_board_seeds_offer_more_than_transpose_only():
    state = rules.new_lobby(game_key="sozcuk_avi", chat_id=-100, thread_id=None,
                            owner_id=1, owner_name="A", now=100)
    boards = {tuple(rules.start(state, user_id=1, now=101,
                                rng=random.Random(seed))["board"])
              for seed in range(100)}
    assert len(boards) >= 12
