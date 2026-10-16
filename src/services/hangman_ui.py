"""Telegram presentation helpers for the hangman round."""

from __future__ import annotations

from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from src.games.hangman import available_keys


def hangman_keyboard(state: dict) -> InlineKeyboardMarkup:
    """Build a compact Turkish alphabet keyboard for the current round."""

    keys = available_keys(state)
    rows = []
    for index in range(0, len(keys), 7):
        rows.append([
            InlineKeyboardButton(label, callback_data=f"hangman_guess:{token}")
            for token, label in keys[index:index + 7]
        ])
    rows.append([
        InlineKeyboardButton("ğŸ’¡ Ä°pucu", callback_data="hangman_hint"),
        InlineKeyboardButton("ğŸ›‘ Oyunu bÄ±rak", callback_data="hangman_stop"),
    ])
    return InlineKeyboardMarkup(rows)


def render_hangman_text(state: dict, *, result: str | None = None) -> str:
    """Render only persisted round fields; user text is escaped at the edge."""

    hidden = " ".join(escape(str(value)) for value in state.get("hidden", []))
    guessed = ", ".join(escape(str(value).upper()) for value in state.get("guessed_letters", [])) or "â€”"
    difficulty = escape(str(state.get("difficulty_label", "Normal")))
    lives = int(state.get("lives", 0) or 0)
    max_lives = int(state.get("max_lives", lives) or lives)
    hints = int(state.get("hints_used", 0) or 0)
    header = "ğŸª‘ <b>ADAM ASMACA</b>"
    if result == "won":
        header = "ğŸ¥³ <b>ADAM ASMACA â€” KAZANDIN</b>"
    elif result == "lost":
        header = "ğŸ’€ <b>ADAM ASMACA â€” CANLAR BÄ°TTÄ°</b>"
    return (
        f"{header}\n\n"
        f"Kelime: <code>{hidden}</code>\n"
        f"Can: <b>{lives}/{max_lives}</b> Â· Zorluk: <b>{difficulty}</b>\n"
        f"Tahminler: <code>{guessed}</code> Â· Ä°pucu: <b>{hints}</b>\n\n"
        "Bir harfe bas; klavye kalan seÃ§enekleri gÃ¶sterir."
    )
