"""Telegram presentation for the persistent chess game."""

from __future__ import annotations

from html import escape
from typing import Any

import chess

from src.games.chess import chess_board, promotion_labels
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

PIECE_SYMBOLS = {
    "P": "♙", "N": "♘", "B": "♗", "R": "♖", "Q": "♕", "K": "♔",
    "p": "♟", "n": "♞", "b": "♝", "r": "♜", "q": "♛", "k": "♚",
}
# a1 is a dark square. Text symbols render consistently on Telegram buttons.
EMPTY_SQUARES = ("⬛", "⬜")


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
        ranks = range(8)
        files = range(7, -1, -1)
    else:
        ranks = range(7, -1, -1)
        files = range(8)
    return [chess.square(file_index, rank_index) for rank_index in ranks for file_index in files]


def _square_label(board: chess.Board, square: int, *, selected: str | None, targets: set[str]) -> str:
    name = chess.square_name(square)
    piece = board.piece_at(square)
    symbol = PIECE_SYMBOLS.get(piece.symbol(), "·") if piece else None
    if piece is not None:
        # Force text presentation: solid/hollow pieces identify black/white
        # consistently even on clients that otherwise turn the pawn blue.
        return str(symbol) + "\ufe0e"
    if name in targets:
        return "•"
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
            InlineKeyboardButton("Maçı başlat", callback_data=f"chess:{round_id}:start"),
        ])
        rows.append([InlineKeyboardButton("Oyunu bitir", callback_data=f"chess:{round_id}:stop")])
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
            name = chess.square_name(square)
            style = ("primary" if name == selected else
                     "danger" if name in targets and board.piece_at(square) else
                     "success" if name in targets else None)
            row.append(InlineKeyboardButton(
                _square_label(board, square, selected=selected, targets=targets),
                callback_data=f"chess:{round_id}:sq:{name}",
                api_kwargs={"style": style} if style else None,
            ))
        rows.append(row)

    files = "hgfedcba" if str(state.get("turn")) == "black" else "abcdefgh"
    rows.append([InlineKeyboardButton(file.upper(), callback_data=f"chess:{round_id}:axis:{file}") for file in files])
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
    lines = ["♟️ <b>Satranç</b>"]
    if status == "lobby":
        lines += [
            "",
            "İki kişilik oyun · " + ("Rakip bekleniyor" if len(players) < 2 else "Maç başlamaya hazır"),
            f"♔ Beyaz: <b>{_player_label(state, 'white')}</b>",
            f"♚ Siyah: <b>{_player_label(state, 'black')}</b>",
            "",
            "Rengini seç. İkinci oyuncu katıldığında Maçı başlat düğmesini kullan.",
        ]
    else:
        turn = str(state.get("turn") or "white")
        lines += [
            "",
            f"<b>{'Beyaz' if turn == 'white' else 'Siyah'} altta</b> · sütunlar {'a → h' if turn == 'white' else 'h → a'}",
            f"Üst sıra {'1' if turn == 'black' else '8'} · Alt sıra {'8' if turn == 'black' else '1'}",
            f"♔ <b>{_player_label(state, 'white')}</b> · Beyaz",
            f"♚ <b>{_player_label(state, 'black')}</b> · Siyah",
        ]
        if status == "playing":
            lines += ["", f"Sıra: <b>{_player_label(state, turn)}</b> · {'Beyaz' if turn == 'white' else 'Siyah'}"]
            if state.get("selected_square"):
                lines.append(f"Seçili: <code>{escape(str(state['selected_square']))}</code> · Mavi kare")
                targets = ", ".join(escape(str(target)) for target in state.get("legal_targets", []))
                lines.append(f"Hedefler: <code>{targets or '—'}</code>")
                lines.append("Yeşil: boş hedef · Kırmızı: taş al")
            if state.get("draw_offer_by"):
                lines.append("Beraberlik teklifi yanıt bekliyor.")
            try:
                if chess_board(state).is_check():
                    lines.append("Şah çekildi. Şahını koruyan bir hamle seç.")
            except ValueError:
                lines.append("Tahta şu anda gösterilemiyor. Biraz sonra yeniden dene.")
        else:
            result = str(state.get("result") or "—")
            reason = {
                "checkmate": "Şah mat", "stalemate": "Pat", "insufficient_material": "Mat için yeterli taş yok",
                "seventyfive_moves": "75 hamle kuralı", "fivefold_repetition": "Beş kez konum tekrarı",
                "fifty_moves": "50 hamle kuralı", "threefold_repetition": "Üç kez konum tekrarı",
                "timeout": "Süre doldu", "resignation": "Oyuncu pes etti", "agreement": "Beraberlik kabul edildi",
            }.get(str(state.get("termination") or ""))
            lines += ["", f"<b>Oyun sona erdi</b> · Sonuç: <code>{escape(result)}</code>"]
            if reason:
                lines.append(f"Neden: {reason}")
            winner = state.get("winner_id")
            if winner:
                winner_item = next((item for item in players if int(item.get("id", 0) or 0) == int(winner)), None)
                lines.append(f"🏆 Kazanan: <b>{escape(str((winner_item or {}).get('name') or 'Oyuncu'))}</b>")
        lines += [
            f"Hamle: <b>{len(state.get('moves') or [])}</b>" + (f" · Son hamle: <code>{escape(str(state.get('last_move')))}</code>" if state.get("last_move") else ""),
        ]
    if note:
        lines += ["", escape(str(note))]
    return "\n".join(lines)


__all__ = ["chess_keyboard", "render_chess_text"]
