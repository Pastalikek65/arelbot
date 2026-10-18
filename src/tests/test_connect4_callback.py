from types import SimpleNamespace

import pytest

from src.handlers.callback_handler import CallbackHandler
from src.services.word_game_ui import new_connect4_state


class Ledger:
    async def claim(self, *args, **kwargs):
        return True


class DataPort:
    reachable = True

    def __init__(self, state):
        self.state = state

    async def get_game_state(self, chat_id, game_type):
        return self.state if game_type == "connect4" else None

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


def _update(user_id):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id, first_name=f"u{user_id}", full_name=f"u{user_id}"),
        effective_chat=SimpleNamespace(id=-100, type="supergroup"),
        effective_message=SimpleNamespace(message_id=120),
    )


@pytest.mark.asyncio
async def test_connect4_callback_scopes_round_and_tracks_each_actor():
    state = new_connect4_state(owner_id=1, owner_name="u1", chat_id=-100, prompt_message_id=99)
    data_port = DataPort(state)
    handler = CallbackHandler(SimpleNamespace(), SimpleNamespace(owner_id=999), None, data_port=data_port, game_action_ledger=Ledger())
    context = SimpleNamespace(chat_data={"connect4": state})

    query = Query()
    await handler._handle_connect4_callback(_update(2), query, context, f"connect4:{state['round_id']}:join")
    assert len(state["players"]) == 2
    await handler._handle_connect4_callback(_update(1), query, context, f"connect4:{state['round_id']}:start")
    assert state["status"] == "playing"
    await handler._handle_connect4_callback(_update(1), query, context, f"connect4:{state['round_id']}:col:0")
    assert state["board"][-1][0] == 1
    assert state["last_actor_id"] == 1
    assert query.edits


@pytest.mark.asyncio
async def test_connect4_callback_rejects_old_panel_without_mutating():
    state = new_connect4_state(owner_id=1, owner_name="u1", chat_id=-100, prompt_message_id=99)
    data_port = DataPort(state)
    handler = CallbackHandler(SimpleNamespace(), SimpleNamespace(owner_id=999), None, data_port=data_port, game_action_ledger=Ledger())
    context = SimpleNamespace(chat_data={"connect4": state})
    query = Query(message_id=100)
    await handler._handle_connect4_callback(_update(2), query, context, f"connect4:{state['round_id']}:join")
    assert len(state["players"]) == 1
    assert query.answers
