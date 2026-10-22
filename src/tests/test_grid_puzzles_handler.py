"""Telegram callback ownership, topics and canonical panel tests."""

from types import SimpleNamespace
import time

import pytest

from src.games import grid_puzzles as grid
from src.handlers.grid_puzzles_handler import GridPuzzlesHandler
from src.services.grid_puzzles_service import GridPuzzlesService
from src.services.grid_puzzles_ui import callback_data
from src.tests.test_grid_puzzles_service import Repository


class Bot:
    def __init__(self):
        self.sent = []
        self.edits = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(message_id=100 + len(self.sent))

    async def edit_message_text(self, **kwargs):
        self.edits.append(kwargs)


class Message:
    def __init__(self, message_id=51, topic=None):
        self.message_id = message_id
        self.is_topic_message = topic is not None
        self.message_thread_id = topic
        self.date = None
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))
        return SimpleNamespace(message_id=55 + len(self.replies))


class Query:
    def __init__(self, data, message, token="q1"):
        self.data, self.message, self.id = data, message, token
        self.answers = []

    async def answer(self, text="", **kwargs):
        self.answers.append((text, kwargs))


def update(uid=1, chat=-100, topic=None, message_id=51, data=None, token="q1"):
    message = Message(message_id, topic)
    return SimpleNamespace(effective_chat=SimpleNamespace(id=chat,
                           type="private" if chat > 0 else "supergroup"),
                           effective_user=SimpleNamespace(id=uid, first_name="Oyuncu"),
                           effective_message=message,
                           callback_query=Query(data, message, token) if data else None)


@pytest.mark.asyncio
async def test_mines_command_one_panel_and_bound_last_cell_callback():
    service = GridPuzzlesService(Repository())
    handler, bot = GridPuzzlesHandler(service), Bot()
    event = update(topic=7)
    await handler.command(event, SimpleNamespace(bot=bot, args=[]), grid.MINES_KEY)
    current = await service.current(game_key=grid.MINES_KEY, chat_id=-100, thread_id=7)
    assert current["panel_message_id"] == 101 and current["deadline"] > time.time()
    assert len(event.effective_message.replies) == 0 and len(bot.sent) == 1
    click = update(topic=7, message_id=101, data=callback_data(current, "open", 35))
    assert await handler.callback(click, SimpleNamespace(bot=bot))
    assert click.callback_query.answers[0][0] == ""
    played = await service.current(game_key=grid.MINES_KEY, chat_id=-100, thread_id=7)
    assert played["first_open"] == 35 and 35 not in played["mines"]
    assert len(bot.edits) >= 1 and len(bot.sent) == 1


@pytest.mark.asyncio
async def test_wrong_actor_topic_message_and_stale_turn_are_rejected():
    repo = Repository()
    service = GridPuzzlesService(repo)
    state = grid.new_memory(chat_id=-100, thread_id=7, owner_id=1,
                            owner_name="Bir", round_id="r", now=time.time())
    state = grid.memory_start(state, user_id=1)
    state = grid.activate(state)
    state = grid.advance(state, now=state["deadline"])
    state = grid.activate(state)
    state["panel_message_id"] = 56
    repo.rows[(-100, service.key(grid.MEMORY_KEY, 7))] = state
    handler, bot = GridPuzzlesHandler(service), Bot()
    data = callback_data(state, "pick", 0)
    for event in (update(uid=2, topic=7, message_id=56, data=data),
                  update(uid=1, topic=8, message_id=56, data=data),
                  update(uid=1, topic=7, message_id=55, data=data),
                  update(uid=1, chat=1, message_id=56, data=data)):
        assert await handler.callback(event, SimpleNamespace(bot=bot))
        assert event.callback_query.answers[0][1]["show_alert"]
    good = update(uid=1, topic=7, message_id=56, data=data, token="good")
    await handler.callback(good, SimpleNamespace(bot=bot))
    assert good.callback_query.answers[0][0] == ""
    old = update(uid=1, topic=7, message_id=56, data=data, token="old")
    await handler.callback(old, SimpleNamespace(bot=bot))
    assert old.callback_query.answers[0][1]["show_alert"]
    assert await handler.on_text(good, SimpleNamespace(bot=bot), "kart") is False


@pytest.mark.asyncio
async def test_panel_send_failure_leaves_clock_stopped_until_bounded_cancel():
    repo = Repository()
    service = GridPuzzlesService(repo)
    state = grid.new_mines(chat_id=-100, thread_id=None, owner_id=1,
                           owner_name="Bir", round_id="r", now=1)
    repo.rows[(-100, service.key(grid.MINES_KEY))] = state

    class FailingBot(Bot):
        async def send_message(self, **kwargs):
            raise RuntimeError("delivery")

    with pytest.raises(RuntimeError):
        await GridPuzzlesHandler(service).publish(FailingBot(), game_key=grid.MINES_KEY, chat_id=-100)
    current = await service.current(game_key=grid.MINES_KEY, chat_id=-100)
    assert current["deadline"] is None and not current["phase_ready"]
    await service.advance(game_key=grid.MINES_KEY, chat_id=-100,
                          round_id="r", now=current["panel_wait_until"])
    closed = await service.current(game_key=grid.MINES_KEY, chat_id=-100)
    assert closed["phase"] == "cancelled" and not closed["score_pending"]
