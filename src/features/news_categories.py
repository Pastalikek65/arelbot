"""Shared, dependency-free Turkish news category normalization."""

from __future__ import annotations

import unicodedata


NEWS_CATEGORY_ALIASES = {
    "guncel": "guncel",
    "dunya": "dunya",
    "ekonomi": "ekonomi",
    "spor": "spor",
    "kultur": "kultur",
    "teknoloji": "teknoloji",
    "saglik": "saglik",
}


def normalize_news_category(value: object) -> str | None:
    """Normalize a bounded Turkish news category alias."""

    raw = " ".join(str(value or "").split()).casefold()
    folded = unicodedata.normalize("NFKD", raw)
    folded = "".join(char for char in folded if not unicodedata.combining(char))
    folded = folded.replace("ı", "i")
    return NEWS_CATEGORY_ALIASES.get(folded)
