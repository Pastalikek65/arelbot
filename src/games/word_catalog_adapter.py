"""Adapters that bridge the curated word catalog to legacy game state.

The Telegram handlers still persist the historical ``word_guess`` shape.  This
module keeps that wire format stable while centralising draw, category and clue
selection in :mod:`src.games.word_catalog`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from src.games.word_catalog import (
    WordCatalog,
    WordEntry,
    normalize_difficulty,
    normalize_turkish,
    safe_word_entry,
)


_CATEGORY_BY_KEY = {
    "hayvanlar": "hayvanlar",
    "yemekler": "yemekler",
    "sehirler": "sehirler",
    "doga": "doga",
    "bilim": "bilim",
    "spor": "spor",
    "sanat": "sanat",
    "tarih": "tarih",
    "teknoloji": "teknoloji",
    "meslekler": "meslekler",
    "uzay": "uzay",
    "genel": "genel",
    "genelkultur": "genel",
    "eskikelimeler": "tarih",
    "egitim": "genel",
    "saglik": "genel",
    "cografya": "cografya",
}


def canonical_category(value: object | None) -> str | None:
    """Map Turkish/legacy labels to a stable catalog category key."""

    key = normalize_turkish(value)
    if not key:
        return None
    return _CATEGORY_BY_KEY.get(key, key)


def catalog_difficulty(value: object | None) -> str | None:
    """Return the Turkish difficulty token expected by ``WordCatalog``."""

    if value is None:
        return None
    normalized = normalize_difficulty(value)
    return normalized


def _derived_difficulty(word: str) -> str:
    """Give entries from old snapshots a bounded difficulty.

    Old ``words.json`` records did not carry difficulty.  Length is a stable,
    deterministic fallback and avoids silently treating the whole legacy bank
    as one difficulty bucket.
    """

    length = len(normalize_turkish(word))
    if length <= 5:
        return "kolay"
    if length <= 8:
        return "orta"
    if length <= 12:
        return "zor"
    return "uzman"


def _to_entry(raw: Mapping[str, Any], index: int) -> WordEntry | None:
    word = str(raw.get("word") or "").strip()
    if not word:
        return None
    category = canonical_category(raw.get("category")) or "genel"
    difficulty = raw.get("difficulty") or _derived_difficulty(word)
    description = str(raw.get("description") or "").strip()
    if len(description) < 15:
        description = "Günlük hayatta veya genel kültürde karşılaşabileceğin bir kavramdır."
    hints = tuple(str(item).strip() for item in (raw.get("hints") or ()) if str(item).strip())
    tags = tuple(str(item).strip() for item in (raw.get("tags") or ()) if str(item).strip())
    entry = WordEntry(
        key=normalize_turkish(raw.get("key") or word) or f"word-{index}",
        word=word,
        category=category,
        difficulty=str(difficulty),
        description=description,
        hints=hints,
        tags=tags,
    )
    return entry if safe_word_entry(entry) else None


@dataclass(frozen=True, slots=True)
class CatalogDraw:
    """Legacy-compatible projection of a catalog draw."""

    entry: WordEntry
    cycle_reset: bool
    remaining: int

    def to_legacy(self, catalog: WordCatalog) -> dict[str, Any]:
        return {
            "word": self.entry.word,
            "category": self.entry.category,
            "description": self.entry.description,
            "hints": list(catalog.hints_for(self.entry)),
            "difficulty": self.entry.difficulty,
            "catalog_key": self.entry.key,
            "catalog_cycle_reset": self.cycle_reset,
            "catalog_remaining": self.remaining,
        }


class WordCatalogAdapter:
    """Selection facade used by Anlatmaca and other word rounds.

    ``WordGuessGame`` can continue to expose its old dictionaries while this
    facade gives it deterministic, scope-aware selection.  Invalid or unsafe
    records are discarded at the boundary; no answer or unsafe clue can enter
    an active round.
    """

    def __init__(self, records: Iterable[Mapping[str, Any]], *, seed: int = 0) -> None:
        entries = []
        for index, record in enumerate(records):
            if isinstance(record, Mapping):
                entry = _to_entry(record, index)
                if entry is not None:
                    entries.append(entry)
        if not entries:
            raise ValueError("word_catalog_adapter_empty")
        self.catalog = WordCatalog(entries=entries, seed=int(seed))
        self._by_key = {entry.key: entry for entry in self.catalog.entries()}

    @staticmethod
    def category(value: object | None) -> str | None:
        return canonical_category(value)

    @staticmethod
    def difficulty(value: object | None) -> str | None:
        return catalog_difficulty(value)

    def draw(
        self,
        *,
        scope: str,
        category: object | None = None,
        difficulty: object | None = None,
    ) -> CatalogDraw:
        result = self.catalog.draw(
            scope=str(scope),
            category=canonical_category(category),
            difficulty=catalog_difficulty(difficulty),
        )
        return CatalogDraw(result.entry, result.cycle_reset, result.remaining)

    def hints(self, entry: WordEntry | str) -> tuple[str, ...]:
        return self.catalog.hints_for(entry)

    def categories(self) -> tuple[str, ...]:
        return self.catalog.categories()


__all__ = [
    "CatalogDraw",
    "WordCatalogAdapter",
    "canonical_category",
    "catalog_difficulty",
]
