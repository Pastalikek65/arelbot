"""Independent adversarial checks for secret social-game transitions."""

import pytest
import time
from telegram.error import Forbidden

from src.games import spy, vampire
from src.handlers.vampire_handler import VampireHandler
from src.services.vampire_service import VampireService
from src.services.vampire_ui import render_vampire
from src.tests.test_vampire_handler import Bot, prepared_state
from src.tests.test_vampire_service import Repository


@pytest.mark.parametrize("departing", (1, 2))
def test_spy_question_turn_gets_fair_clock_after_actor_departure(departing):
    state = spy.new_lobby(chat_id=-100, thread_id=7, owner_id=1,
                          owner_name="Bir", round_id="review", now=0)
    for user_id in range(2, 6):
        state = spy.join(state, user_id=user_id, user_name=str(user_id), now=1)
    state = spy.start(state, user_id=1, spy_id=5,
                      location_index=0, first_index=0, now=2)
    for user_id in range(1, 6):
        state = spy.delivered(state, user_id=user_id, now=3)
        state = spy.confirm(state, user_id=user_id, now=4)
    state["deadline"] = 94
    state["question_started_at"] = 4
    state = spy.question_target(state, user_id=1, target_id=2, now=5)

    restarted = spy.leave(state, user_id=departing, now=93)

    assert restarted["phase"] == "question"
    assert restarted["target_id"] is None
    assert restarted["deadline"] is None or restarted["deadline"] >= 93 + spy.TURN_SECONDS


def test_cancelled_vampire_match_keeps_undelivered_roles_private():
    state = vampire.new_lobby(chat_id=-100, thread_id=7, owner_id=1,
                              owner_name="Bir", round_id="review", now=0)
    for user_id in range(2, 8):
        state = vampire.join(state, user_id=user_id, user_name=str(user_id), now=1)
    for user_id in range(1, 8):
        state = vampire.ready(state, user_id=user_id, now=2)
    state = vampire.start(state, user_id=1,
                          roles=["vampire", "seer", "healer", "villager",
                                 "villager", "villager", "villager"], now=3)

    cancelled = vampire.cancel(state, user_id=1)

    assert cancelled["phase"] == "cancelled"
    assert cancelled["settings"]["reveal_on_death"] is False
    assert all("role" not in player for player in vampire.public_state(cancelled)["players"])
    panel = render_vampire(cancelled)
    assert "Kâhin" not in panel and "Şifacı" not in panel


@pytest.mark.asyncio
async def test_vampire_phase_clock_waits_for_private_action_delivery():
    repository = Repository()
    service = VampireService(repository)
    state = prepared_state()
    for user_id in range(1, 8):
        state = vampire.role_ack(state, user_id=user_id, now=5)
    state["panel_dirty"] = True
    state["panel_wait_until"] = time.time() + 300
    repository.rows[(-100, service.key(7))] = state
    bot = Bot()
    bot.fail = Forbidden("private chat blocked")

    await VampireHandler(service).process(bot, {"chat_id": -100, "state": state})

    current = await service.current(-100, 7)
    assert current["phase"] == "cancelled" or current["deadline"] is None
    assert not current.get("private_delivery")
    assert current["phase"] == "cancelled" or current["panel_dirty"] is False
