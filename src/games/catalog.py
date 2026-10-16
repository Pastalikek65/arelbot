"""Shared local game catalog metadata.

The catalog is deliberately dependency-free so Telegram menus, the tool
runtime, and replay tests all use the same canonical category identifiers.
"""

from __future__ import annotations

import unicodedata


WORD_GAME_CATEGORY_LABELS = {
    "hayvanlar": "Hayvanlar",
    "yemekler": "Yemekler",
    # The ids below are kept for replaying old persisted rounds; labels are
    # deliberately correct UTF-8 even when an older snapshot used U+FFFD.
    "ÅŸehirler": "Åehirler",
    "doÄŸa": "DoÄŸa",
    "bilim": "Bilim",
    "spor": "Spor",
    "sanat": "Sanat",
    "tarih": "Tarih",
    "teknoloji": "Teknoloji",
    "meslekler": "Meslekler",
    "uzay": "Uzay",
    "genel": "Genel",
}


def _fold(value: object) -> str:
    text = str(value or "").strip().casefold().replace("Ä±", "i")
    return "".join(
        char for char in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(char)
    )


_CATEGORY_ALIASES = {
    _fold(canonical): canonical for canonical in WORD_GAME_CATEGORY_LABELS
}
_CATEGORY_ALIASES.update(
    {
        "sehir": "ÅŸehirler",
        "sehirler": "ÅŸehirler",
        "sehir hayati": "ÅŸehirler",
        "ÅŸehir": "ÅŸehirler",
        "ÅŸehirler": "ÅŸehirler",
        "dogal": "doÄŸa",
        "doga": "doÄŸa",
        "doÄŸa": "doÄŸa",
        "uzay bilimleri": "uzay",
        "meslek": "meslekler",
        "meslek hayati": "meslekler",
    }
)


def normalize_word_game_category(value: object) -> str | None:
    """Return a canonical category for Turkish and ASCII aliases."""

    return _CATEGORY_ALIASES.get(_fold(value))
