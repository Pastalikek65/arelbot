"""Independent product-contract checks for the reasoning arcade."""

import pytest

from src.games import reasoning_arcade as rules


@pytest.mark.parametrize("game", [rules.CALENDAR, rules.TRUE_FALSE, rules.DETECTIVE])
def test_joined_player_can_leave_without_cancelling_other_players(game):
    state = rules.new(game_key=game, chat_id=-100, thread_id=7,
                      owner_id=1, owner_name="Kurucu", round_id="r", now=100)
    state = rules.join(state, user_id=2, user_name="İki", now=101)
    state = rules.join(state, user_id=3, user_name="Üç", now=102)
    state = rules.start(state, user_id=1, now=103)
    updated = rules.leave(state, user_id=2, now=104)
    assert updated["phase"] == "playing"
    assert not next(p for p in updated["players"] if p["id"] == 2)["active"]
    assert next(p for p in updated["players"] if p["id"] == 3)["active"]
