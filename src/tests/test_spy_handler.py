"""Telegram adapter replay, DM and actor boundary tests for Casus."""
import time
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from telegram.error import BadRequest, Forbidden

from src.handlers.spy_handler import SpyHandler
from src.services.spy_service import SpyService
from src.services.spy_ui import callback_data


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
        row = self.rows.get((chat, key))
        if not row or row["version"] != expected_version:
            return False
        self.rows[(chat, key)] = deepcopy(state)
        return True


class Bot:
    def __init__(self):
        self.sent = []
        self.edited = []
        self.private_fail = set()
        self.group_fail = False
        self.next_id = 100

    async def send_message(self, **kwargs):
        if kwargs["chat_id"] == -100 and self.group_fail:
            raise BadRequest("Temporary group delivery failure")
        if kwargs["chat_id"] in self.private_fail:
            raise Forbidden("Forbidden")
        self.sent.append(kwargs)
        self.next_id += 1
        return SimpleNamespace(message_id=self.next_id)

    async def edit_message_text(self, **kwargs):
        if kwargs["chat_id"] == -100 and self.group_fail:
            raise BadRequest("Temporary group delivery failure")
        self.edited.append(kwargs)

    async def get_me(self):
        return SimpleNamespace(username="arel_test_bot")


class Message:
    def __init__(self, message_id=1, chat_id=-100, thread_id=7, when=100):
        self.message_id = message_id
        self.chat_id = chat_id
        self.message_thread_id = thread_id
        self.is_topic_message = thread_id is not None
        self.date = datetime.fromtimestamp(when, UTC)
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))
        return SimpleNamespace(message_id=200)


class Query:
    def __init__(self, data, user_id, message):
        self.data, self.message = data, message
        self.id = f"q{user_id}:{data}"
        self.answers = []

    async def answer(self, text=None, **kwargs):
        self.answers.append((text, kwargs))


def update(chat_type, user_id, message, query=None):
    return SimpleNamespace(effective_chat=SimpleNamespace(id=message.chat_id, type=chat_type),
                           effective_user=SimpleNamespace(id=user_id, first_name=f"U{user_id}"),
                           effective_message=message, callback_query=query, edited_message=None)


async def fixture_match():
    repo, bot = Repository(), Bot()
    svc = SpyService(repo)
    handler = SpyHandler(svc)
    await svc.open(chat_id=-100, thread_id=7, user_id=1, user_name="Bir")
    round_id = (await svc.current(-100, 7))["round_id"]
    for user in (2, 3, 4):
        await svc.act(chat_id=-100, thread_id=7, user_id=user, user_name=f"U{user}",
                      action_id=f"join:{user}", action="join", round_id=round_id)
    for user in (1, 2, 3, 4):
        payload = await svc.mint_invite(chat_id=-100, thread_id=7, user_id=user,
                                        round_id=round_id, request_id=f"ready:{user}")
        await svc.accept_invite(payload, user_id=user)
    await svc.act(chat_id=-100, thread_id=7, user_id=1,
                  action_id="start", action="start", round_id=round_id)
    return svc, handler, bot


@pytest.mark.asyncio
async def test_delivery_forbidden_retry_ack_and_no_group_secret():
    svc, handler, bot = await fixture_match()
    state = await svc.current(-100, 7)
    bot.private_fail.add(4)
    await handler.process(bot, {"chat_id": -100, "state": state})
    state = await svc.current(-100, 7)
    assert state["phase"] == "delivery"
    assert state["deadline"] <= state["created_at"] + 300 + 10
    assert sum(p["delivered"] for p in state["players"]) == 3
    assert all("Mekân:" not in sent["text"] for sent in bot.sent if sent["chat_id"] == -100)
    assert all("Casussun" not in sent["text"] for sent in bot.sent if sent["chat_id"] == -100)
    innocent_roles = [sent["text"].split("</b>", 1)[0]
                      for sent in bot.sent if sent["chat_id"] not in {-100, state["spy_id"]}]
    assert len(innocent_roles) == len(set(innocent_roles))
    bot.private_fail.clear()
    # A failed send leaves a durable 30-second lease, then retries.
    state["role_retry_4"] = 0
    state["version"] += 1
    assert await svc.repository.compare_and_save_game_state(-100, "casus_t7", state,
                                                              expected_version=state["version"] - 1)
    await handler.deliver_roles(bot, chat_id=-100, thread_id=7)
    state = await svc.current(-100, 7)
    assert all(p["delivered"] for p in state["players"])
    ctx = SimpleNamespace(bot=bot)
    for user in (1, 2, 3, 4):
        player = next(p for p in state["players"] if p["id"] == user)
        private_message = Message(message_id=player["dm_message_id"], chat_id=user, thread_id=None)
        q = Query(callback_data(state, "ack"), user, private_message)
        await handler.callback(update("private", user, private_message, q), ctx)
        assert q.answers[-1][0] is None
    assert (await svc.current(-100, 7))["phase"] == "question"


@pytest.mark.asyncio
async def test_callback_topic_panel_and_actor_rejection():
    svc, handler, bot = await fixture_match()
    state = await svc.current(-100, 7)
    await handler.publish(bot, chat_id=-100, thread_id=7)
    state = await svc.current(-100, 7)
    ctx = SimpleNamespace(bot=bot)
    wrong_topic = Message(message_id=state["panel_message_id"], chat_id=-100, thread_id=8)
    q = Query(callback_data(state, "leave"), 2, wrong_topic)
    await handler.callback(update("supergroup", 2, wrong_topic, q), ctx)
    assert q.answers[-1][1].get("show_alert") is True
    assert next(p for p in (await svc.current(-100, 7))["players"] if p["id"] == 2)["active"]
    old_panel = Message(message_id=state["panel_message_id"] - 1, chat_id=-100, thread_id=7)
    q2 = Query(callback_data(state, "leave"), 2, old_panel)
    await handler.callback(update("supergroup", 2, old_panel, q2), ctx)
    assert q2.answers[-1][1].get("show_alert") is True
    assert next(p for p in (await svc.current(-100, 7))["players"] if p["id"] == 2)["active"]


@pytest.mark.asyncio
async def test_ready_callback_opens_private_link_without_group_message():
    svc, handler, bot = await fixture_match()
    # Reopen a fresh lobby after cancelling the delivery round.
    state = await svc.current(-100, 7)
    await svc.act(chat_id=-100, thread_id=7, user_id=1, action_id="stop", action="stop",
                  round_id=state["round_id"])
    await svc.open(chat_id=-100, thread_id=7, user_id=1, user_name="Bir")
    await handler.publish(bot, chat_id=-100, thread_id=7)
    state = await svc.current(-100, 7)
    sent_count = len(bot.sent)
    message = Message(message_id=state["panel_message_id"], chat_id=-100, thread_id=7)
    query = Query(callback_data(state, "ready"), 1, message)
    await handler.callback(update("supergroup", 1, message, query), SimpleNamespace(bot=bot))
    assert query.answers[-1][1]["url"].startswith("https://t.me/arel_test_bot?start=sp_")
    assert len(bot.sent) == sent_count


@pytest.mark.asyncio
async def test_private_guess_buttons_do_not_leak_before_result():
    svc, handler, bot = await fixture_match()
    state = await svc.current(-100, 7)
    for user in (1, 2, 3, 4):
        await svc.act(chat_id=-100, thread_id=7, user_id=user,
                      action_id=f"delivered:{user}", action="delivered", round_id=state["round_id"],
                      message_id=100 + user)
        await svc.act(chat_id=-100, thread_id=7, user_id=user,
                      action_id=f"confirm:{user}", action="confirm", round_id=state["round_id"])
    state = await svc.current(-100, 7)
    await handler.publish(bot, chat_id=-100, thread_id=7)
    state = await svc.current(-100, 7)
    assert state["phase"] == "question" and state["deadline"] is not None
    spy_id = state["spy_id"]
    private_message = Message(message_id=100 + spy_id, chat_id=spy_id, thread_id=None)
    ctx = SimpleNamespace(bot=bot)
    from src.games import spy
    index = next(i for i, (place, _) in enumerate(spy.LOCATIONS) if place == state["location"])
    from src.services.spy_service import _b36
    for action in ("g0", f"s{_b36(index)}", f"c{_b36(index)}"):
        q = Query(callback_data(state, action), spy_id, private_message)
        await handler.callback(update("private", spy_id, private_message, q), ctx)
        assert q.answers[-1][0] is None
    assert (await svc.current(-100, 7))["winner"] == "spy"
    assert all(state["location"] not in sent["text"] for sent in bot.sent
               if sent["chat_id"] == -100 and "kazandı" not in sent["text"])


@pytest.mark.asyncio
async def test_private_guess_requires_selection_and_correct_actor():
    svc, handler, bot = await fixture_match()
    state = await svc.current(-100, 7)
    for user in (1, 2, 3, 4):
        await svc.act(chat_id=-100, thread_id=7, user_id=user,
                      action_id=f"delivered:{user}", action="delivered", round_id=state["round_id"],
                      message_id=100 + user)
        await svc.act(chat_id=-100, thread_id=7, user_id=user,
                      action_id=f"confirm:{user}", action="confirm", round_id=state["round_id"])
    await handler.publish(bot, chat_id=-100, thread_id=7)
    state = await svc.current(-100, 7)
    from src.games import spy
    from src.services.spy_service import _b36
    index = next(i for i, (place, _) in enumerate(spy.LOCATIONS) if place == state["location"])
    innocent = next(p["id"] for p in state["players"] if p["id"] != state["spy_id"])
    for actor in (innocent, state["spy_id"]):
        message = Message(message_id=100 + actor, chat_id=actor, thread_id=None)
        q = Query(callback_data(state, f"c{_b36(index)}"), actor, message)
        await handler.callback(update("private", actor, message, q), SimpleNamespace(bot=bot))
        assert q.answers[-1][1].get("show_alert") is True
    assert (await svc.current(-100, 7))["phase"] == "question"


@pytest.mark.asyncio
async def test_group_panel_failure_does_not_consume_question_time():
    svc, handler, bot = await fixture_match()
    state = await svc.current(-100, 7)
    for user in (1, 2, 3, 4):
        await svc.act(chat_id=-100, thread_id=7, user_id=user,
                      action_id=f"delivered:{user}", action="delivered",
                      round_id=state["round_id"], message_id=400 + user)
        await svc.act(chat_id=-100, thread_id=7, user_id=user,
                      action_id=f"confirm:{user}", action="confirm", round_id=state["round_id"])
    state = await svc.current(-100, 7)
    assert state["phase"] == "question" and state["deadline"] is None
    bot.group_fail = True
    with pytest.raises(BadRequest):
        await handler.publish(bot, chat_id=-100, thread_id=7)
    assert (await svc.current(-100, 7))["deadline"] is None
    bot.group_fail = False
    # A persisted failed lease becomes due again and only then starts the clock.
    state = await svc.current(-100, 7)
    state["panel_retry_at"] = 0
    state["version"] += 1
    assert await svc.repository.compare_and_save_game_state(-100, "casus_t7", state,
                                                              expected_version=state["version"] - 1)
    await handler.publish(bot, chat_id=-100, thread_id=7)
    assert (await svc.current(-100, 7))["deadline"] is not None


@pytest.mark.asyncio
async def test_unrecoverable_group_panel_failure_cancels_without_reward():
    svc, _, _ = await fixture_match()
    state = await svc.current(-100, 7)
    for user in (1, 2, 3, 4):
        await svc.act(chat_id=-100, thread_id=7, user_id=user,
                      action_id=f"delivered:{user}", action="delivered",
                      round_id=state["round_id"], message_id=500 + user)
        await svc.act(chat_id=-100, thread_id=7, user_id=user,
                      action_id=f"confirm:{user}", action="confirm", round_id=state["round_id"])
    state = await svc.current(-100, 7)
    assert state["deadline"] is None and state["panel_wait_until"] is not None
    await svc.advance(chat_id=-100, thread_id=7, round_id=state["round_id"],
                      now=state["panel_wait_until"])
    state = await svc.current(-100, 7)
    assert state["phase"] == "cancelled" and not state["score_pending"]


@pytest.mark.asyncio
async def test_full_question_discussion_private_vote_revote_reward_flow():
    svc, handler, bot = await fixture_match()
    class Scores:
        def __init__(self):
            self.events = set()
        async def record_score(self, *, event_id, **_):
            self.events.add(event_id)
    scores = Scores()
    svc.leaderboard = scores
    state = await svc.current(-100, 7)
    for user in (1, 2, 3, 4):
        await svc.act(chat_id=-100, thread_id=7, user_id=user,
                      action_id=f"delivered:{user}", action="delivered",
                      round_id=state["round_id"], message_id=300 + user)
        await svc.act(chat_id=-100, thread_id=7, user_id=user,
                      action_id=f"confirm:{user}", action="confirm", round_id=state["round_id"])
    state = await svc.current(-100, 7)
    assert state["deadline"] is None
    await handler.publish(bot, chat_id=-100, thread_id=7)
    state = await svc.current(-100, 7)
    assert state["deadline"] is not None
    ctx = SimpleNamespace(bot=bot)
    for turn in range(4):
        state = await svc.current(-100, 7)
        asker = state["question_order"][turn]
        target = state["question_order"][(turn + 1) % 4]
        panel = Message(message_id=state["panel_message_id"], chat_id=-100, thread_id=7,
                        when=time.time() + 1)
        from src.services.spy_service import _b36
        q = Query(callback_data(state, f"t{_b36(target)}.{_b36(turn)}"), asker, panel)
        await handler.callback(update("supergroup", asker, panel, q), ctx)
        assert q.answers[-1][0] is None
        for actor, phrase in ((asker, "Bugün kalabalık mı?"), (target, "Bazen kalabalık.")):
            message = Message(message_id=1000 + turn * 2 + (actor == target),
                              chat_id=-100, thread_id=7, when=time.time() + 1)
            assert await handler.on_text(update("supergroup", actor, message), ctx, phrase)
    state = await svc.current(-100, 7)
    assert state["phase"] == "discussion" and state["deadline"] is not None
    await svc.advance(chat_id=-100, thread_id=7, round_id=state["round_id"],
                      now=state["deadline"])
    state = await svc.current(-100, 7)
    assert state["phase"] == "vote" and state["deadline"] is None
    await handler.publish(bot, chat_id=-100, thread_id=7)
    for round_number in (1, 2):
        state = await svc.current(-100, 7)
        assert state["vote_round"] == round_number and state["deadline"] is not None
        for voter, target in ((1, 2), (2, 1), (3, 1), (4, 2)):
            panel = Message(message_id=state["panel_message_id"], chat_id=-100, thread_id=7,
                            when=time.time() + 1)
            q = Query(callback_data(state, f"v{_b36(target)}.{_b36(round_number)}"), voter, panel)
            await handler.callback(update("supergroup", voter, panel, q), ctx)
            assert q.answers[-1][0] is None
    state = await svc.current(-100, 7)
    assert state["phase"] == "finished" and state["winner"] == "spy"
    assert len(scores.events) == 4
    assert not state["score_pending"]
    await handler._flush(ctx, -100, 7)
    assert len(scores.events) == 4
    assert all("Oyuncu" in sent["text"] or "Casus" in sent["text"]
               for sent in bot.sent if sent["chat_id"] == -100)
