from types import SimpleNamespace

import pytest

from src.handlers.callback_handler import CallbackHandler
from src.services.tictactoe_ui import new_tictactoe_state


class Ledger:
    async def claim(self, *args, **kwargs):
        return True


class DataPort:
    reachable = True

    def __init__(self, state):
        self.state = state

    async def get_game_state(self, chat_id, game_type):
        return self.state if game_type == "tictactoe" else None

    async def save_game_state(self, chat_id, game_type, state):
        self.state = state

    async def delete_game_state(self, chat_id, game_type):
        self.state = None


class Query:
    def __init__(self, message_id=99):
        self.message = SimpleNamespace(message_id=message_id)
        self.edits = []
        self.answers = []

    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))

    async def edit_message_text(self, text, **kwargs):
        self.edits.append((text, kwargs))


def update(user_id):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id, first_name=f"u{user_id}"),
        effective_chat=SimpleNamespace(id=-100, type="supergroup"),
    )


@pytest.mark.asyncio
async def test_tictactoe_callback_scopes_round_and_persists_moves():
    state = new_tictactoe_state(owner_id=1, owner_name="u1", chat_id=-100, prompt_message_id=99)
    data_port = DataPort(state)
    handler = CallbackHandler(SimpleNamespace(), SimpleNamespace(owner_id=999), None, data_port=data_port, game_action_ledger=Ledger())
    context = SimpleNamespace(chat_data={"tictactoe": state})
    query = Query()
    await handler._handle_tictactoe_callback(update(2), query, context, f"tictactoe:{state['round_id']}:join")
    await handler._handle_tictactoe_callback(update(1), query, context, f"tictactoe:{state['round_id']}:start")
    await handler._handle_tictactoe_callback(update(1), query, context, f"tictactoe:{state['round_id']}:cell:0")
    assert state["board"][0] == 1
    assert query.edits


@pytest.mark.asyncio
async def test_tictactoe_callback_rejects_old_prompt_without_mutating():
    state = new_tictactoe_state(owner_id=1, owner_name="u1", chat_id=-100, prompt_message_id=99)
    data_port = DataPort(state)
    handler = CallbackHandler(SimpleNamespace(), SimpleNamespace(owner_id=999), None, data_port=data_port, game_action_ledger=Ledger())
    context = SimpleNamespace(chat_data={"tictactoe": state})
    query = Query(message_id=100)
    await handler._handle_tictactoe_callback(update(2), query, context, f"tictactoe:{state['round_id']}:join")
    assert len(state["players"]) == 1
    assert query.answers

