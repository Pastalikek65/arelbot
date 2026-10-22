"""Telegram identity, canonical panel, routing and bounded delivery tests."""

import time
from types import SimpleNamespace

import pytest

from src.games import reasoning_arcade as rules
from src.handlers.reasoning_arcade_handler import ReasoningArcadeHandler
from src.services import reasoning_arcade_ui as ui
from src.services.reasoning_arcade_service import ReasoningArcadeService
from src.tests.test_reasoning_arcade_service import Repository


class Bot:
    def __init__(self):
        self.sent = []
        self.edited = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(message_id=100 + len(self.sent))

    async def edit_message_text(self, **kwargs):
        self.edited.append(kwargs)


class Message:
    def __init__(self, message_id=51, topic=7):
        self.message_id = message_id
        self.is_topic_message = topic is not None
        self.message_thread_id = topic
        self.date = None
        self.reply_to_message = None
        self.replies = []

    async def reply_text(self, text, **_):
        self.replies.append(text)


class Query:
    def __init__(self, data, message, token="q1"):
        self.data, self.message, self.id = data, message, token
        self.answers = []

    async def answer(self, text=None, **kwargs):
        self.answers.append((text, kwargs))


def update(*, uid=1, topic=7, chat=-100, message_id=51,
           data=None, token="q1"):
    message = Message(message_id, topic)
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat,
            type="private" if chat > 0 else "supergroup"),
        effective_user=SimpleNamespace(id=uid, first_name="Oyuncu"),
        effective_message=message,
        callback_query=Query(data, message, token) if data else None,
        edited_message=None)


@pytest.mark.asyncio
async def test_rank_command_panel_callback_scope_and_empty_ack():
    service = ReasoningArcadeService(Repository())
    handler, bot = ReasoningArcadeHandler(service), Bot()
    event = update()
    await handler.command(event, SimpleNamespace(bot=bot, args=[]), rules.RANK)
    state = await service.current(game_key=rules.RANK, chat_id=-100, thread_id=7)
    assert state["panel_message_id"] == 101 and state["deadline"] > time.time()
    assert len(bot.sent) == 1 and not event.effective_message.replies
    data = ui.callback_data(state, "r1")
    for invalid in (update(uid=2, message_id=101, data=data),
                    update(topic=8, message_id=101, data=data),
                    update(message_id=99, data=data),
                    update(chat=1, message_id=101, data=data)):
        assert await handler.callback(invalid, SimpleNamespace(bot=bot))
        assert invalid.callback_query.answers[0][1]["show_alert"]
    accepted = update(message_id=101, data=data, token="right")
    assert await handler.callback(accepted, SimpleNamespace(bot=bot))
    assert accepted.callback_query.answers[0][0] is None
    played = await service.current(game_key=rules.RANK, chat_id=-100, thread_id=7)
    assert played["step"] == 1 and len(bot.edited) == 1
    old = update(message_id=101, data=data, token="old")
    await handler.callback(old, SimpleNamespace(bot=bot))
    assert old.callback_query.answers[0][1]["show_alert"]


@pytest.mark.asyncio
async def test_failed_first_panel_keeps_rank_clock_stopped_then_cancels():
    service = ReasoningArcadeService(Repository())
    _, opened = await service.open(game_key=rules.RANK, chat_id=-100,
                                   thread_id=7, user_id=1, user_name="Bir", now=100)

    class FailingBot(Bot):
        async def send_message(self, **_):
            raise RuntimeError("delivery unavailable")

    with pytest.raises(RuntimeError, match="delivery unavailable"):
        await ReasoningArcadeHandler(service).publish(
            FailingBot(), game_key=rules.RANK, chat_id=-100, thread_id=7)
    waiting = await service.current(game_key=rules.RANK, chat_id=-100, thread_id=7)
    assert waiting["deadline"] is None
    await service.advance(game_key=rules.RANK, chat_id=-100, thread_id=7,
                          round_id=opened["round_id"], now=waiting["panel_wait_until"])
    ended = await service.current(game_key=rules.RANK, chat_id=-100, thread_id=7)
    assert ended["phase"] == "cancelled" and not ended["score_pending"]


@pytest.mark.asyncio
async def test_detective_text_routing_requires_live_case_and_slashes():
    handler = ReasoningArcadeHandler(ReasoningArcadeService(Repository()))
    metadata = {"game_key": rules.DETECTIVE, "phase": "playing"}
    assert handler.accepts_text(metadata)
    assert not handler.accepts_text({"game_key": rules.RANK, "phase": "playing"})
    assert not handler.accepts_text({"game_key": rules.DETECTIVE, "phase": "lobby"})
    assert not await handler.on_text(update(), SimpleNamespace(bot=Bot()), "Mira / sera / anahtar")
