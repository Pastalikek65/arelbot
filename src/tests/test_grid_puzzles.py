"""Independent acceptance rules for memory pairs and a safe minefield."""

import pytest

from src.games import grid_puzzles as grid


def test_memory_mismatch_reveals_then_flips_after_three_seconds():
    state = grid.new_memory(chat_id=-100, thread_id=7, owner_id=1,
                            owner_name="Bir", round_id="r", now=1,
                            cards=["A", "B", "A", "B", "C", "D", "C", "D",
                                   "E", "F", "E", "F", "G", "H", "G", "H"])
    state = grid.memory_start(state, user_id=1, now=2)
    state = grid.activate(state, now=3)
    state = grid.advance(state, now=state["deadline"])
    state = grid.activate(state, now=20)
    state = grid.memory_pick(state, user_id=1, index=0, now=21)
    state = grid.memory_pick(state, user_id=1, index=1, now=22)
    assert state["phase"] == "mismatch" and state["deadline"] is None
    assert grid.public_view(state)["visible"][0] == "A"
    assert grid.public_view(state)["visible"][1] == "B"
    state = grid.activate(state, now=23)
    assert state["deadline"] >= 26
    assert grid.advance(state, now=25) is state
    state = grid.advance(state, now=state["deadline"])
    assert state["phase"] == "turn" and grid.public_view(state)["visible"][0] is None


def test_mines_first_click_and_neighbors_are_safe_and_flood_is_deterministic():
    state = grid.new_mines(chat_id=-100, thread_id=None, owner_id=1,
                           owner_name="Bir", round_id="r", now=1, seed=123)
    state = grid.activate(state, now=2)
    state = grid.mine_open(state, user_id=1, index=14, now=3)
    safe = {14, 7, 8, 9, 13, 15, 19, 20, 21}
    assert not safe.intersection(state["mines"])
    assert state["opened"] and 14 in state["opened"]
    assert grid.mine_count(state, 14) == 0
    assert state["phase"] in {"playing", "finished"}
    clone = grid.new_mines(chat_id=-100, thread_id=None, owner_id=1,
                           owner_name="Bir", round_id="r2", now=1, seed=123)
    clone = grid.activate(clone, now=2)
    clone = grid.mine_open(clone, user_id=1, index=14, now=3)
    assert clone["mines"] == state["mines"] and clone["opened"] == state["opened"]


def test_memory_actor_rotation_match_bonus_and_turn_timeout():
    state = grid.new_memory(chat_id=-100, thread_id=None, owner_id=1,
        owner_name="Bir", round_id="r", now=1,
        cards=["A", "B", "A", "B", "C", "D", "C", "D",
               "E", "F", "E", "F", "G", "H", "G", "H"])
    state = grid.memory_join(state, user_id=2, user_name="İki", now=2)
    state = grid.memory_start(state, user_id=1, now=3)
    state = grid.activate(state, now=4)
    state = grid.advance(state, now=12)
    state = grid.activate(state, now=13)
    state = grid.memory_pick(state, user_id=1, index=0, now=14)
    with pytest.raises(ValueError, match="not_actor"):
        grid.memory_pick(state, user_id=2, index=2, now=15)
    state = grid.memory_pick(state, user_id=1, index=2, now=15)
    assert state["players"][0]["score"] == 1 and state["current_actor"] == 1
    state = grid.activate(state, now=16)
    state = grid.advance(state, now=state["deadline"])
    assert state["current_actor"] == 2 and state["phase"] == "turn"


def test_mines_loss_is_not_rewarded_and_flags_are_bounded():
    state = grid.new_mines(chat_id=-100, thread_id=None, owner_id=1,
                           owner_name="Bir", round_id="r", now=1, seed=123)
    state = grid.activate(state, now=2)
    state = grid.mine_open(state, user_id=1, index=14, now=3)
    for index in [i for i in range(36) if i not in state["opened"]][:6]:
        state = grid.mine_flag(state, user_id=1, index=index, now=4)
    assert len(state["flags"]) == 6
    candidate = next(i for i in range(36) if i not in state["opened"] and i not in state["flags"])
    with pytest.raises(ValueError, match="flag_limit"):
        grid.mine_flag(state, user_id=1, index=candidate, now=5)
    # Remove a flag if it covers a mine, then open that mine.
    mine = state["mines"][0]
    if mine in state["flags"]:
        state = grid.mine_flag(state, user_id=1, index=mine, now=6)
    state = grid.mine_open(state, user_id=1, index=mine, now=7)
    assert state["phase"] == "finished" and state["outcome"] == "mine"
    assert not state["score_pending"] and not state["winner_ids"]


def test_mines_all_safe_cells_produce_one_victory():
    state = grid.new_mines(chat_id=-100, thread_id=None, owner_id=1,
                           owner_name="Bir", round_id="r", now=1, seed=987)
    state = grid.activate(state, now=2)
    state = grid.mine_open(state, user_id=1, index=0, now=3)
    for index in range(36):
        if state["phase"] == "finished":
            break
        if index not in state["mines"] and index not in state["opened"]:
            state = grid.mine_open(state, user_id=1, index=index, now=4)
    assert state["phase"] == "finished" and state["outcome"] == "cleared"
    assert state["winner_ids"] == [1] and state["score_pending"]


def test_mine_mode_button_is_target_state_not_replayable_toggle():
    state = grid.new_mines(chat_id=-100, thread_id=None, owner_id=1,
                           owner_name="Bir", round_id="r", now=1, seed=987)
    state = grid.activate(state, now=2)
    state = grid.mine_mode(state, user_id=1, enabled=True, now=3)
    assert state["flag_mode"]
    with pytest.raises(ValueError, match="stale_turn"):
        grid.mine_mode(state, user_id=1, enabled=True, now=4)


def test_mines_timeout_cancels_without_win_or_reward():
    state = grid.new_mines(chat_id=-100, thread_id=None, owner_id=1,
                           owner_name="Bir", round_id="r", now=1, seed=987)
    state = grid.activate(state, now=2)
    state = grid.advance(state, now=state["deadline"])
    assert state["phase"] == "cancelled" and not state["score_pending"]
    with pytest.raises(ValueError, match="stale_turn"):
        grid.mine_open(state, user_id=1, index=0, now=603)
