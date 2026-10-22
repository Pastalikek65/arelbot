"""Independent rule checks for all three logic puzzle modes."""
import pytest

from src.games import logic_puzzles as game
from src.services.logic_puzzles_ui import keyboard


@pytest.mark.parametrize("size", [4, 9])
def test_curated_sudoku_has_exactly_one_valid_solution(size):
    for seed in (0, 1, 17, 9001):
        board, solution = game.puzzle(size, seed)
        assert len(game.solve(board, limit=2)) == 1
        assert game.solve(board, limit=2)[0] == solution
        assert all(len(row) == size for row in board)
        box = int(size ** .5)
        required = set(range(1, size + 1))
        assert all(set(row) == required for row in solution)
        assert all({solution[r][c] for r in range(size)} == required for c in range(size))
        assert all({solution[r][c] for r in range(br, br + box)
                    for c in range(bc, bc + box)} == required
                   for br in range(0, size, box) for bc in range(0, size, box))


def test_sudoku_fixed_clue_conflict_undo_hint_and_owner():
    state = game.new(game_key="sudoku", chat_id=-100, thread_id=None,
                     owner_id=1, owner_name="Bir", round_id="r", now=100, seed=3)
    state["panel_ready"] = True
    fixed = next((r, c) for r in range(4) for c in range(4) if state["givens"][r][c])
    state = game.select(state, user_id=1, row=fixed[0], col=fixed[1], now=101)
    with pytest.raises(ValueError, match="lp_fixed_cell"):
        game.digit(state, user_id=1, value=2, now=102)
    empty = next((r, c) for r in range(4) for c in range(4) if not state["givens"][r][c])
    state = game.select(state, user_id=1, row=empty[0], col=empty[1], now=103)
    with pytest.raises(ValueError, match="lp_not_owner"):
        game.digit(state, user_id=2, value=state["solution"][empty[0]][empty[1]], now=104)
    conflict = next(d for d in range(1, 5) if d != state["solution"][empty[0]][empty[1]]
                    and not game._valid_move(state["board"], *empty, d))
    with pytest.raises(ValueError, match="lp_conflict"):
        game.digit(state, user_id=1, value=conflict, now=104)
    state = game.digit(state, user_id=1, value=state["solution"][empty[0]][empty[1]], now=105)
    state = game.undo(state, user_id=1, now=106)
    assert state["board"][empty[0]][empty[1]] == 0
    state = game.hint(state, user_id=1, now=107)
    assert state["hints_left"] == 2 and state["hint_count"] == 1
    assert state["solution"] != game.public_state(state).get("solution")
    cancelled = game.cancel(state, user_id=1, now=108)
    assert cancelled["phase"] == "cancelled" and not cancelled["score_pending"]


def test_number_bounds_round_rotation_and_rejected_guess_preserve_clock():
    state = game.new(game_key="sayisal_tahmin", chat_id=-100, thread_id=None,
                     owner_id=1, owner_name="Bir", round_id="r", now=100)
    state = game.join(state, user_id=2, user_name="İki", now=101)
    state = game.start(state, user_id=1, now=102, secret=700)
    assert state["phase"] == "turn_pending" and state["deadline"] is None
    state.update(phase="turn", deadline=150, panel_ready=True)
    with pytest.raises(ValueError, match="lp_guess_out_of_range"):
        game.number_guess(state, user_id=1, value=1001, now=110)
    assert state["deadline"] == 150
    state = game.number_guess(state, user_id=1, value=500, now=111)
    assert state["low"] == 501 and state["deadline"] is None
    state.update(phase="turn", deadline=170)
    state = game.number_guess(state, user_id=2, value=700, now=112)
    assert state["phase"] == "round_result" and state["scores"]["2"] == 1
    next_state = game._copy(state, 113)
    game._next_number_round(next_state, 113, secret=345)
    assert next_state["round_no"] == 2 and next_state["turn_index"] == 1
    assert "secret" not in game.public_state(next_state)


def test_reflex_false_start_penalty_and_one_press_each_round():
    state = game.new(game_key="hizli_buton", chat_id=-100, thread_id=None,
                     owner_id=1, owner_name="Bir", round_id="r", now=100)
    state = game.join(state, user_id=2, user_name="İki", now=101)
    state = game.start(state, user_id=1, now=102, delay=3, symbol="◆")
    assert state["go_at"] is None and state["deadline"] is None
    assert "target" not in game.public_state(state)
    state = game.reflex_press(state, user_id=1, symbol=None, now=103)
    assert state["scores"]["1"] == -1
    state.update(phase="go", deadline=120)
    with pytest.raises(ValueError, match="lp_already_pressed"):
        game.reflex_press(state, user_id=1, symbol="◆", now=105)
    state = game.reflex_press(state, user_id=2, symbol="◆", now=106)
    assert state["phase"] == "round_result" and state["scores"]["2"] == 2
    with pytest.raises(ValueError, match="lp_wrong_phase"):
        game.reflex_press(state, user_id=2, symbol="◆", now=107)


def test_nine_by_nine_keyboard_is_bounded_and_row_focused():
    state = game.new(game_key="sudoku", chat_id=-100, thread_id=None,
                     owner_id=1, owner_name="Bir", round_id="r", now=100,
                     size=9, seed=1)
    buttons = keyboard(state).inline_keyboard
    assert sum(len(row) for row in buttons) <= 30
    assert max(map(len, buttons)) <= 3
    state = game.focus_row(state, user_id=1, row=8, now=101)
    assert state["focus_row"] == 8
    assert any(button.callback_data.endswith(":c88") for row in keyboard(state).inline_keyboard
               for button in row)


@pytest.mark.parametrize("secret", [1, 2, 500, 999, 1000])
def test_solo_number_round_solved_by_ten_binary_search_guesses(secret):
    state = game.new(game_key="sayisal_tahmin", chat_id=-100, thread_id=None,
                     owner_id=1, owner_name="Bir", round_id="r", now=100)
    state = game.start(state, user_id=1, now=101, secret=secret)
    assert state["guess_budget"] == 10
    for attempt in range(10):
        state.update(phase="turn", deadline=200 + attempt)
        guess = (state["low"] + state["high"]) // 2
        state = game.number_guess(state, user_id=1, value=guess, now=102 + attempt)
        if state["phase"] == "round_result":
            break
    assert state["scores"]["1"] == 1
    assert state["guesses_counts"]["1"] <= 10


def test_number_wrong_turn_rotates_and_timeout_uses_only_one_budget():
    state = game.new(game_key="sayisal_tahmin", chat_id=-100, thread_id=None,
                     owner_id=1, owner_name="Bir", round_id="r", now=100)
    state = game.join(state, user_id=2, user_name="İki", now=101)
    state = game.start(state, user_id=1, now=102, secret=900)
    state.update(phase="turn", deadline=130)
    state = game.number_guess(state, user_id=1, value=500, now=103)
    assert state["turn_index"] == 1 and state["guesses_counts"] == {"1": 1, "2": 0}
    state.update(phase="turn", deadline=135)
    state = game.advance(state, now=136)
    assert state["turn_index"] == 0 and state["guesses_counts"] == {"1": 1, "2": 1}
    next_round = game._copy(state, 137)
    game._next_number_round(next_round, 137, secret=400)
    assert next_round["round_no"] == 2 and next_round["turn_index"] == 1


def test_sudoku_digit_rejects_previous_cell_binding():
    state = game.new(game_key="sudoku", chat_id=-100, thread_id=None,
                     owner_id=1, owner_name="Bir", round_id="r", now=100)
    state["panel_ready"] = True
    cells = [(r, c) for r in range(4) for c in range(4) if not state["givens"][r][c]]
    previous, current = cells[:2]
    state = game.select(state, user_id=1, row=previous[0], col=previous[1], now=101)
    state = game.select(state, user_id=1, row=current[0], col=current[1], now=102)
    with pytest.raises(ValueError, match="lp_stale_cell"):
        game.digit(state, user_id=1, value=state["solution"][previous[0]][previous[1]],
                   expected_cell=previous, now=103)
    assert state["board"][current[0]][current[1]] == 0


def test_sudoku_can_clear_old_edit_after_hint_clears_undo_history():
    state = game.new(game_key="sudoku", chat_id=-100, thread_id=None,
                     owner_id=1, owner_name="Bir", round_id="r", now=100)
    state["panel_ready"] = True
    cells = [(r, c) for r in range(4) for c in range(4) if not state["givens"][r][c]]
    first, second = cells[:2]
    state = game.select(state, user_id=1, row=first[0], col=first[1], now=101)
    state = game.digit(state, user_id=1, value=state["solution"][first[0]][first[1]],
                       expected_cell=first, now=102)
    state = game.hint(state, user_id=1, now=103)
    assert not state["undo"]
    state = game.select(state, user_id=1, row=first[0], col=first[1], now=104)
    state = game.digit(state, user_id=1, value=0, expected_cell=first, now=105)
    assert state["board"][first[0]][first[1]] == 0
    fixed = next((r, c) for r in range(4) for c in range(4) if state["givens"][r][c])
    state = game.select(state, user_id=1, row=fixed[0], col=fixed[1], now=106)
    with pytest.raises(ValueError, match="lp_fixed_cell"):
        game.digit(state, user_id=1, value=0, expected_cell=fixed, now=107)
