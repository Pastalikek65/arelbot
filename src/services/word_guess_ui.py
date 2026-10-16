"""Telegram presentation helpers for the word-guessing game."""

from __future__ import annotations

from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def word_guess_keyboard(state: dict) -> InlineKeyboardMarkup:
    """Return the restart-safe controls for an active guessing round."""

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("ğŸ’¡ Ä°pucu", callback_data="word_hint"),
                InlineKeyboardButton("â­ï¸ Pas geÃ§", callback_data="word_skip"),
            ],
            [InlineKeyboardButton("ğŸ›‘ Oyunu bitir", callback_data="word_guess_stop")],
        ]
    )


def render_word_guess_text(state: dict, *, note: str | None = None) -> str:
    """Render only public clue state; the target answer never enters this view."""

    category = escape(str(state.get("category", "Genel")))
    description = escape(str(state.get("description", "TanÄ±m hazÄ±rlanÄ±yor.")))
    attempts = int(state.get("attempts", 0) or 0)
    hints_used = int(state.get("hint_index", 0) or 0)
    hint_total = len(state.get("hints", []) or [])
    difficulty = escape(str(state.get("difficulty_label", state.get("difficulty", "Normal"))))
    note_text = f"\n\n{note}" if note else ""
    return (
        "ğŸ—£ï¸ <b>KELÄ°ME ANLATMACA</b>\n\n"
        f"Kategori: <b>{category}</b> Â· Zorluk: <b>{difficulty}</b>\n"
        f"ğŸ“ <b>TanÄ±m:</b>\n<i>{description}</i>\n\n"
        f"Deneme: <b>{attempts}</b> Â· Ä°pucu: <b>{hints_used}/{hint_total}</b>\n"
        "CevabÄ± mesaj olarak yaz; panelden ipucu alabilir, pas geÃ§ebilir veya turu kapatabilirsin."
        f"{note_text}"
    )
