"""Telegram presentation for the persistent chess game."""

from __future__ import annotations

from html import escape
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import chess

from src.games.chess import chess_board, promotion_labels


PIECE_SYMBOLS = {
    "P": "♙", "N": "♘", "B": "♗", "R": "♖", "Q": "♕", "K": "♔",
    "p": "♟", "n": "♞", "b": "♝", "r": "♜", "q": "♛", "k": "♚",
}
# a1 is a dark square. Text symbols render consistently on Telegram buttons.
EMPTY_SQUARES = ("▪", "▫")


def _players(state: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in state.get("players", []) if isinstance(item, dict)]


def _player_label(state: dict[str, Any], color: str) -> str:
    for item in _players(state):
        if item.get("color") == color:
            return escape(str(item.get("name") or "Oyuncu")[:64])
    return "—"


def _oriented_squares(turn: str) -> list[int]:
    # The active player is always closest to the bottom, matching the
    # interaction model of the referenced chessbot.
    if str(turn) == "black":
        ranks = range(0, 8)
        files = range(7, -1, -1)
    else:
        ranks = range(7, -1, -1)
        files = range(0, 8)
    return [chess.square(file_index, rank_index) for rank_index in ranks for file_index in files]


def _square_label(board: chess.Board, square: int, *, selected: str | None, targets: set[str]) -> str:
    name = chess.square_name(square)
    piece = board.piece_at(square)
    symbol = PIECE_SYMBOLS.get(piece.symbol(), "·") if piece else None
    if name == selected:
        return f"▣{symbol or '·'}"
    if name in targets:
        return f"×{symbol}" if piece else "◇"
    if piece is not None:
        return ("○" if piece.color == chess.WHITE else "●") + symbol
    return EMPTY_SQUARES[(chess.square_file(square) + chess.square_rank(square)) % 2]


def chess_keyboard(state: dict[str, Any]) -> InlineKeyboardMarkup:
    round_id = str(state.get("round_id") or "legacy")
    status = str(state.get("status") or "lobby")
    rows: list[list[InlineKeyboardButton]] = []
    if status == "lobby":
        rows.append([
            InlineKeyboardButton("♔ Beyazı seç", callback_data=f"chess:{round_id}:color:white"),
            InlineKeyboardButton("♚ Siyahı seç", callback_data=f"chess:{round_id}:color:black"),
        ])
        rows.append([
            InlineKeyboardButton("➕ Oyuna katıl", callback_data=f"chess:{round_id}:join"),
            InlineKeyboardButton("▶️ Başlat", callback_data=f"chess:{round_id}:start"),
        ])
        rows.append([InlineKeyboardButton("🛑 Lobiyi kapat", callback_data=f"chess:{round_id}:stop")])
        return InlineKeyboardMarkup(rows)
    if status not in {"playing", "finished", "cancelled", "expired"}:
        return InlineKeyboardMarkup([[InlineKeyboardButton("♟️ Yeni satranç", callback_data="game_start_chess")]])

    board = chess_board(state)
    selected = str(state.get("selected_square") or "") or None
    targets = {str(item) for item in (state.get("legal_targets") or ())}
    oriented = _oriented_squares(str(state.get("turn") or "white"))
    for offset in range(0, 64, 8):
        row = []
        for square in oriented[offset:offset + 8]:
            row.append(InlineKeyboardButton(
                _square_label(board, square, selected=selected, targets=targets),
                callback_data=f"chess:{round_id}:sq:{chess.square_name(square)}",
            ))
        rows.append(row)

    pending = state.get("pending_promotion")
    if isinstance(pending, dict):
        rows.append([
            InlineKeyboardButton(label, callback_data=f"chess:{round_id}:promote:{key}")
            for key, label in promotion_labels().items()
        ])
    if status == "playing":
        if state.get("draw_offer_by"):
            rows.append([
                InlineKeyboardButton("🤝 Beraberliği kabul et", callback_data=f"chess:{round_id}:draw_accept"),
                InlineKeyboardButton("↩️ Teklifi reddet", callback_data=f"chess:{round_id}:draw_decline"),
            ])
        else:
            rows.append([InlineKeyboardButton("🤝 Beraberlik teklif et", callback_data=f"chess:{round_id}:draw_offer")])
        rows.append([
            InlineKeyboardButton("🏳️ Pes et", callback_data=f"chess:{round_id}:resign"),
            InlineKeyboardButton("🛑 Oyunu bitir", callback_data=f"chess:{round_id}:stop"),
        ])
    else:
        rows.append([
            InlineKeyboardButton("🔄 Yeni tur", callback_data=f"chess:{round_id}:restart"),
            InlineKeyboardButton("🛑 Paneli kapat", callback_data=f"chess:{round_id}:stop"),
        ])
    return InlineKeyboardMarkup(rows)


def render_chess_text(state: dict[str, Any], *, note: str | None = None) -> str:
    status = str(state.get("status") or "lobby")
    players = _players(state)
    lines = ["♟️ <b>AREL SATRANÇ</b>"]
    if status == "lobby":
        lines += [
            "",
            "iki kişilik kalıcı pvp masası hazır",
            f"♔ beyaz: <b>{_player_label(state, 'white')}</b>",
            f"♚ siyah: <b>{_player_label(state, 'black')}</b>",
            "",
            "renk seç, arkadaşın katılsın ve başlat düğmesine bas",
        ]
    else:
        turn = str(state.get("turn") or "white")
        lines += [
            "",
            f"<b>{'Beyaz' if turn == 'white' else 'Siyah'} altta</b> · sütunlar {'a → h' if turn == 'white' else 'h → a'}",
            "○ Beyaz taşlar · ● Siyah taşlar · ▣ seçili · ◇ hamle · × alma",
        ]
        if status == "playing":
            lines += ["", f"Sıra: <b>{_player_label(state, turn)}</b> · {'Beyaz' if turn == 'white' else 'Siyah'}"]
            if state.get("selected_square"):
                lines.append(f"Seçili kare: <code>{escape(str(state['selected_square']))}</code> · ◇ geçerli hedef")
            if state.get("draw_offer_by"):
                lines.append("🤝 rakipten beraberlik teklifi var")
            try:
                if chess_board(state).is_check():
                    lines.append("⚠️ şah çekildi")
            except ValueError:
                lines.append("⚠️ tahta kaydı doğrulanamadı")
        else:
            result = str(state.get("result") or "—")
            termination = str(state.get("termination") or "oyun sonu").replace("_", " ")
            lines += ["", f"<b>tur tamamlandı</b> · sonuç: <code>{escape(result)}</code>", f"neden: {escape(termination)}"]
            winner = state.get("winner_id")
            if winner:
                winner_item = next((item for item in players if int(item.get("id", 0) or 0) == int(winner)), None)
                lines.append(f"🏆 kazanan: <b>{escape(str((winner_item or {}).get('name') or 'Oyuncu'))}</b>")
        lines += [
            f"♔ { _player_label(state, 'white') }  ·  ♚ { _player_label(state, 'black') }",
            f"hamle: <b>{len(state.get('moves') or [])}</b>" + (f" · son: <code>{escape(str(state.get('last_move')))}</code>" if state.get("last_move") else ""),
        ]
    if note:
        lines += ["", escape(str(note))]
    return "\n".join(lines)


__all__ = ["chess_keyboard", "render_chess_text"]
