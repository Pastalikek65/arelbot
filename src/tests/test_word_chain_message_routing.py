"""Ordinary group words are bounded word-chain turns."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import unicodedata

import pytest

from src.handlers.message_handler import MessageHandler
from src.services.word_chain_service import WordChainService
from src.tests.test_word_chain import Repository
from src.services.word_chain_ui import word_chain_keyboard


async def _playing(*, topic=None):
    service = WordChainService(Repository())
    await service.open(chat_id=-100, thread_id=topic, user_id=1, user_name="İlk")
    await service.act(chat_id=-100, thread_id=topic, user_id=2, user_name="İkinci",
                      action_id="cmd:10", action="join")
    await service.act(chat_id=-100, thread_id=topic, user_id=1,
                      action_id="cmd:11", action="start")
    handler = object.__new__(MessageHandler)
    handler.moderation = None
    handler.media_service = None
    handler.word_chain_service = service
    handler.tabu_handler = SimpleNamespace(on_text=AsyncMock(return_value=False))
    return service, handler


def _update(text, *, user_id=1, message_id=12, topic=None, is_topic=False):
    message = SimpleNamespace(
        text=text, message_id=message_id, date=datetime.now(timezone.utc),
        message_thread_id=topic, is_topic_message=is_topic, reply_text=AsyncMock(),
    )
    return SimpleNamespace(
        message=message, edited_message=None, effective_message=message,
        effective_chat=SimpleNamespace(id=-100, type="supergroup"),
        effective_user=SimpleNamespace(id=user_id, first_name=f"Oyuncu{user_id}"),
    )


@pytest.mark.asyncio
async def test_turn_owner_plain_word_scores_and_duplicate_is_one_move():
    service, handler = await _playing()
    incoming = _update("kalem", topic=95, is_topic=False)

    await asyncio.wait_for(handler.handle_message(incoming, SimpleNamespace()), timeout=2)
    state = await service.current(-100, None)
    assert state["last_word"] == "kalem" and state["scores"]["1"] == 5
    assert state["turn_count"] == 1
    assert incoming.message.reply_text.await_count == 1

    await asyncio.wait_for(handler.handle_message(incoming, SimpleNamespace()), timeout=2)
    assert (await service.current(-100, None))["turn_count"] == 1


@pytest.mark.asyncio
async def test_invalid_word_is_visible_and_does_not_extend_turn():
    service, handler = await _playing(topic=7)
    incoming = _update("şçğü", topic=7, is_topic=True)
    before = await service.current(-100, 7)

    assert await asyncio.wait_for(handler._check_game_inputs(incoming, SimpleNamespace(), "ŞÇĞÜ"), timeout=2)

    after = await service.current(-100, 7)
    assert after["turn_expires_at"] == before["turn_expires_at"]
    assert after["turn_count"] == 0
    assert "yok" in incoming.message.reply_text.await_args.args[0].lower()


@pytest.mark.asyncio
async def test_other_player_other_topic_and_nonword_stay_outside_chain():
    service, handler = await _playing(topic=7)
    other_player = _update("kalem", user_id=2, topic=7, is_topic=True)
    other_topic = _update("kalem", topic=8, is_topic=True)
    sentence = _update("kalem yazdım", topic=7, is_topic=True)

    for incoming, text in ((other_player, "KALEM"), (other_topic, "KALEM"), (sentence, "KALEM YAZDIM")):
        assert not await asyncio.wait_for(handler._check_word_chain_input(incoming, SimpleNamespace(), text), timeout=2)
        incoming.message.reply_text.assert_not_awaited()
    assert (await service.current(-100, 7))["turn_count"] == 0


@pytest.mark.asyncio
async def test_decomposed_turkish_word_is_normalized_before_submission():
    service, handler = await _playing()
    incoming = _update(unicodedata.normalize("NFD", "ağaç"))

    assert await asyncio.wait_for(handler._check_game_inputs(incoming, SimpleNamespace(), incoming.message.text), timeout=2)
    assert (await service.current(-100, None))["last_word"] == "ağaç"


@pytest.mark.asyncio
async def test_final_result_is_sent_when_reward_store_temporarily_fails():
    service, handler = await _playing()
    state = await service.current(-100, None)
    state["turn_count"] = state["max_turns"] - 1
    state["turn_index"] = 0
    state["accepted_count"] = 1
    service.repository.rows[-100, "kelime_zinciri"] = state
    service.settle_scores = AsyncMock(side_effect=RuntimeError("temporarily unavailable"))
    incoming = _update("kalem")

    assert await asyncio.wait_for(handler._check_game_inputs(incoming, SimpleNamespace(), "KALEM"), timeout=2)

    finished = await service.current(-100, None)
    assert finished["phase"] == "finished" and finished["score_pending"]
    assert "kazanan" in incoming.message.reply_text.await_args.args[0].lower()


@pytest.mark.asyncio
async def test_plain_word_with_active_legacy_game_scores_neither(monkeypatch):
    service, handler = await _playing()
    handler.data_port = object()
    async def hydrate(_database, _chat_id, chat_data):
        chat_data["word_game"] = {"status": "playing", "prompt_message_id": 44,
                                  "letters": list("kalem"), "valid_words": ["kalem"]}
    monkeypatch.setattr("src.handlers.message_handler.hydrate_games", hydrate)
    incoming = _update("kalem")
    context = SimpleNamespace(chat_data={}, job_queue=None)

    assert await handler._check_game_inputs(incoming, context, "KALEM")
    assert (await service.current(-100, None))["turn_count"] == 0
    assert context.chat_data["word_game"]["status"] == "playing"
    assert "birden fazla" in incoming.message.reply_text.await_args.args[0].lower()


@pytest.mark.asyncio
async def test_reply_to_current_chain_panel_resolves_active_game_ambiguity(monkeypatch):
    service, handler = await _playing()
    handler.data_port = object()
    async def hydrate(_database, _chat_id, chat_data):
        chat_data["word_guess"] = {"status": "playing", "prompt_message_id": 44,
                                   "word": "kalem", "attempts": 0}
    monkeypatch.setattr("src.handlers.message_handler.hydrate_games", hydrate)
    state = await service.current(-100, None)
    incoming = _update("kalem")
    incoming.message.reply_to_message = SimpleNamespace(
        message_id=60, from_user=SimpleNamespace(id=900),
        reply_markup=word_chain_keyboard(state),
    )
    context = SimpleNamespace(chat_data={}, job_queue=None, bot=SimpleNamespace(id=900))

    assert await handler._check_game_inputs(incoming, context, "KALEM")
    assert (await service.current(-100, None))["turn_count"] == 1


@pytest.mark.asyncio
async def test_reply_to_legacy_prompt_does_not_enter_chain(monkeypatch):
    service, handler = await _playing()
    handler.data_port = object()
    async def hydrate(_database, _chat_id, chat_data):
        chat_data["word_game"] = {"status": "playing", "prompt_message_id": 44,
                                  "letters": list("kalem"), "valid_words": ["kalem"]}
    monkeypatch.setattr("src.handlers.message_handler.hydrate_games", hydrate)
    incoming = _update("kalem")
    incoming.message.reply_to_message = SimpleNamespace(message_id=44)
    context = SimpleNamespace(chat_data={}, job_queue=None)

    assert not await handler._check_word_chain_input(incoming, context, "KALEM")
    assert (await service.current(-100, None))["turn_count"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [
    {"status": "finished", "prompt_message_id": 44},
    {"status": "playing", "prompt_message_id": 44, "thread_id": 8},
])
async def test_terminal_or_other_topic_legacy_game_does_not_block_chain(monkeypatch, legacy):
    service, handler = await _playing(topic=7)
    handler.data_port = object()
    async def hydrate(_database, _chat_id, chat_data):
        chat_data["word_guess"] = dict(legacy)
    monkeypatch.setattr("src.handlers.message_handler.hydrate_games", hydrate)
    incoming = _update("kalem", topic=7, is_topic=True)

    assert await handler._check_game_inputs(incoming, SimpleNamespace(chat_data={}, job_queue=None), "KALEM")
    assert (await service.current(-100, 7))["turn_count"] == 1


@pytest.mark.asyncio
async def test_old_chain_panel_reply_cannot_override_legacy_ambiguity(monkeypatch):
    service, handler = await _playing()
    handler.data_port = object()
    async def hydrate(_database, _chat_id, chat_data):
        chat_data["hangman"] = {"status": "playing", "prompt_message_id": 44}
    monkeypatch.setattr("src.handlers.message_handler.hydrate_games", hydrate)
    state = await service.current(-100, None)
    stale_panel = dict(state, turn_count=state["turn_count"] + 1)
    incoming = _update("kalem")
    incoming.message.reply_to_message = SimpleNamespace(
        message_id=60, from_user=SimpleNamespace(id=900),
        reply_markup=word_chain_keyboard(stale_panel),
    )

    assert await handler._check_game_inputs(
        incoming, SimpleNamespace(chat_data={}, job_queue=None, bot=SimpleNamespace(id=900)), "KALEM"
    )
    assert (await service.current(-100, None))["turn_count"] == 0
    assert "birden fazla" in incoming.message.reply_text.await_args.args[0].lower()
