"""Telegram panel delivery is one group message with protected private boards."""
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from telegram.error import Forbidden

from src.games import social_arcade as rules
from src.handlers.social_arcade_handler import SocialArcadeHandler
from src.services import social_arcade_ui as ui
from src.services.social_arcade_service import SocialArcadeService
from src.tests.test_social_arcade_service import Repository, current, delivered, setup


class Bot:
    def __init__(self):
        self.sent = []
        self.edited = []
        self.deleted = []
        self.counter = 40

    async def send_message(self, **kwargs):
        self.counter += 1
        self.sent.append(kwargs)
        return SimpleNamespace(message_id=self.counter)

    async def edit_message_text(self, **kwargs):
        self.edited.append(kwargs)
        return True

    async def do_api_request(self, endpoint, api_kwargs):
        self.sent.append({"endpoint": endpoint, **api_kwargs})
        self.counter += 1
        if endpoint == "deleteEphemeralMessage":
            return True
        return {"message_id": 0,
                "receiver_user": {"id": api_kwargs["ephemeral_message_parameters"]["receiver_user_id"]},
                "ephemeral_message_id": self.counter}

    async def delete_message(self, **kwargs):
        self.deleted.append(kwargs)
        return True


@pytest.mark.parametrize("key", rules.GAME_KEYS)
def test_cancelled_unstarted_social_lobby_renders_without_round_fields(key):
    lobby = rules.new(game_key=key, chat_id=-100, thread_id=8,
                      owner_id=1, owner_name="Kurucu", round_id="cancel", now=100)
    cancelled = rules.act(lobby, user_id=1, action="stop", now=101)
    text = ui.group_text(cancelled)
    assert cancelled["phase"] == "cancelled"
    assert "ödülsüz" in text
    assert ui.group_keyboard(cancelled).inline_keyboard


@pytest.mark.asyncio
async def test_cancelled_identity_lobby_retries_and_clears_stale_group_panel(monkeypatch):
    monkeypatch.setattr("src.services.social_arcade_service.time.time", lambda: 101)
    service = SocialArcadeService(Repository())
    _, lobby = await service.open(game_key="kimim_ben", chat_id=-100,
                                  thread_id=8, user_id=1, user_name="Kurucu", now=100)
    bot = Bot()
    handler = SocialArcadeHandler(service)
    await handler.process(bot, {"state": lobby})
    current_lobby = await current(service, "kimim_ben")
    await service.act(game_key="kimim_ben", chat_id=-100, thread_id=8,
                      user_id=1, action_id="stop:test", action="stop",
                      round_id=current_lobby["round_id"], phase_seq=current_lobby["phase_seq"])
    await handler.process(bot, {"state": await current(service, "kimim_ben")})
    closed = await current(service, "kimim_ben")
    assert closed["phase"] == "cancelled" and closed["panel_dirty"] is False
    assert any("ödülsüz" in edit["text"] for edit in bot.edited)


@pytest.mark.asyncio
async def test_battleship_one_group_panel_and_protected_secret_boards(monkeypatch):
    monkeypatch.setattr("src.services.social_arcade_service.time.time", lambda: 103)
    service, _, _ = await setup("amiral_batti", 2)
    bot = Bot()
    handler = SocialArcadeHandler(service)
    state = await current(service, "amiral_batti")
    await handler.process(bot, {"chat_id": -100, "state": state})
    state = await current(service, "amiral_batti")
    group = [entry for entry in bot.sent if entry["chat_id"] == -100]
    private = [entry for entry in bot.sent if entry["chat_id"] in {1, 2}]
    assert len(group) == 1
    assert len(private) == 2
    assert all(entry["protect_content"] is True for entry in private)
    assert state["phase_ready"] and state["deadline"] == 163
    assert len(bot.edited) >= 1
    assert "Kendi gemilerin" not in group[0]["text"]
    assert all("Kendi gemilerin" in entry["text"] for entry in private)


@pytest.mark.asyncio
async def test_author_private_input_receipt_and_single_group_panel(monkeypatch):
    monkeypatch.setattr("src.services.social_arcade_service.time.time", lambda: 103)
    service, _, _ = await setup("iki_dogru_bir_yalan", 3)
    bot = Bot()
    handler = SocialArcadeHandler(service)
    state = await current(service, "iki_dogru_bir_yalan")
    await handler.process(bot, {"chat_id": -100, "state": state})
    state = await current(service, "iki_dogru_bir_yalan")
    assert state["phase_ready"] and state["deadline"] == 223
    assert len([entry for entry in bot.sent if entry["chat_id"] == -100]) == 1
    assert len([entry for entry in bot.sent if entry["chat_id"] == 1]) == 1
    assert not any(entry["chat_id"] in {2, 3} for entry in bot.sent)


@pytest.mark.asyncio
async def test_private_text_routes_only_canonical_reply_and_question_prefix(monkeypatch):
    monkeypatch.setattr("src.services.social_arcade_service.time.time", lambda: 104)
    service, _, _ = await setup("kimim_ben", 3)
    await delivered(service, "kimim_ben", t=103)
    state = await current(service, "kimim_ben")
    receipt = state["private_panels"]["1"]
    replies = []

    async def reply_text(value):
        replies.append(value)

    message = SimpleNamespace(message_id=receipt["message_id"] + 1,
        reply_to_message=SimpleNamespace(message_id=receipt["message_id"]),
        date=datetime.fromtimestamp(104, tz=UTC), reply_text=reply_text)
    update = SimpleNamespace(effective_chat=SimpleNamespace(type="private"),
        effective_user=SimpleNamespace(id=1), effective_message=message, edited_message=None)
    handler = SocialArcadeHandler(service)
    consumed = await handler.on_private_text(update, SimpleNamespace(bot=Bot()),
        "? Bu kişi insan mı?", routes=[{"chat_id": -100, "game_type": "kimim_ben_t8"}])
    state = await current(service, "kimim_ben")
    assert consumed and not replies and state["phase"] == "answer"
    assert state["question_text"] == "Bu kişi insan mı?"


def _group_callback(state, user_id, code):
    answers = []

    async def answer(*args, **kwargs):
        answers.append((args, kwargs))

    query = SimpleNamespace(id=f"q-{user_id}-{code}",
        data=ui.callback_data(state, code), answer=answer,
        message=SimpleNamespace(message_id=state["panel_message_id"],
                                is_topic_message=True, message_thread_id=8))
    update = SimpleNamespace(callback_query=query,
        effective_chat=SimpleNamespace(id=-100, type="supergroup"),
        effective_user=SimpleNamespace(id=user_id, first_name=f"Oyuncu {user_id}"))
    return update, answers


@pytest.mark.asyncio
async def test_identity_lobby_uses_existing_dm_without_start(monkeypatch):
    monkeypatch.setattr("src.services.social_arcade_service.time.time", lambda: 101)
    service = SocialArcadeService(Repository())
    _, state = await service.open(game_key="kimim_ben", chat_id=-100,
        thread_id=8, user_id=1, user_name="Kurucu", now=100)
    bot = Bot()
    handler = SocialArcadeHandler(service)
    await handler.process(bot, {"state": state})
    state = await current(service, "kimim_ben")
    update, answers = _group_callback(state, 1, "ready")
    await handler.callback(update, SimpleNamespace(bot=bot))
    state = await current(service, "kimim_ben")
    assert state["players"][0]["ready"] is True
    assert state["dm_invites"] == {}
    assert any(entry["chat_id"] == 1 and entry["protect_content"] for entry in bot.sent)
    assert answers[-1][0] == ("Özel sohbet hazır.",)


@pytest.mark.asyncio
async def test_identity_ready_button_is_idempotent_after_dm_is_open(monkeypatch):
    monkeypatch.setattr("src.services.social_arcade_service.time.time", lambda: 101)
    service = SocialArcadeService(Repository())
    _, state = await service.open(game_key="kimim_ben", chat_id=-100,
        thread_id=8, user_id=1, user_name="Kurucu", now=100)
    bot = Bot()
    handler = SocialArcadeHandler(service)
    await handler.process(bot, {"state": state})
    first, _ = _group_callback(await current(service, "kimim_ben"), 1, "ready")
    await handler.callback(first, SimpleNamespace(bot=bot))
    sent_before = len(bot.sent)
    second, answers = _group_callback(await current(service, "kimim_ben"), 1, "ready")
    await handler.callback(second, SimpleNamespace(bot=bot))
    assert len(bot.sent) == sent_before
    assert answers[-1][0] == ("Özel sohbetin zaten hazır.",)


@pytest.mark.asyncio
async def test_identity_lobby_falls_back_to_link_if_dm_is_closed(monkeypatch):
    monkeypatch.setattr("src.services.social_arcade_service.time.time", lambda: 101)
    service = SocialArcadeService(Repository())
    _, state = await service.open(game_key="kimim_ben", chat_id=-100,
        thread_id=8, user_id=1, user_name="Kurucu", now=100)

    class ClosedBot(Bot):
        async def send_message(self, **kwargs):
            if kwargs["chat_id"] == 1:
                raise Forbidden("bot blocked")
            return await super().send_message(**kwargs)

        async def get_me(self):
            return SimpleNamespace(username="test_bot")

    bot = ClosedBot()
    handler = SocialArcadeHandler(service)
    await handler.process(bot, {"state": state})
    update, answers = _group_callback(await current(service, "kimim_ben"), 1, "ready")
    await handler.callback(update, SimpleNamespace(bot=bot))
    state = await current(service, "kimim_ben")
    assert state["players"][0]["ready"] is False
    assert state["dm_invites"].get("1")
    assert answers[-1][1]["url"].startswith("https://t.me/test_bot?start=sa_")


@pytest.mark.asyncio
async def test_identity_card_is_ephemeral_and_subject_cannot_request_it(monkeypatch):
    monkeypatch.setattr("src.services.social_arcade_service.time.time", lambda: 103)
    service, _, _ = await setup("kimim_ben", 3)

    bot = Bot()
    handler = SocialArcadeHandler(service)
    await handler.process(bot, {"state": await current(service, "kimim_ben")})
    state = await current(service, "kimim_ben")
    subject = state["players"][state["subject_index"]]["id"]
    helper = next(p["id"] for p in state["players"] if p["id"] != subject)
    update, answers = _group_callback(state, helper, "card")
    await handler.callback(update, SimpleNamespace(bot=bot))
    overlays = [entry for entry in bot.sent if entry.get("endpoint") == "sendMessage"]
    assert len(overlays) == 2
    assert overlays[0]["ephemeral_message_parameters"] == {
        "receiver_user_id": helper, "callback_query_id": update.callback_query.id}
    assert state["identities"][str(subject)] not in overlays[0]["text"]
    assert state["identities"][str(subject)] in overlays[1]["text"]
    assert overlays[1]["ephemeral_message_parameters"]["receiver_user_id"] == helper
    assert all(state["identities"][str(subject)] not in entry.get("text", "")
               for entry in bot.sent if entry["chat_id"] == -100 and not entry.get("ephemeral_message_parameters"))
    assert any(entry.get("endpoint") == "deleteEphemeralMessage" for entry in bot.sent)
    assert "yalnız sana" in answers[-1][0][0]
    count = len(bot.sent)
    update, answers = _group_callback(state, subject, "card")
    await handler.callback(update, SimpleNamespace(bot=bot))
    assert len(bot.sent) == count and answers[-1][1]["show_alert"] is True
    update, answers = _group_callback(state, 999, "card")
    await handler.callback(update, SimpleNamespace(bot=bot))
    assert len(bot.sent) == count and answers[-1][1]["show_alert"] is True


@pytest.mark.asyncio
async def test_identity_card_deletes_unverified_group_probe(monkeypatch):
    monkeypatch.setattr("src.services.social_arcade_service.time.time", lambda: 103)
    service, _, _ = await setup("kimim_ben", 3)

    class LegacyBot(Bot):
        async def do_api_request(self, endpoint, api_kwargs):
            self.sent.append({"endpoint": endpoint, **api_kwargs})
            return {"message_id": 999}

    bot = LegacyBot()
    handler = SocialArcadeHandler(service)
    await handler.process(bot, {"state": await current(service, "kimim_ben")})
    state = await current(service, "kimim_ben")
    subject = state["players"][state["subject_index"]]["id"]
    helper = next(p["id"] for p in state["players"] if p["id"] != subject)
    update, answers = _group_callback(state, helper, "card")
    await handler.callback(update, SimpleNamespace(bot=bot))
    assert bot.deleted == [{"chat_id": -100, "message_id": 999}]
    assert state["identities"][str(subject)] not in bot.sent[-1]["text"]
    assert state["identities"][str(subject)] in answers[-1][0][0]


@pytest.mark.asyncio
async def test_identity_card_falls_back_to_private_alert_if_second_ephemeral_send_is_unverified(monkeypatch):
    monkeypatch.setattr("src.services.social_arcade_service.time.time", lambda: 103)
    service, _, _ = await setup("kimim_ben", 3)

    class DowngradedBot(Bot):
        async def do_api_request(self, endpoint, api_kwargs):
            if endpoint == "sendMessage" and any(
                entry.get("endpoint") == "sendMessage" for entry in self.sent):
                self.sent.append({"endpoint": endpoint, **api_kwargs})
                return {"message_id": 998}
            return await super().do_api_request(endpoint, api_kwargs)

    bot = DowngradedBot()
    handler = SocialArcadeHandler(service)
    await handler.process(bot, {"state": await current(service, "kimim_ben")})
    state = await current(service, "kimim_ben")
    subject = state["players"][state["subject_index"]]["id"]
    helper = next(p["id"] for p in state["players"] if p["id"] != subject)
    update, answers = _group_callback(state, helper, "card")
    await handler.callback(update, SimpleNamespace(bot=bot))
    assert {"chat_id": -100, "message_id": 998} in bot.deleted
    assert state["identities"][str(subject)] in answers[-1][0][0]
    assert answers[-1][1]["show_alert"] is True
