"""Pure presentation helpers for the unified ``/kultur`` surface.

The helpers deliberately contain no provider or Telegram client calls.  They
only build bounded, owner-scoped callback payloads so the command and callback
handlers can share one Turkish category picker.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from src.features.culture_categories import normalize_culture_category


CULTURE_CATEGORIES: tuple[tuple[str, str, str], ...] = (
    ("movie", "🎬 Film", "film"),
    ("series", "📺 Dizi", "dizi"),
    ("book", "📚 Kitap", "kitap"),
    ("music", "🎵 Müzik", "müzik"),
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
        "🎭 <b>Kültür önerileri</b>\n\n"
        "Film, dizi, kitap veya müzik seç. Daha ilgili sonuçlar için "
        "bir eser, sanatçı veya konu adı ekleyebilirsin.",
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
        "music": ("müzik", "rock"),
    }
    if normalized not in labels:
        return culture_category_panel(owner)
    command, example = labels[normalized]
    title = CULTURE_CATEGORY_LABELS[normalized]
    return (
        f"{title} seçildi.\n\n"
        f"<code>/kultur {command} {example}</code>\n"
        f"veya doğrudan <code>/kultur {command}</code> yazabilirsin.",
        InlineKeyboardMarkup(
            [[InlineKeyboardButton("🎭 Kategori değiştir", callback_data=f"culture_menu:{owner}")]]
        ),
    )
