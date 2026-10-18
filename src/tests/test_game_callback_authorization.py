from types import SimpleNamespace

import pytest

from src.handlers.callback_handler import CallbackHandler


class _Auth:
    def __init__(self, allowed=False):
        self.allowed = allowed
        self.calls = []

    async def is_admin(self, chat_id, user_id):
        self.calls.append((chat_id, user_id))
        return self.allowed


class _Query:
    def __init__(self):
        self.message = SimpleNamespace(message_id=99)
        self.answers = []

    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))


class _DataPort:
    reachable = True

    def __init__(self):
        self.deleted = []

    async def get_game_state(self, chat_id, game_type):
        return None

    async def delete_game_state(self, chat_id, game_type):
        self.deleted.append((chat_id, game_type))


def _update(user_id=22):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id, first_name="u"),
        effective_chat=SimpleNamespace(id=-100, type="supergroup"),
    )


@pytest.mark.asyncio
async def test_group_stop_all_requires_admin_and_does_not_delete_state():
    auth = _Auth(False)
    data_port = _DataPort()
    handler = CallbackHandler(
        SimpleNamespace(), SimpleNamespace(owner_id=999), None,
        data_port=data_port, authorization=auth,
    )
    context = SimpleNamespace(chat_data={"tictactoe": {"status": "playing"}})
    query = _Query()

    update = _update()
    update.callback_query = query
    query.data = "game_stop_all"
    await handler.handle_callback_query(update, context)

    assert not data_port.deleted
    assert context.chat_data["tictactoe"]
    assert query.answers and query.answers[-1][1].get("show_alert") is True


@pytest.mark.asyncio
async def test_active_word_round_cannot_be_overwritten_by_other_group_member():
    auth = _Auth(False)
    data_port = _DataPort()
    handler = CallbackHandler(
        SimpleNamespace(), SimpleNamespace(owner_id=999), None,
        data_port=data_port, authorization=auth,
    )
    state = {"status": "active", "owner_id": 11}
    context = SimpleNamespace(chat_data={"hangman": state})
    query = _Query()

    await handler._handle_game_start_callback(_update(22), query, context, "game_start_hangman")

    assert context.chat_data["hangman"] is state
    assert query.answers and query.answers[-1][1].get("show_alert") is True

