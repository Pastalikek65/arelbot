"""Independent merge, spawn, victory, and timeout invariants for 2048."""

import random

import pytest

from src.games import tile2048 as rules


@pytest.mark.parametrize("direction,line,expected,gained", [
    ("left", [2, 2, 2, 2], [4, 4, 0, 0], 8),
    ("right", [2, 2, 2, 2], [0, 0, 4, 4], 8),
    ("left", [4, 4, 8, 8], [8, 16, 0, 0], 24),
    ("left", [2, 2, 4, 0], [4, 4, 0, 0], 4),
])
def test_one_merge_per_tile_per_move(direction, line, expected, gained):
    board = [line, [0] * 4, [0] * 4, [0] * 4]
    shifted, score, changed = rules.slide(board, direction)
    assert shifted[0] == expected
    assert score == gained
    assert changed and board[0] == line


def test_vertical_merge_and_initial_two_weighted_spawns():
    board = [[2, 0, 0, 0], [2, 0, 0, 0], [4, 0, 0, 0], [4, 0, 0, 0]]
    up, gained, changed = rules.slide(board, "up")
    assert changed and gained == 12
    assert [row[0] for row in up] == [4, 8, 0, 0]
    down, gained, _ = rules.slide(board, "down")
    assert gained == 12 and [row[0] for row in down] == [0, 0, 4, 8]
    initial = rules.new(chat_id=-100, thread_id=7, owner_id=1,
                        owner_name="Bir", round_id="r", now=100, rng=random.Random(1))
    values = [cell for row in initial["board"] for cell in row if cell]
    assert len(values) == 2 and set(values) <= {2, 4}


def test_noop_has_no_spawn_or_score_and_2048_can_continue_then_cashout():
    state = rules.new(chat_id=-100, thread_id=None, owner_id=1,
                      owner_name="Bir", round_id="r", now=100, rng=random.Random(1))
    state.update(board=[[2, 4, 8, 16], [0] * 4, [0] * 4, [0] * 4], deadline=200,
                 panel_wait_until=None)
    with pytest.raises(ValueError, match="t2_no_move"):
        rules.move(state, user_id=1, direction="left", now=101, rng=random.Random(3))
    assert state["board"][0] == [2, 4, 8, 16] and state["score"] == 0
    state["board"] = [[1024, 1024, 0, 0], [0] * 4, [0] * 4, [0] * 4]
    won = rules.move(state, user_id=1, direction="left", now=102, rng=random.Random(4))
    assert won["won"] and won["best_tile"] == 2048 and won["score"] == 2048
    assert won["phase"] == "playing" and won["deadline"] is None
    with pytest.raises(ValueError, match="t2_wrong_phase"):
        rules.cashout(won, user_id=1, now=103)
    won["deadline"], won["panel_wait_until"] = 300, None
    finished = rules.cashout(won, user_id=1, now=104)
    assert finished["phase"] == "finished" and finished["winner_ids"] == [1]
    assert finished["score_pending"]


def test_full_board_without_merge_finishes_loss_without_winner():
    state = rules.new(chat_id=-100, thread_id=None, owner_id=1,
                      owner_name="Bir", round_id="r", now=100, rng=random.Random(1))
    state.update(board=[[2, 2, 8, 16], [64, 128, 256, 512],
                        [512, 1024, 64, 128], [128, 256, 512, 1024]],
                 deadline=200, panel_wait_until=None)
    lost = rules.move(state, user_id=1, direction="left", now=101, rng=random.Random(0))
    assert lost["board"][0] == [4, 8, 16, 2]
    assert lost["phase"] == "finished" and not lost["won"]
    assert lost["winner_ids"] == [] and lost["score"] == 4
    assert not rules.legal_moves(lost["board"])


def test_panel_and_idle_timeouts_cancel_without_rewards():
    state = rules.new(chat_id=-100, thread_id=None, owner_id=1,
                      owner_name="Bir", round_id="r", now=100, rng=random.Random(1))
    assert rules.advance(state, now=399) is state
    expired = rules.advance(state, now=400)
    assert expired["phase"] == "cancelled" and not expired["score_pending"]
    state["deadline"], state["panel_wait_until"] = 500, None
    expired = rules.advance(state, now=500)
    assert expired["reason"] == "idle_timeout" and not expired["score_pending"]
