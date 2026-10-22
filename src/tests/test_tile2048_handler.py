"""2048 Telegram panel and callback isolation."""

import time
from types import SimpleNamespace

import pytest

from src.games import tile2048 as rules
from src.handlers.tile2048 import Tile2048Handler
from src.services.tile2048_service import Tile2048Service
from src.tests.test_tile2048_service import Repository
from src.ui import tile2048_ui as ui


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
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))


class Query:
    def __init__(self, data, message, token):
        self.data, self.message, self.id = data, message, token
        self.answers = []

    async def answer(self, text="", **kwargs):
        self.answers.append((text, kwargs))


def event(*, user=1, chat=-100, topic=None, message_id=51, data=None, token="q"):
    message = Message(message_id, topic)
    return SimpleNamespace(effective_chat=SimpleNamespace(id=chat,
                           type="private" if chat > 0 else "supergroup"),
        effective_user=SimpleNamespace(id=user, first_name="Oyuncu"),
        effective_message=message,
        callback_query=Query(data, message, token) if data else None)


@pytest.mark.asyncio
async def test_command_one_panel_callback_canonical_and_topic_binding():
    service = Tile2048Service(Repository())
    handler, bot = Tile2048Handler(service), Bot()
    opened = event(topic=7)
    await handler.command(opened, SimpleNamespace(bot=bot, args=[]), "2048")
    state = await service.current(game_key="2048", chat_id=-100, thread_id=7)
    assert state["panel_message_id"] == 101 and state["deadline"] > time.time()
    assert len(bot.sent) == 1 and not opened.effective_message.replies
    assert bot.sent[0]["message_thread_id"] == 7
    assert "Nasıl oynanır?" in str(bot.sent[0]["reply_markup"].to_dict())
    assert len(ui.render(state).encode("utf-16-le")) // 2 <= 4096
    _direction, code = next((direction, code) for direction, code in
                           (("up", "u"), ("left", "l"), ("down", "d"), ("right", "r"))
                           if rules.slide(state["board"], direction)[2])
    data = ui.callback_data(state, code)
    assert len(data.encode()) <= 64
    for bad in (event(user=2, topic=7, message_id=101, data=data),
                event(topic=8, message_id=101, data=data),
                event(topic=7, message_id=999, data=data),
                event(chat=1, topic=7, message_id=101, data=data)):
        assert await handler.callback(bad, SimpleNamespace(bot=bot))
        assert bad.callback_query.answers[0][1]["show_alert"]
    good = event(topic=7, message_id=101, data=data, token="valid")
    assert await handler.callback(good, SimpleNamespace(bot=bot))
    assert good.callback_query.answers == [("", {})]
    after = await service.current(game_key="2048", chat_id=-100, thread_id=7)
    assert after["moves"] == 1 and after["phase_seq"] != state["phase_seq"]
    assert len(bot.sent) == 1 and bot.edits
    stale = event(topic=7, message_id=101, data=data, token="old")
    await handler.callback(stale, SimpleNamespace(bot=bot))
    assert stale.callback_query.answers[0][1]["show_alert"]
    assert not await handler.callback(event(), SimpleNamespace(bot=bot))


@pytest.mark.asyncio
async def test_delivery_failure_keeps_clock_stopped_then_worker_recovers():
    service = Tile2048Service(Repository())
    start = time.time()
    _, opened = await service.open(game_key="2048", chat_id=-100, user_id=1,
                                   user_name="X", now=start)

    class FailingBot(Bot):
        async def send_message(self, **kwargs):
            raise RuntimeError("network")

    with pytest.raises(RuntimeError, match="network"):
        await Tile2048Handler(service).publish(FailingBot(), game_key="2048",
                                              chat_id=-100)
    waiting = await service.current(game_key="2048", chat_id=-100)
    assert waiting["deadline"] is None and waiting["panel_wait_until"] == start + 300
    bot = Bot()
    await Tile2048Handler(service).process(bot, {"chat_id": -100, "state": opened})
    # The 30-second lease prevents immediate duplicate sends; process retries later.
    assert len(bot.sent) == 0
    claimed = await service.claim_panel(game_key="2048", chat_id=-100, now=start + 31)
    assert claimed is not None
    assert await service.acknowledge_panel(game_key="2048", chat_id=-100,
        round_id=opened["round_id"], expected_version=claimed["version"],
        message_id=101, now=start + 32)
    assert (await service.current(game_key="2048", chat_id=-100))["deadline"] == start + 632


@pytest.mark.asyncio
async def test_cancel_for_owner_and_terminal_replay_controls():
    service = Tile2048Service(Repository())
    handler, bot = Tile2048Handler(service), Bot()
    await handler.command(event(), SimpleNamespace(bot=bot, args=[]), "2048")
    assert not await handler.cancel_for(event(user=2), SimpleNamespace(bot=bot))
    assert await handler.cancel_for(event(user=1), SimpleNamespace(bot=bot))
    state = await service.current(game_key="2048", chat_id=-100)
    assert state["phase"] == "cancelled" and not state["score_pending"]
    buttons = [button.callback_data for row in ui.keyboard(state).inline_keyboard
               for button in row]
    assert "game_start_2048" in buttons and "menu_games:boards" in buttons
    assert await handler.on_text(event(), SimpleNamespace(bot=bot), "left") is False


@pytest.mark.asyncio
async def test_private_chat_can_open_and_play_with_signed_chat_callback():
    service = Tile2048Service(Repository())
    handler, bot = Tile2048Handler(service), Bot()
    await handler.command(event(chat=42), SimpleNamespace(bot=bot, args=[]), "2048")
    state = await service.current(game_key="2048", chat_id=42)
    assert state["panel_message_id"] == 101 and state["deadline"] is not None
    _direction, code = next((direction, code) for direction, code in
                           (("up", "u"), ("left", "l"), ("down", "d"), ("right", "r"))
                           if rules.slide(state["board"], direction)[2])
    data = ui.callback_data(state, code)
    assert ui.parse_callback(data)[0] == 42
    click = event(chat=42, message_id=101, data=data)
    assert await handler.callback(click, SimpleNamespace(bot=bot))
    assert click.callback_query.answers == [("", {})]
    assert (await service.current(game_key="2048", chat_id=42))["moves"] == 1
    live = await service.current(game_key="2048", chat_id=42)
    cancel = event(chat=42, message_id=101, data=ui.callback_data(live, "x"),
                   token="private-stop")
    await handler.callback(cancel, SimpleNamespace(bot=bot))
    assert cancel.callback_query.answers == [("", {})]
    assert (await service.current(game_key="2048", chat_id=42))["phase"] == "cancelled"
