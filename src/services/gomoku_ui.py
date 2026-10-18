"""Telegram presentation adapter for the restart-safe Gomoku engine.

The game rules live in :mod:`src.games.gomoku`.  This module only owns the
small dictionary envelope used by Telegram handlers and the inline keyboard
rendering.  The envelope deliberately keeps the immutable engine snapshot
under ``engine`` so a lobby can exist before the second player joins while a
started round remains fully replayable after a process restart.
"""

from __future__ import annotations

from html import escape
import time
from uuid import uuid4
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from src.games.gomoku import (
    DEFAULT_BOARD_SIZE,
    DEFAULT_WIN_LENGTH,
    GomokuState,
    GomokuStatus,
    apply_move,
    create_game,
)


SUPPORTED_BOARD_SIZES = (6, 7, 8, 10, 12, 15)
TURN_TIMEOUT_SECONDS = 300
SCHEMA_VERSION = 1


def _board_size(value: object) -> int:
    try:
        size = int(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("invalid_gomoku_board_size") from None
    if size not in SUPPORTED_BOARD_SIZES:
        raise ValueError("invalid_gomoku_board_size")
    return size


def _players(state: dict[str, Any]) -> list[dict[str, Any]]:
    raw = state.get("players")
    return [dict(item) for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _player_ids(state: dict[str, Any]) -> list[int] | None:
    players = _players(state)
    if len(players) != 2:
        return None
    try:
        ids = [int(item["id"]) for item in players]
    except (KeyError, TypeError, ValueError):
        return None
    if any(item <= 0 for item in ids) or len(set(ids)) != 2:
        return None
    return ids


def _name(state: dict[str, Any], user_id: object) -> str:
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return "oyuncu"
    for player in _players(state):
        try:
            if int(player.get("id", 0)) == uid:
                return str(player.get("name") or "oyuncu")[:64]
        except (TypeError, ValueError):
            continue
    return "oyuncu"


def new_gomoku_state(
    *,
    owner_id: int,
    owner_name: str,
    chat_id: int,
    prompt_message_id: int | None = None,
    board_size: int = DEFAULT_BOARD_SIZE,
    thread_id: int | None = None,
) -> dict[str, Any]:
    """Create a lobby envelope; no engine is created until a second player joins."""

    size = _board_size(board_size)
    if int(owner_id) <= 0 or int(chat_id) == 0:
        raise ValueError("invalid_gomoku_scope")
    return {
        "schema_version": SCHEMA_VERSION,
        "round_id": uuid4().hex[:12],
        "chat_id": int(chat_id),
        "thread_id": int(thread_id) if thread_id is not None else None,
        "owner_id": int(owner_id),
        "prompt_message_id": int(prompt_message_id or 0),
        "status": "lobby",
        "board_size": size,
        "win_length": DEFAULT_WIN_LENGTH,
        "players": [{"id": int(owner_id), "name": str(owner_name or "Oyuncu")[:64], "token": "⚫"}],
        "engine": None,
        "board": [0] * (size * size),
        "current_player_id": None,
        "winner_id": None,
        "moves": 0,
        "last_actor_id": None,
        "last_actor_message_id": None,
        "turn_timeout_seconds": TURN_TIMEOUT_SECONDS,
        "turn_expires_at": None,
    }


def gomoku_select_size(state: dict[str, Any], *, user_id: int, board_size: int) -> dict[str, Any]:
    """Select a board size while the lobby is open, preserving the round id."""

    if str(state.get("status") or "lobby") != "lobby":
        return {"status": "not_lobby", "changed": False}
    if int(user_id) != int(state.get("owner_id", 0) or 0):
        return {"status": "not_owner", "changed": False}
    try:
        size = _board_size(board_size)
    except ValueError:
        return {"status": "invalid_board_size", "changed": False}
    state["board_size"] = size
    state["win_length"] = DEFAULT_WIN_LENGTH
    state["board"] = [0] * (size * size)
    state["engine"] = None
    state["moves"] = 0
    state["current_player_id"] = None
    state["winner_id"] = None
    state["turn_expires_at"] = None
    return {"status": "selected", "changed": True, "board_size": size}


def gomoku_join(state: dict[str, Any], *, user_id: int, user_name: str) -> dict[str, Any]:
    if str(state.get("status") or "lobby") != "lobby":
        return {"status": "finished", "changed": False}
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return {"status": "invalid_user", "changed": False}
    if uid <= 0:
        return {"status": "invalid_user", "changed": False}
    players = _players(state)
    if any(int(item.get("id", 0) or 0) == uid for item in players):
        return {"status": "already_joined", "changed": False}
    if len(players) >= 2 or not isinstance(state.get("players"), list):
        return {"status": "full", "changed": False}
    state["players"].append({"id": uid, "name": str(user_name or "Oyuncu")[:64], "token": "⚪"})
    return {"status": "joined", "changed": True}


def gomoku_start(state: dict[str, Any]) -> dict[str, Any]:
    if str(state.get("status") or "lobby") != "lobby":
        return {"status": "already_started", "changed": False}
    players = _player_ids(state)
    if players is None:
        return {"status": "needs_player", "changed": False}
    try:
        size = _board_size(state.get("board_size", DEFAULT_BOARD_SIZE))
        win_length = int(state.get("win_length", DEFAULT_WIN_LENGTH) or DEFAULT_WIN_LENGTH)
        engine = create_game(
            chat_id=int(state["chat_id"]),
            players=players,
            board_size=size,
            win_length=win_length,
            game_id=str(state.get("round_id") or ""),
        )
    except (KeyError, TypeError, ValueError):
        return {"status": "invalid_state", "changed": False}
    _sync_engine(state, engine)
    state["status"] = "playing"
    state["turn_expires_at"] = time.time() + _timeout(state)
    return {"status": "started", "changed": True}


def gomoku_move(
    state: dict[str, Any],
    *,
    user_id: int,
    cell: int,
    message_id: int | None = None,
    action_id: str | None = None,
) -> dict[str, Any]:
    """Apply one cell move through the pure Gomoku engine."""

    if str(state.get("status")) != "playing":
        return {"status": "not_playing", "changed": False}
    expires_at = state.get("turn_expires_at")
    try:
        if expires_at is not None and time.time() >= float(expires_at):
            state["status"] = "expired"
            return {"status": "expired", "changed": True}
    except (TypeError, ValueError, OverflowError):
        return {"status": "invalid_state", "changed": False}
    try:
        engine = GomokuState.from_dict(state["engine"])
        index = int(cell)
        row, column = engine.coordinates_for(index)
    except (KeyError, TypeError, ValueError):
        return {"status": "invalid_state" if "engine" in state else "invalid_cell", "changed": False}
    action = str(action_id or f"cell:{index}:{engine.version}")
    result = apply_move(
        engine,
        player_id=int(user_id),
        row=row,
        column=column,
        action_id=action,
    )
    status = result.status.value
    if not result.ok:
        return {"status": status, "changed": False, "reason": result.reason}
    _sync_engine(state, result.state)
    state["last_actor_id"] = int(user_id)
    state["last_actor_message_id"] = int(message_id or 0)
    if result.status is GomokuStatus.WON:
        state["status"] = "won"
        state["winner_id"] = int(user_id)
        state["turn_expires_at"] = None
    elif result.status is GomokuStatus.DRAW:
        state["status"] = "draw"
        state["turn_expires_at"] = None
    else:
        state["turn_expires_at"] = time.time() + _timeout(state)
    return {
        "status": status,
        "changed": True,
        "row": row,
        "column": column,
        "winning_line": result.winning_line,
    }


def gomoku_keyboard(state: dict[str, Any]) -> InlineKeyboardMarkup:
    rid = str(state.get("round_id") or "legacy")
    status = str(state.get("status") or "lobby")
    if status == "lobby":
        selected = int(state.get("board_size", DEFAULT_BOARD_SIZE) or DEFAULT_BOARD_SIZE)
        sizes = []
        for size in SUPPORTED_BOARD_SIZES:
            marker = "✅ " if size == selected else ""
            sizes.append(InlineKeyboardButton(f"{marker}{size}×{size}", callback_data=f"gomoku:{rid}:size:{size}"))
        rows = [sizes[:3], sizes[3:]]
        join_row = [InlineKeyboardButton("➕ Katıl", callback_data=f"gomoku:{rid}:join")]
        if len(_players(state)) >= 2:
            join_row.append(InlineKeyboardButton("▶️ Başlat", callback_data=f"gomoku:{rid}:start"))
        rows.append(join_row)
        rows.append([InlineKeyboardButton("🛑 Lobiyi kapat", callback_data=f"gomoku:{rid}:stop")])
        return InlineKeyboardMarkup(rows)
    if status not in {"playing"}:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🎮 Yeni Gomoku", callback_data="game_start_gomoku")],
            [InlineKeyboardButton("🛑 Oyunu kapat", callback_data=f"gomoku:{rid}:stop")],
        ])
    try:
        size = _board_size(state.get("board_size", DEFAULT_BOARD_SIZE))
        engine = GomokuState.from_dict(state["engine"])
        board = engine.board
    except (KeyError, TypeError, ValueError):
        return InlineKeyboardMarkup([[InlineKeyboardButton("🎮 Yeni Gomoku", callback_data="game_start_gomoku")]])
    rows: list[list[InlineKeyboardButton]] = []
    markers = {0: "·", 1: "⚫", 2: "⚪"}
    for row in range(size):
        rows.append([
            InlineKeyboardButton(markers.get(board[row * size + col], "·"), callback_data=f"gomoku:{rid}:cell:{row * size + col}")
            for col in range(size)
        ])
    rows.append([InlineKeyboardButton("🛑 Oyunu bitir", callback_data=f"gomoku:{rid}:stop")])
    return InlineKeyboardMarkup(rows)


def render_gomoku_text(state: dict[str, Any], *, note: str | None = None) -> str:
    players = _players(state)
    names = " ve ".join(escape(str(item.get("name") or "Oyuncu")) for item in players) or "henüz kimse yok"
    size = int(state.get("board_size", DEFAULT_BOARD_SIZE) or DEFAULT_BOARD_SIZE)
    engine: GomokuState | None = None
    try:
        engine = GomokuState.from_dict(state["engine"])
    except (KeyError, TypeError, ValueError):
        pass
    status = str(state.get("status") or "lobby")
    if status == "lobby":
        turn = "İkinci oyuncu katılsın; sonra başlat düğmesine basılır."
    elif status == "playing":
        current_id = engine.turn_player_id if engine is not None else state.get("current_player_id")
        turn = f"Sıra: <b>{escape(_name(state, current_id))}</b>"
    elif status == "won":
        turn = f"🏆 Kazanan: <b>{escape(_name(state, state.get('winner_id')))}</b>"
    elif status == "expired":
        turn = "⌛ Süre doldu; bu tur kapandı."
    else:
        turn = "🤝 Berabere."
    note_text = f"\n\n{escape(str(note))}" if note else ""
    return (
        "⚫⚪ <b>GOMOKU BEŞLİ</b>\n\n"
        f"Tahta: <b>{size}×{size}</b>\n"
        "Hedef: <b>5 taş</b>\n"
        f"Oyuncular: {names}\n{turn}\n\n"
        "Tahta hamleleri aşağıdaki düğmelerde gösteriliyor.\n"
        "Beş taşı yatay, dikey veya çapraz hizala. Boş bir hücreye basarak hamle yap."
        f"{note_text}"
    )


def _timeout(state: dict[str, Any]) -> float:
    try:
        return float(max(30, min(3600, int(state.get("turn_timeout_seconds", TURN_TIMEOUT_SECONDS) or TURN_TIMEOUT_SECONDS))))
    except (TypeError, ValueError, OverflowError):
        return float(TURN_TIMEOUT_SECONDS)


def _sync_engine(state: dict[str, Any], engine: GomokuState) -> None:
    state["engine"] = engine.to_dict()
    state["board_size"] = engine.config.board_size
    state["win_length"] = engine.config.win_length
    state["board"] = list(engine.board)
    state["moves"] = engine.version
    state["current_player_id"] = engine.turn_player_id if engine.status is GomokuStatus.ACTIVE else None
    state["winner_id"] = engine.winner_id


__all__ = [
    "DEFAULT_BOARD_SIZE",
    "SUPPORTED_BOARD_SIZES",
    "TURN_TIMEOUT_SECONDS",
    "gomoku_join",
    "gomoku_keyboard",
    "gomoku_move",
    "gomoku_select_size",
    "gomoku_start",
    "new_gomoku_state",
    "render_gomoku_text",
]
