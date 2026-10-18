from src.games.hangman import apply_guess, available_keys, reveal_hint
from src.services.hangman_ui import hangman_keyboard, render_hangman_text


def _state():
    return {
        "word": "KEDI",
        "hidden": ["_", "_", "_", "_"],
        "lives": 6,
        "max_lives": 6,
        "guessed_letters": [],
        "wrong_guesses": 0,
        "hints_used": 0,
        "difficulty_label": "Normal",
    }


def test_guess_updates_mask_and_duplicate_is_idempotent():
    state = _state()

    assert apply_guess(state, "k")["status"] == "hit"
    assert state["hidden"] == ["K", "_", "_", "_"]
    assert apply_guess(state, "k")["status"] == "duplicate"
    assert state["lives"] == 6


def test_miss_reduces_life_and_hint_reveals_without_reducing_life():
    state = _state()

    assert apply_guess(state, "z")["status"] == "miss"
    assert state["lives"] == 5
    assert reveal_hint(state)["status"] == "hint"
    assert state["hints_used"] == 1
    assert state["lives"] == 5


def test_turkish_keyboard_has_callback_buttons_and_hides_used_normalized_letters():
    state = _state()
    apply_guess(state, "c_tr")
    keys = available_keys(state)
    labels = {label for _token, label in keys}
    assert "C" not in labels
    assert "Ç" not in labels

    markup = hangman_keyboard(state)
    callbacks = [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
    ]
    assert "hangman_hint" in callbacks
    assert "hangman_stop" in callbacks
    assert all(callback.startswith("hangman_guess:") or callback.startswith("hangman_") for callback in callbacks)


def test_hangman_render_is_bounded_and_contains_round_context():
    text = render_hangman_text(_state())
    assert "ADAM ASMACA" in text
    assert "6/6" in text
    assert "Normal" in text
