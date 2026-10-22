"""Complete deterministic two- and four-seat card matches."""
from collections import Counter
import random

import pytest

from src.games import card_table as rules


def ready_lobby(game, count):
    state = rules.new(game_key=game, chat_id=-100, thread_id=7,
                      owner_id=1, owner_name="Bir", round_id="r", now=100)
    for uid in range(2, count + 1):
        state = rules.join(state, user_id=uid, user_name=f"Oyuncu {uid}", now=100)
    for uid in range(1, count + 1):
        state = rules.ready(state, user_id=uid)
    return state


@pytest.mark.parametrize("players,deals", [(2, 6), (4, 3)])
def test_pisti_full_52_card_match_equal_deals_and_team_scoring(players, deals):
    state = rules.start(ready_lobby("pisti", players), user_id=1, now=101, seed=77)
    assert len(state["table"]) == 4 and len(state["deck"]) == 48 - players * 4
    assert all(len(hand) == 4 for hand in state["hands"].values())
    seen = set(state["table"])
    for hand in state["hands"].values():
        seen.update(hand)
    seen.update(state["deck"])
    assert seen == set(rules.DECK)
    for turn in range(48):
        assert state["phase"] == "playing"
        actor = state["players"][state["turn_index"]]["id"]
        state["phase_ready"], state["deadline"] = True, 1000 + turn
        state = rules.act(state, user_id=actor, action="play", value=0, now=200 + turn)
    assert state["phase"] == "finished" and state["turn_no"] == 48
    assert state["deal_no"] == deals and not state["deck"]
    assert sum(len(pile) for pile in state["captured"].values()) == 52
    assert not any(state["hands"].values()) and not state["table"]
    if players == 4:
        assert state["scores"]["1"] == state["scores"]["3"]
        assert state["scores"]["2"] == state["scores"]["4"]


def test_pisti_single_card_capture_and_jack_pisti_values():
    state = rules.start(ready_lobby("pisti", 2), user_id=1, now=101, seed=1)
    state["table"] = ["4H"]
    state["hands"]["1"][0] = "4S"
    state["phase_ready"], state["deadline"] = True, 200
    result = rules.act(state, user_id=1, action="play", value=0, now=102)
    assert result["pisti_points"][0] == 10 and not result["table"]
    state["table"] = ["JD"]
    state["hands"]["1"][0] = "JS"
    result = rules.act(state, user_id=1, action="play", value=0, now=102)
    assert result["pisti_points"][0] == 20


@pytest.mark.parametrize("players", [2, 4])
def test_pisti_tie_records_scores_without_declaring_both_sides_winners(players):
    state = rules.start(ready_lobby("pisti", players), user_id=1, now=101, seed=8)
    for turn in range(48):
        state["phase_ready"], state["deadline"] = True, 1000
        actor = state["players"][state["turn_index"]]["id"]
        state = rules.act(state, user_id=actor, action="play", value=0, now=200 + turn)
    assert state["phase"] == "finished"
    assert state["team_scores"] == [8, 8]
    assert state["winner_ids"] == []
    assert state["score_pending"] is True
    assert set(state["scores"].values()) == {8}
    for side, points in enumerate(state["score_breakdown"]):
        assert sum(points.values()) == state["team_scores"][side]


@pytest.mark.parametrize("players", [3, 8])
def test_oldmaid_full_match_server_draws_secretly_until_single_king(players):
    state = rules.start(ready_lobby("papaz_kimde", players), user_id=1,
                        now=101, seed=17)
    deck = [card for hand in state["hands"].values() for card in hand] + state["discarded"]
    assert len(deck) == 49 and len(set(deck)) == 49
    assert Counter(rules.rank(card) for card in deck)["K"] == 1
    randomizer = random.Random(931)
    for turn in range(240):
        if state["phase"] == "finished":
            break
        assert state["phase"] == "playing"
        actor = state["players"][state["turn_index"]]["id"]
        target = rules.draw_target(state)
        assert target != actor and state["hands"][str(target)]
        state["phase_ready"], state["deadline"] = True, 1000 + turn
        state = rules.act(state, user_id=actor, action="draw",
                          now=200 + turn, rng=randomizer)
        assert "hands" not in rules.public_state(state)
        assert "last_draw" not in rules.public_state(state)
    assert state["phase"] == "finished", (players, state.get("draw_count"))
    assert len(state["winner_ids"]) == players - 1
    assert state["hands"][str(state["loser_id"])] == ["KS"]
    assert all(not hand for uid, hand in state["hands"].items()
               if int(uid) != state["loser_id"])


def test_oldmaid_indexed_hidden_draw_and_legacy_random_draw():
    state = rules.start(ready_lobby("papaz_kimde", 3), user_id=1, now=101, seed=17)
    actor = state["players"][state["turn_index"]]["id"]
    target = rules.draw_target(state)
    target_cards = list(state["hands"][str(target)])
    assert len(target_cards) > 1
    state["phase_ready"], state["deadline"] = True, 200
    chosen = rules.act(state, user_id=actor, action="draw", value=1, now=102)
    assert chosen["last_draw"]["card"] == target_cards[1]
    assert chosen["last_draw"]["target"] == target
    assert state["hands"][str(target)] == target_cards
    with pytest.raises(ValueError, match="ct_invalid_card"):
        rules.act(state, user_id=actor, action="draw", value=len(target_cards), now=102)
    with pytest.raises(ValueError, match="ct_invalid_card"):
        rules.act(state, user_id=actor, action="draw", value=True, now=102)
    legacy = rules.act(state, user_id=actor, action="draw", now=102,
                       rng=random.Random(7))
    assert legacy["last_draw"]["card"] in target_cards


def test_timeout_and_player_leave_are_finite():
    state = rules.start(ready_lobby("pisti", 2), user_id=1, now=101, seed=2)
    state["phase_ready"], state["deadline"] = True, 102
    after = rules.advance(state, now=102)
    assert after["turn_no"] == 1 and after["timeouts"]["1"] == 1
    # Pişti forfeits the whole side, while a lobby departure is removable.
    state = rules.start(ready_lobby("pisti", 4), user_id=1, now=101, seed=2)
    after = rules.act(state, user_id=3, action="leave", now=102)
    assert after["phase"] == "finished" and after["winner_ids"] == [2, 4]
    assert after["deadline"] is None and after["panel_wait_until"] is None
    assert not after["score_pending"] and all(score == 0 for score in after["scores"].values())


@pytest.mark.parametrize("game,players", [("pisti", 2), ("papaz_kimde", 3)])
def test_three_timeouts_report_forfeit_without_artificial_points(game, players):
    state = rules.start(ready_lobby(game, players), user_id=1, now=101, seed=2)
    actor = state["players"][state["turn_index"]]["id"]
    state["timeouts"][str(actor)] = 2
    state["phase_ready"], state["deadline"] = True, 102
    state = rules.advance(state, now=102)
    assert state["phase"] == "finished"
    assert state["reason"] == "three_timeouts"
    assert state["winner_ids"] and not state["score_pending"]
    assert all(score == 0 for score in state["scores"].values())
