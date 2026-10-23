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
    assert "Sıradaki oyuncu: <b>Ayşe</b>" in bot.send_message.await_args.kwargs["text"]
    buttons = [button for row in bot.send_message.await_args.kwargs["reply_markup"].inline_keyboard
               for button in row]
    assert buttons[0].text == "▶️ Sıradaki: Ayşe"
    assert buttons[1].text == "🃏 Kartlarımı aç"
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


def test_turn_button_is_present_for_inline_and_legacy_tables():
    game = state()
    assert "Sıradaki oyuncu" in render_uno_text(game)
    for inline in (True, False):
        if not inline:
            game.pop("inline_query")
        labels = [button.text for row in uno_keyboard(game).inline_keyboard for button in row]
        assert labels[0] == "▶️ Sıradaki: Ayşe"
