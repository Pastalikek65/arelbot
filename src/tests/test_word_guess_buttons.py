from src.services.word_guess_ui import render_word_guess_text, word_guess_keyboard


def _state():
    return {
        "category": "Bilim",
        "description": "Bir ÅŸeyi aÃ§Ä±klayan, gÃ¼nlÃ¼k hayatta kullanÄ±lan gÃ¼venli bir tanÄ±m.",
        "attempts": 2,
        "hint_index": 1,
        "hints": ["Ä°lk ipucu"],
        "difficulty": "normal",
        "time_limit": 240,
    }


def test_word_guess_panel_exposes_hint_skip_and_stop_controls():
    markup = word_guess_keyboard(_state())
    callbacks = [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
    ]

    assert {"word_hint", "word_skip", "word_guess_stop"}.issubset(callbacks)


def test_word_guess_render_does_not_expose_answer_and_shows_progress():
    text = render_word_guess_text(_state(), note="ğŸ’¡ Yeni ipucu gÃ¶sterildi.")

    assert "Bilim" in text
    assert "Deneme: <b>2</b>" in text
    assert "Ä°pucu: <b>1/1</b>" in text
    assert "Bir ÅŸeyi aÃ§Ä±klayan" in text
    assert "word" not in text.casefold()
