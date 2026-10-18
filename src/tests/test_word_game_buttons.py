from src.games.word_game import reveal_word_hint, shuffle_rack
from src.services.word_game_ui import render_word_game_text, word_game_keyboard


def _state():
    return {
        "letters": "KELIMELER",
        "category": "genel",
        "difficulty_label": "Normal",
        "target_count": 3,
        "found_words": [],
        "valid_words": ["kelime", "eller", "kiler"],
        "hints_used": 0,
    }


def test_shuffle_keeps_rack_inventory_and_tracks_action():
    state = _state()
    before = sorted(state["letters"])
    shuffle_rack(state)

    assert sorted(state["letters"]) == before
    assert state["shuffle_count"] == 1


def test_word_hint_is_bounded_and_penalized_state_is_persistable():
    state = _state()
    result = reveal_word_hint(state)

    assert result["status"] == "hint"
    assert result["hint"]["length"] == 6
    assert result["hint"]["first"] == "K"
    assert result["hint"]["last"] == "E"
    assert state["hints_used"] == 1
    assert "word" not in result["hint"]


def test_word_game_panel_contains_control_callbacks_and_safe_projection():
    state = _state()
    markup = word_game_keyboard(state)
    callbacks = [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
    ]

    assert {"word_game_shuffle", "word_game_hint", "word_game_stop"}.issubset(callbacks)
    assert "KELIMELER" in render_word_game_text(state)
    assert "Bulunan: <b>0</b>" in render_word_game_text(state)
