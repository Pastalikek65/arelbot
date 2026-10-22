"""Departures remain durable across rounds, transport, and reward settlement."""

from types import SimpleNamespace

import pytest

from src.games import reasoning_arcade as rules
from src.handlers.reasoning_arcade_handler import ReasoningArcadeHandler
from src.services import reasoning_arcade_ui as ui
from src.services.reasoning_arcade_service import ReasoningArcadeService
from src.tests.test_reasoning_arcade_handler import Bot, update
from src.tests.test_reasoning_arcade_service import Ledger, Repository, ack, act, current


@pytest.mark.asyncio
async def test_service_leave_deactivates_score_and_completes_remaining_rounds():
    repo, ledger = Repository(), Ledger()
    service = ReasoningArcadeService(repo, leaderboard=ledger, economy=ledger)
    _, opened = await service.open(game_key=rules.CALENDAR, chat_id=-100,
                                   thread_id=7, user_id=1, user_name="Bir", now=100)
    rid = opened["round_id"]
    for uid in (2, 3):
        await act(service, rules.CALENDAR, rid, "join", f"join:{uid}", 101,
                  user_id=uid, user_name=str(uid))
    await act(service, rules.CALENDAR, rid, "start", "start", 102)
    for turn in range(5):
        moment = 103 + turn * 5
        live = await ack(service, rules.CALENDAR, rid, moment)
        solution = live["puzzle"]["solution"]
        if turn == 0:
            for offset, value in enumerate(solution):
                await act(service, rules.CALENDAR, rid, "choose", f"left:{offset}",
                          moment + .01 + offset * .01, user_id=2, value=value)
            await act(service, rules.CALENDAR, rid, "confirm", "left:confirm",
                      moment + .1, user_id=2)
            await act(service, rules.CALENDAR, rid, "leave", "left:leave",
                      moment + .2, user_id=2)
            with pytest.raises(ValueError, match="ra_not_player"):
                await act(service, rules.CALENDAR, rid, "choose", "left:replay",
                          moment + .3, user_id=2, value=0)
        for uid in (1, 3):
            for offset, value in enumerate(solution):
                await act(service, rules.CALENDAR, rid, "choose",
                          f"pick:{turn}:{uid}:{offset}", moment + .4 + offset * .01,
                          user_id=uid, value=value)
            await act(service, rules.CALENDAR, rid, "confirm", f"ok:{turn}:{uid}",
                      moment + .6 + uid * .01, user_id=uid)
    finished = await current(service, rules.CALENDAR)
    assert finished["phase"] == "finished"
    assert finished["winner_ids"] == [1, 3]
    assert next(p for p in finished["players"] if p["id"] == 2)["score"] == 5
    restarted = ReasoningArcadeService(repo, leaderboard=ledger, economy=ledger)
    await restarted.settle_scores(game_key=rules.CALENDAR, chat_id=-100, thread_id=7)
    await restarted.settle_scores(game_key=rules.CALENDAR, chat_id=-100, thread_id=7)
    assert len(ledger.scores) == 2 and len(ledger.coins) == 2
    assert all(not event.endswith(":2") for event in ledger.scores)


@pytest.mark.asyncio
async def test_handler_leave_callback_transfers_owner_and_lone_player_continues():
    service = ReasoningArcadeService(Repository())
    handler, bot = ReasoningArcadeHandler(service), Bot()
    _, opened = await service.open(game_key=rules.TRUE_FALSE, chat_id=-100,
                                   thread_id=7, user_id=1, user_name="Bir")
    rid = opened["round_id"]
    await service.act(game_key=rules.TRUE_FALSE, chat_id=-100, thread_id=7,
        user_id=2, user_name="İki", action="join", action_id="join2",
        round_id=rid)
    await handler.publish(bot, game_key=rules.TRUE_FALSE, chat_id=-100, thread_id=7)
    lobby = await current(service, rules.TRUE_FALSE)
    event = update(uid=1, message_id=lobby["panel_message_id"],
                   data=ui.callback_data(lobby, "l"), token="leave1")
    assert await handler.callback(event, SimpleNamespace(bot=bot))
    assert event.callback_query.answers == [(None, {})]
    after = await current(service, rules.TRUE_FALSE)
    assert after["owner_id"] == 2 and after["phase"] == "lobby"
    assert not after["players"][0]["active"]
    await service.act(game_key=rules.TRUE_FALSE, chat_id=-100, thread_id=7,
        user_id=2, action="start", action_id="start2", round_id=rid)
    assert (await current(service, rules.TRUE_FALSE))["phase"] == "playing"


@pytest.mark.parametrize("game", [rules.CALENDAR, rules.TRUE_FALSE, rules.DETECTIVE])
def test_last_player_leave_cancels_without_rewards(game):
    state = rules.new(game_key=game, chat_id=-100, thread_id=None,
                      owner_id=1, owner_name="Bir", round_id="r", now=100)
    state = rules.start(state, user_id=1, now=101)
    state = rules.leave(state, user_id=1, now=102)
    assert state["phase"] == "cancelled"
    assert state["deadline"] is None and state["panel_wait_until"] is None
    assert not state["score_pending"] and not state["winner_ids"]
