"""Telegram panel delivery is one group message with protected private boards."""
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.handlers.social_arcade_handler import SocialArcadeHandler
from src.tests.test_social_arcade_service import current, delivered, setup


class Bot:
    def __init__(self):
        self.sent = []
        self.edited = []
        self.counter = 40

    async def send_message(self, **kwargs):
        self.counter += 1
        self.sent.append(kwargs)
        return SimpleNamespace(message_id=self.counter)

    async def edit_message_text(self, **kwargs):
        self.edited.append(kwargs)
        return True


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
