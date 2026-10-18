"""Telegram presentation helpers for the word-guessing game."""

from __future__ import annotations

from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def _scoped(prefix: str, state: dict) -> str:
    round_id = str(state.get("round_id") or "").strip()
    return f"{prefix}:{round_id}" if round_id else prefix


def word_guess_keyboard(state: dict) -> InlineKeyboardMarkup:
    """Return restart-safe controls for an active guessing round."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("💡 İpucu", callback_data=_scoped("word_hint", state)),
                InlineKeyboardButton("⏭️ Pas geç", callback_data=_scoped("word_skip", state)),
            ],
            [InlineKeyboardButton("🛑 Oyunu bitir", callback_data=_scoped("word_guess_stop", state))],
        ]
    )


def render_word_guess_text(state: dict, *, note: str | None = None) -> str:
    """Render public clue state; the target answer never enters this view."""
    category = escape(str(state.get("category", "Genel")))
    description = escape(str(state.get("description", "Tanım hazırlanıyor.")))
    attempts = int(state.get("attempts", 0) or 0)
    hints_used = int(state.get("hint_index", 0) or 0)
    hint_total = len(state.get("hints", []) or [])
    difficulty = escape(str(state.get("difficulty_label", state.get("difficulty", "Normal"))))
    note_text = f"\n\n{escape(str(note))}" if note else ""
    return (
        "🗣️ <b>KELİME ANLATMACA</b>\n\n"
        f"Kategori: <b>{category}</b> · Zorluk: <b>{difficulty}</b>\n"
        f"📝 <b>Tanım:</b>\n<i>{description}</i>\n\n"
        f"Deneme: <b>{attempts}</b> · İpucu: <b>{hints_used}/{hint_total}</b>\n"
        "Cevabı mesaj olarak yaz; panelden ipucu alabilir, pas geçebilir veya turu kapatabilirsin."
        f"{note_text}"
    )
