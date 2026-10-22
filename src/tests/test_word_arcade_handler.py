"""Telegram actor, panel and secret-routing contracts for Word Arcade."""
import time
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.handlers.word_arcade_handler import WordArcadeHandler
from src.services.word_arcade_service import WordArcadeService
from src.services.word_arcade_ui import callback_data, render


class Repository:
    def __init__(self):
        self.rows = {}

    async def get_game_state(self, chat, key):
        return deepcopy(self.rows.get((chat, key)))

    async def create_game_state_if_absent(self, chat, key, state):
        if (chat, key) in self.rows:
            return False
        self.rows[(chat, key)] = deepcopy(state)
        return True

    async def compare_and_save_game_state(self, chat, key, state, *, expected_version):
        previous = self.rows.get((chat, key))
        if previous is None or previous["version"] != expected_version:
            return False
        self.rows[(chat, key)] = deepcopy(state)
        return True


class Bot:
    def __init__(self):
        self.sent, self.edited, self.next_id = [], [], 100

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)
        self.next_id += 1
        return SimpleNamespace(message_id=self.next_id)

    async def edit_message_text(self, **kwargs):
        self.edited.append(kwargs)

    async def get_me(self):
        return SimpleNamespace(username="arelbot_test")


class Message:
    def __init__(self, message_id=1, chat_id=-100, thread_id=7, reply_to=None):
        self.message_id, self.chat_id = message_id, chat_id
        self.message_thread_id = thread_id
        self.is_topic_message = thread_id is not None
        self.reply_to_message = reply_to
        self.date = datetime.fromtimestamp(time.time() + 1, UTC)
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))
        return SimpleNamespace(message_id=300)


class Query:
    def __init__(self, data, user_id, message):
        self.data, self.message = data, message
        self.id = f"q:{user_id}:{data}"
        self.answers = []

    async def answer(self, text=None, **kwargs):
        self.answers.append((text, kwargs))


def update(user_id, message, query=None, private=False):
    return SimpleNamespace(effective_chat=SimpleNamespace(
        id=message.chat_id, type="private" if private else "supergroup"),
        effective_user=SimpleNamespace(id=user_id, first_name=f"U{user_id}"),
        effective_message=message, callback_query=query, edited_message=None)


@pytest.mark.asyncio
async def test_secret_never_enters_group_panel_and_wrong_actor_callback_rejected():
    svc, bot = WordArcadeService(Repository()), Bot()
    handler = WordArcadeHandler(svc)
    await svc.open(game_key="yasak_harf", chat_id=-100, thread_id=7,
                   user_id=1, user_name="Bir")
    state = await svc.current(game_key="yasak_harf", chat_id=-100, thread_id=7)
    await svc.act(game_key="yasak_harf", chat_id=-100, thread_id=7,
                  user_id=2, action="join", action_id="join", user_name="İki",
                  round_id=state["round_id"])
    await svc.act(game_key="yasak_harf", chat_id=-100, thread_id=7,
                  user_id=1, action="start", action_id="start",
                  round_id=state["round_id"], card_order=[0, 1, 2, 3])
    state = await svc.current(game_key="yasak_harf", chat_id=-100, thread_id=7)
    assert state["target"] not in render(state)
    await handler.publish(bot, game_key="yasak_harf", chat_id=-100, thread_id=7)
    state = await svc.current(game_key="yasak_harf", chat_id=-100, thread_id=7)
    panel = Message(message_id=state["panel_message_id"])
    wrong = Query(callback_data(state, "dm"), 2, panel)
    assert await handler.callback(update(2, panel, wrong), SimpleNamespace(bot=bot))
    assert wrong.answers[-1][1].get("show_alert")
    assert not any(state["target"] in sent["text"] for sent in bot.sent if sent["chat_id"] == -100)


@pytest.mark.asyncio
async def test_hunt_plain_group_answer_and_stale_reply_binding():
    svc, bot = WordArcadeService(Repository()), Bot()
    handler = WordArcadeHandler(svc)
    await svc.open(game_key="sozcuk_avi", chat_id=-100, thread_id=7,
                   user_id=1, user_name="Bir")
    state = await svc.current(game_key="sozcuk_avi", chat_id=-100, thread_id=7)
    await svc.act(game_key="sozcuk_avi", chat_id=-100, thread_id=7,
                  user_id=1, action="start", action_id="start", round_id=state["round_id"])
    await handler.publish(bot, game_key="sozcuk_avi", chat_id=-100, thread_id=7)
    state = await svc.current(game_key="sozcuk_avi", chat_id=-100, thread_id=7)
    ctx = SimpleNamespace(bot=bot)
    wrong_reply = Message(message_id=501, reply_to=Message(message_id=999))
    assert not await handler.on_text(update(1, wrong_reply), ctx, "kedi")
    valid = Message(message_id=502, reply_to=Message(message_id=state["panel_message_id"]))
    assert await handler.on_text(update(1, valid), ctx, "kedi")
    state = await svc.current(game_key="sozcuk_avi", chat_id=-100, thread_id=7)
    assert state["scores"]["1"] > 0


@pytest.mark.asyncio
async def test_marathon_private_reply_routes_by_actor_and_does_not_echo_secret():
    svc, bot = WordArcadeService(Repository()), Bot()
    handler = WordArcadeHandler(svc)
    await svc.open(game_key="harf_maratonu", chat_id=-100, thread_id=7,
                   user_id=1, user_name="Bir")
    state = await svc.current(game_key="harf_maratonu", chat_id=-100, thread_id=7)
    await svc.act(game_key="harf_maratonu", chat_id=-100, thread_id=7,
                  user_id=2, action="join", action_id="join", user_name="İki",
                  round_id=state["round_id"])
    await svc.act(game_key="harf_maratonu", chat_id=-100, thread_id=7,
                  user_id=1, action="start", action_id="start", round_id=state["round_id"],
                  card_order=[0, 1, 2, 3, 4])
    await svc.register_private(game_key="harf_maratonu", chat_id=-100,
        thread_id=7, user_id=1, round_id=state["round_id"], message_id=301)
    await handler.publish(bot, game_key="harf_maratonu", chat_id=-100, thread_id=7)
    state = await svc.current(game_key="harf_maratonu", chat_id=-100, thread_id=7)
    panel = Message(message_id=state["panel_message_id"])
    query = Query(callback_data(state, "dm"), 2, panel)
    assert await handler.callback(update(2, panel, query), SimpleNamespace(bot=bot))
    assert query.answers[-1][1]["url"].startswith("https://t.me/arelbot_test?start=wa_")
    token = query.answers[-1][1]["url"].split("start=", 1)[1]
    private_start = Message(message_id=500, chat_id=2, thread_id=None)
    assert await handler.deep_link(update(2, private_start, private=True),
                                   SimpleNamespace(bot=bot), token)
    route = [{"chat_id": -100, "game_type": "harf_maratonu_t7"}]
    private_answer = Message(message_id=501, chat_id=2, thread_id=None,
                             reply_to=Message(message_id=300, chat_id=2, thread_id=None))
    assert not await handler.on_private_text(update(3, private_answer, private=True),
                                             SimpleNamespace(bot=bot), "kedi", routes=route)
    assert await handler.on_private_text(update(2, private_answer, private=True),
                                         SimpleNamespace(bot=bot), "kedi", routes=route)
    state = await svc.current(game_key="harf_maratonu", chat_id=-100, thread_id=7)
    assert state["submissions"] == {"2": "kedi"}
    assert "kedi" not in render(state)
    assert not any("kedi" in item["text"] for item in bot.sent if item["chat_id"] == -100)


@pytest.mark.asyncio
async def test_forbidden_caption_edit_invalidates_clue_without_awarding_guess():
    svc, bot = WordArcadeService(Repository()), Bot()
    handler = WordArcadeHandler(svc)
    await svc.open(game_key="yasak_harf", chat_id=-100, thread_id=7,
                   user_id=1, user_name="Bir")
    state = await svc.current(game_key="yasak_harf", chat_id=-100, thread_id=7)
    await svc.act(game_key="yasak_harf", chat_id=-100, thread_id=7,
                  user_id=2, action="join", action_id="join", user_name="İki",
                  round_id=state["round_id"])
    await svc.act(game_key="yasak_harf", chat_id=-100, thread_id=7,
                  user_id=1, action="start", action_id="start", round_id=state["round_id"],
                  card_order=[0, 1, 2, 3])
    state = await svc.current(game_key="yasak_harf", chat_id=-100, thread_id=7)
    state.update(phase="clue", phase_seq=state["phase_seq"] + 1,
                 private_message_id=400, private_ack=True,
                 deadline=time.time() + 60, input_floor=100)
    state["version"] += 1
    assert await svc.repository.compare_and_save_game_state(-100, "yasak_harf_t7", state,
                                                             expected_version=state["version"] - 1)
    caption = Message(message_id=501)
    assert await handler.on_clue_text(update(1, caption), SimpleNamespace(bot=bot),
                                      "Bu bir yazı aracı.")
    state = await svc.current(game_key="yasak_harf", chat_id=-100, thread_id=7)
    assert state["clue_count"] == 1
    edit = update(1, caption)
    edit.edited_message = caption
    caption.edit_date = datetime.fromtimestamp(time.time() + 2, UTC)
    assert await handler.on_clue_text(edit, SimpleNamespace(bot=bot), "kalem")
    state = await svc.current(game_key="yasak_harf", chat_id=-100, thread_id=7)
    assert state["phase"] == "reveal" and state["scores"] == {"1": 0, "2": 0}


def test_accepts_text_uses_only_public_routing_metadata():
    handler = WordArcadeHandler(SimpleNamespace())
    assert handler.accepts_text({"game_key": "sozcuk_avi", "phase": "hunt"})
    assert handler.accepts_text({"game_key": "yasak_harf", "phase": "clue"})
    assert handler.accepts_text({"game_key": "ortak_hikaye", "phase": "story_turn"})
    assert handler.accepts_text({"game_key": "bomba", "phase": "bomb_turn"})
    assert not handler.accepts_text({"game_key": "harf_maratonu", "phase": "collect"})
