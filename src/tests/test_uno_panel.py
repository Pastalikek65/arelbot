from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services.uno_panel import publish_uno_panel
from src.services.uno_ui import render_uno_text, uno_keyboard


def state():
    return {
        "status": "playing", "round_id": "test", "revision": 3,
        "chat_id": -100, "prompt_message_id": 17, "panel_turn_player_id": 1,
        "current_player_id": 2, "players": [
            {"user_id": 1, "name": "Ali", "hand": [], "score": 0},
            {"user_id": 2, "name": "Ayşe", "hand": [], "score": 0},
        ], "discard": [{"color": "red", "kind": "5"}], "current_color": "red",
        "inline_query": "uno signed", "thread_id": 42,
    }


@pytest.mark.asyncio
async def test_new_turn_sends_recent_panel_with_turn_and_hand_button_and_retires_old():
    game = state()
    service = SimpleNamespace(set_prompt_message=AsyncMock(), mark_panel_delivered=AsyncMock())
    bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=18)),
                          delete_message=AsyncMock(), edit_message_text=AsyncMock())
    panel_id = await publish_uno_panel(bot, service, -100, game)
    assert panel_id == 18
    assert bot.send_message.await_args.kwargs["message_thread_id"] == 42
    text = bot.send_message.await_args.kwargs["text"]
    assert "Sıra: <b>Ayşe</b>" in text
    assert "Oyuncular · maç puanları" not in text
    assert len(text) < 180
    buttons = [button for row in bot.send_message.await_args.kwargs["reply_markup"].inline_keyboard
               for button in row]
    assert buttons[0].text == "🃏 Kartları gör"
    assert all("Sıradaki:" not in button.text for button in buttons)
    service.set_prompt_message.assert_awaited_once_with(chat_id=-100, message_id=18,
                                                        expected_revision=3)
    service.mark_panel_delivered.assert_awaited_once_with(-100, 3, 18)
    bot.delete_message.assert_awaited_once_with(chat_id=-100, message_id=17)
    bot.edit_message_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_same_turn_edits_existing_panel_without_group_message():
    game = state()
    game["panel_turn_player_id"] = 2
    service = SimpleNamespace(set_prompt_message=AsyncMock(), mark_panel_delivered=AsyncMock())
    bot = SimpleNamespace(send_message=AsyncMock(), delete_message=AsyncMock(),
                          edit_message_text=AsyncMock())
    await publish_uno_panel(bot, service, -100, game)
    bot.edit_message_text.assert_awaited_once()
    bot.send_message.assert_not_awaited()
    service.mark_panel_delivered.assert_awaited_once_with(-100, 3, 17)


def test_hand_button_is_first_for_inline_and_legacy_tables():
    game = state()
    assert "Sıra: <b>Ayşe</b>" in render_uno_text(game)
    for inline in (True, False):
        if not inline:
            game.pop("inline_query")
        labels = [button.text for row in uno_keyboard(game).inline_keyboard for button in row]
        assert labels[0] == "🃏 Kartları gör"


def test_uno_source_stays_available_outside_the_game_panel():
    game = state()
    game["source_url"] = "https://t.me/ArelChat_Bot?start=uno_source"
    buttons = [button for row in uno_keyboard(game).inline_keyboard for button in row]
    assert all("Kaynak kodu" not in button.text for button in buttons)
    assert game["source_url"].endswith("uno_source")


def test_rules_only_in_lobby_and_no_navigation_or_close_controls():
    game = state()
    for status in ("playing", "lobby", "finished"):
        game["status"] = status
        labels = [button.text for row in uno_keyboard(game).inline_keyboard for button in row]
        assert "🎮 Oyun salonu" not in labels
        assert not any("Masayı kapat" in label or "Maçı bitir" in label or
                       label == "Oyunu bitir" for label in labels)
        assert ("📖 Nasıl oynanır?" in labels) == (status == "lobby")
    game["status"] = "playing"
    game.pop("inline_query")
    labels = [button.text for row in uno_keyboard(game).inline_keyboard for button in row]
    assert labels[0] == "🃏 Kartları gör"
    assert "📖 Nasıl oynanır?" not in labels
