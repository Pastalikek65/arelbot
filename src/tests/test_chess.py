from __future__ import annotations

import chess
import pytest

from src.games.chess import (
    START_FEN,
    chess_board,
    chess_click,
    chess_join,
    chess_new_round,
    chess_offer_draw,
    chess_promote,
    chess_respond_draw,
    chess_resign,
    chess_start,
    new_chess_state,
)


def started_state():
    state = new_chess_state(owner_id=10, owner_name="A", chat_id=-100)
    assert chess_join(state, user_id=20, user_name="B")["status"] == "joined"
    assert chess_start(state, user_id=10)["status"] == "started"
    return state


def move(state, user_id, source, target):
    assert chess_click(state, user_id=user_id, square=source)["status"] == "selected"
    return chess_click(state, user_id=user_id, square=target)


def test_chess_lobby_and_colors_are_bounded():
    state = new_chess_state(owner_id=10, owner_name="A", chat_id=-100)
    assert chess_join(state, user_id=20, user_name="B")["changed"]
    assert chess_join(state, user_id=30, user_name="C")["status"] == "full"
    assert chess_start(state, user_id=10)["status"] == "started"
    assert {item["color"] for item in state["players"]} == {"white", "black"}
    assert state["fen"] == START_FEN


def test_chess_turn_and_illegal_move_validation():
    state = started_state()
    assert chess_click(state, user_id=20, square="e7")["status"] == "not_your_turn"
    assert chess_click(state, user_id=10, square="e4")["status"] == "empty_square"
    assert chess_click(state, user_id=10, square="e2")["status"] == "selected"
    assert chess_click(state, user_id=10, square="e5")["status"] == "invalid_destination"
    assert state["moves"] == []


def test_chess_turn_deadline_expires_and_assigns_timeout_winner():
    state = started_state()
    state["turn_expires_at"] = 0

    result = chess_click(state, user_id=10, square="e2")

    assert result["status"] == "expired"
    assert state["status"] == "expired"
    assert state["termination"] == "timeout"
    assert state["winner_id"] == 20
    assert state["turn_expires_at"] is None


def test_chess_move_refreshes_deadline(monkeypatch):
    state = started_state()
    monkeypatch.setattr("src.games.chess.time.time", lambda: 1000.0)

    result = move(state, 10, "e2", "e4")

    assert result["status"] == "playing"
    assert state["turn_expires_at"] == 1000.0 + state["turn_timeout_seconds"]


def test_chess_san_history_and_castling():
    state = started_state()
    assert move(state, 10, "e2", "e4")["san"] == "e4"
    move(state, 20, "e7", "e5")
    move(state, 10, "g1", "f3")
    move(state, 20, "b8", "c6")
    move(state, 10, "f1", "c4")
    move(state, 20, "g8", "f6")
    result = move(state, 10, "e1", "g1")
    assert result["status"] == "playing"
    assert state["moves"][-1] == "O-O"
    assert chess_board(state).king(chess.WHITE) == chess.G1
    assert len(state["uci_moves"]) == len(state["moves"]) == 7


def test_chess_en_passant_is_legal():
    state = started_state()
    move(state, 10, "e2", "e4")
    move(state, 20, "a7", "a6")
    move(state, 10, "e4", "e5")
    move(state, 20, "d7", "d5")
    result = move(state, 10, "e5", "d6")
    assert result["status"] == "playing"
    board = chess_board(state)
    assert board.piece_at(chess.D6).symbol() == "P"
    assert board.piece_at(chess.D5) is None


def test_chess_checkmate_and_winner():
    state = started_state()
    move(state, 10, "f2", "f3")
    move(state, 20, "e7", "e5")
    move(state, 10, "g2", "g4")
    result = move(state, 20, "d8", "h4")
    assert result["status"] == "finished"
    assert state["termination"] == "checkmate"
    assert state["winner_id"] == 20
    assert state["result"] == "0-1"


def test_chess_draw_offer_and_resignation():
    state = started_state()
    assert chess_offer_draw(state, user_id=10)["status"] == "draw_offered"
    assert chess_offer_draw(state, user_id=20)["status"] == "draw_already_offered"
    assert chess_respond_draw(state, user_id=20, accept=False)["status"] == "draw_declined"
    assert chess_offer_draw(state, user_id=10)["status"] == "draw_offered"
    assert chess_respond_draw(state, user_id=20, accept=True)["status"] == "draw_agreed"
    assert state["result"] == "1/2-1/2"

    state = started_state()
    assert chess_resign(state, user_id=10)["status"] == "resigned"
    assert state["winner_id"] == 20


def test_chess_restart_keeps_players_but_discards_old_round():
    state = started_state()
    old_round = state["round_id"]
    move(state, 10, "e2", "e4")
    result = chess_new_round(state, user_id=10)
    assert result["status"] == "restarted"
    assert state["round_id"] != old_round
    assert state["fen"] == START_FEN
    assert state["moves"] == []
    assert len(state["players"]) == 2


def test_chess_promotion_transition():
    # Build a legal replay where the a-file is cleared by a black capture and
    # the black rook moves away from a8 before white promotes.
    state = started_state()
    sequence = [
        (10, "a2", "a4"), (20, "h7", "h6"),
        (10, "a4", "a5"), (20, "h6", "h5"),
        (10, "b2", "b4"), (20, "h5", "h4"),
        (10, "b4", "b5"), (20, "h4", "h3"),
        (10, "b5", "b6"), (20, "a7", "b6"),
        (10, "a5", "a6"), (20, "b8", "c6"),
        (10, "a6", "a7"), (20, "a8", "b8"),
    ]
    for user_id, source, target in sequence:
        assert move(state, user_id, source, target)["status"] == "playing"
    assert chess_click(state, user_id=10, square="a7")["status"] == "selected"
    result = chess_click(state, user_id=10, square="a8")
    assert result["status"] == "promotion_required"
    assert chess_promote(state, user_id=10, piece="q")["status"] == "playing"
    assert chess_board(state).piece_at(chess.A8).symbol() == "Q"


def test_chess_board_rebuild_rejects_tampered_move_log():
    state = started_state()
    state["uci_moves"] = ["e2e5"]
    with pytest.raises(ValueError, match="invalid_chess_move_log"):
        chess_board(state)
