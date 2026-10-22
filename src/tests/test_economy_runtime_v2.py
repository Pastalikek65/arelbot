"""Pending financial work survives a restart independently of public panels."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.error import TimedOut

from src.handlers.game_runtime import GameRuntime


@pytest.mark.asyncio
async def test_runtime_retries_roulette_money_then_edits_and_acknowledges_canonical_result():
    state = {'phase': 'finished', 'game_id': 'synthetic', 'panel_message_id': 42}
    roulette = SimpleNamespace(expire=AsyncMock(), settle_pending=AsyncMock(), claim_result_panel=AsyncMock(return_value=state),
                               result_text=lambda _: 'Maç bitti', acknowledge_result_panel=AsyncMock())
    runtime = GameRuntime(SimpleNamespace(), roulette=roulette)
    bot = SimpleNamespace(edit_message_text=AsyncMock(), send_message=AsyncMock())
    await runtime._process(bot, {'chat_id': -100, 'game_type': 'roulette', 'state': state})
    roulette.settle_pending.assert_awaited_once_with(chat_id=-100)
    assert bot.edit_message_text.call_args.kwargs['message_id'] == 42
    bot.send_message.assert_not_called()
    roulette.acknowledge_result_panel.assert_awaited_once_with(chat_id=-100, game_id='synthetic', message_id=42)


@pytest.mark.asyncio
async def test_uncertain_roulette_edit_is_not_acknowledged_or_resent_as_public_duplicate():
    state = {'phase': 'finished', 'game_id': 'synthetic', 'panel_message_id': 42}
    roulette = SimpleNamespace(expire=AsyncMock(), settle_pending=AsyncMock(), claim_result_panel=AsyncMock(return_value=state),
                               result_text=lambda _: 'Maç bitti', acknowledge_result_panel=AsyncMock())
    runtime = GameRuntime(SimpleNamespace(), roulette=roulette)
    bot = SimpleNamespace(edit_message_text=AsyncMock(side_effect=TimedOut()), send_message=AsyncMock())
    await runtime._process(bot, {'chat_id': -100, 'game_type': 'roulette', 'state': state})
    assert runtime.delivery_failures == 1
    roulette.acknowledge_result_panel.assert_not_called()
    bot.send_message.assert_not_called()


@pytest.mark.parametrize('phase', ['pending_bet', 'pending_double', 'finished', 'cancelled'])
@pytest.mark.asyncio
async def test_runtime_restores_only_the_blackjack_owner_from_its_storage_key(phase):
    blackjack = SimpleNamespace(resume_pending=AsyncMock(), claim_terminal_panel=AsyncMock(return_value=None))
    runtime = GameRuntime(SimpleNamespace(), blackjack=blackjack)
    await runtime._process(SimpleNamespace(), {'chat_id': -100, 'game_type': 'blackjack:7', 'state': {'status': phase}})
    blackjack.resume_pending.assert_awaited_once_with(chat_id=-100, user_id=7)


@pytest.mark.parametrize('uncertain', [False, True])
@pytest.mark.asyncio
async def test_blackjack_final_panel_is_recovered_and_acknowledged_only_after_delivery(uncertain):
    state = {'terminal_panel_pending': True, 'event_id': 'synthetic',
             'panel_message_id': 42, 'terminal_text': 'El bitti'}
    blackjack = SimpleNamespace(resume_pending=AsyncMock(),
        claim_terminal_panel=AsyncMock(return_value=state), acknowledge_terminal_panel=AsyncMock())
    runtime = GameRuntime(SimpleNamespace(), blackjack=blackjack)
    bot = SimpleNamespace(edit_message_text=AsyncMock(side_effect=TimedOut() if uncertain else None),
                          send_message=AsyncMock())
    await runtime._process(bot, {'chat_id': -100, 'game_type': 'blackjack:7', 'state': state})
    bot.send_message.assert_not_called()
    if uncertain:
        blackjack.acknowledge_terminal_panel.assert_not_called()
        assert runtime.delivery_failures == 1
    else:
        blackjack.acknowledge_terminal_panel.assert_awaited_once_with(
            chat_id=-100, user_id=7, event_id='synthetic', message_id=42)
