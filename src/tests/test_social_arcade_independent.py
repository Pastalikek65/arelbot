"""Independent social-arcade review regressions; production code is untouched."""

from copy import deepcopy

import pytest

from src.games import social_arcade as rules
from src.tests.test_social_arcade_service import act, current, delivered, setup


def test_eliminated_poison_player_leaving_does_not_cancel_living_match():
    state = rules.new(game_key="zehirli_kadeh", chat_id=-100, thread_id=8,
                      owner_id=1, owner_name="Bir", round_id="r", now=100)
    for uid in (2, 3):
        state = rules.join(state, user_id=uid, user_name=str(uid), now=101)
    for uid in (1, 2, 3):
        state = rules.ready(state, user_id=uid)
    state = rules.start(state, user_id=1, now=102, seed=0)
    # A zero-health player remains in the roster after a poisoned drink.
    state = deepcopy(state)
    state["health"]["3"] = 0
    state["round_no"] = 3
    assert state["health"]["1"] > 0 and state["health"]["2"] > 0
    after = rules.act(state, user_id=3, action="leave", now=103)
    assert after["phase"] == "poison"
    assert not after["players"][2]["active"]


@pytest.mark.asyncio
async def test_delayed_private_card_ack_cannot_satisfy_later_author_revision():
    key = "iki_dogru_bir_yalan"
    service, _, _ = await setup(key, 3)
    live = await delivered(service, key, t=103)
    await act(service, key, live, 1, "claim", text="Bugün çay içtim",
              t=104, private=True)
    awaiting = await current(service, key)
    assert "1" not in awaiting["private_delivery"]
    assert awaiting["private_dirty"]
    first_revision = await service.claim_delivery(
        game_key=key, chat_id=-100, thread_id=8,
        kind="private", user_id=1, now=104)
    assert first_revision is not None
    # The previous acknowledged card remains the accepted input source while
    # this edited card is in flight. Another author message advances content.
    await act(service, key, awaiting, 1, "claim", text="Bisiklet kullanırım",
              t=104.5, private=True)
    newer = await current(service, key)
    assert len(newer["claims"]) == 2 and "1" not in newer["private_delivery"]
    status, _ = await service.acknowledge_private(
        game_key=key, chat_id=-100, thread_id=8,
        round_id=newer["round_id"], phase_seq=newer["phase_seq"],
        user_id=1, message_id=200, now=105)
    after = await current(service, key)
    assert status != "applied"
    assert "1" not in after["private_delivery"]
    assert after["private_dirty"]


@pytest.mark.asyncio
async def test_all_timeout_team_match_does_not_award_idle_tie():
    key = "takim_bilgi_ligi"
    service, _, _ = await setup(key, 4)
    for turn in range(10):
        live = await delivered(service, key, t=110 + turn * 10)
        assert live["phase"] == "trivia" and live["turn_no"] == turn
        await service.advance(game_key=key, chat_id=-100, thread_id=8,
                              round_id=live["round_id"], now=live["deadline"])
        result = await delivered(service, key, t=live["deadline"] + 1)
        assert result["phase"] == "result"
        await service.advance(game_key=key, chat_id=-100, thread_id=8,
                              round_id=result["round_id"], now=result["deadline"])
    finished = await current(service, key)
    assert finished["phase"] == "finished"
    assert finished["team_scores"] == [0, 0]
    assert finished["winner_ids"] == []
    assert not finished["score_pending"]
