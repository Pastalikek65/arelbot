"""Telegram presentation helpers for the hangman round."""

from __future__ import annotations

from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from src.games.hangman import available_keys


def _scoped(prefix: str, state: dict) -> str:
    round_id = str(state.get("round_id") or "").strip()
    return f"{prefix}:{round_id}" if round_id else prefix


def hangman_keyboard(state: dict) -> InlineKeyboardMarkup:
    """Build a Turkish alphabet keyboard scoped to the persisted round."""
    round_id = str(state.get("round_id") or "").strip()
    keys = available_keys(state)
    rows = []
    for index in range(0, len(keys), 7):
        rows.append([
            InlineKeyboardButton(
                label,
                callback_data=(f"hangman_guess:{round_id}:{token}" if round_id else f"hangman_guess:{token}"),
            )
            for token, label in keys[index:index + 7]
        ])
    rows.append([
        InlineKeyboardButton("💡 İpucu", callback_data=_scoped("hangman_hint", state)),
        InlineKeyboardButton("Oyunu bitir", callback_data=_scoped("hangman_stop", state)),
    ])
    return InlineKeyboardMarkup(rows)


def render_hangman_text(state: dict, *, result: str | None = None) -> str:
    """Render only persisted round fields; user text is escaped at the edge."""
    hidden = " ".join(escape(str(value)) for value in state.get("hidden", []))
    guessed = ", ".join(escape(str(value).upper()) for value in state.get("guessed_letters", [])) or "—"
    difficulty = escape(str(state.get("difficulty_label", "Normal")))
    lives = int(state.get("lives", 0) or 0)
    max_lives = int(state.get("max_lives", lives) or lives)
    hints = int(state.get("hints_used", 0) or 0)
    header = "🪑 <b>Adam Asmaca</b>"
    if result == "won":
        header = "<b>Adam Asmaca · Kazandın</b>"
    elif result == "lost":
        header = "<b>Adam Asmaca · Canların bitti</b>"
    return (
        f"{header}\n\n"
        f"Kelime: <code>{hidden}</code>\n"
        f"Can: <b>{lives}/{max_lives}</b> · Zorluk: <b>{difficulty}</b>\n"
        f"Tahminler: <code>{guessed}</code> · İpucu: <b>{hints}</b>\n\n"
        + ("Yeni oyun için /adamasmaca yaz." if result in {"won", "lost"}
           else "Bir harf seç veya tek harfi mesaj olarak yaz.")
    )
