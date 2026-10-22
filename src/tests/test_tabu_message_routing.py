"""Tabu clues must be checked before media-link routing."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.handlers.message_handler import MessageHandler
from src.handlers.tabu_handler import TabuHandler
from src.services.tabu_service import TabuService
from src.games import tabu
from src.tests.test_tabu_service import prepared


def _incoming(text):
    message = SimpleNamespace(text=text)
    return SimpleNamespace(
        message=message,
        effective_message=message,
        effective_chat=SimpleNamespace(id=-100, type="supergroup"),
        effective_user=SimpleNamespace(id=7),
    )


@pytest.mark.asyncio
async def test_active_tabu_clue_with_media_url_reaches_game_before_media_queue():
    handler = object.__new__(MessageHandler)
    handler.moderation = None
    handler.media_service = object()
    handler.sudo_manager = SimpleNamespace(is_blacklisted=lambda *_: False)
    handler.tabu_handler = SimpleNamespace(on_text=AsyncMock(return_value=True))
    handler.orchestrator_media_link = AsyncMock()
    incoming = _incoming("Yasak kelime https://youtube.com/watch?v=example")

    await asyncio.wait_for(handler.handle_message(incoming, SimpleNamespace()), timeout=2)

    handler.tabu_handler.on_text.assert_awaited_once()
    handler.orchestrator_media_link.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_tabu_media_url_keeps_media_routing():
    handler = object.__new__(MessageHandler)
    handler.moderation = None
    handler.media_service = object()
    handler.sudo_manager = SimpleNamespace(is_blacklisted=lambda *_: False)
    handler.tabu_handler = SimpleNamespace(on_text=AsyncMock(return_value=False))
    handler.orchestrator_media_link = AsyncMock()
    incoming = _incoming("https://youtube.com/watch?v=example")

    await asyncio.wait_for(handler.handle_message(incoming, SimpleNamespace()), timeout=2)

    handler.orchestrator_media_link.assert_awaited_once()


async def _active_tabu():
    service, _ = await prepared()
    _, card = await service.act(chat_id=-100, thread_id=7, user_id=1, action="start", action_id="start")
    await service.act(chat_id=-100, thread_id=7, user_id=1, action="delivered",
                      action_id="delivered", card_id=card["card_id"])
    await service.act(chat_id=-100, thread_id=7, user_id=1, action="ack",
                      action_id="ack", card_id=card["card_id"], input_floor=100)
    adapter = TabuHandler(service)
    adapter._flush = AsyncMock()
    handler = object.__new__(MessageHandler)
    handler.moderation = None
    handler.media_service = None
    handler.tabu_handler = adapter
    return service, handler


def _tabu_update(text, *, edited=False, message_id=101, date=None, user_id=1):
    date = date or datetime.now(timezone.utc)
    message = SimpleNamespace(text=text, message_id=message_id, date=date,
                              edit_date=datetime.now(timezone.utc) if edited else None,
                              message_thread_id=7, is_topic_message=True)
    return SimpleNamespace(
        message=None if edited else message,
        edited_message=message if edited else None,
        effective_message=message,
        effective_chat=SimpleNamespace(id=-100, type="supergroup"),
        effective_user=SimpleNamespace(id=user_id),
    )


@pytest.mark.asyncio
async def test_edited_narrator_clue_is_rechecked_for_forbidden_expression():
    service, handler = await _active_tabu()
    state = await service.current(-100, 7)
    forbidden = tabu.private_card(state, user_id=1, card_id=state["card_id"])["forbidden"][0]
    original = _tabu_update("Bu bir ipucu")

    await asyncio.wait_for(handler.handle_message(original, SimpleNamespace()), timeout=2)
    assert (await service.current(-100, 7))["phase"] == "active"

    edited = _tabu_update(forbidden, edited=True, date=original.message.date)
    await asyncio.wait_for(handler.handle_message(edited, SimpleNamespace()), timeout=2)

    current = await service.current(-100, 7)
    assert current["phase"] == "appeal"
    assert current["pending_outcome"] == "forbidden"


@pytest.mark.asyncio
@pytest.mark.parametrize("next_card_floor", [100, 200])
async def test_edit_of_previous_card_cannot_affect_next_active_card(next_card_floor):
    service, handler = await _active_tabu()
    original = _tabu_update("Bu bir ipucu")
    await asyncio.wait_for(handler.handle_message(original, SimpleNamespace()), timeout=2)
    state = await service.current(-100, 7)
    state["card_id"] = "next-card"
    state["active_message_floor"] = next_card_floor
    state["active_since"] = original.message.date.timestamp() + 1
    service.repository.rows[-100, "tabu_t7"] = state
    forbidden = state["card"]["forbidden"][0]

    edited = _tabu_update(forbidden, edited=True, date=original.message.date)
    await asyncio.wait_for(handler.handle_message(edited, SimpleNamespace()), timeout=2)

    current = await service.current(-100, 7)
    assert current["phase"] == "active"
    assert current["card_id"] == "next-card"


@pytest.mark.asyncio
async def test_edited_guess_cannot_win_card():
    service, handler = await _active_tabu()
    state = await service.current(-100, 7)
    target = state["card"]["target"]
    original = _tabu_update("Yanlış tahmin", user_id=3)
    await asyncio.wait_for(handler.handle_message(original, SimpleNamespace()), timeout=2)

    edited = _tabu_update(target, edited=True, date=original.message.date, user_id=3)
    await asyncio.wait_for(handler.handle_message(edited, SimpleNamespace()), timeout=2)

    assert (await service.current(-100, 7))["phase"] == "active"
