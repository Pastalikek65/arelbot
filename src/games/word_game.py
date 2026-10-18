"""Pure controls for the letter-rack word game."""

from __future__ import annotations

import random

from src.games.vocabulary import normalize_game_word


def shuffle_rack(state: dict) -> dict:
    """Shuffle the visible rack without changing its letter inventory."""

    letters = list(str(state.get("letters", "")))
    if len(letters) > 1:
        original = "".join(letters)
        for _ in range(4):
            random.shuffle(letters)
            if "".join(letters) != original:
                break
    state["letters"] = "".join(letters)
    state["shuffle_count"] = int(state.get("shuffle_count", 0) or 0) + 1
    return state


def reveal_word_hint(state: dict) -> dict:
    """Give a bounded clue without exposing a complete answer."""

    found = {normalize_game_word(word) for word in state.get("found_words", [])}
    candidates = {
        normalize_game_word(word)
        for word in state.get("valid_words", [])
        if normalize_game_word(word) not in found
    }
    history = state.get("hint_history", [])
    for word in sorted(candidates, key=lambda value: (-len(value), value)):
        # First/last would disclose the full answer for one/two-letter words.
        if len(word) < 3 or not word.isalpha():
            continue
        hint = {"length": len(word), "first": word[0].upper(), "last": word[-1].upper()}
        # Compare the displayed clue, rather than a hidden answer. Two words
        # with the same signature must not charge the player for the same tip.
        if any(isinstance(previous, dict) and all(previous.get(key) == value for key, value in hint.items()) for previous in history):
            continue
        state["hints_used"] = int(state.get("hints_used", 0) or 0) + 1
        state.setdefault("hint_history", []).append(hint)
        return {"status": "hint", "hint": hint, "state": state}
    return {"status": "no_hint", "state": state}
