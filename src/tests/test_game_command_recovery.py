from types import SimpleNamespace

import pytest

from src.handlers.command_handler import CommandHandler


class DataPort:
    reachable = True

    def __init__(self, states):
        self.states = dict(states)

    async def get_game_state(self, chat_id, game_type):
        value = self.states.get((int(chat_id), str(game_type)))
        if value is None:
            return None
        if isinstance(value, dict):
            return dict(value)
        return value

    async def save_game_state(self, chat_id, game_type, state):
        self.states[(int(chat_id), str(game_type))] = state

    async def delete_game_state(self, chat_id, game_type):
        self.states.pop((int(chat_id), str(game_type)), None)


class Message:
    message_id = 10
    message_thread_id = None

    def __init__(self):
        self.calls = []

    async def reply_text(self, text, **kwargs):
        self.calls.append((text, kwargs))
        return SimpleNamespace(message_id=11)


def make_update(message):
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=-100, type="supergroup"),
        effective_user=SimpleNamespace(id=3, first_name="oyuncu"),
        effective_message=message,
        message=message,
    )


def make_handler(data_port):
    return CommandHandler(
        SimpleNamespace(),
        SimpleNamespace(),
        None,
        None,
        data_port=data_port,
    )


@pytest.mark.asyncio
async def test_bottle_command_resumes_persisted_lobby_after_restart():
    data_port = DataPort({(-100, "bottle"): {"players": [1, 2]}})
    message = Message()
    context = SimpleNamespace(chat_data={}, args=[])

    await make_handler(data_port).handle_sisecevir_command(make_update(message), context)

    assert context.chat_data["bottle_players"] == [1, 2]
    assert message.calls
    assert "Lobisi" in message.calls[0][0]


@pytest.mark.asyncio
async def test_legacy_batak_command_resumes_persisted_lobby_after_restart():
    data_port = DataPort({(-100, "batak"): {"players": [1], "names": ["bir"]}})
    message = Message()
    context = SimpleNamespace(chat_data={}, args=[])

    await make_handler(data_port).handle_batak_command(make_update(message), context)

    assert context.chat_data["batak_players"] == [1]
    assert context.chat_data["batak_names"] == ["bir"]
    assert message.calls
