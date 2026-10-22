from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.handlers.callback_handler import CallbackHandler
from src.handlers.command_handler import CommandHandler
from src.services.gomoku_ui import (
    SUPPORTED_BOARD_SIZES,
    gomoku_keyboard,
    new_gomoku_state,
    render_gomoku_text,
)


class Ledger:
    async def claim(self, *args, **kwargs):
        return True


class DataPort:
    reachable = True

    def __init__(self, state=None):
        self.state = deepcopy(state)
        self.saved = []

    async def get_game_state(self, chat_id, game_type):
        return deepcopy(self.state) if game_type == "gomoku" else None

    async def save_game_state(self, chat_id, game_type, state):
        if game_type == "gomoku":
            self.state = deepcopy(state)
            self.saved.append((chat_id, game_type, deepcopy(state)))

    async def initialize_game_state_version_if_missing(self, chat_id, game_type):
        if self.state is not None and "version" not in self.state:
            self.state["version"] = 0
            return True
        return False

    async def create_game_state_if_absent(self, chat_id, game_type, state):
        if self.state is not None:
            return False
        self.state = deepcopy(state)
        self.saved.append((chat_id, game_type, deepcopy(state)))
        return True

    async def compare_and_save_game_state(self, chat_id, game_type, state, *, expected_version):
        if self.state is None or self.state.get("version") != expected_version:
            return False
        self.state = deepcopy(state)
        self.saved.append((chat_id, game_type, deepcopy(state)))
        return True

    async def delete_game_state(self, chat_id, game_type):
        if game_type == "gomoku":
            self.state = None


class ReplyMessage:
    message_id = 700

    def __init__(self, thread_id=42):
        self.calls = []
        self.message_thread_id = thread_id

    async def reply_text(self, text, **kwargs):
        self.calls.append((text, kwargs))
        return SimpleNamespace(message_id=self.message_id)


def command_update(user_id=1, *, thread_id=42):
    message = ReplyMessage(thread_id)
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id, first_name=f"u{user_id}"),
        effective_chat=SimpleNamespace(id=-100, type="supergroup"),
        effective_message=message,
        message=message,
    )


def callback_update(user_id=1, *, thread_id=42, message_id=700):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id, first_name=f"u{user_id}"),
        effective_chat=SimpleNamespace(id=-100, type="supergroup"),
        effective_message=SimpleNamespace(message_thread_id=thread_id),
    )


class Query:
    def __init__(self, *, message_id=700, thread_id=42):
        self.message = SimpleNamespace(message_id=message_id, message_thread_id=thread_id)
        self.edits = []
        self.answers = []

    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))

    async def edit_message_text(self, text, **kwargs):
        self.edits.append((text, kwargs))


def _handler(data_port):
    return CallbackHandler(
        SimpleNamespace(), SimpleNamespace(owner_id=999), None,
        data_port=data_port, game_action_ledger=Ledger(),
    )


@pytest.mark.asyncio
async def test_gomoku_command_is_forum_scoped_and_shows_board_sizes():
    data_port = DataPort()
    handler = CommandHandler(SimpleNamespace(), SimpleNamespace(), None, None, data_port=data_port)
    update = command_update(thread_id=42)
    context = SimpleNamespace(chat_data={}, args=[])

    await handler.handle_gomoku_command(update, context)

    state = data_port.state
    assert state["thread_id"] == 42
    assert state["board_size"] == 10
    callbacks = {
        button.callback_data
        for row in update.effective_message.calls[0][1]["reply_markup"].inline_keyboard
        for button in row
    }
    round_id = state["round_id"]
    assert {f"gomoku:{round_id}:size:{size}" for size in SUPPORTED_BOARD_SIZES} <= callbacks
    assert update.effective_message.calls[0][1]["message_thread_id"] == 42


@pytest.mark.asyncio
async def test_gomoku_callbacks_join_start_and_move_are_round_scoped():
    state = new_gomoku_state(owner_id=1, owner_name="u1", chat_id=-100, prompt_message_id=700, thread_id=42)
    data_port = DataPort(state)
    handler = _handler(data_port)
    context = SimpleNamespace(chat_data={"gomoku": state})
    rid = state["round_id"]

    await handler._handle_gomoku_callback(callback_update(2), Query(), context, f"gomoku:{rid}:join")
    assert len(data_port.state["players"]) == 2
    await handler._handle_gomoku_callback(callback_update(1), Query(), context, f"gomoku:{rid}:start")
    assert data_port.state["status"] == "playing"
    query = Query()
    await handler._handle_gomoku_callback(callback_update(1), query, context, f"gomoku:{rid}:cell:0")
    assert data_port.state["moves"] == 1
    assert query.edits

    before = data_port.state["moves"]
    await handler._handle_gomoku_callback(callback_update(2), Query(thread_id=99), context, f"gomoku:{rid}:cell:1")
    assert data_port.state["moves"] == before


@pytest.mark.asyncio
async def test_gomoku_size_is_owner_only_and_stale_round_is_rejected():
    state = new_gomoku_state(owner_id=1, owner_name="u1", chat_id=-100, prompt_message_id=700, thread_id=42)
    data_port = DataPort(state)
    handler = _handler(data_port)
    context = SimpleNamespace(chat_data={"gomoku": state})
    rid = state["round_id"]

    await handler._handle_gomoku_callback(callback_update(2), Query(), context, f"gomoku:{rid}:size:15")
    assert data_port.state["board_size"] == 10
    query = Query()
    await handler._handle_gomoku_callback(callback_update(1), query, context, f"gomoku:{rid}:size:15")
    assert data_port.state["board_size"] == 15
    assert query.edits
    await handler._handle_gomoku_callback(callback_update(1), Query(), context, "gomoku:old:size:6")
    assert data_port.state["board_size"] == 15


@pytest.mark.asyncio
async def test_gomoku_owner_can_stop_and_persistence_is_cancelled():
    state = new_gomoku_state(owner_id=1, owner_name="u1", chat_id=-100, prompt_message_id=700, thread_id=42)
    data_port = DataPort(state)
    handler = _handler(data_port)
    context = SimpleNamespace(chat_data={"gomoku": state})
    rid = state["round_id"]
    query = Query()
    await handler._handle_gomoku_callback(callback_update(1), query, context, f"gomoku:{rid}:stop")
    assert data_port.state["status"] == "cancelled"
    assert data_port.state["board_panel_dirty"] is False
    assert data_port.state["prompt_message_id"] == 700
    assert query.edits


def test_gomoku_lobby_keyboard_has_compact_size_rows():
    state = new_gomoku_state(owner_id=1, owner_name="u1", chat_id=-100)
    markup = gomoku_keyboard(state)
    assert len(markup.inline_keyboard[0]) == 3
    assert len(markup.inline_keyboard[1]) == 3


def test_gomoku_render_keeps_board_markers_in_buttons_only():
    state = new_gomoku_state(owner_id=1, owner_name="u1", chat_id=-100)
    text = render_gomoku_text(state)
    assert "·" not in text
    assert "aşağıdaki düğmelerde" in text
