"""Telegram presentation helpers for the letter-rack game."""

from __future__ import annotations

from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def word_game_keyboard(state: dict) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("ğŸ”€ Harfleri karÄ±ÅŸtÄ±r", callback_data="word_game_shuffle"),
            InlineKeyboardButton("ğŸ’¡ Ä°pucu", callback_data="word_game_hint"),
        ],
        [
            InlineKeyboardButton("ğŸ§© Kategori seÃ§", callback_data="game_word_categories"),
            InlineKeyboardButton("ğŸšï¸ Zorluk", callback_data="game_word_difficulty"),
        ],
        [InlineKeyboardButton("ğŸ›‘ Oyunu bitir", callback_data="word_game_stop")],
    ])


def render_word_game_text(state: dict, *, note: str | None = None) -> str:
    found = [str(value).upper() for value in state.get("found_words", [])]
    found_text = ", ".join(escape(value) for value in found[-12:]) or "henÃ¼z yok"
    note_text = f"\n\n{note}" if note else ""
    return (
        "ğŸ”  <b>KELÄ°ME TÃœRETME</b>\n\n"
        f"Kategori: <b>{escape(str(state.get('category', 'genel')).title())}</b> Â· "
        f"Zorluk: <b>{escape(str(state.get('difficulty_label', 'Normal')))}</b>\n"
        f"Harfler: <code>{escape(str(state.get('letters', '')))}</code>\n"
        f"Hedef: <b>{int(state.get('target_count', 0) or 0)}</b> kelime Â· "
        f"Bulunan: <b>{len(found)}</b> Â· Ä°pucu: <b>{int(state.get('hints_used', 0) or 0)}</b>\n"
        f"Bulunan kelimeler: <code>{found_text}</code>\n\n"
        "Bir kelime gÃ¶nder; butonlardan da harfleri karÄ±ÅŸtÄ±rabilir veya ipucu alabilirsin."
        f"{note_text}"
    )
