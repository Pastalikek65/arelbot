"""Presentation must preserve game protocol, privacy and board information."""

from copy import deepcopy

import pytest

from src.telegram.button_theme import decorate_markup


@pytest.mark.parametrize(
    "text,callback,icon,style",
    [
        ("Katıl", "wa:a:join", "✅", "success"),
        ("Başlat", "ra:a:start", "⚡️", "success"),
        ("Oyunu bitir", "game_stop_all", "🏁", "danger"),
        ("Ana menü", "menu_main", "🏠", None),
        ("Özel sohbeti hazırla", "sa:a:ready", "🔐", None),
        ("Harf Maratonu", "game_start_harf_maratonu", "🔤", None),
        ("Dedektif", "game_start_dedektif", "🔎", None),
        ("Oyun salonu", "menu_games", "🎮", "primary"),
    ],
)
def test_semantic_icons_preserve_callbacks(text, callback, icon, style):
    original = {"inline_keyboard": [[{"text": text, "callback_data": callback}]]}
    saved = deepcopy(original)
    themed = decorate_markup(original, premium=False)["inline_keyboard"][0][0]
    assert themed["text"].startswith(icon + " ")
    assert themed["callback_data"] == callback
    assert themed.get("style") == style
    assert original == saved


def test_custom_icon_comes_from_verified_catalog_and_has_clean_text():
    original = {"inline_keyboard": [[{"text": "🎮 Oyun salonu", "callback_data": "menu_games"}]]}
    themed = decorate_markup(original, premium=True)["inline_keyboard"][0][0]
    assert themed["icon_custom_emoji_id"] == "5309950797704865693"
    assert themed["text"] == "Oyun salonu"
    assert decorate_markup(themed_markup := {"inline_keyboard": [[themed]]}, premium=True) == themed_markup


@pytest.mark.parametrize("text", ["1", "A6", "♟", "🟥", "⬜", "a", "Evet", "Hayır", "Mira", "kalem", "1. sıra"])
def test_board_cells_cards_votes_and_answers_are_not_decorated(text):
    original = {"inline_keyboard": [[{"text": text, "callback_data": "opaque:game:data"}]]}
    assert decorate_markup(original, premium=True) == original


def test_url_pay_copy_and_inline_fields_are_not_rewritten():
    buttons = [
        {"text": "📚 Kaynaklar", "url": "https://example.com"},
        {"text": "100 XTR", "pay": True},
        {"text": "Kopyala", "copy_text": {"text": "payload"}},
        {"text": "Paylaş", "switch_inline_query": ""},
    ]
    result = decorate_markup({"inline_keyboard": [buttons]}, premium=True)["inline_keyboard"][0]
    for before, after in zip(buttons, result):
        assert {k: v for k, v in after.items() if k not in {"text", "icon_custom_emoji_id", "style"}} == {
            k: v for k, v in before.items() if k != "text"
        }


def test_explicit_button_visuals_and_reply_keyboard_are_preserved():
    original = {
        "inline_keyboard": [
            [{"text": "Katıl", "callback_data": "j", "icon_custom_emoji_id": "12345", "style": "primary"}]
        ]
    }
    assert decorate_markup(original, premium=True) == original
    reply = {"keyboard": [[{"text": "telefon", "request_contact": True}]], "resize_keyboard": True}
    assert decorate_markup(reply, premium=True) == reply


def test_all_forty_games_have_distinct_semantic_icon_mapping():
    from src.games.catalog import GAME_DEFINITIONS
    from src.telegram.button_theme import GAME_ICONS

    assert set(GAME_DEFINITIONS) <= set(GAME_ICONS)
    for key in GAME_DEFINITIONS:
        button = {"text": GAME_DEFINITIONS[key].label, "callback_data": "game_start_" + key}
        result = decorate_markup({"inline_keyboard": [[button]]}, premium=False)["inline_keyboard"][0][0]
        assert result["text"].startswith(GAME_ICONS[key] + " ")
        assert result["callback_data"] == button["callback_data"]


def test_settings_and_help_get_icons_but_existing_language_flags_stay():
    buttons = [
        {"text": "İçerik denetimi: açık", "callback_data": "ayar_mod_toggle"},
        {"text": "Yeni grup ve mantık oyunları", "callback_data": "help_feat_arcade"},
        {"text": "🇹🇷 Türkçe", "callback_data": "lang_tr"},
    ]
    result = decorate_markup({"inline_keyboard": [buttons]}, premium=False)["inline_keyboard"][0]
    assert result[0]["text"].startswith("⚙️ ")
    assert result[1]["text"].startswith("💡 ")
    assert result[2] == buttons[2]


def test_request_formatting_does_not_open_catalog_files(monkeypatch):
    from pathlib import Path

    def forbidden_read(*args, **kwargs):
        raise AssertionError("request-time file I/O")

    monkeypatch.setattr(Path, "read_text", forbidden_read)
    markup = {"inline_keyboard": [[{"text": "Katıl", "callback_data": "join"}]]}
    assert decorate_markup(markup, premium=True)["inline_keyboard"][0][0]["icon_custom_emoji_id"]
