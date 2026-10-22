from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.handlers.callback_handler import CallbackHandler
from src.handlers.command_handler import CommandHandler
from src.services.tictactoe_ui import new_tictactoe_state


class Ledger:
    async def claim(self, *args, **kwargs):
        return True


class DataPort:
    reachable = True

    def __init__(self, state=None):
        self.state = deepcopy(state)
        self.saved = []

    async def get_game_state(self, chat_id, game_type):
        return deepcopy(self.state) if game_type == "tictactoe" else None

    async def save_game_state(self, chat_id, game_type, state):
        if game_type == "tictactoe":
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
        if game_type == "tictactoe":
            self.state = None


class ReplyMessage:
    message_id = 700

    def __init__(self):
        self.calls = []
        self.message_thread_id = 42

    async def reply_text(self, text, **kwargs):
        self.calls.append((text, kwargs))
        return SimpleNamespace(message_id=self.message_id)


def command_update(user_id=1, *, thread_id=42):
    message = ReplyMessage()
    message.message_thread_id = thread_id
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
        callback_query=SimpleNamespace(),
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


@pytest.mark.asyncio
async def test_xox_command_presents_board_sizes_and_preserves_forum_thread():
    data_port = DataPort()
    handler = CommandHandler(
        SimpleNamespace(), SimpleNamespace(), None, None, data_port=data_port
    )
    update = command_update(thread_id=42)
    context = SimpleNamespace(chat_data={}, args=[])

    await handler.handle_tictactoe_command(update, context)

    assert data_port.state["thread_id"] == 42
    assert data_port.state["board_size"] == 6
    assert len(data_port.state["board"]) == 36
    markup = update.effective_message.calls[0][1]["reply_markup"]
    callbacks = {
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data
    }
    round_id = data_port.state["round_id"]
    assert {f"tictactoe:{round_id}:size:{size}" for size in (6, 7, 8)} <= callbacks
    assert update.effective_message.calls[0][1]["message_thread_id"] == 42


@pytest.mark.asyncio
async def test_xox_size_callback_is_owner_only_and_round_scoped():
    state = new_tictactoe_state(owner_id=1, owner_name="u1", chat_id=-100, prompt_message_id=700)
    state["thread_id"] = 42
    data_port = DataPort(state)
    handler = CallbackHandler(
        SimpleNamespace(), SimpleNamespace(owner_id=999), None,
        data_port=data_port, game_action_ledger=Ledger()
    )
    context = SimpleNamespace(chat_data={"tictactoe": state})
    round_id = state["round_id"]

    await handler._handle_tictactoe_callback(
        callback_update(2), Query(), context, f"tictactoe:{round_id}:size:6"
    )
    assert state.get("board_size") in (None, 3)

    query = Query()
    await handler._handle_tictactoe_callback(
        callback_update(1), query, context, f"tictactoe:{round_id}:size:6"
    )
    assert data_port.state["board_size"] == 6
    assert query.edits

    before = data_port.state["board_size"]
    await handler._handle_tictactoe_callback(
        callback_update(1), Query(), context, "tictactoe:stale-round:size:8"
    )
    assert data_port.state["board_size"] == before


@pytest.mark.asyncio
async def test_xox_size_callback_rejects_cross_topic_forum_click():
    state = new_tictactoe_state(owner_id=1, owner_name="u1", chat_id=-100, prompt_message_id=700)
    state["thread_id"] = 42
    data_port = DataPort(state)
    handler = CallbackHandler(
        SimpleNamespace(), SimpleNamespace(owner_id=999), None,
        data_port=data_port, game_action_ledger=Ledger()
    )
    context = SimpleNamespace(chat_data={"tictactoe": state})
    query = Query(thread_id=99)

    await handler._handle_tictactoe_callback(
        callback_update(1, thread_id=99), query, context,
        f"tictactoe:{state['round_id']}:size:7"
    )

    assert state.get("board_size") in (None, 3)
    assert query.answers


@pytest.mark.asyncio
async def test_xox_restart_button_starts_clean_round_for_owner():
    state = new_tictactoe_state(owner_id=1, owner_name="u1", chat_id=-100, prompt_message_id=700)
    state["thread_id"] = 42
    state["status"] = "playing"
    state["players"].append({"id": 2, "name": "u2", "token": "O"})
    state["current_player_id"] = 1
    state["board"][0] = 1
    state["moves"] = 1
    data_port = DataPort(state)
    handler = CallbackHandler(
        SimpleNamespace(), SimpleNamespace(owner_id=999), None,
        data_port=data_port, game_action_ledger=Ledger()
    )
    context = SimpleNamespace(chat_data={"tictactoe": state})
    old_round_id = state["round_id"]
    query = Query()

    await handler._handle_tictactoe_callback(
        callback_update(1), query, context, f"tictactoe:{old_round_id}:restart"
    )

    assert data_port.state["round_id"] != old_round_id
    assert data_port.state["moves"] == 0
    assert all(cell == 0 for cell in data_port.state["board"])
    assert query.edits
