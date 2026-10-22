"""Durability and source-rule regression tests for the maildian UNO adapter."""

import json
import random

import pytest

from src.games import uno_upstream as uno


def started(seed=7):
    state = uno.new_uno_state(chat_id=-100, owner_id=1, owner_name="Bir")
    state = uno.join_player(state, user_id=2, name="İki")
    return uno.start_round(state, rng=random.Random(seed))


def conserved(state):
    cards = state["deck"] + state["discard"] + [c for p in state["players"] for c in p["hand"]]
    assert len(cards) == 108
    assert len({c["id"] for c in cards}) == 108


def setup_cards(state, *, top_kind, player_kind, other_kind=None):
    """Move physical cards into a controlled position without duplicating any."""
    state = json.loads(json.dumps(state))
    pool = state["deck"] + state["discard"] + [c for p in state["players"] for c in p["hand"]]
    def take(kind):
        return pool.pop(next(i for i, c in enumerate(pool) if c["kind"] == kind))
    top = take(top_kind)
    card = take(player_kind)
    first = next(p for p in state["players"] if p["user_id"] == state["current_player_id"])
    second = next(p for p in state["players"] if p is not first)
    first["hand"] = [card]
    second["hand"] = [take(other_kind)] if other_kind else []
    state["deck"] = pool
    state["discard"] = [top]
    state["current_color"] = top["color"]
    state["pending_draw"] = 0
    state["awaiting_color"] = False
    first["drawn_card_id"] = None
    second["drawn_card_id"] = None
    # Test fixtures may omit conservation; production transitions never do.
    return state, first, second, card


def test_new_round_is_json_safe_and_conserves_all_physical_cards():
    state = started()
    assert state["engine"] == "maildian"
    assert uno.validate_state(json.loads(json.dumps(state)))
    conserved(state)
    same = uno.start_round(
        uno.join_player(uno.new_uno_state(chat_id=-100, owner_id=1, owner_name="Bir"), user_id=2, name="İki"),
        rng=random.Random(7),
    )
    assert state["deck"] == same["deck"]
    assert state["discard"] == same["discard"]


def test_draw_two_can_stack_then_next_player_draws_four():
    state = started()
    # Construct a full conservation-preserving hand setup.
    pool = state["deck"] + state["discard"] + [c for p in state["players"] for c in p["hand"]]
    draw_twos = [c for c in pool if c["kind"] == "draw2"]
    top, first_card, second_card = draw_twos[:3]
    reserved = {c["id"] for c in (top, first_card, second_card)}
    fillers = [c for c in pool if c["id"] not in reserved]
    current = next(p for p in state["players"] if p["user_id"] == state["current_player_id"])
    other = next(p for p in state["players"] if p is not current)
    current["hand"] = [first_card, fillers.pop()]
    other["hand"] = [second_card, fillers.pop()]
    state["discard"] = [top]
    state["deck"] = fillers
    state["current_color"] = top["color"]
    state["pending_draw"] = 2
    conserved(state)
    state = uno.play_card(state, user_id=current["user_id"], card_id=first_card["id"], action_id="a")
    assert state["pending_draw"] == 4
    assert state["current_player_id"] == other["user_id"]
    assert uno.can_play(state, second_card, player_id=other["user_id"])
    state = uno.draw_card(json.loads(json.dumps(state)), user_id=other["user_id"], action_id="b")
    assert state["last_penalty"] == {"user_id": other["user_id"], "cards": 4}
    assert state["pending_draw"] == 0
    conserved(state)


def test_draw_four_bluff_challenge_penalizes_previous_player():
    state = started()
    pool = state["deck"] + state["discard"] + [c for p in state["players"] for c in p["hand"]]
    wild = next(c for c in pool if c["kind"] == "draw4")
    top = next(c for c in pool if c["color"] == "red" and c["kind"].isdigit())
    match = next(c for c in pool if c["color"] == "red" and c["id"] != top["id"])
    other = next(c for c in pool if c["id"] not in {wild["id"], top["id"], match["id"]})
    reserved = {c["id"] for c in (wild, top, match, other)}
    current = next(p for p in state["players"] if p["user_id"] == state["current_player_id"])
    challenger = next(p for p in state["players"] if p is not current)
    current["hand"] = [wild, match]
    challenger["hand"] = [other]
    state["deck"] = [c for c in pool if c["id"] not in reserved]
    state["discard"] = [top]
    state["current_color"] = "red"
    conserved(state)
    state = uno.play_card(state, user_id=current["user_id"], card_id=wild["id"], action_id="play")
    assert state["awaiting_color"]
    state = uno.choose_color(state, user_id=current["user_id"], color="blue", action_id="color")
    assert state["current_player_id"] == challenger["user_id"]
    assert state["pending_draw"] == 4
    state = uno.call_bluff(json.loads(json.dumps(state)), user_id=challenger["user_id"], action_id="bluff")
    assert state["last_penalty"] == {"user_id": current["user_id"], "cards": 4}
    assert state["pending_draw"] == 0
    conserved(state)


def test_last_wild_cannot_be_played_and_replayed_action_is_rejected():
    state = started()
    state, player, _, wild = setup_cards(state, top_kind="4", player_kind="wild", other_kind="5")
    assert not uno.can_play(state, wild, player_id=player["user_id"])
    with pytest.raises(uno.UnoError, match="UNO_CARD_CANNOT_PLAY"):
        uno.play_card(state, user_id=player["user_id"], card_id=wild["id"], action_id="one")
    state = started()
    player = next(p for p in state["players"] if p["user_id"] == state["current_player_id"])
    state = uno.draw_card(state, user_id=player["user_id"], action_id="once")
    with pytest.raises(uno.UnoError, match="UNO_DUPLICATE_ACTION"):
        uno.draw_card(state, user_id=player["user_id"], action_id="once")


def test_serialized_full_match_reaches_a_scored_winner_without_card_loss():
    state = started(16)
    for turn in range(300):
        if state["status"] == "finished":
            break
        state = json.loads(json.dumps(state))
        user_id = state["current_player_id"]
        player = next(p for p in state["players"] if p["user_id"] == user_id)
        action_id = f"turn-{turn}"
        if state["awaiting_color"]:
            state = uno.choose_color(state, user_id=user_id, color="red", action_id=action_id)
        else:
            legal = [card for card in player["hand"] if uno.can_play(state, card, player_id=user_id)]
            if legal:
                state = uno.play_card(state, user_id=user_id, card_id=legal[0]["id"], action_id=action_id)
            elif player["drawn_card_id"]:
                state = uno.skip_after_draw(state, user_id=user_id, action_id=action_id)
            else:
                state = uno.draw_card(state, user_id=user_id, action_id=action_id)
        conserved(state)
        assert uno.validate_state(state)
    assert state["status"] == "finished"
    assert state["winner_id"] in {1, 2}
    assert state["round_points"] > 0
    assert next(p for p in state["players"] if p["user_id"] == state["winner_id"])["score"] == state["round_points"]


def _repack(state, *, draw_count, graveyard_count, top_kind=None):
    state = json.loads(json.dumps(state))
    pool = state["deck"] + state["discard"] + [c for p in state["players"] for c in p["hand"]]
    if top_kind:
        index = next(i for i, c in enumerate(pool) if c["kind"] == top_kind)
        top = pool.pop(index)
    else:
        top = pool.pop()
    state["discard"] = pool[:graveyard_count] + [top]
    state["deck"] = pool[graveyard_count:graveyard_count + draw_count]
    remaining = pool[graveyard_count + draw_count:]
    middle = len(remaining) // 2
    state["players"][0]["hand"] = remaining[:middle]
    state["players"][1]["hand"] = remaining[middle:]
    state["current_color"] = top["color"] if top["color"] != "black" else "red"
    for player in state["players"]:
        player["drawn_card_id"] = None
        player["draw_attempted"] = False
    conserved(state)
    assert uno.validate_state(state)
    return state


def test_empty_deck_draw_persists_pass_state_instead_of_stalling():
    state = _repack(started(), draw_count=0, graveyard_count=0)
    actor = state["current_player_id"]
    state = uno.draw_card(state, user_id=actor, action_id="empty")
    player = next(p for p in state["players"] if p["user_id"] == actor)
    assert state["current_player_id"] == actor
    assert player["draw_attempted"] and player["drawn_card_id"] is None
    with pytest.raises(uno.UnoError, match="UNO_DRAW_ALREADY_TAKEN"):
        uno.draw_card(state, user_id=actor, action_id="again")
    state = uno.skip_after_draw(state, user_id=actor, action_id="pass")
    assert state["current_player_id"] != actor
    conserved(state)


def test_partial_penalty_draw_keeps_cards_and_advances_turn():
    state = _repack(started(), draw_count=2, graveyard_count=0, top_kind="draw4")
    state["pending_draw"] = 4
    actor = state["current_player_id"]
    before = len(next(p for p in state["players"] if p["user_id"] == actor)["hand"])
    state = uno.draw_card(state, user_id=actor, action_id="partial")
    after = len(next(p for p in state["players"] if p["user_id"] == actor)["hand"])
    assert after - before == 2
    assert state["last_penalty"] == {"user_id": actor, "cards": 2}
    assert state["pending_draw"] == 0 and state["current_player_id"] != actor
    conserved(state)


def test_discard_recycle_is_repeatable_from_private_persisted_seed():
    state = _repack(started(), draw_count=0, graveyard_count=9)
    assert "private_shuffle_seed" not in uno.player_view(state, state["current_player_id"])
    actor = state["current_player_id"]
    a = uno.draw_card(json.loads(json.dumps(state)), user_id=actor, action_id="draw")
    b = uno.draw_card(json.loads(json.dumps(state)), user_id=actor, action_id="draw")
    assert a["deck"] == b["deck"] and a["players"] == b["players"]
    assert a["private_shuffle_count"] == state["private_shuffle_count"] + 1
    conserved(a)


@pytest.mark.parametrize("damage", [
    lambda s: s.pop("discard"),
    lambda s: s.update(current_player_id=999),
    lambda s: s["players"][0]["hand"][0].update(kind="counterfeit"),
    lambda s: s["deck"].append(dict(s["deck"][0])),
])
def test_corrupt_playing_snapshots_rejected_before_engine_access(damage):
    state = started()
    damage(state)
    assert not uno.validate_state(state)
    with pytest.raises(uno.UnoError, match="UNO_INVALID_STATE"):
        uno.draw_card(state, user_id=state["players"][0]["user_id"], action_id="x")
