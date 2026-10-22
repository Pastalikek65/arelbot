"""Contract tests for the deterministic Gomoku/Connect-N core."""

from __future__ import annotations

import json

import pytest

from src.games.gomoku import (
    DEFAULT_BOARD_SIZE,
    DEFAULT_WIN_LENGTH,
    GomokuConfig,
    GomokuMove,
    GomokuStatus,
    apply_move,
    create_game,
    replay_moves,
)


def _game(*, size: int = DEFAULT_BOARD_SIZE):
    return create_game(chat_id=-100, players=(101, 202), board_size=size)


def test_config_accepts_supported_sizes_and_five_in_row_by_default():
    assert DEFAULT_BOARD_SIZE == 10
    assert DEFAULT_WIN_LENGTH == 5
    assert GomokuConfig(board_size=6).board_size == 6
    assert GomokuConfig(board_size=15).win_length == 5


@pytest.mark.parametrize("size", [0, 5, 16, -1, True])
def test_config_rejects_out_of_range_board_sizes(size):
    with pytest.raises(ValueError, match="invalid_gomoku_board_size"):
        GomokuConfig(board_size=size)


@pytest.mark.parametrize("win_length", [0, 1, 2, 16])
def test_config_rejects_impossible_connect_lengths(win_length):
    with pytest.raises(ValueError, match="invalid_gomoku_win_length"):
        GomokuConfig(board_size=6, win_length=win_length)


def test_game_rejects_invalid_scope_and_duplicate_players():
    with pytest.raises(ValueError, match="invalid_gomoku_scope"):
        create_game(chat_id=0, players=(101, 202))
    with pytest.raises(ValueError, match="invalid_gomoku_players"):
        create_game(chat_id=-100, players=(101, 101))
    with pytest.raises(ValueError, match="invalid_gomoku_players"):
        create_game(chat_id=-100, players=(101,))


def test_turn_ownership_and_occupied_move_are_rejected_without_mutation():
    state = _game(size=6)
    wrong_turn = apply_move(state, player_id=202, row=0, column=0)
    assert wrong_turn.status is GomokuStatus.NOT_YOUR_TURN
    assert wrong_turn.state == state

    moved = apply_move(state, player_id=101, row=0, column=0)
    assert moved.status is GomokuStatus.MOVED
    occupied = apply_move(moved.state, player_id=202, row=0, column=0)
    assert occupied.status is GomokuStatus.OCCUPIED
    assert occupied.state == moved.state


@pytest.mark.parametrize(
    "moves",
    [
        # horizontal
        [(0, 0), (1, 0), (0, 1), (1, 1), (0, 2), (1, 2), (0, 3), (1, 3), (0, 4)],
        # vertical
        [(0, 0), (0, 1), (1, 0), (1, 1), (2, 0), (2, 1), (3, 0), (3, 1), (4, 0)],
        # top-left to bottom-right
        [(0, 0), (0, 1), (1, 1), (0, 2), (2, 2), (0, 3), (3, 3), (0, 4), (4, 4)],
        # bottom-left to top-right
        [(5, 0), (0, 0), (4, 1), (0, 1), (3, 2), (0, 2), (2, 3), (0, 3), (1, 4)],
    ],
)
def test_five_in_row_is_detected_in_all_directions(moves):
    state = _game(size=6)
    result = None
    for turn, (row, column) in enumerate(moves):
        result = apply_move(state, player_id=101 if turn % 2 == 0 else 202, row=row, column=column)
        assert result.ok
        state = result.state
    assert result is not None
    assert result.status is GomokuStatus.WON
    assert result.state.winner_id == 101
    assert len(result.winning_line) >= 5


def test_extra_line_stays_a_win_and_game_is_immutable_after_completion():
    state = _game(size=6)
    winning = [(0, 0), (1, 0), (0, 1), (1, 1), (0, 2), (1, 2), (0, 3), (1, 3), (0, 4)]
    for turn, (row, column) in enumerate(winning):
        state = apply_move(state, player_id=101 if turn % 2 == 0 else 202, row=row, column=column).state
    result = apply_move(state, player_id=202, row=2, column=2)
    assert result.status is GomokuStatus.FINISHED
    assert result.state == state


def test_duplicate_action_is_idempotent_and_does_not_change_turn():
    state = _game(size=6)
    first = apply_move(state, player_id=101, row=0, column=0, action_id="telegram:1")
    duplicate = apply_move(first.state, player_id=101, row=0, column=1, action_id="telegram:1")
    assert first.status is GomokuStatus.MOVED
    assert duplicate.status is GomokuStatus.DUPLICATE_ACTION
    assert duplicate.state == first.state


def test_invalid_coordinates_and_malformed_state_are_safe():
    state = _game(size=6)
    for row, column in [(-1, 0), (6, 0), (0, -1), (0, 6)]:
        result = apply_move(state, player_id=101, row=row, column=column)
        assert result.status is GomokuStatus.INVALID_COORDINATE
    assert apply_move(state, player_id=101, row=True, column=0).status is GomokuStatus.INVALID_COORDINATE


def test_draw_is_reported_when_the_board_is_full_without_winner():
    # This deterministic order fills a 6x6 board without creating a five-line
    # for either marker (a checkerboard would accidentally create diagonals).
    state = _game(size=6)
    player_ids = (101, 202)
    cells = [5, 32, 16, 20, 11, 1, 26, 29, 28, 13, 9, 30, 10, 18, 34, 25, 31, 12, 0, 21, 23, 14, 35, 15, 33, 2, 4, 7, 8, 3, 6, 22, 17, 24, 19, 27]
    result = None
    for turn, cell in enumerate(cells):
        row, column = divmod(cell, 6)
        result = apply_move(state, player_id=player_ids[turn % 2], row=row, column=column)
        assert result.ok
        state = result.state
    assert result is not None
    assert result.status is GomokuStatus.DRAW
    assert result.state.status is GomokuStatus.DRAW
    assert result.state.winner_id is None


def test_serialization_is_canonical_and_replay_is_deterministic():
    state = _game(size=10)
    sequence = [(0, 0), (1, 0), (0, 1), (1, 1), (0, 2)]
    for index, (row, column) in enumerate(sequence):
        state = apply_move(
            state,
            player_id=101 if index % 2 == 0 else 202,
            row=row,
            column=column,
            action_id=f"move-{index + 1}",
        ).state
    payload = state.to_dict()
    assert json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) == state.to_json()
    restored = type(state).from_dict(json.loads(state.to_json()))
    assert restored == state
    replayed = replay_moves(
        chat_id=-100,
        players=(101, 202),
        board_size=10,
        game_id=state.game_id,
        moves=state.moves,
    )
    assert replayed == state


def test_replay_rejects_tampered_order_or_unknown_player():
    state = _game(size=6)
    first = apply_move(state, player_id=101, row=0, column=0, action_id="a").state
    moves = list(first.moves)
    with pytest.raises(ValueError, match="invalid_gomoku_replay"):
        replay_moves(chat_id=-100, players=(101, 202), board_size=6, moves=[GomokuMove(1, 202, 0, 1, "b")])


def test_available_moves_are_sorted_and_coordinate_helpers_round_trip():
    state = _game(size=6)
    assert state.available_moves()[0] == (0, 0)
    moved = apply_move(state, player_id=101, row=2, column=4).state
    assert (2, 4) not in moved.available_moves()
    assert moved.index_for(2, 4) == 16
    assert moved.coordinates_for(16) == (2, 4)

