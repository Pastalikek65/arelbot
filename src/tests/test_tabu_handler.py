"""Telegram adapter boundaries: private roles, group topics and delivery."""

from types import SimpleNamespace

import pytest
from telegram.error import BadRequest, Forbidden

from src.handlers.tabu_handler import TabuHandler
from src.services.tabu_service import TabuService
from src.services.tabu_ui import callback_data
from src.tests.test_tabu_service import Repository, prepared


class Bot:
    def __init__(self):
        self.sent, self.edits = [], []
        self.forbid_private = False

    async def send_message(self, **kwargs):
        if self.forbid_private and int(kwargs["chat_id"]) > 0:
            raise Forbidden("blocked")
        self.sent.append(kwargs)
        return SimpleNamespace(message_id=len(self.sent) + 100)

    async def edit_message_text(self, **kwargs):
        self.edits.append(kwargs)

    async def get_me(self):
        return SimpleNamespace(username="TestBot")


class Message:
    message_id = 55
    message_thread_id = None
    date = None

    def __init__(self):
        self.sent = []

    async def reply_text(self, text, **kwargs):
        self.sent.append((text, kwargs))
        return SimpleNamespace(message_id=55 + len(self.sent))


class Query:
    id = "q1"

    def __init__(self, data, message):
        self.data, self.message, self.answers, self.edits = data, message, [], []

    async def answer(self, text="", **kwargs):
        self.answers.append((text, kwargs))

    async def edit_message_text(self, *args, **kwargs):
        self.edits.append((args, kwargs))
        raise BadRequest("Message is not modified")


def update(uid=1, chat=-100, topic=None):
    message = Message()
    message.message_thread_id = topic
    message.is_topic_message = topic is not None
    return SimpleNamespace(effective_chat=SimpleNamespace(id=chat, type="private" if chat > 0 else "supergroup"),
                           effective_user=SimpleNamespace(id=uid, first_name="A"), effective_message=message)


@pytest.mark.asyncio
async def test_new_lobby_has_one_canonical_group_panel():
    service = TabuService(Repository())
    adapter, bot, event = TabuHandler(service), Bot(), update()
    await adapter.command(event, SimpleNamespace(args=[], bot=bot))
    assert len(event.effective_message.sent) + len(bot.sent) == 1
    assert (await service.current(-100))["panel_message_id"] == 56
    await adapter.command(event, SimpleNamespace(args=["durum"], bot=bot))
    assert len(event.effective_message.sent) + len(bot.sent) == 1
    assert len(bot.edits) == 1


@pytest.mark.asyncio
async def test_refresh_updates_clock_and_handles_unchanged_message():
    service = TabuService(Repository())
    _, state = await service.open(chat_id=-100, user_id=1, user_name="A")
    event = update()
    event.callback_query = Query(callback_data(state, "refresh"), event.effective_message)
    await TabuHandler(service).callback(event, SimpleNamespace(bot=Bot()))
    assert len(event.callback_query.edits) == 1
    assert event.callback_query.answers[0][0] == ""


@pytest.mark.asyncio
async def test_private_forged_stop_and_other_topic_cannot_mutate_group():
    service = TabuService(Repository())
    _, state = await service.open(chat_id=-100, thread_id=7, user_id=1, user_name="A")
    for event in (update(chat=1), update(topic=8)):
        event.callback_query = Query(callback_data(state, "stop"), event.effective_message)
        await TabuHandler(service).callback(event, SimpleNamespace(bot=Bot()))
        assert event.callback_query.answers[0][1]["show_alert"]
    assert (await service.current(-100, 7))["phase"] == "lobby"


@pytest.mark.asyncio
async def test_blocked_private_delivery_never_starts_the_clock():
    service, round_id = await prepared()
    _, state = await service.act(chat_id=-100, thread_id=7, user_id=1, action="start", action_id="start")
    bot = Bot()
    bot.forbid_private = True
    assert not await TabuHandler(service).deliver_card(bot, chat_id=-100, thread_id=7)
    current = await service.current(-100, 7)
    assert current["phase"] == "card_delivery" and current["deadline"] is None and not current["delivered"]


@pytest.mark.asyncio
async def test_finished_panel_is_visible_even_when_rewards_need_retry():
    service, _ = await prepared()
    state = await service.current(-100, 7)
    state.update(phase="finished", score_pending=True, winner_team="red", panel_dirty=True)
    state["team_scores"] = {"red": 2, "blue": 1}
    service.repository.rows[-100, "tabu_t7"] = state
    bot = Bot()
    await TabuHandler(service).process(bot, {"chat_id": -100, "state": state})
    assert any("kazandı" in item["text"] for item in bot.sent)
    assert (await service.current(-100, 7))["score_pending"] is True


@pytest.mark.asyncio
async def test_private_ack_starts_after_group_marker_and_rejects_older_guess():
    from src.games import tabu
    service, _ = await prepared()
    _, state = await service.act(chat_id=-100, thread_id=7, user_id=1, action="start", action_id="start")
    await service.act(chat_id=-100, thread_id=7, user_id=1, action="delivered", action_id="delivery", card_id=state["card_id"])
    bot, adapter = Bot(), TabuHandler(service)
    forged = update(uid=2, chat=2)
    forged.callback_query = Query(callback_data(state, "ack"), forged.effective_message)
    await adapter.callback(forged, SimpleNamespace(bot=bot))
    assert not bot.sent and (await service.current(-100, 7)).get("ack_retry_at", 0) == 0
    event = update(uid=1, chat=1)
    event.callback_query = Query(callback_data(state, "ack"), event.effective_message)
    await adapter.callback(event, SimpleNamespace(bot=bot))
    current = await service.current(-100, 7)
    assert current["phase"] == "active" and current["active_message_floor"] == 101
    target = tabu.private_card(current, user_id=1, card_id=current["card_id"])["target"]
    old = update(uid=3, topic=7)
    assert await adapter.on_text(old, SimpleNamespace(bot=bot), target)
    assert (await service.current(-100, 7))["phase"] == "active"


@pytest.mark.asyncio
async def test_ready_link_uses_callback_url_without_group_message():
    service = TabuService(Repository())
    _, state = await service.open(chat_id=-100, user_id=1, user_name="A")
    event = update()
    event.callback_query = Query(callback_data(state, "ready"), event.effective_message)
    bot = Bot()
    await TabuHandler(service).callback(event, SimpleNamespace(bot=bot))
    assert event.callback_query.answers[0][1]["url"].startswith("https://t.me/TestBot?start=tb_")
    assert not event.effective_message.sent
    assert not bot.sent


@pytest.mark.asyncio
async def test_finished_panel_replay_opens_new_lobby_once():
    service = TabuService(Repository())
    _, state = await service.open(chat_id=-100, user_id=1, user_name="A")
    state.update(phase="cancelled", panel_dirty=False, panel_message_id=55)
    service.repository.rows[-100, "tabu"] = state
    event = update(uid=2)
    event.callback_query = Query(callback_data(state, "replay"), event.effective_message)
    await TabuHandler(service).callback(event, SimpleNamespace(bot=Bot()))
    renewed = await service.current(-100)
    assert renewed["phase"] == "lobby" and renewed["owner_id"] == 2
    assert renewed["round_id"] != state["round_id"]
