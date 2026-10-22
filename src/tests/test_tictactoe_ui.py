from src.services.tictactoe_ui import (
    new_tictactoe_state,
    render_tictactoe_text,
    tictactoe_join,
    tictactoe_keyboard,
    tictactoe_move,
    tictactoe_new_round,
    tictactoe_select_size,
    tictactoe_start,
)
import time
import pytest


def started_state():
    state = new_tictactoe_state(owner_id=10, owner_name="A", chat_id=-100)
    assert tictactoe_join(state, user_id=20, user_name="B")["changed"]
    assert tictactoe_start(state)["status"] == "started"
    return state


def test_xox_lobby_is_bounded_and_round_scoped():
    state = new_tictactoe_state(owner_id=10, owner_name="A", chat_id=-100)
    assert state["round_id"]
    assert tictactoe_join(state, user_id=20, user_name="B")["status"] == "joined"
    assert tictactoe_join(state, user_id=30, user_name="C")["status"] == "full"
    assert tictactoe_start(state)["status"] == "started"


def test_xox_turns_and_win_are_deterministic():
    state = started_state()
    assert tictactoe_move(state, user_id=20, cell=0)["status"] == "not_your_turn"
    assert tictactoe_move(state, user_id=10, cell=0)["status"] == "moved"
    assert tictactoe_move(state, user_id=20, cell=3)["status"] == "moved"
    assert tictactoe_move(state, user_id=10, cell=1)["status"] == "moved"
    assert tictactoe_move(state, user_id=20, cell=4)["status"] == "moved"
    assert tictactoe_move(state, user_id=10, cell=2)["status"] == "won"
    assert state["winner_id"] == 10


def test_xox_rejects_occupied_and_out_of_range_cells():
    state = started_state()
    assert tictactoe_move(state, user_id=10, cell=0)["status"] == "moved"
    assert tictactoe_move(state, user_id=20, cell=0)["status"] == "occupied"
    assert tictactoe_move(state, user_id=20, cell=99)["status"] == "invalid_cell"


def test_xox_rejects_malformed_persisted_cells_without_raising():
    state = started_state()
    state["board"][0] = "not-a-user"
    result = tictactoe_move(state, user_id=10, cell=1)
    assert result["status"] == "invalid_state"


def test_xox_render_escapes_names_without_rendering_cell_numbers():
    state = new_tictactoe_state(owner_id=10, owner_name="<admin>", chat_id=-100)
    text = render_tictactoe_text(state)
    assert "&lt;admin&gt;" in text
    assert "1 | 2 | 3" not in text


def test_xox_keyboard_uses_blank_cells_then_marks_the_selected_cell():
    state = started_state()
    markup = tictactoe_keyboard(state)
    board_buttons = [button for row in markup.inline_keyboard[:-1] for button in row]
    assert all(button.text == "\u00a0" for button in board_buttons)

    assert tictactoe_move(state, user_id=10, cell=0)["status"] == "moved"
    markup = tictactoe_keyboard(state)
    board_buttons = [button for row in markup.inline_keyboard[:-1] for button in row]
    assert board_buttons[0].text == "X"
    assert all(button.text == "\u00a0" for button in board_buttons[1:])

    assert tictactoe_move(state, user_id=20, cell=1)["status"] == "moved"
    markup = tictactoe_keyboard(state)
    board_buttons = [button for row in markup.inline_keyboard[:-1] for button in row]
    assert board_buttons[1].text == "O"


def test_xox_new_round_clears_previous_marks_and_rotates_round_id():
    state = started_state()
    assert tictactoe_move(state, user_id=10, cell=0)["status"] == "moved"
    old_round_id = state["round_id"]
    result = tictactoe_new_round(state, user_id=10)
    assert result["status"] == "restarted"
    assert result["changed"] is True
    assert state["round_id"] != old_round_id
    assert state["status"] == "playing"
    assert state["moves"] == 0
    assert all(cell == 0 for cell in state["board"])
    assert state["current_player_id"] == 10


def test_xox_start_repairs_marks_accidentally_left_in_a_lobby_snapshot():
    state = new_tictactoe_state(owner_id=10, owner_name="A", chat_id=-100)
    tictactoe_join(state, user_id=20, user_name="B")
    state["board"][0] = 10
    state["moves"] = 1
    assert tictactoe_start(state)["status"] == "started"
    assert state["moves"] == 0
    assert all(cell == 0 for cell in state["board"])


@pytest.mark.parametrize("board_size", [6, 7, 8])
def test_xox_supports_valid_large_boards_and_four_in_a_row(board_size):
    state = new_tictactoe_state(owner_id=10, owner_name="A", chat_id=-100, board_size=board_size)
    assert state["board_size"] == board_size
    assert state["win_length"] == 4
    assert len(state["board"]) == board_size * board_size
    assert tictactoe_join(state, user_id=20, user_name="B")["changed"]
    assert tictactoe_start(state)["status"] == "started"

    # X takes four horizontal cells; O plays harmless cells between turns.
    moves = [0, board_size, 1, board_size + 1, 2, board_size + 2, 3]
    for index, cell in enumerate(moves[:-1]):
        result = tictactoe_move(state, user_id=10 if index % 2 == 0 else 20, cell=cell)
    markup = tictactoe_keyboard(state)
    assert len(markup.inline_keyboard) == board_size + 1
    assert all(len(row) == board_size for row in markup.inline_keyboard[:-1])
    assert len(markup.inline_keyboard[-1]) == 2
    result = tictactoe_move(state, user_id=10, cell=moves[-1])
    assert result["status"] == "won"
    assert state["winner_id"] == 10


def _play_sequence(state, cells):
    result = None
    for index, cell in enumerate(cells):
        result = tictactoe_move(state, user_id=10 if index % 2 == 0 else 20, cell=cell)
    return result


@pytest.mark.parametrize(
    "cells",
    [
        [0, 1, 6, 2, 12, 3, 18],
        [3, 0, 8, 1, 13, 2, 18],
        [0, 1, 7, 2, 14, 3, 21],
    ],
)
def test_xox_detects_vertical_and_both_diagonal_four_lines(cells):
    state = new_tictactoe_state(owner_id=10, owner_name="A", chat_id=-100, board_size=6)
    tictactoe_join(state, user_id=20, user_name="B")
    tictactoe_start(state)
    result = _play_sequence(state, cells)
    assert result["status"] == "won"


def test_xox_rejects_invalid_board_sizes_and_malformed_dimensions():
    with pytest.raises(ValueError, match="invalid_board_size"):
        new_tictactoe_state(owner_id=10, owner_name="A", chat_id=-100, board_size=5)

    state = new_tictactoe_state(owner_id=10, owner_name="A", chat_id=-100, board_size=6)
    tictactoe_join(state, user_id=20, user_name="B")
    tictactoe_start(state)
    state["board"] = [0] * 9
    assert tictactoe_move(state, user_id=10, cell=0)["status"] == "invalid_state"


def test_xox_legacy_three_by_three_state_remains_compatible():
    state = started_state()
    state.pop("board_size", None)
    state.pop("win_length", None)
    state["schema_version"] = 1
    assert "board_size" not in state
    assert tictactoe_move(state, user_id=10, cell=0)["status"] == "moved"
    assert tictactoe_move(state, user_id=20, cell=3)["status"] == "moved"
    assert tictactoe_move(state, user_id=10, cell=1)["status"] == "moved"
    assert tictactoe_move(state, user_id=20, cell=4)["status"] == "moved"
    assert tictactoe_move(state, user_id=10, cell=2)["status"] == "won"


def test_xox_timeout_closes_round_without_accepting_late_move():
    state = started_state()
    state["turn_expires_at"] = time.time() - 1
    assert tictactoe_move(state, user_id=10, cell=0)["status"] == "expired"
    assert state["status"] == "expired"
    assert tictactoe_move(state, user_id=10, cell=1)["status"] == "not_playing"


def test_xox_full_board_without_a_four_line_is_a_draw():
    state = new_tictactoe_state(owner_id=10, owner_name="A", chat_id=-100, board_size=6)
    tictactoe_join(state, user_id=20, user_name="B")
    tictactoe_start(state)
    encoded = "XOXXOOOOOXOXXXOOOXXOXXXOOOXXXOXXOXOO"
    state["board"] = [10 if value == "X" else 20 for value in encoded[:-1]] + [0]
    state["moves"] = 35
    state["current_player_id"] = 10
    result = tictactoe_move(state, user_id=10, cell=35)
    assert result["status"] == "draw"
    assert state["status"] == "draw"


def test_xox_size_selection_is_owner_only_lobby_only_and_resets_round_board():
    state = new_tictactoe_state(owner_id=10, owner_name="A", chat_id=-100, prompt_message_id=44)
    tictactoe_join(state, user_id=20, user_name="B")
    state["board"][0] = 10
    state["moves"] = 1
    round_id = state["round_id"]

    assert tictactoe_select_size(state, user_id=20, board_size=6)["status"] == "not_owner"
    result = tictactoe_select_size(state, user_id=10, board_size=6)
    assert result["status"] == "selected" and result["changed"] is True
    assert state["board_size"] == 6
    assert state["win_length"] == 4
    assert state["board"] == [0] * 36
    assert state["moves"] == 0 and state["winner_id"] is None
    assert state["current_player_id"] is None and state["turn_expires_at"] is None
    assert state["round_id"] == round_id
    assert state["prompt_message_id"] == 44

    tictactoe_start(state)
    assert tictactoe_select_size(state, user_id=10, board_size=7)["status"] == "not_lobby"


def test_xox_size_selection_validates_sizes_and_legacy_schema_round_identity():
    state = new_tictactoe_state(owner_id=10, owner_name="A", chat_id=-100)
    state["schema_version"] = 1
    round_id = state["round_id"]
    for board_size in (3, 5, 9, "bad"):
        result = tictactoe_select_size(state, user_id=10, board_size=board_size)
        assert result["status"] == "invalid_board_size"
        assert state["round_id"] == round_id

    state["round_id"] = ""
    assert tictactoe_select_size(state, user_id=10, board_size=6)["status"] == "invalid_state"

