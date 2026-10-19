"""Arelbot chess domain engine.

The upstream Piterden/chessbot interaction model is adapted as a native,
transport-neutral game: a persistent two-player lobby, callback-driven board,
rotating orientation, legal move validation and resumable PGN/SAN history.
Telegram handlers only call these pure state transitions.
"""

from __future__ import annotations

from copy import deepcopy
import time
import uuid
from typing import Any

import chess


SCHEMA_VERSION = 1
START_FEN = chess.Board().fen()
_VALID_COLORS = {"white", "black"}
_PROMOTION_PIECES = {"q": chess.QUEEN, "r": chess.ROOK, "b": chess.BISHOP, "n": chess.KNIGHT}
_PROMOTION_LABELS = {"q": "Vezir", "r": "Kale", "b": "Fil", "n": "At"}
TURN_TIMEOUT_SECONDS = 30 * 60


def _round_id() -> str:
    return uuid.uuid4().hex[:12]


def _timeout_seconds(state: dict[str, Any]) -> int:
    try:
        return max(1, min(24 * 60 * 60, int(state.get("turn_timeout_seconds", TURN_TIMEOUT_SECONDS))))
    except (TypeError, ValueError, OverflowError):
        return TURN_TIMEOUT_SECONDS


def _player(state: dict[str, Any], user_id: int) -> dict[str, Any] | None:
    for item in state.get("players", []):
        if isinstance(item, dict) and int(item.get("id", 0) or 0) == int(user_id):
            return item
    return None


def _players(state: dict[str, Any]) -> list[dict[str, Any]]:
    raw = state.get("players")
    if not isinstance(raw, list):
        state["players"] = []
        return state["players"]
    return raw


def _board(state: dict[str, Any]) -> chess.Board:
    """Rebuild a board from the initial position and the durable UCI log."""

    board = chess.Board()
    moves = state.get("uci_moves") or []
    if not isinstance(moves, list):
        raise ValueError("invalid_chess_move_log")
    try:
        for raw in moves:
            board.push_uci(str(raw))
    except (ValueError, chess.IllegalMoveError, chess.InvalidMoveError) as exc:
        raise ValueError("invalid_chess_move_log") from exc
    return board


def _safe_board(state: dict[str, Any]) -> chess.Board:
    board = _board(state)
    stored_fen = str(state.get("fen") or "").strip()
    if stored_fen and stored_fen != board.fen():
        # The move log is authoritative; this repairs old or partially written
        # snapshots without accepting a forged board position.
        state["fen"] = board.fen()
    return board


def _color_name(value: bool) -> str:
    return "white" if value else "black"


def _color_label(value: str | None) -> str:
    return {"white": "Beyaz", "black": "Siyah"}.get(str(value), "Renk seçilmedi")


def _finish_if_terminal(state: dict[str, Any], board: chess.Board) -> None:
    outcome = board.outcome(claim_draw=True)
    if outcome is None:
        return
    state["status"] = "finished"
    state["result"] = outcome.result()
    state["termination"] = str(outcome.termination.name).lower()
    state["winner_id"] = None
    if outcome.winner is not None:
        winner_color = _color_name(bool(outcome.winner))
        for item in _players(state):
            if item.get("color") == winner_color:
                state["winner_id"] = int(item.get("id", 0) or 0)
                break


def _expire_if_needed(state: dict[str, Any]) -> bool:
    """Close a playing round whose active player's deadline has elapsed."""

    if str(state.get("status")) != "playing":
        return False
    raw_deadline = state.get("turn_expires_at")
    if raw_deadline is None:
        # Legacy snapshots did not carry a deadline. Start one on their first
        # post-deploy interaction instead of expiring an otherwise valid game.
        state["turn_timeout_seconds"] = _timeout_seconds(state)
        state["turn_expires_at"] = time.time() + state["turn_timeout_seconds"]
        return False
    try:
        expired = time.time() >= float(raw_deadline)
    except (TypeError, ValueError, OverflowError):
        expired = True
    if not expired:
        return False
    current_user = int(state.get("turn_user_id", 0) or 0)
    current_color = str(state.get("turn") or "white")
    winner_color = "black" if current_color == "white" else "white"
    winner_id = next(
        (
            int(item.get("id", 0) or 0)
            for item in _players(state)
            if item.get("color") == winner_color
        ),
        None,
    )
    state.update(
        {
            "status": "expired",
            "result": "0-1" if current_color == "white" else "1-0",
            "termination": "timeout",
            "winner_id": winner_id if current_user else None,
            "turn_expires_at": None,
            "selected_square": None,
            "legal_targets": [],
            "pending_promotion": None,
            "draw_offer_by": None,
        }
    )
    return True


def _push_move(state: dict[str, Any], board: chess.Board, move: chess.Move) -> dict[str, Any]:
    san = board.san(move)
    uci = move.uci()
    board.push(move)
    state.setdefault("uci_moves", []).append(uci)
    state.setdefault("moves", []).append(san)
    state["fen"] = board.fen()
    state["turn"] = _color_name(board.turn)
    state["last_move"] = san
    state["last_actor_id"] = int(state.get("turn_user_id", 0) or 0)
    state["turn_user_id"] = next(
        (int(item.get("id", 0) or 0) for item in _players(state) if item.get("color") == _color_name(board.turn)),
        None,
    )
    state["selected_square"] = None
    state["legal_targets"] = []
    state["pending_promotion"] = None
    state["draw_offer_by"] = None
    _finish_if_terminal(state, board)
    state["turn_expires_at"] = (
        time.time() + _timeout_seconds(state)
        if state.get("status") == "playing"
        else None
    )
    return {
        "changed": True,
        "status": state.get("status", "playing"),
        "san": san,
        "uci": uci,
        "result": state.get("result"),
    }


def new_chess_state(
    *, owner_id: int, owner_name: str, chat_id: int, thread_id: int | None = None
) -> dict[str, Any]:
    if int(owner_id) <= 0 or int(chat_id) == 0:
        raise ValueError("invalid_chess_scope")
    return {
        "schema_version": SCHEMA_VERSION,
        "game_key": "chess",
        "round_id": _round_id(),
        "chat_id": int(chat_id),
        "thread_id": int(thread_id) if thread_id is not None else None,
        "owner_id": int(owner_id),
        "status": "lobby",
        "turn_timeout_seconds": TURN_TIMEOUT_SECONDS,
        "turn_expires_at": None,
        "players": [{"id": int(owner_id), "name": str(owner_name or "Oyuncu")[:64], "color": None}],
        "fen": START_FEN,
        "turn": "white",
        "turn_user_id": None,
        "uci_moves": [],
        "moves": [],
        "selected_square": None,
        "legal_targets": [],
        "pending_promotion": None,
        "draw_offer_by": None,
        "result": None,
        "termination": None,
        "winner_id": None,
        "last_move": None,
        "last_actor_id": None,
        "prompt_message_id": 0,
    }


def chess_choose_color(state: dict[str, Any], *, user_id: int, color: str) -> dict[str, Any]:
    if str(state.get("status")) != "lobby":
        return {"changed": False, "status": "not_lobby"}
    color = str(color).strip().casefold()
    if color not in _VALID_COLORS:
        return {"changed": False, "status": "invalid_color"}
    player = _player(state, user_id)
    if player is None or int(user_id) != int(state.get("owner_id", 0) or 0):
        return {"changed": False, "status": "not_owner"}
    for other in _players(state):
        if other is not player and other.get("color") == color:
            return {"changed": False, "status": "color_taken"}
    player["color"] = color
    return {"changed": True, "status": "color_selected", "color": color}


def chess_join(state: dict[str, Any], *, user_id: int, user_name: str) -> dict[str, Any]:
    if str(state.get("status")) != "lobby":
        return {"changed": False, "status": "not_lobby"}
    if _player(state, user_id) is not None:
        return {"changed": False, "status": "already_joined"}
    players = _players(state)
    if len(players) >= 2:
        return {"changed": False, "status": "full"}
    owner_color = str(players[0].get("color") or "")
    color = "black" if owner_color == "white" else "white"
    if owner_color not in _VALID_COLORS:
        color = "black"
    players.append({"id": int(user_id), "name": str(user_name or "Oyuncu")[:64], "color": color})
    if players[0].get("color") not in _VALID_COLORS:
        players[0]["color"] = "white"
        players[1]["color"] = "black"
    return {"changed": True, "status": "joined", "color": players[-1]["color"]}


def chess_start(state: dict[str, Any], *, user_id: int) -> dict[str, Any]:
    if int(user_id) != int(state.get("owner_id", 0) or 0):
        return {"changed": False, "status": "not_owner"}
    if str(state.get("status")) != "lobby":
        return {"changed": False, "status": "not_lobby"}
    players = _players(state)
    if len(players) != 2:
        return {"changed": False, "status": "needs_player"}
    colors = [str(item.get("color") or "") for item in players]
    if set(colors) != _VALID_COLORS:
        players[0]["color"], players[1]["color"] = "white", "black"
    state.update(
        {
            "status": "playing",
            "fen": START_FEN,
            "turn": "white",
            "turn_user_id": next(int(item["id"]) for item in players if item.get("color") == "white"),
            "uci_moves": [],
            "moves": [],
            "selected_square": None,
            "legal_targets": [],
            "pending_promotion": None,
            "result": None,
            "termination": None,
            "winner_id": None,
            "draw_offer_by": None,
            "turn_timeout_seconds": _timeout_seconds(state),
            "turn_expires_at": time.time() + _timeout_seconds(state),
        }
    )
    return {"changed": True, "status": "started"}


def chess_click(state: dict[str, Any], *, user_id: int, square: str) -> dict[str, Any]:
    if str(state.get("status")) != "playing":
        return {"changed": False, "status": "not_playing"}
    if _expire_if_needed(state):
        return {"changed": True, "status": "expired", "winner_id": state.get("winner_id")}
    player = _player(state, user_id)
    if player is None:
        return {"changed": False, "status": "not_player"}
    try:
        target = chess.parse_square(str(square).strip().casefold())
    except ValueError:
        return {"changed": False, "status": "invalid_square"}
    board = _safe_board(state)
    if player.get("color") != _color_name(board.turn):
        return {"changed": False, "status": "not_your_turn"}
    pending = state.get("pending_promotion")
    if pending:
        return {"changed": False, "status": "promotion_required"}
    selected = state.get("selected_square")
    if selected is None:
        piece = board.piece_at(target)
        if piece is None:
            return {"changed": False, "status": "empty_square"}
        if piece.color != board.turn:
            return {"changed": False, "status": "not_your_piece"}
        targets = [chess.square_name(move.to_square) for move in board.legal_moves if move.from_square == target]
        if not targets:
            return {"changed": False, "status": "no_legal_move"}
        state["selected_square"] = chess.square_name(target)
        state["legal_targets"] = sorted(set(targets))
        return {"changed": True, "status": "selected", "square": chess.square_name(target), "targets": state["legal_targets"]}

    try:
        source = chess.parse_square(str(selected))
    except ValueError:
        state["selected_square"] = None
        state["legal_targets"] = []
        return {"changed": False, "status": "invalid_state"}
    if target == source:
        state["selected_square"] = None
        state["legal_targets"] = []
        return {"changed": True, "status": "deselected"}
    candidate = [move for move in board.legal_moves if move.from_square == source and move.to_square == target]
    if not candidate:
        piece = board.piece_at(target)
        if piece is not None and piece.color == board.turn:
            state["selected_square"] = None
            state["legal_targets"] = []
            return chess_click(state, user_id=user_id, square=chess.square_name(target))
        return {"changed": False, "status": "invalid_destination"}
    promotions = [move for move in candidate if move.promotion]
    if len(promotions) > 1:
        state["pending_promotion"] = {"from": chess.square_name(source), "to": chess.square_name(target)}
        return {
            "changed": True,
            "status": "promotion_required",
            "from": chess.square_name(source),
            "to": chess.square_name(target),
            "options": tuple(_PROMOTION_PIECES),
        }
    return _push_move(state, board, candidate[0])


def chess_promote(state: dict[str, Any], *, user_id: int, piece: str) -> dict[str, Any]:
    if str(state.get("status")) != "playing":
        return {"changed": False, "status": "not_playing"}
    if _expire_if_needed(state):
        return {"changed": True, "status": "expired", "winner_id": state.get("winner_id")}
    player = _player(state, user_id)
    if player is None:
        return {"changed": False, "status": "not_player"}
    board = _safe_board(state)
    if player.get("color") != _color_name(board.turn):
        return {"changed": False, "status": "not_your_turn"}
    pending = state.get("pending_promotion")
    key = str(piece).strip().casefold()
    if not isinstance(pending, dict) or key not in _PROMOTION_PIECES:
        return {"changed": False, "status": "invalid_promotion"}
    try:
        source = chess.parse_square(str(pending["from"]))
        target = chess.parse_square(str(pending["to"]))
    except (KeyError, ValueError, TypeError):
        return {"changed": False, "status": "invalid_state"}
    move = chess.Move(source, target, promotion=_PROMOTION_PIECES[key])
    if move not in board.legal_moves:
        return {"changed": False, "status": "invalid_promotion"}
    return _push_move(state, board, move)


def chess_offer_draw(state: dict[str, Any], *, user_id: int) -> dict[str, Any]:
    if str(state.get("status")) != "playing":
        return {"changed": False, "status": "not_playing"}
    if _expire_if_needed(state):
        return {"changed": True, "status": "expired", "winner_id": state.get("winner_id")}
    if _player(state, user_id) is None:
        return {"changed": False, "status": "not_player"}
    if state.get("draw_offer_by"):
        return {"changed": False, "status": "draw_already_offered"}
    state["draw_offer_by"] = int(user_id)
    return {"changed": True, "status": "draw_offered"}


def chess_respond_draw(state: dict[str, Any], *, user_id: int, accept: bool) -> dict[str, Any]:
    if str(state.get("status")) != "playing":
        return {"changed": False, "status": "not_playing"}
    if _expire_if_needed(state):
        return {"changed": True, "status": "expired", "winner_id": state.get("winner_id")}
    offerer = int(state.get("draw_offer_by", 0) or 0)
    if not offerer:
        return {"changed": False, "status": "no_draw_offer"}
    if int(user_id) == offerer or _player(state, user_id) is None:
        return {"changed": False, "status": "not_opponent"}
    if accept:
        state["status"] = "finished"
        state["result"] = "1/2-1/2"
        state["termination"] = "agreement"
        state["winner_id"] = None
        state["draw_offer_by"] = None
        return {"changed": True, "status": "draw_agreed"}
    state["draw_offer_by"] = None
    return {"changed": True, "status": "draw_declined"}


def chess_resign(state: dict[str, Any], *, user_id: int) -> dict[str, Any]:
    if str(state.get("status")) != "playing":
        return {"changed": False, "status": "not_playing"}
    if _expire_if_needed(state):
        return {"changed": True, "status": "expired", "winner_id": state.get("winner_id")}
    player = _player(state, user_id)
    if player is None:
        return {"changed": False, "status": "not_player"}
    color = str(player.get("color"))
    winner_color = "black" if color == "white" else "white"
    winner = next((int(item.get("id", 0) or 0) for item in _players(state) if item.get("color") == winner_color), None)
    state.update({"status": "finished", "result": "0-1" if color == "white" else "1-0", "termination": "resignation", "winner_id": winner, "draw_offer_by": None})
    return {"changed": True, "status": "resigned", "winner_id": winner}


def chess_new_round(state: dict[str, Any], *, user_id: int) -> dict[str, Any]:
    if int(user_id) != int(state.get("owner_id", 0) or 0):
        return {"changed": False, "status": "not_owner"}
    if len(_players(state)) != 2:
        return {"changed": False, "status": "needs_player"}
    state.update({
        "round_id": _round_id(), "status": "playing", "fen": START_FEN,
        "turn": "white", "turn_user_id": next((int(item["id"]) for item in _players(state) if item.get("color") == "white"), None),
        "uci_moves": [], "moves": [], "selected_square": None, "legal_targets": [],
        "pending_promotion": None, "draw_offer_by": None, "result": None,
        "termination": None, "winner_id": None, "last_move": None, "last_actor_id": None,
        "turn_timeout_seconds": _timeout_seconds(state),
        "turn_expires_at": time.time() + _timeout_seconds(state),
    })
    return {"changed": True, "status": "restarted"}


def chess_board(state: dict[str, Any]) -> chess.Board:
    """Expose a validated board snapshot for presentation and diagnostics."""

    return _safe_board(state)


def promotion_labels() -> dict[str, str]:
    return dict(_PROMOTION_LABELS)


__all__ = [
    "SCHEMA_VERSION", "START_FEN", "chess_board", "chess_choose_color", "chess_click",
    "chess_join", "chess_new_round", "chess_offer_draw", "chess_promote", "chess_respond_draw",
    "chess_resign", "chess_start", "new_chess_state", "promotion_labels",
    "TURN_TIMEOUT_SECONDS",
]
