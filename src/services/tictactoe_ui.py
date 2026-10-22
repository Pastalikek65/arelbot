"""Bounded, restart-safe Tic-Tac-Toe domain and Telegram presentation.

The handler only forwards callbacks to these pure state transitions.  Every
callback carries a round id so an old inline panel can never mutate a newer
round in the same chat.
"""

from __future__ import annotations

from html import escape
import time
from uuid import uuid4

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


SUPPORTED_BOARD_SIZES = (3, 6, 7, 8)
BOARD_SIZE = 9  # legacy 3x3 cell count kept for existing callers
TURN_TIMEOUT_SECONDS = 180
DEFAULT_BOARD_SIZE = 3
# Telegram inline buttons need a non-empty label.  A non-breaking space keeps
# an unused XOX cell visually empty while remaining a valid button label.
EMPTY_CELL_LABEL = "\u00a0"


def _validate_board_size(value: object) -> int:
    try:
        board_size = int(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("invalid_board_size") from None
    if board_size not in SUPPORTED_BOARD_SIZES:
        raise ValueError("invalid_board_size")
    return board_size


def _state_dimensions(state: dict) -> tuple[int, int] | None:
    """Resolve current dimensions while accepting schema-v1 3x3 states."""
    board = state.get("board")
    if "board_size" not in state:
        if isinstance(board, list) and len(board) == BOARD_SIZE:
            return DEFAULT_BOARD_SIZE, 3
        return None
    try:
        board_size = _validate_board_size(state.get("board_size"))
        win_length = int(state.get("win_length") or (3 if board_size == 3 else 4))
    except (TypeError, ValueError, OverflowError):
        return None
    expected = 3 if board_size == 3 else 4
    if win_length != expected:
        return None
    return board_size, win_length


def new_tictactoe_state(
    *, owner_id: int, owner_name: str, chat_id: int, prompt_message_id: int | None = None,
    board_size: int = DEFAULT_BOARD_SIZE, win_length: int | None = None, size: int | None = None,
) -> dict:
    board_size = _validate_board_size(size if size is not None else board_size)
    expected_win_length = 3 if board_size == 3 else 4
    if win_length is None:
        win_length = expected_win_length
    if int(win_length) != expected_win_length:
        raise ValueError("invalid_win_length")
    owner = {"id": int(owner_id), "name": str(owner_name or "Oyuncu")[:64], "token": "X"}
    return {
        "schema_version": 2,
        "round_id": uuid4().hex[:12],
        "chat_id": int(chat_id),
        "owner_id": int(owner_id),
        "prompt_message_id": int(prompt_message_id or 0),
        "status": "lobby",
        "players": [owner],
        "board_size": board_size,
        "win_length": int(win_length),
        "board": [0] * (board_size * board_size),
        "current_player_id": None,
        "moves": 0,
        "winner_id": None,
        "turn_timeout_seconds": TURN_TIMEOUT_SECONDS,
        "turn_expires_at": None,
        "last_actor_id": None,
        "last_actor_message_id": None,
    }


def _players(state: dict) -> list[dict]:
    raw = state.get("players")
    return [dict(item) for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _player_ids(state: dict) -> list[int] | None:
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


def _player_tokens(state: dict) -> dict[int, str]:
    """Return stable X/O labels, including for legacy emoji token states."""

    tokens: dict[int, str] = {}
    for index, item in enumerate(_players(state)):
        try:
            user_id = int(item["id"])
        except (KeyError, TypeError, ValueError):
            continue
        raw = str(item.get("token") or "").strip().upper()
        tokens[user_id] = raw if raw in {"X", "O"} else ("X" if index == 0 else "O")
    return tokens


def tictactoe_keyboard(state: dict) -> InlineKeyboardMarkup:
    rid = str(state.get("round_id") or "legacy")
    status = str(state.get("status") or "lobby")
    if status == "lobby":
        rows = [[InlineKeyboardButton("➕ Katıl", callback_data=f"tictactoe:{rid}:join")]]
        if len(_players(state)) == 2:
            rows[0].append(InlineKeyboardButton("Maçı başlat", callback_data=f"tictactoe:{rid}:start"))
        rows.append([InlineKeyboardButton("Oyunu bitir", callback_data=f"tictactoe:{rid}:stop")])
        return InlineKeyboardMarkup(rows)
    if status not in {"playing"}:
        return InlineKeyboardMarkup([[InlineKeyboardButton("🎮 Yeni oyun", callback_data="game_start_tictactoe")]])
    dimensions = _state_dimensions(state)
    board_size = dimensions[0] if dimensions else DEFAULT_BOARD_SIZE
    tokens = _player_tokens(state)
    board = state.get("board")
    expected_cells = board_size * board_size
    cells = board if isinstance(board, list) and len(board) == expected_cells else [0] * expected_cells
    rows = []
    for row in range(board_size):
        buttons = []
        for column in range(board_size):
            cell = row * board_size + column
            try:
                value = int(cells[cell] or 0)
            except (TypeError, ValueError, OverflowError):
                value = 0
            label = tokens.get(value, EMPTY_CELL_LABEL) if value else EMPTY_CELL_LABEL
            buttons.append(InlineKeyboardButton(label, callback_data=f"tictactoe:{rid}:cell:{cell}"))
        rows.append(buttons)
    rows.append([
        InlineKeyboardButton("🔄 Yeni tur", callback_data=f"tictactoe:{rid}:restart"),
        InlineKeyboardButton("🛑 Oyunu bitir", callback_data=f"tictactoe:{rid}:stop"),
    ])
    return InlineKeyboardMarkup(rows)


def render_tictactoe_text(state: dict, *, note: str | None = None) -> str:
    players = _players(state)
    names = " · ".join(escape(str(item.get("name") or "Oyuncu")) for item in players) or "henüz kimse yok"
    dimensions = _state_dimensions(state)
    board_size = dimensions[0] if dimensions else DEFAULT_BOARD_SIZE
    status = str(state.get("status") or "lobby")
    if status == "lobby":
        turn = "İkinci oyuncu katıldığında Maçı başlat düğmesine bas."
    elif status == "playing":
        current = next((item for item in players if str(item.get("id")) == str(state.get("current_player_id"))), None)
        turn = f"Sıra: <b>{escape(str((current or {}).get('name') or 'oyuncu'))}</b>"
    elif status == "won":
        winner = next((item for item in players if str(item.get("id")) == str(state.get("winner_id"))), None)
        turn = f"🏆 Kazanan: <b>{escape(str((winner or {}).get('name') or 'oyuncu'))}</b>"
    elif status == "expired":
        turn = "⌛ Süre doldu; bu tur kapandı."
    elif status == "cancelled":
        turn = "Oyun iptal edildi."
    else:
        turn = "🤝 Berabere."
    note_text = f"\n\n{escape(str(note))}" if note else ""
    return (
        "❌⭕ <b>XOX</b>\n\n"
        f"Oyuncular: {names}\n{turn}\n\n"
        f"Hamleler aşağıdaki boş düğmelerden yapılır.\n\n"
        f"{('Üç' if board_size == 3 else str(dimensions[1]))} aynı taşı yatay, dikey veya çapraz hizala."
        f"{note_text}"
    )


def tictactoe_join(state: dict, *, user_id: int, user_name: str) -> dict:
    if str(state.get("status") or "lobby") != "lobby":
        return {"status": "finished", "changed": False}
    players = _players(state)
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return {"status": "invalid_user", "changed": False}
    if uid <= 0 or any(int(item.get("id", 0) or 0) == uid for item in players):
        return {"status": "already_joined", "changed": False}
    if len(players) >= 2 or not isinstance(state.get("players"), list):
        return {"status": "full", "changed": False}
    state["players"].append({"id": uid, "name": str(user_name or "Oyuncu")[:64], "token": "O"})
    return {"status": "joined", "changed": True}


def tictactoe_select_size(state: dict, *, user_id: int, board_size: int) -> dict:
    """Select a large-board variant while the lobby is still open.

    Size selection is an owner-only lobby operation. Validation is completed
    before any state is changed so stale or malformed callback payloads cannot
    partially reset a round. The round id is preserved so the inline panel
    remains bound to the same round while the board variant changes.
    """
    if not isinstance(state, dict):
        return {"status": "invalid_state", "changed": False}

    # Accept the two persisted schemas we can migrate. A callback must carry a
    # real round token; an absent/empty token could mutate an unrelated game.
    try:
        schema_version = int(state.get("schema_version", 1))
    except (TypeError, ValueError, OverflowError):
        return {"status": "invalid_state", "changed": False}
    if schema_version not in (1, 2):
        return {"status": "invalid_state", "changed": False}
    round_id = state.get("round_id")
    if (
        not isinstance(round_id, str)
        or not 1 <= len(round_id) <= 64
        or any(character not in "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ-_" for character in round_id)
    ):
        return {"status": "invalid_state", "changed": False}

    try:
        uid = int(user_id)
    except (TypeError, ValueError, OverflowError):
        return {"status": "invalid_user", "changed": False}
    if uid <= 0:
        return {"status": "invalid_user", "changed": False}

    try:
        selected_size = _validate_board_size(board_size)
    except ValueError:
        return {"status": "invalid_board_size", "changed": False}
    # The 3x3 board is the default, not a selectable large variant.
    if selected_size not in (6, 7, 8):
        return {"status": "invalid_board_size", "changed": False}

    if str(state.get("status") or "lobby") != "lobby":
        return {"status": "not_lobby", "changed": False}

    try:
        owner_id = int(state.get("owner_id") or 0)
    except (TypeError, ValueError, OverflowError):
        return {"status": "invalid_state", "changed": False}
    if owner_id <= 0:
        return {"status": "invalid_state", "changed": False}
    if uid != owner_id:
        return {"status": "not_owner", "changed": False}

    # Reset all per-round fields while preserving players, prompt message and
    # round identity for the existing inline panel.
    state["schema_version"] = 2
    state["board_size"] = selected_size
    state["win_length"] = 4
    state["board"] = [0] * (selected_size * selected_size)
    state["moves"] = 0
    state["winner_id"] = None
    state["current_player_id"] = None
    state["turn_expires_at"] = None
    state["last_actor_id"] = None
    state["last_actor_message_id"] = None
    return {
        "status": "selected",
        "changed": True,
        "board_size": selected_size,
        "win_length": 4,
        "round_id": round_id,
    }


def tictactoe_start(state: dict) -> dict:
    if str(state.get("status") or "lobby") != "lobby":
        return {"status": "already_started", "changed": False}
    ids = _player_ids(state)
    if ids is None:
        return {"status": "needs_player", "changed": False}
    dimensions = _state_dimensions(state)
    board = state.get("board")
    if dimensions is None or not isinstance(board, list) or len(board) != dimensions[0] * dimensions[0]:
        return {"status": "invalid_state", "changed": False}
    # A lobby is never allowed to carry moves from an older round.  This also
    # repairs legacy snapshots that were persisted before round reset existed.
    board[:] = [0] * (dimensions[0] * dimensions[0])
    state["moves"] = 0
    state["winner_id"] = None
    state["last_actor_id"] = None
    state["last_actor_message_id"] = None
    state["status"] = "playing"
    state["current_player_id"] = ids[0]
    state["turn_expires_at"] = time.time() + max(30, min(3600, int(state.get("turn_timeout_seconds", TURN_TIMEOUT_SECONDS) or TURN_TIMEOUT_SECONDS)))
    return {"status": "started", "changed": True}


def tictactoe_new_round(state: dict, *, user_id: int) -> dict:
    """Start a clean round while retaining the lobby players and board size."""

    if not isinstance(state, dict):
        return {"status": "invalid_state", "changed": False}
    try:
        uid = int(user_id)
        owner_id = int(state.get("owner_id") or 0)
    except (TypeError, ValueError, OverflowError):
        return {"status": "invalid_state", "changed": False}
    if uid != owner_id:
        return {"status": "not_owner", "changed": False}
    players = _players(state)
    ids = _player_ids(state)
    dimensions = _state_dimensions(state)
    if ids is None or dimensions is None:
        return {"status": "needs_player", "changed": False}
    state["round_id"] = uuid4().hex[:12]
    state["schema_version"] = 2
    state["status"] = "playing"
    state["board"] = [0] * (dimensions[0] * dimensions[0])
    state["moves"] = 0
    state["winner_id"] = None
    state["current_player_id"] = ids[0]
    state["turn_expires_at"] = time.time() + max(
        30,
        min(3600, int(state.get("turn_timeout_seconds", TURN_TIMEOUT_SECONDS) or TURN_TIMEOUT_SECONDS)),
    )
    state["last_actor_id"] = None
    state["last_actor_message_id"] = None
    return {
        "status": "restarted",
        "changed": True,
        "round_id": state["round_id"],
        "players": players,
    }


def _has_line(board: list[int], value: int, board_size: int = DEFAULT_BOARD_SIZE, win_length: int = 3) -> bool:
    for row in range(board_size):
        for column in range(board_size):
            for row_step, column_step in ((0, 1), (1, 0), (1, 1), (1, -1)):
                end_row = row + (win_length - 1) * row_step
                end_column = column + (win_length - 1) * column_step
                if not (0 <= end_row < board_size and 0 <= end_column < board_size):
                    continue
                indexes = [
                    (row + offset * row_step) * board_size + column + offset * column_step
                    for offset in range(win_length)
                ]
                if all(board[index] == value for index in indexes):
                    return True
    return False


def tictactoe_move(state: dict, *, user_id: int, cell: int, message_id: int | None = None) -> dict:
    if str(state.get("status")) != "playing":
        return {"status": "not_playing", "changed": False}
    try:
        if state.get("turn_expires_at") is not None and time.time() >= float(state["turn_expires_at"]):
            state["status"] = "expired"
            return {"status": "expired", "changed": True}
        uid = int(user_id)
        cell = int(cell)
        current = int(state.get("current_player_id") or 0)
    except (TypeError, ValueError, OverflowError):
        return {"status": "invalid_state", "changed": False}
    ids = _player_ids(state)
    dimensions = _state_dimensions(state)
    board = state.get("board")
    expected_cells = dimensions[0] * dimensions[0] if dimensions else 0
    if ids is None or dimensions is None or not isinstance(board, list) or len(board) != expected_cells:
        return {"status": "invalid_state", "changed": False}
    if current != uid:
        return {"status": "not_your_turn", "changed": False}
    if not 0 <= cell < expected_cells:
        return {"status": "invalid_cell", "changed": False}
    normalized_board: list[int] = []
    for value in board:
        try:
            cell_value = int(value or 0)
        except (TypeError, ValueError, OverflowError):
            return {"status": "invalid_state", "changed": False}
        if cell_value not in (0, *ids):
            return {"status": "invalid_state", "changed": False}
        normalized_board.append(cell_value)
    board[:] = normalized_board
    if board[cell] != 0:
        return {"status": "occupied", "changed": False}
    board[cell] = uid
    state["moves"] = sum(1 for value in board if value != 0)
    state["last_actor_id"] = uid
    state["last_actor_message_id"] = int(message_id or 0)
    board_size, win_length = dimensions
    if _has_line(board, uid, board_size, win_length):
        state["status"] = "won"
        state["winner_id"] = uid
        return {"status": "won", "changed": True, "cell": cell}
    if int(state["moves"]) >= expected_cells:
        state["status"] = "draw"
        return {"status": "draw", "changed": True, "cell": cell}
    state["current_player_id"] = ids[(ids.index(uid) + 1) % 2]
    state["turn_expires_at"] = time.time() + max(30, min(3600, int(state.get("turn_timeout_seconds", TURN_TIMEOUT_SECONDS) or TURN_TIMEOUT_SECONDS)))
    return {"status": "moved", "changed": True, "cell": cell}

