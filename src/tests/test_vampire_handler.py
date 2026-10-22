"""Telegram delivery, recipient identity, and failure recovery."""

from types import SimpleNamespace
import time
from copy import deepcopy

import pytest
from telegram.error import Forbidden

from src.games import vampire
from src.handlers.vampire_handler import VampireHandler
from src.services.vampire_service import VampireService
from src.services.vampire_ui import callback_data, group_keyboard, render_vampire
from src.tests.test_vampire_service import Repository, Leaderboard


class Bot:
    def __init__(self):
        self.sent = []
        self.edits = []
        self.fail = None

    async def send_message(self, **kwargs):
        if self.fail and kwargs["chat_id"] > 0:
            raise self.fail
        self.sent.append(kwargs)
        return SimpleNamespace(message_id=100 + len(self.sent))

    async def edit_message_text(self, **kwargs):
        self.edits.append(kwargs)

    async def get_me(self):
        return SimpleNamespace(username="TestBot")


class Query:
    def __init__(self, data, message, token="q1"):
        self.data, self.message, self.id = data, message, token
        self.answers = []

    async def answer(self, text="", **kwargs):
        self.answers.append((text, kwargs))


def event(user_id, chat_id, message_id, data, *, topic=None, token="q1"):
    message = SimpleNamespace(message_id=message_id, is_topic_message=topic is not None,
                              message_thread_id=topic)
    return SimpleNamespace(effective_user=SimpleNamespace(id=user_id, first_name="Oyuncu"),
                           effective_chat=SimpleNamespace(id=chat_id,
                               type="private" if chat_id > 0 else "supergroup"),
                           callback_query=Query(data, message, token=token))


def prepared_state():
    state = vampire.new_lobby(chat_id=-100, thread_id=7, owner_id=1,
                              owner_name="Bir", round_id="round", now=1)
    for uid in range(2, 8):
        state = vampire.join(state, user_id=uid, user_name=str(uid), now=2)
    for uid in range(1, 8):
        state = vampire.ready(state, user_id=uid, now=3)
    state = vampire.start(state, user_id=1,
        roles=["vampire", "seer", "healer", "villager", "villager", "villager", "villager"], now=4)
    state["private_dirty"] = True
    return state


@pytest.mark.asyncio
async def test_forbidden_and_transient_private_send_keep_role_unconfirmed():
    repo = Repository()
    service = VampireService(repo)
    state = prepared_state()
    repo.rows[(-100, service.key(7))] = state
    handler, bot = VampireHandler(service), Bot()
    bot.fail = Forbidden("blocked")
    await handler.deliver_private(bot, chat_id=-100, thread_id=7)
    current = await service.current(-100, 7)
    assert current["phase"] == "role_delivery" and current["private_dirty"]
    assert not any(p["role_ack"] for p in current["players"])
    with pytest.raises(ValueError, match="role_not_delivered"):
        await service.act(chat_id=-100, thread_id=7, user_id=1, action_id="ack", action="ack",
                          round_id=state["round_id"], phase_seq=state["phase_seq"])
    # A transient exception also preserves the persistent 30-second claim.
    current["private_retry"] = {}
    repo.rows[(-100, service.key(7))] = current
    bot.fail = RuntimeError("network")
    with pytest.raises(RuntimeError):
        await handler.deliver_private(bot, chat_id=-100, thread_id=7)


@pytest.mark.asyncio
async def test_private_buttons_are_recipient_and_message_bound():
    repo = Repository()
    service = VampireService(repo)
    state = prepared_state()
    state["last_activity_at"] = time.time()
    repo.rows[(-100, service.key(7))] = state
    handler, bot = VampireHandler(service), Bot()
    await handler.deliver_private(bot, chat_id=-100, thread_id=7)
    delivered = await service.current(-100, 7)
    assert all(kwargs["protect_content"] for kwargs in bot.sent)
    own = int(delivered["private_delivery"]["1"])
    data = callback_data(delivered, "ack")
    wrong = event(2, 2, own, data)
    await handler.callback(wrong, SimpleNamespace(bot=bot))
    assert wrong.callback_query.answers[0][1]["show_alert"]
    forged_message = event(1, 1, own + 1, data)
    await handler.callback(forged_message, SimpleNamespace(bot=bot))
    assert forged_message.callback_query.answers[0][1]["show_alert"]
    correct = event(1, 1, own, data)
    await handler.callback(correct, SimpleNamespace(bot=bot))
    assert correct.callback_query.answers[0][0] == ""
    assert next(p for p in (await service.current(-100, 7))["players"] if p["id"] == 1)["role_ack"]
    assert "Rolün" not in render_vampire(await service.current(-100, 7))


@pytest.mark.asyncio
async def test_panel_delivery_failure_does_not_start_phase_clock():
    repo = Repository()
    service = VampireService(repo)
    state = prepared_state()
    for uid in range(1, 8):
        state = vampire.role_ack(state, user_id=uid, now=5)
    state["panel_dirty"] = True
    repo.rows[(-100, service.key(7))] = state
    handler = VampireHandler(service)

    class FailingBot(Bot):
        async def send_message(self, **kwargs):
            if kwargs["chat_id"] < 0:
                raise RuntimeError("group delivery failed")
            return await super().send_message(**kwargs)

    with pytest.raises(RuntimeError):
        await handler.publish(FailingBot(), chat_id=-100, thread_id=7)
    current = await service.current(-100, 7)
    assert current["phase"] == "night" and current["deadline"] is None
    assert not current["phase_ready"]
    assert current["panel_wait_until"] is not None
    await service.advance(chat_id=-100, thread_id=7, round_id="round",
                          now=current["panel_wait_until"])
    closed = await service.current(-100, 7)
    assert closed["phase"] == "cancelled" and closed["cancel_reason"] == "panel_delivery_timeout"
    assert not closed["score_pending"]


@pytest.mark.asyncio
async def test_one_blocked_night_dm_keeps_clock_frozen_until_retry_succeeds():
    repo = Repository()
    service = VampireService(repo)
    state = prepared_state()
    for uid in range(1, 8):
        state = vampire.role_ack(state, user_id=uid, now=5)
    state.update(panel_ready=True, phase_ready=False, deadline=None,
                 panel_wait_until=time.time() + 300, panel_dirty=False)
    repo.rows[(-100, service.key(7))] = state

    class OneBlockedBot(Bot):
        blocked = True

        async def send_message(self, **kwargs):
            if self.blocked and kwargs["chat_id"] == 2:
                raise Forbidden("blocked")
            return await super().send_message(**kwargs)

    bot = OneBlockedBot()
    handler = VampireHandler(service)
    await handler.deliver_private(bot, chat_id=-100, thread_id=7)
    current = await service.current(-100, 7)
    assert set(current["private_delivery"]) == {"1", "3"}
    assert current["panel_ready"] and not current["phase_ready"]
    assert current["deadline"] is None and not current["panel_dirty"]
    abandoned_repo = Repository()
    abandoned_repo.rows[(-100, service.key(7))] = deepcopy(current)
    abandoned = VampireService(abandoned_repo)
    await abandoned.advance(chat_id=-100, thread_id=7, round_id="round",
                            now=current["panel_wait_until"])
    cancelled = await abandoned.current(-100, 7)
    assert cancelled["phase"] == "cancelled" and not cancelled["score_pending"]
    with pytest.raises(ValueError, match="stale_phase"):
        await service.act(chat_id=-100, thread_id=7, user_id=1,
            action_id="too-early", action="night", target_id=4,
            round_id="round", phase_seq=current["phase_seq"])
    current["private_retry"]["2"] = 0
    repo.rows[(-100, service.key(7))] = current
    bot.blocked = False
    await handler.deliver_private(bot, chat_id=-100, thread_id=7)
    current = await service.current(-100, 7)
    assert current["phase_ready"] and current["deadline"] > time.time()
    assert current["panel_wait_until"] is None and current["panel_dirty"]


@pytest.mark.asyncio
async def test_group_forgery_other_topic_and_old_panel_rejected():
    repo = Repository()
    service = VampireService(repo)
    _, state = await service.open(chat_id=-100, thread_id=7, user_id=1, user_name="Bir")
    state["panel_message_id"] = 42
    repo.rows[(-100, service.key(7))] = state
    handler, bot = VampireHandler(service), Bot()
    for chat_id, topic, message_id in [(-100, 8, 42), (-100, 7, 41), (1, None, 42)]:
        update = event(1, chat_id, message_id, callback_data(state, "stop"), topic=topic)
        await handler.callback(update, SimpleNamespace(bot=bot))
        assert update.callback_query.answers[0][1]["show_alert"]
    assert (await service.current(-100, 7))["phase"] == "lobby"


@pytest.mark.asyncio
async def test_ready_link_is_callback_only_and_adds_no_group_message():
    repo = Repository()
    service = VampireService(repo)
    _, state = await service.open(chat_id=-100, thread_id=7, user_id=1, user_name="Bir")
    state["panel_message_id"] = 42
    state["panel_dirty"] = False
    repo.rows[(-100, service.key(7))] = state
    handler, bot = VampireHandler(service), Bot()
    update = event(1, -100, 42, callback_data(state, "ready"), topic=7)
    await handler.callback(update, SimpleNamespace(bot=bot))
    assert len(update.callback_query.answers) == 1
    assert update.callback_query.answers[0][1]["url"].startswith("https://t.me/TestBot?start=vp_")
    assert bot.sent == []


@pytest.mark.asyncio
async def test_lobby_setting_buttons_are_visible_owner_only_and_stale_safe():
    repo = Repository()
    service = VampireService(repo)
    _, state = await service.open(chat_id=-100, thread_id=7, user_id=1, user_name="Bir")
    state["panel_message_id"] = 42
    state["panel_dirty"] = False
    repo.rows[(-100, service.key(7))] = state
    panel = render_vampire(state)
    assert "Art arda aynı kişiyi koruma: kapalı" in panel
    assert "Şifacının kendini iyileştirmesi: açık" in panel
    assert "Ölümde rol açıklama: kapalı" in panel
    assert len(group_keyboard(state).inline_keyboard) >= 6
    handler, bot = VampireHandler(service), Bot()
    data = callback_data(state, "repeat_protection", 1)
    other = event(2, -100, 42, data, topic=7, token="wrong-owner")
    await handler.callback(other, SimpleNamespace(bot=bot))
    assert other.callback_query.answers[0][1]["show_alert"]
    assert not (await service.current(-100, 7))["settings"]["repeat_protection"]
    owner = event(1, -100, 42, data, topic=7, token="owner")
    await handler.callback(owner, SimpleNamespace(bot=bot))
    assert owner.callback_query.answers[0][0] == ""
    assert (await service.current(-100, 7))["settings"]["repeat_protection"]
    stale = event(1, -100, 42, data, topic=7, token="stale")
    await handler.callback(stale, SimpleNamespace(bot=bot))
    assert stale.callback_query.answers[0][1]["show_alert"]


@pytest.mark.asyncio
async def test_lobby_setting_expiry_rejects_mutation():
    service = VampireService(Repository())
    _, state = await service.open(chat_id=-100, user_id=1, user_name="Bir")
    with pytest.raises(ValueError, match="stale_phase"):
        await service.act(chat_id=-100, user_id=1, action_id="setting-expired",
            action="configure", round_id=state["round_id"], option="self_heal", enabled=False,
            now=state["last_activity_at"] + vampire.LOBBY_SECONDS)
    assert (await service.current(-100))["settings"]["self_heal"]


@pytest.mark.asyncio
async def test_committed_seer_finding_survives_send_failure_and_restart():
    repo = Repository()
    service = VampireService(repo)
    state = prepared_state()
    for uid in range(1, 8):
        state = vampire.role_ack(state, user_id=uid, now=5)
    state = vampire.activate(state, now=time.time())
    state["private_delivery"] = {"2": 77}
    state["panel_dirty"] = False
    repo.rows[(-100, service.key(7))] = state

    class FailingFindingBot(Bot):
        async def send_message(self, **kwargs):
            if "Kâhin sonucu" in kwargs["text"]:
                raise RuntimeError("temporary")
            return await super().send_message(**kwargs)

    bot = FailingFindingBot()
    handler = VampireHandler(service)
    update = event(2, 2, 77, callback_data(state, "night", 1), token="seer")
    await handler.callback(update, SimpleNamespace(bot=bot))
    committed = await service.current(-100, 7)
    finding = committed["seer_findings"]["1:2"]
    assert committed["actions"]["2"] == 1
    assert not finding["delivered"] and committed["private_dirty"]
    assert "Kâhin sonucu" not in render_vampire(committed)
    await service.advance(chat_id=-100, thread_id=7, round_id="round", now=committed["deadline"])
    committed = await service.current(-100, 7)
    assert committed["phase"] == "discussion" and committed["actions"] == {}
    # The worker can resume from persisted state after a restart and lease expiry.
    committed["seer_findings"]["1:2"]["retry_at"] = 0
    repo.rows[(-100, service.key(7))] = committed
    restarted = VampireHandler(VampireService(repo))
    recovered_bot = Bot()
    await restarted.deliver_findings(recovered_bot, chat_id=-100, thread_id=7)
    assert recovered_bot.sent[0]["chat_id"] == 2 and recovered_bot.sent[0]["protect_content"]
    assert (await restarted.service.current(-100, 7))["seer_findings"]["1:2"]["delivered"]


@pytest.mark.asyncio
async def test_full_handler_match_private_night_public_morning_vote_and_reward():
    repo, leaderboard = Repository(), Leaderboard()
    service = VampireService(repo, leaderboard=leaderboard)
    state = prepared_state()
    state["last_activity_at"] = time.time()
    repo.rows[(-100, service.key(7))] = state
    handler, bot = VampireHandler(service), Bot()
    await handler.deliver_private(bot, chat_id=-100, thread_id=7)
    for uid in range(1, 8):
        current = await service.current(-100, 7)
        private_id = int(current["private_delivery"][str(uid)])
        update = event(uid, uid, private_id, callback_data(current, "ack"), token=f"ack:{uid}")
        await handler.callback(update, SimpleNamespace(bot=bot))
        assert update.callback_query.answers[0][0] == ""
    current = await service.current(-100, 7)
    assert current["phase"] == "night" and current["phase_ready"]
    assert current["deadline"] > time.time()
    assert all("Rolün" not in sent["text"] for sent in bot.sent if sent["chat_id"] < 0)
    for uid, target in ((1, 4), (3, 4), (2, 1)):
        current = await service.current(-100, 7)
        private_id = int(current["private_delivery"][str(uid)])
        update = event(uid, uid, private_id, callback_data(current, "night", target), token=f"night:{uid}")
        await handler.callback(update, SimpleNamespace(bot=bot))
        assert update.callback_query.answers[0][0] == ""
    assert any("Kâhin sonucu" in sent["text"] and sent["chat_id"] == 2
               for sent in bot.sent)
    current = await service.current(-100, 7)
    await service.advance(chat_id=-100, thread_id=7, round_id="round", now=current["deadline"])
    await handler.process(bot, {"chat_id": -100, "state": await service.current(-100, 7)})
    current = await service.current(-100, 7)
    assert current["phase"] == "discussion" and current["last_deaths"] == []
    assert "Şifacı" not in render_vampire(current)
    await service.advance(chat_id=-100, thread_id=7, round_id="round", now=current["deadline"])
    await handler.process(bot, {"chat_id": -100, "state": await service.current(-100, 7)})
    current = await service.current(-100, 7)
    assert current["phase"] == "vote" and current["phase_ready"]
    for uid in (2, 3, 4, 5):
        current = await service.current(-100, 7)
        private_id = int(current["private_delivery"][str(uid)])
        update = event(uid, uid, private_id, callback_data(current, "vote", 1), token=f"vote:{uid}")
        await handler.callback(update, SimpleNamespace(bot=bot))
        assert update.callback_query.answers[0][0] == ""
    current = await service.current(-100, 7)
    await service.advance(chat_id=-100, thread_id=7, round_id="round", now=current["deadline"])
    await handler.process(bot, {"chat_id": -100, "state": await service.current(-100, 7)})
    current = await service.current(-100, 7)
    assert current["phase"] == "finished" and current["winner"] == "villagers"
    assert len(leaderboard.events) == 7
    await handler.process(bot, {"chat_id": -100, "state": current})
    assert len(leaderboard.events) == 7
    assert "Vampir" in render_vampire(current)
