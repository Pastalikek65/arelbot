"""Dependency-free culture category aliases shared by command and inline UX."""

from __future__ import annotations

import unicodedata


CULTURE_CATEGORY_ALIASES = {
    "film": "movie",
    "filmler": "movie",
    "movie": "movie",
    "dizi": "series",
    "diziler": "series",
    "series": "series",
    "kitap": "book",
    "kitaplar": "book",
    "book": "book",
    "müzik": "music",
    "muzik": "music",
    "şarkı": "music",
    "sarki": "music",
    "music": "music",
}


def normalize_culture_category(value: object) -> str | None:
    raw = " ".join(str(value or "").split()).casefold()
    folded = unicodedata.normalize("NFKD", raw)
    folded = "".join(char for char in folded if not unicodedata.combining(char))
    folded = folded.replace("ı", "i")
    return CULTURE_CATEGORY_ALIASES.get(folded)
