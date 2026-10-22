"""The public Batak panel stays playable without revealing players' hands."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.games.batak_round import BatakRound
from src.handlers.callback_handler import CallbackHandler
from src.handlers.command_handler import CommandHandler
from src.services.batak_service import BatakService


class Database:
    def __init__(self):
        self.states = {}

    async def get_game_state(self, chat_id, key):
        return self.states.get((chat_id, key))

    async def save_game_state(self, chat_id, key, state):
        self.states[(chat_id, key)] = state

    async def delete_game_state(self, chat_id, key):
        self.states.pop((chat_id, key), None)


def _buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


@pytest.mark.asyncio
async def test_group_start_and_each_next_turn_keep_actions_without_card_faces():
    service = BatakService(Database())
    await service.create_lobby(chat_id=-100, owner_id=1, owner_name="Bir")
    await service.join(chat_id=-100, user_id=2, user_name="İki", action_id="join")
    handler = CallbackHandler(SimpleNamespace(), SimpleNamespace(), None, batak_service=service)
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=-100),
                             effective_user=SimpleNamespace(id=1, first_name="Bir"))
    query = SimpleNamespace(message=SimpleNamespace(message_id=40),
                            edit_message_text=AsyncMock(), answer=AsyncMock())
    lobby_id = (await service.snapshot(-100))["lobby_id"]
    await handler._handle_batak_round_callback(update, query, None, f"batak:start:{lobby_id}")
    panel = query.edit_message_text.await_args.args[0]
    buttons = _buttons(query.edit_message_text.await_args.kwargs["reply_markup"])
    assert "Senin elin" not in panel
    assert "Elim" in " ".join(button.text for button in buttons)
    assert all("♠" not in button.text and "♥" not in button.text
               for button in buttons if button.callback_data.startswith("batak:play:"))

    play = next(button.callback_data for button in buttons
                if button.callback_data.startswith("batak:play:"))
    await handler._handle_batak_round_callback(update, query, None, play)
    buttons = _buttons(query.edit_message_text.await_args.kwargs["reply_markup"])
    assert any(button.callback_data.startswith("batak:play:2:") for button in buttons)

    state = await service.snapshot(-100)
    lead = state["trick"][0]["card"][1]
    hand = state["hands"]["2"]
    position = next((i + 1 for i, card in enumerate(hand) if card[1] == lead), 1)
    play_second = next(button.callback_data for button in buttons
                       if button.callback_data.endswith(f":{position}") and
                       button.callback_data.startswith("batak:play:2:"))
    update.effective_user = SimpleNamespace(id=2, first_name="İki")
    await handler._handle_batak_round_callback(update, query, None, play_second)
    assert "Son löveyi" in query.edit_message_text.await_args.args[0]


def test_follow_suit_keeps_public_positions_indistinguishable():
    state = BatakRound.lobby(1, "<Bir>&")
    BatakRound.join(state, 2, "İki")
    BatakRound.start(state)
    state["current_turn"] = 2
    state["trick"] = [{"player_id": 1, "card": ["A", "♥"]}]
    state["hands"]["2"] = [["2", "♠"], ["3", "♥"], ["4", "♣"]]
    buttons = _buttons(CallbackHandler._batak_keyboard(state))
    choices = [button.text for button in buttons if button.callback_data.startswith("batak:play:")]
    assert choices == ["Kart #1", "Kart #2", "Kart #3"]
    assert "İlk renk Kupa" in CallbackHandler._batak_public_text(state)
    assert "&lt;Bir&gt;&amp;" in CallbackHandler._batak_public_text(state)


@pytest.mark.asyncio
async def test_only_private_hand_marks_follow_suit_legality():
    state = BatakRound.lobby(1, "Bir")
    BatakRound.join(state, 2, "İki")
    BatakRound.start(state)
    state["current_turn"] = 2
    state["trick"] = [{"player_id": 1, "card": ["A", "♥"]}]
    state["hands"]["2"] = [["2", "♠"], ["3", "♥"], ["4", "♣"]]
    database = Database()
    await database.save_game_state(-100, "batak", state)
    handler = CallbackHandler(SimpleNamespace(), SimpleNamespace(), None,
                              batak_service=BatakService(database))
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=-100),
                             effective_user=SimpleNamespace(id=2, first_name="İki"))
    query = SimpleNamespace(message=SimpleNamespace(message_id=40),
                            edit_message_text=AsyncMock(), answer=AsyncMock())
    await handler._handle_batak_round_callback(update, query, None, "batak:hand:0")
    alert = query.answer.await_args.args[0]
    assert "#2 3♥ ✓" in alert
    assert "#1 2♠ ✓" not in alert
    assert query.edit_message_text.await_count == 0


@pytest.mark.asyncio
async def test_hand_alert_is_visible_only_to_a_player_and_never_edits_group():
    service = BatakService(Database())
    await service.create_lobby(chat_id=-100, owner_id=1, owner_name="Bir")
    await service.join(chat_id=-100, user_id=2, user_name="İki", action_id="join")
    await service.start(chat_id=-100, user_id=1, action_id="start")
    handler = CallbackHandler(SimpleNamespace(), SimpleNamespace(), None, batak_service=service)
    query = SimpleNamespace(message=SimpleNamespace(message_id=40),
                            edit_message_text=AsyncMock(), answer=AsyncMock())
    spectator = SimpleNamespace(effective_chat=SimpleNamespace(id=-100),
                                effective_user=SimpleNamespace(id=99, first_name="Konuk"))
    await handler._handle_batak_round_callback(spectator, query, None, "batak:hand:0")
    assert "görüntülenemedi" in query.answer.await_args.args[0]
    assert query.edit_message_text.await_count == 0

    player = SimpleNamespace(effective_chat=SimpleNamespace(id=-100),
                             effective_user=SimpleNamespace(id=1, first_name="Bir"))
    await handler._handle_batak_round_callback(player, query, None, "batak:hand:0")
    assert "Elin" in query.answer.await_args.args[0]
    assert len(query.answer.await_args.args[0]) <= 200
    assert query.answer.await_args.kwargs["show_alert"] is True
    assert query.edit_message_text.await_count == 0


@pytest.mark.asyncio
async def test_batak_command_recovers_active_round_with_safe_panel():
    service = BatakService(Database())
    await service.create_lobby(chat_id=-100, owner_id=1, owner_name="Bir")
    await service.join(chat_id=-100, user_id=2, user_name="İki", action_id="join")
    await service.start(chat_id=-100, user_id=1, action_id="start")
    message = SimpleNamespace(reply_text=AsyncMock())
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=-100, type="supergroup"),
                             effective_user=SimpleNamespace(id=1, first_name="Bir"),
                             effective_message=message, message=message)
    handler = CommandHandler(SimpleNamespace(), SimpleNamespace(), None, None,
                             batak_service=service)
    await handler.handle_batak_command(update, SimpleNamespace())
    panel = message.reply_text.await_args.args[0]
    markup = message.reply_text.await_args.kwargs["reply_markup"]
    assert "Senin elin" not in panel
    assert any(button.callback_data.startswith("batak:play:1:") for button in _buttons(markup))


@pytest.mark.asyncio
async def test_recovered_panel_binds_message_and_adds_signed_gallery_button():
    service = BatakService(Database())
    await service.create_lobby(chat_id=-100, owner_id=1, owner_name="Bir")
    await service.join(chat_id=-100, user_id=2, user_name="İki", action_id="join")
    await service.start(chat_id=-100, user_id=1, action_id="start")
    panel = SimpleNamespace(message_id=315, edit_reply_markup=AsyncMock())
    message = SimpleNamespace(reply_text=AsyncMock(return_value=panel))
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=-100, type="supergroup"),
                             effective_user=SimpleNamespace(id=1, first_name="Bir"),
                             effective_message=message, message=message)
    handler = CommandHandler(SimpleNamespace(), SimpleNamespace(), None, None,
                             batak_service=service)
    handler.batak_inline_service = SimpleNamespace(create_query=lambda chat_id, state: f"batak {chat_id}.{state['game_id']}")
    await handler.handle_batak_command(update, SimpleNamespace())
    state = await service.snapshot(-100)
    assert state["prompt_message_id"] == 315
    markup = panel.edit_reply_markup.await_args.kwargs["reply_markup"]
    assert any(button.switch_inline_query_current_chat for button in _buttons(markup))
    assert all("♠" not in button.text for button in _buttons(markup))


@pytest.mark.asyncio
async def test_persisted_action_acknowledges_edit_failure_with_recovery_command():
    service = BatakService(Database())
    await service.create_lobby(chat_id=-100, owner_id=1, owner_name="Bir")
    lobby_id = (await service.snapshot(-100))["lobby_id"]
    handler = CallbackHandler(SimpleNamespace(), SimpleNamespace(), None, batak_service=service)
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=-100),
                             effective_user=SimpleNamespace(id=2, first_name="İki"))
    query = SimpleNamespace(message=SimpleNamespace(message_id=40),
                            edit_message_text=AsyncMock(side_effect=RuntimeError("edit failed")),
                            answer=AsyncMock())
    await handler._handle_batak_round_callback(update, query, None, f"batak:join:{lobby_id}")
    assert len((await service.snapshot(-100))["players"]) == 2
    assert "/batak" in query.answer.await_args.args[0]
    assert query.answer.await_args.kwargs["show_alert"] is True


@pytest.mark.asyncio
async def test_unversioned_lobby_callback_only_prompts_refresh():
    service = BatakService(Database())
    await service.create_lobby(chat_id=-100, owner_id=1, owner_name="Bir")
    handler = CallbackHandler(SimpleNamespace(), SimpleNamespace(), None, batak_service=service)
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=-100),
                             effective_user=SimpleNamespace(id=2, first_name="İki"))
    query = SimpleNamespace(message=SimpleNamespace(message_id=40),
                            edit_message_text=AsyncMock(), answer=AsyncMock())
    await handler._handle_batak_round_callback(update, query, None, "batak:join")
    assert len((await service.snapshot(-100))["players"]) == 1
    assert query.edit_message_text.await_count == 0
    assert "/batak" in query.answer.await_args.args[0]
