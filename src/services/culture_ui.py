"""Pure presentation helpers for the unified ``/kultur`` surface.

The helpers deliberately contain no provider or Telegram client calls.  They
only build bounded, owner-scoped callback payloads so the command and callback
handlers can share one Turkish category picker.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from src.features.culture_categories import normalize_culture_category


CULTURE_CATEGORIES: tuple[tuple[str, str, str], ...] = (
    ("movie", "ğŸ¬ Film", "film"),
    ("series", "ğŸ“º Dizi", "dizi"),
    ("book", "ğŸ“š Kitap", "kitap"),
    ("music", "ğŸµ MÃ¼zik", "mÃ¼zik"),
)

CULTURE_CATEGORY_LABELS = {key: label for key, label, _ in CULTURE_CATEGORIES}


def culture_category_panel(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Render the category picker for the unified command."""

    owner = int(user_id)
    keyboard = [
        [
            InlineKeyboardButton(
                left_label,
                callback_data=f"culture:{owner}:{left_key}",
            ),
            InlineKeyboardButton(
                right_label,
                callback_data=f"culture:{owner}:{right_key}",
            ),
        ]
        for (left_key, left_label, _), (right_key, right_label, _) in zip(
            CULTURE_CATEGORIES[::2], CULTURE_CATEGORIES[1::2]
        )
    ]
    return (
        "ğŸ­ <b>KÃ¼ltÃ¼r Ã¶nerileri</b>\n\n"
        "Film, dizi, kitap veya mÃ¼zik seÃ§; istersen tÃ¼r ya da konu da ekle. "
        "Arel kaynaklÄ± sonuÃ§larÄ± toparlar, kristal kÃ¼reyi deÄŸil.",
        InlineKeyboardMarkup(keyboard),
    )


def culture_category_help(user_id: int, category: str) -> tuple[str, InlineKeyboardMarkup]:
    """Render a command hint after a category button is selected."""

    owner = int(user_id)
    normalized = normalize_culture_category(category) or ""
    labels = {
        "movie": ("film", "bilim kurgu"),
        "series": ("dizi", "komedi"),
        "book": ("kitap", "fantastik"),
        "music": ("mÃ¼zik", "rock"),
    }
    if normalized not in labels:
        return culture_category_panel(owner)
    command, example = labels[normalized]
    title = CULTURE_CATEGORY_LABELS[normalized]
    return (
        f"{title} seÃ§ildi.\n\n"
        f"<code>/kultur {command} {example}</code>\n"
        f"veya doÄŸrudan <code>/kultur {command}</code> yazabilirsin.",
        InlineKeyboardMarkup(
            [[InlineKeyboardButton("ğŸ­ Kategori deÄŸiÅŸtir", callback_data=f"culture_menu:{owner}")]]
        ),
    )
