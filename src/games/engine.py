"""Shared, deterministic rules for Arelbot's interactive games."""

from __future__ import annotations

from dataclasses import dataclass
import unicodedata
from typing import Iterable

from .catalog import normalize_word_game_category


@dataclass(frozen=True)
class DifficultyProfile:
    key: str
    label: str
    min_target: int
    max_target: int
    min_word_length: int
    max_word_length: int
    time_limit_seconds: int
    score_multiplier: float
    hint_penalty: int


DIFFICULTIES: dict[str, DifficultyProfile] = {
    "easy": DifficultyProfile("easy", "Kolay", 5, 7, 3, 6, 300, 0.8, 1),
    "normal": DifficultyProfile("normal", "Normal", 7, 10, 3, 10, 240, 1.0, 2),
    "hard": DifficultyProfile("hard", "Zor", 8, 12, 4, 14, 180, 1.35, 3),
    "expert": DifficultyProfile("expert", "Uzman", 10, 15, 5, 20, 150, 1.75, 4),
}


def _fold(value: object) -> str:
    text = str(value or "").strip().casefold().replace("Ä±", "i")
    return "".join(
        char for char in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(char)
    )


_DIFFICULTY_ALIASES = {
    "easy": "easy",
    "kolay": "easy",
    "normal": "normal",
    "orta": "normal",
    "medium": "normal",
    "hard": "hard",
    "zor": "hard",
    "expert": "expert",
    "uzman": "expert",
}


def normalize_difficulty(value: object) -> DifficultyProfile:
    """Resolve Turkish and ASCII difficulty names to one bounded profile."""

    key = _DIFFICULTY_ALIASES.get(_fold(value), "normal")
    return DIFFICULTIES[key]


def parse_word_game_request(tokens: Iterable[object] | None) -> tuple[str | None, DifficultyProfile]:
    """Split `/kelimeoyunu [kategori] [zorluk]` arguments safely."""

    values = [str(token).strip() for token in (tokens or []) if str(token).strip()]
    difficulty = normalize_difficulty(None)
    remaining: list[str] = []
    for value in values:
        folded = _fold(value)
        if folded in _DIFFICULTY_ALIASES:
            difficulty = normalize_difficulty(value)
        else:
            remaining.append(value)
    category = normalize_word_game_category(" ".join(remaining)) if remaining else None
    return category, difficulty


def _bounded_score(value: float) -> int:
    return max(1, min(100, int(round(value))))


def calculate_word_game_score(
    word: str,
    elapsed_seconds: float,
    difficulty: object = "normal",
    combo: int = 1,
    hints_used: int = 0,
) -> int:
    """Calculate a reproducible score for a word accepted from a letter rack."""

    profile = difficulty if isinstance(difficulty, DifficultyProfile) else normalize_difficulty(difficulty)
    length = max(1, len(str(word or "").strip()))
    elapsed = max(0.0, float(elapsed_seconds or 0.0))
    combo_bonus = min(20, max(0, int(combo) - 1) * 2)
    speed_bonus = max(0, int((profile.time_limit_seconds - elapsed) / 10))
    hint_penalty = max(0, int(hints_used)) * profile.hint_penalty
    raw = (length * 2) + combo_bonus + speed_bonus - hint_penalty
    return _bounded_score(raw * profile.score_multiplier)


def calculate_word_guess_score(
    elapsed_seconds: float,
    difficulty: object = "normal",
    attempts: int = 1,
    hints_used: int = 0,
) -> int:
    """Calculate an answer score for the clue-based anlatmaca round."""

    profile = difficulty if isinstance(difficulty, DifficultyProfile) else normalize_difficulty(difficulty)
    elapsed = max(0.0, float(elapsed_seconds or 0.0))
    attempt_bonus = max(0, 6 - max(1, int(attempts))) * 2
    speed_bonus = max(0, int((profile.time_limit_seconds - elapsed) / 15))
    hint_penalty = max(0, int(hints_used)) * profile.hint_penalty
    raw = 10 + attempt_bonus + speed_bonus - hint_penalty
    return _bounded_score(raw * profile.score_multiplier)


def calculate_hangman_score(
    elapsed_seconds: float,
    difficulty: object = "normal",
    lives_remaining: int = 0,
    max_lives: int = 6,
    wrong_guesses: int = 0,
    hints_used: int = 0,
) -> int:
    """Calculate a hangman score from speed, accuracy, and difficulty."""

    profile = difficulty if isinstance(difficulty, DifficultyProfile) else normalize_difficulty(difficulty)
    elapsed = max(0.0, float(elapsed_seconds or 0.0))
    maximum = max(1, int(max_lives or 1))
    remaining = max(0, min(maximum, int(lives_remaining or 0)))
    accuracy_bonus = int((remaining / maximum) * 20)
    speed_bonus = max(0, int((profile.time_limit_seconds - elapsed) / 15))
    wrong_penalty = max(0, int(wrong_guesses)) * profile.hint_penalty
    hint_penalty = max(0, int(hints_used)) * profile.hint_penalty
    raw = 10 + accuracy_bonus + speed_bonus - wrong_penalty - hint_penalty
    return _bounded_score(raw * profile.score_multiplier)

