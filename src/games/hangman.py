"""Pure hangman round rules shared by text and inline-button inputs."""

from __future__ import annotations

from src.games.vocabulary import normalize_game_word


# Tokens stay ASCII in callback data.  Turkish display keys normalize to the
# same fair comparison alphabet used by the local vocabulary.
HANGMAN_KEYS: tuple[tuple[str, str, str], ...] = (
    ("a", "A", "a"),
    ("b", "B", "b"),
    ("c", "C", "c"),
    ("c_tr", "Ç", "c"),
    ("d", "D", "d"),
    ("e", "E", "e"),
    ("f", "F", "f"),
    ("g", "G", "g"),
    ("g_tr", "Ğ", "g"),
    ("h", "H", "h"),
    ("i", "I/İ", "i"),
    ("j", "J", "j"),
    ("k", "K", "k"),
    ("l", "L", "l"),
    ("m", "M", "m"),
    ("n", "N", "n"),
    ("o", "O", "o"),
    ("o_tr", "Ö", "o"),
    ("p", "P", "p"),
    ("r", "R", "r"),
    ("s", "S", "s"),
    ("s_tr", "Ş", "s"),
    ("t", "T", "t"),
    ("u", "U", "u"),
    ("u_tr", "Ü", "u"),
    ("v", "V", "v"),
    ("y", "Y", "y"),
    ("z", "Z", "z"),
)

_TOKEN_TO_LETTER = {token: normalized for token, _label, normalized in HANGMAN_KEYS}


def _word(state: dict) -> str:
    return normalize_game_word(state.get("word", ""))


def _finished(state: dict) -> bool:
    """Check terminal rounds without normalizing/mutating their saved state."""
    if int(state.get("lives", 0) or 0) <= 0:
        return True
    word = _word(state)
    guessed = {normalize_game_word(value) for value in state.get("guessed_letters", [])}
    return bool(word) and all(letter in guessed for letter in word)


def _guessed(state: dict) -> list[str]:
    values: list[str] = []
    for value in state.get("guessed_letters", []):
        normalized = normalize_game_word(value)
        if len(normalized) == 1 and normalized not in values:
            values.append(normalized)
    state["guessed_letters"] = values
    return values


def _refresh_hidden(state: dict) -> None:
    word = _word(state)
    guessed = set(_guessed(state))
    hidden = state.get("hidden")
    if not isinstance(hidden, list) or len(hidden) != len(word):
        hidden = ["_"] * len(word)
        state["hidden"] = hidden
    for index, letter in enumerate(word):
        if letter in guessed:
            hidden[index] = letter.upper()


def apply_guess(state: dict, token: object) -> dict:
    """Apply one letter token and return a bounded result envelope."""

    if _finished(state):
        return {"status": "invalid", "reason": "round_finished", "state": state}

    raw = str(token or "").strip().casefold()
    letter = _TOKEN_TO_LETTER.get(raw) or normalize_game_word(raw)
    if len(letter) != 1 or not letter.isalpha():
        return {"status": "invalid", "state": state}

    guessed = _guessed(state)
    if letter in guessed:
        return {"status": "duplicate", "letter": letter, "state": state}

    guessed.append(letter)
    state["guessed_letters"] = guessed
    _refresh_hidden(state)
    word = _word(state)
    if letter not in word:
        state["lives"] = max(0, int(state.get("lives", 0) or 0) - 1)
        state["wrong_guesses"] = int(state.get("wrong_guesses", 0) or 0) + 1

    if word and "_" not in state["hidden"]:
        status = "won"
    elif int(state.get("lives", 0) or 0) <= 0:
        status = "lost"
    else:
        status = "hit" if letter in word else "miss"
    return {"status": status, "letter": letter, "state": state}


def reveal_hint(state: dict) -> dict:
    """Reveal one unseen character, charging a hint against the round."""

    if _finished(state):
        return {"status": "no_hint", "reason": "round_finished", "state": state}

    word = _word(state)
    guessed = set(_guessed(state))
    candidates = [letter for letter in word if letter not in guessed]
    if not candidates:
        return {"status": "no_hint", "state": state}
    letter = candidates[0]
    guessed_list = _guessed(state)
    guessed_list.append(letter)
    state["guessed_letters"] = guessed_list
    state["hints_used"] = int(state.get("hints_used", 0) or 0) + 1
    _refresh_hidden(state)
    status = "won" if word and "_" not in state["hidden"] else "hint"
    return {"status": status, "letter": letter, "state": state}


def available_keys(state: dict) -> list[tuple[str, str]]:
    """Return callback tokens and labels that are still playable."""

    if _finished(state):
        return []

    guessed = set(_guessed(state))
    return [
        (token, label)
        for token, label, normalized in HANGMAN_KEYS
        if normalized not in guessed
    ]
