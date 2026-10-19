from types import SimpleNamespace

import pytest

from src.games.chess import new_chess_state
from src.handlers.callback_handler import CallbackHandler
from src.handlers.command_handler import CommandHandler
from src.services.chess_ui import chess_keyboard, render_chess_text


class Ledger:
    async def claim(self, *args, **kwargs):
        return True


class DataPort:
    reachable = True

    def __init__(self, state=None):
        self.state = state
        self.saved = []

    async def get_game_state(self, chat_id, game_type):
        return self.state if game_type == "chess" else None

    async def save_game_state(self, chat_id, game_type, state):
        if game_type == "chess":
            self.state = state
            self.saved.append(state.copy())

    async def delete_game_state(self, chat_id, game_type):
        if game_type == "chess":
            self.state = None


class Message:
    message_id = 700
    message_thread_id = 42

    def __init__(self):
        self.calls = []

    async def reply_text(self, text, **kwargs):
        self.calls.append((text, kwargs))
        return SimpleNamespace(message_id=self.message_id)


class Query:
    def __init__(self, message_id=700, thread_id=42):
        self.message = SimpleNamespace(message_id=message_id, message_thread_id=thread_id)
        self.answers = []
        self.edits = []

    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))

    async def edit_message_text(self, text, **kwargs):
        self.edits.append((text, kwargs))


def update(user_id=1, thread_id=42):
    message = Message()
    message.message_thread_id = thread_id
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id, first_name=f"u{user_id}"),
        effective_chat=SimpleNamespace(id=-100, type="supergroup"),
        effective_message=message,
        message=message,
    )


@pytest.mark.asyncio
async def test_chess_command_persists_and_binds_forum_topic():
    port = DataPort()
    handler = CommandHandler(SimpleNamespace(), SimpleNamespace(), None, None, data_port=port)
    context = SimpleNamespace(chat_data={}, args=[])
    command_update = update(1)
    await handler.handle_chess_command(command_update, context)
    state = context.chat_data["chess"]
    assert state["thread_id"] == 42
    assert port.saved
    assert command_update.effective_message.calls
    assert command_update.effective_message.calls[0][1]["message_thread_id"] == 42
    assert command_update.effective_message.calls[0][1]["reply_markup"] is not None
    markup = context.chat_data["chess"]
    callbacks = {
        button.callback_data
        for row in chess_keyboard(markup).inline_keyboard
        for button in row
        if button.callback_data
    }
    assert f"chess:{state['round_id']}:join" in callbacks
    assert "AREL SATRANÇ" in render_chess_text(state)


@pytest.mark.asyncio
async def test_chess_callback_selects_and_moves_with_round_scope():
    state = new_chess_state(owner_id=1, owner_name="u1", chat_id=-100, thread_id=42)
    state["prompt_message_id"] = 700
    port = DataPort(state)
    handler = CallbackHandler(SimpleNamespace(), SimpleNamespace(owner_id=999), None, data_port=port, game_action_ledger=Ledger())
    context = SimpleNamespace(chat_data={"chess": state})
    rid = state["round_id"]
    await handler._handle_chess_callback(update(2), Query(), context, f"chess:{rid}:join")
    await handler._handle_chess_callback(update(1), Query(), context, f"chess:{rid}:start")
    query = Query()
    await handler._handle_chess_callback(update(1), query, context, f"chess:{rid}:sq:e2")
    await handler._handle_chess_callback(update(1), query, context, f"chess:{rid}:sq:e4")
    assert state["moves"] == ["e4"]
    assert state["turn"] == "black"
    assert chess_keyboard(state).inline_keyboard[7][0].callback_data.endswith(":sq:h8")
    assert query.edits
    assert all("chess:" in str(button.callback_data) for row in query.edits[-1][1]["reply_markup"].inline_keyboard for button in row if button.callback_data)


@pytest.mark.asyncio
async def test_chess_callback_rejects_stale_round_and_cross_topic():
    state = new_chess_state(owner_id=1, owner_name="u1", chat_id=-100, thread_id=42)
    state["prompt_message_id"] = 700
    port = DataPort(state)
    handler = CallbackHandler(SimpleNamespace(), SimpleNamespace(owner_id=999), None, data_port=port, game_action_ledger=Ledger())
    context = SimpleNamespace(chat_data={"chess": state})
    stale = Query()
    await handler._handle_chess_callback(update(1), stale, context, "chess:stale:sq:e2")
    assert stale.answers
    cross = Query(thread_id=99)
    await handler._handle_chess_callback(update(1, thread_id=99), cross, context, f"chess:{state['round_id']}:sq:e2")
    assert cross.answers


def test_chess_board_keeps_active_player_at_bottom_and_identifies_piece_owners():
    state = new_chess_state(owner_id=1, owner_name="Ada", chat_id=-100)
    state["players"].append({"id": 2, "name": "Bora", "color": "black"})
    state["players"][0]["color"] = "white"
    state["status"] = "playing"
    state["turn"] = "white"
    white = chess_keyboard(state).inline_keyboard
    assert white[0][0].callback_data.endswith(":sq:a8")
    assert white[7][0].callback_data.endswith(":sq:a1")
    assert white[0][0].text.startswith("●")
    assert white[7][0].text.startswith("○")
    white_text = render_chess_text(state)
    assert "Beyaz altta" in white_text
    assert "a → h" in white_text

    state["turn"] = "black"
    black = chess_keyboard(state).inline_keyboard
    assert black[0][0].callback_data.endswith(":sq:h1")
    assert black[7][0].callback_data.endswith(":sq:h8")
    assert black[0][0].text.startswith("○")
    assert black[7][0].text.startswith("●")
    black_text = render_chess_text(state)
    assert "Siyah altta" in black_text
    assert "h → a" in black_text


def test_chess_capture_target_keeps_opponent_piece_visible():
    state = new_chess_state(owner_id=1, owner_name="Ada", chat_id=-100)
    state.update({"status": "playing", "fen": "", "uci_moves": ["e2e4", "d7d5"],
                  "turn": "white", "selected_square": "e4", "legal_targets": ["d5"]})
    rows = chess_keyboard(state).inline_keyboard
    capture = next(button.text for row in rows for button in row if button.callback_data.endswith(":sq:d5"))
    assert "♟" in capture and "×" in capture
