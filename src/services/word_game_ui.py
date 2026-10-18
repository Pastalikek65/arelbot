"""Telegram presentation and bounded rules for interactive word games.

Callbacks are scoped to a persisted round when ``round_id`` is present. Legacy
states without a round id keep their old callback names during migration.
"""

from __future__ import annotations

from html import escape
import time
from uuid import uuid4

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def _scoped(prefix: str, state: dict) -> str:
    round_id = str(state.get("round_id") or "").strip()
    return f"{prefix}:{round_id}" if round_id else prefix


def word_game_keyboard(state: dict) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔀 Harfleri karıştır", callback_data=_scoped("word_game_shuffle", state)),
            InlineKeyboardButton("💡 İpucu", callback_data=_scoped("word_game_hint", state)),
        ],
        [
            InlineKeyboardButton("🧩 Kategori seç", callback_data="game_word_categories"),
            InlineKeyboardButton("🎚️ Zorluk", callback_data="game_word_difficulty"),
        ],
        [InlineKeyboardButton("🛑 Oyunu bitir", callback_data=_scoped("word_game_stop", state))],
    ])


def render_word_game_text(state: dict, *, note: str | None = None) -> str:
    found = [str(value).upper() for value in state.get("found_words", [])]
    found_text = ", ".join(escape(value) for value in found[-12:]) or "henüz yok"
    note_text = f"\n\n{escape(str(note))}" if note else ""
    return (
        "🔠 <b>KELİME TÜRETME</b>\n\n"
        f"Kategori: <b>{escape(str(state.get('category', 'genel')).title())}</b> · "
        f"Zorluk: <b>{escape(str(state.get('difficulty_label', 'Normal')))}</b>\n"
        f"Harfler: <code>{escape(str(state.get('letters', '')))}</code>\n"
        f"Hedef: <b>{int(state.get('target_count', 0) or 0)}</b> kelime · "
        f"Bulunan: <b>{len(found)}</b> · İpucu: <b>{int(state.get('hints_used', 0) or 0)}</b>\n"
        f"Bulunan kelimeler: <code>{found_text}</code>\n\n"
        "Bir kelime gönder; butonlardan harfleri karıştırabilir veya sıradaki ipucunu açabilirsin."
        f"{note_text}"
    )


CONNECT4_ROWS = 6
CONNECT4_COLUMNS = 7


def _connect4_players(state: dict) -> list[dict]:
    raw = state.get("players")
    return [dict(player) for player in raw if isinstance(player, dict)] if isinstance(raw, list) else []


def new_connect4_state(*, owner_id: int, owner_name: str, chat_id: int, prompt_message_id: int | None = None) -> dict:
    """Return a restart-safe two-player Connect Four lobby state."""
    owner = {"id": int(owner_id), "name": str(owner_name or "Oyuncu")[:64], "token": "🔴"}
    return {
        "schema_version": 1,
        "round_id": uuid4().hex[:12],
        "chat_id": int(chat_id),
        "owner_id": int(owner_id),
        "prompt_message_id": int(prompt_message_id or 0),
        "status": "lobby",
        "players": [owner],
        "current_player_id": int(owner_id),
        "board": [[0 for _ in range(CONNECT4_COLUMNS)] for _ in range(CONNECT4_ROWS)],
        "moves": 0,
        "winner_id": None,
        "last_actor_id": None,
        "last_actor_message_id": None,
        "turn_timeout_seconds": 180,
        "turn_expires_at": None,
    }


def connect4_keyboard(state: dict) -> InlineKeyboardMarkup:
    """Create a compact lobby/turn keyboard with round-scoped callbacks."""
    rid = str(state.get("round_id") or "legacy")
    status = str(state.get("status") or "lobby")
    if status == "lobby":
        rows = [[InlineKeyboardButton("➕ Katıl", callback_data=f"connect4:{rid}:join")]]
        if len(_connect4_players(state)) >= 2:
            rows[0].append(InlineKeyboardButton("▶️ Başlat", callback_data=f"connect4:{rid}:start"))
        rows.append([InlineKeyboardButton("🛑 Lobiyi kapat", callback_data=f"connect4:{rid}:stop")])
        return InlineKeyboardMarkup(rows)
    if status != "playing":
        return InlineKeyboardMarkup([[InlineKeyboardButton("🎮 Yeni oyun", callback_data="game_start_connect4")]])
    rows = []
    for start in (0, 4):
        rows.append([
            InlineKeyboardButton(str(column + 1), callback_data=f"connect4:{rid}:col:{column}")
            for column in range(start, min(start + 4, CONNECT4_COLUMNS))
        ])
    rows.append([InlineKeyboardButton("🛑 Oyunu bitir", callback_data=f"connect4:{rid}:stop")])
    return InlineKeyboardMarkup(rows)


def render_connect4_text(state: dict, *, note: str | None = None) -> str:
    players = _connect4_players(state)
    names = " · ".join(escape(str(player.get("name") or "Oyuncu")) for player in players) or "henüz kimse yok"
    tokens = {}
    for player in players:
        try:
            if player.get("id") is not None:
                tokens[int(player.get("id"))] = player.get("token", "🔴")
        except (AttributeError, TypeError, ValueError):
            continue
    board = state.get("board") or []
    lines = []
    for row in (board if isinstance(board, list) else []):
        if not isinstance(row, list):
            row = []
        rendered = []
        for cell in row:
            try:
                cell_id = int(cell) if cell else 0
            except (TypeError, ValueError):
                cell_id = 0
            rendered.append(tokens.get(cell_id, "⚪") if cell_id else "⚪")
        lines.append(" ".join(rendered))
    status = str(state.get("status") or "lobby")
    if status == "lobby":
        turn = "İkinci oyuncuyu bekliyorum"
    elif status == "playing":
        try:
            current_id = int(state.get("current_player_id", 0))
        except (TypeError, ValueError):
            current_id = 0
        current = next((p for p in players if str(p.get("id", "")) == str(current_id)), None)
        turn = f"Sıra: <b>{escape(str((current or {}).get('name', 'oyuncu')))}</b>"
    elif state.get("winner_id"):
        winner = next((p for p in players if str(p.get("id", "")) == str(state.get("winner_id"))), None)
        turn = f"🏆 Kazanan: <b>{escape(str((winner or {}).get('name', 'oyuncu')))}</b>"
    elif status == "expired":
        turn = "⌛ Süre doldu; bu tur kapandı"
    else:
        turn = "🤝 Berabere"
    note_text = f"\n\n{escape(str(note))}" if note else ""
    return (
        "🔴 <b>CONNECT FOUR</b>\n\n"
        f"Oyuncular: {names}\n{turn}\n\n"
        "<code>" + "\n".join(lines) + "</code>\n\n"
        "Dört taşı yatay, dikey veya çapraz hizala. Sütun numarasına bas."
        f"{note_text}"
    )


def connect4_join(state: dict, *, user_id: int, user_name: str) -> dict:
    """Add a player once; return a bounded result without mutating on errors."""
    if str(state.get("status") or "lobby") != "lobby":
        return {"status": "finished", "changed": False}
    players = _connect4_players(state)
    if not isinstance(state.get("players"), list):
        return {"status": "invalid_state", "changed": False}
    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        return {"status": "invalid_user", "changed": False}
    try:
        existing_ids = {int(player.get("id", 0)) for player in players}
    except (TypeError, ValueError):
        return {"status": "invalid_state", "changed": False}
    if user_id <= 0:
        return {"status": "invalid_user", "changed": False}
    if user_id in existing_ids:
        return {"status": "already_joined", "changed": False}
    if len(players) >= 2:
        return {"status": "full", "changed": False}
    players.append({"id": user_id, "name": str(user_name or "Oyuncu")[:64], "token": "🟡"})
    state["players"] = players
    return {"status": "joined", "changed": True}


def connect4_start(state: dict) -> dict:
    if str(state.get("status") or "lobby") != "lobby":
        return {"status": "already_started", "changed": False}
    players = _connect4_players(state)
    ids = []
    try:
        ids = [int(player["id"]) for player in players]
    except (KeyError, TypeError, ValueError):
        return {"status": "invalid_state", "changed": False}
    if len(players) != 2 or len(players) != len(state.get("players") or []) or len(set(ids)) != 2 or any(item <= 0 for item in ids):
        return {"status": "needs_player", "changed": False}
    state["status"] = "playing"
    state["current_player_id"] = ids[0]
    state["turn_expires_at"] = time.time() + max(30, min(3600, int(state.get("turn_timeout_seconds", 180) or 180)))
    return {"status": "started", "changed": True}


def connect4_drop(state: dict, *, user_id: int, column: int, message_id: int | None = None) -> dict:
    """Drop one token with turn, bounds and terminal-state validation."""
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
        user_id = int(user_id)
        column = int(column)
    except (TypeError, ValueError):
        return {"status": "invalid_move", "changed": False}
    try:
        current_player_id = int(state.get("current_player_id", 0))
    except (TypeError, ValueError):
        return {"status": "invalid_state", "changed": False}
    if user_id != current_player_id:
        return {"status": "not_your_turn", "changed": False}
    if not 0 <= column < CONNECT4_COLUMNS:
        return {"status": "invalid_column", "changed": False}
    board = state.get("board")
    if not isinstance(board, list) or len(board) != CONNECT4_ROWS or any(not isinstance(row, list) or len(row) != CONNECT4_COLUMNS for row in board):
        return {"status": "invalid_state", "changed": False}
    players = []
    for player in _connect4_players(state):
        try:
            players.append(int(player.get("id", 0)))
        except (TypeError, ValueError):
            continue
    if user_id not in players or len(players) != len(_connect4_players(state)) or len(set(players)) != 2:
        return {"status": "invalid_state", "changed": False}
    if any(cell not in (0, *players) for row in board for cell in row):
        return {"status": "invalid_state", "changed": False}
    try:
        moves = int(state.get("moves", 0) or 0)
    except (TypeError, ValueError):
        return {"status": "invalid_state", "changed": False}
    if moves < 0 or moves >= CONNECT4_ROWS * CONNECT4_COLUMNS:
        return {"status": "invalid_state", "changed": False}
    row_index = next((index for index in range(CONNECT4_ROWS - 1, -1, -1) if not board[index][column]), None)
    if row_index is None:
        return {"status": "column_full", "changed": False}
    board[row_index][column] = user_id
    state["moves"] = moves + 1
    state["last_actor_id"] = user_id
    state["last_actor_message_id"] = int(message_id or 0)
    if _connect4_has_line(board, row_index, column, user_id):
        state["status"] = "won"
        state["winner_id"] = user_id
        return {"status": "won", "changed": True, "row": row_index, "column": int(column)}
    if int(state["moves"]) >= CONNECT4_ROWS * CONNECT4_COLUMNS:
        state["status"] = "draw"
        return {"status": "draw", "changed": True, "row": row_index, "column": int(column)}
    try:
        next_index = (players.index(user_id) + 1) % len(players)
        state["current_player_id"] = players[next_index]
        state["turn_expires_at"] = time.time() + max(30, min(3600, int(state.get("turn_timeout_seconds", 180) or 180)))
    except (ValueError, ZeroDivisionError):
        return {"status": "invalid_state", "changed": False}
    return {"status": "moved", "changed": True, "row": row_index, "column": int(column)}


def _connect4_has_line(board: list[list[int]], row: int, column: int, player: int) -> bool:
    for delta_row, delta_column in ((0, 1), (1, 0), (1, 1), (1, -1)):
        total = 1
        for direction in (-1, 1):
            r, c = row + delta_row * direction, column + delta_column * direction
            while 0 <= r < CONNECT4_ROWS and 0 <= c < CONNECT4_COLUMNS and board[r][c] == player:
                total += 1
                r += delta_row * direction
                c += delta_column * direction
        if total >= 4:
            return True
    return False
