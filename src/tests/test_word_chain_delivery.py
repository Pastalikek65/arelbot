"""A committed word remains deliverable after Telegram rejects its panel."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.error import TimedOut

from src.handlers.game_runtime import GameRuntime
from src.tests.test_word_chain_message_routing import _playing, _update


@pytest.mark.asyncio
async def test_plain_word_panel_failure_is_retried_before_next_turn_expires():
    service, handler = await _playing()
    incoming = _update("kalem")
    incoming.message.reply_text = AsyncMock(side_effect=TimedOut())
    with pytest.raises(TimedOut):
        await handler._check_word_chain_input(incoming, SimpleNamespace(), "KALEM")
    state = await service.current(-100, None)
    assert state["turn_count"] == 1 and state["panel_dirty"]
    bot = SimpleNamespace(send_message=AsyncMock())
    await GameRuntime(service.repository)._process(bot, {"chat_id":-100,"game_type":"kelime_zinciri","state":state})
    assert bot.send_message.await_count == 1
    assert "süre doldu" not in bot.send_message.await_args.kwargs["text"].lower()
    assert not (await service.current(-100,None))["panel_dirty"]
