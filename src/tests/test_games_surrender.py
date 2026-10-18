"""Blackjack surrender: scoped UI, half-stake settlement and contention."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.games.blackjack import BlackjackGame
from src.services.blackjack_service import BlackjackService
from src.tests.test_games_mechanics import _Database, _Economy, _factory, _hand


class SharedDatabase(_Database):
    def __init__(self):
        super().__init__()
        self.claims = set()
        self.fail_delete = False

    async def claim_game_action(self, **kwargs):
        key = kwargs['action_key']
        if key in self.claims:
            return False
        self.claims.add(key)
        return True

    async def release_game_action(self, action_key):
        self.claims.discard(action_key)

    async def delete_game_state(self, chat_id, game_type):
        if self.fail_delete:
            self.fail_delete = False
            raise RuntimeError('delete_interrupted')
        await super().delete_game_state(chat_id, game_type)


class IdempotentEconomy(_Economy):
    def __init__(self):
        super().__init__()
        self.events = set()
        self.credit_entered = None
        self.credit_continue = None

    async def credit(self, user_id, chat_id, amount, **kwargs):
        if self.credit_entered is not None:
            self.credit_entered.set()
            await self.credit_continue.wait()
        event = kwargs['event_id']
        if event in self.events:
            return False
        self.events.add(event)
        return await super().credit(user_id, chat_id, amount, **kwargs)


def _service(database, economy):
    return BlackjackService(database, economy, game_factory=_factory(['10', '6'], ['10', '7'], deck=['2', '3']))


@pytest.mark.parametrize('bet,refund', [(100, 50), (101, 50), (1, 0)])
def test_surrender_refunds_half_with_integer_rounding_and_no_dealer_draw(bet, refund):
    async def scenario():
        database, economy = SharedDatabase(), IdempotentEconomy()
        service = _service(database, economy)
        started = await service.start(chat_id=-10, user_id=7, user_name='Oyuncu', bet=bet, event_id='surrender')
        token = started['state']['round_token']
        result = await service.action(chat_id=-10, user_id=7, action='surrender', action_id='give-up', round_token=token)
        assert result['terminal'] is True
        assert result['result']['result'] == 'surrender'
        assert result['winnings'] == refund - bet
        assert result['state']['dealer_hand'] == started['state']['dealer_hand']
        assert result['state']['deck'] == started['state']['deck']
        assert economy.balance == 1000 - bet + refund
        assert economy.credits == ([refund] if refund else [])
        assert database.states == {}
        with pytest.raises(ValueError, match='duplicate_action'):
            await service.action(chat_id=-10, user_id=7, action='surrender', action_id='give-up', round_token=token)
        assert economy.balance == 1000 - bet + refund
    asyncio.run(scenario())


def test_surrender_is_rejected_after_a_hit_without_changing_balance_or_state():
    async def scenario():
        database, economy = SharedDatabase(), IdempotentEconomy()
        service = _service(database, economy)
        started = await service.start(chat_id=-10, user_id=7, user_name='Oyuncu', bet=100, event_id='hit-first')
        await service.action(chat_id=-10, user_id=7, action='hit', action_id='hit')
        before = deepcopy(database.states)
        with pytest.raises(ValueError, match='surrender_only_initial_hand'):
            await service.action(chat_id=-10, user_id=7, action='surrender', action_id='late', round_token=started['state']['round_token'])
        assert database.states == before
        assert economy.balance == 900
        assert economy.credits == []
    asyncio.run(scenario())


@pytest.mark.parametrize('status', ['finished', 'blackjack', 'bust', 'surrendered', 'stand'])
def test_domain_rejects_surrender_after_the_initial_active_hand(status):
    game = BlackjackGame()
    state = _hand(['10', '6'], ['10', '7'], status=status)
    before = deepcopy(state)
    with pytest.raises(ValueError, match='surrender_only_initial_hand'):
        game.surrender(state)
    assert state == before


@pytest.mark.parametrize('action', ['surrender', 'stand', 'hit', 'double', 'cancel'])
def test_surrender_serializes_different_callbacks_and_cancel_across_service_instances(action):
    async def scenario():
        database, economy = SharedDatabase(), IdempotentEconomy()
        first, second = _service(database, economy), _service(database, economy)
        started = await first.start(chat_id=-10, user_id=7, user_name='Oyuncu', bet=100, event_id='race')
        token = started['state']['round_token']
        economy.credit_entered, economy.credit_continue = asyncio.Event(), asyncio.Event()
        surrender = asyncio.create_task(first.action(chat_id=-10, user_id=7, action='surrender', action_id='first', round_token=token))
        await asyncio.wait_for(economy.credit_entered.wait(), timeout=1)
        try:
            with pytest.raises(ValueError, match='game_busy'):
                if action == 'cancel':
                    await second.cancel(chat_id=-10, user_id=7)
                else:
                    await second.action(chat_id=-10, user_id=7, action=action, action_id='second', round_token=token)
        finally:
            economy.credit_continue.set()
            result = await asyncio.wait_for(surrender, timeout=1)
        assert result['result']['result'] == 'surrender'
        assert economy.balance == 950
        assert economy.credits == [50]
        assert database.states == {}
    asyncio.run(scenario())


def test_surrender_settlement_survives_delete_failure_and_service_restart():
    async def scenario():
        database, economy = SharedDatabase(), IdempotentEconomy()
        service = _service(database, economy)
        started = await service.start(chat_id=-10, user_id=7, user_name='Oyuncu', bet=100, event_id='retry')
        token = started['state']['round_token']
        database.fail_delete = True
        with pytest.raises(RuntimeError, match='delete_interrupted'):
            await service.action(chat_id=-10, user_id=7, action='surrender', action_id='retry-callback', round_token=token)
        assert database.states[-10, 'blackjack:7']['status'] == 'surrendered'
        assert economy.balance == 950
        restarted = _service(database, economy)
        result = await restarted.action(chat_id=-10, user_id=7, action='surrender', action_id='retry-callback', round_token=token)
        assert result['terminal'] is True
        assert economy.balance == 950
        assert economy.credits == [50]
        assert database.states == {}
    asyncio.run(scenario())


def test_stale_or_unscoped_surrender_cannot_act_on_a_new_hand():
    async def scenario():
        database, economy = SharedDatabase(), IdempotentEconomy()
        service = _service(database, economy)
        old = await service.start(chat_id=-10, user_id=7, user_name='Oyuncu', bet=100, event_id='old')
        await service.cancel(chat_id=-10, user_id=7)
        await service.start(chat_id=-10, user_id=7, user_name='Oyuncu', bet=100, event_id='new')
        before = deepcopy(database.states)
        for token in (None, old['state']['round_token']):
            with pytest.raises(ValueError, match='stale_game'):
                await service.action(chat_id=-10, user_id=7, action='surrender', action_id=f'stale-{token}', round_token=token)
        assert database.states == before
        assert economy.balance == 900
    asyncio.run(scenario())


def test_initial_and_after_hit_keyboards_expose_only_available_actions():
    from src.handlers.command_handler import CommandHandler
    state = _hand(['10', '6'], ['10', '7'], deck=['2'], status='playing')
    state['round_token'] = 'abc123'
    keyboard = CommandHandler._blackjack_keyboard(7, state)
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert 'bj:7:abc123:surrender' in callbacks
    assert all(len(value.encode('utf-8')) <= 64 for value in callbacks)
    BlackjackGame().hit(state)
    callbacks = [button.callback_data for row in CommandHandler._blackjack_keyboard(7, state).inline_keyboard for button in row]
    assert all(not value.endswith((':surrender', ':double')) for value in callbacks)


def test_surrender_help_explains_integer_half_refund():
    state = _hand(['10', '6'], ['10', '7'])
    state['bet'] = 101
    text = BlackjackGame().format_game(state)
    assert 'Pes et' in text and '50' in text and 'ilk' in text.lower()


def test_scoped_surrender_callback_uses_service_and_removes_terminal_buttons():
    from src.handlers.callback_handler import CallbackHandler
    async def scenario():
        handler = CallbackHandler.__new__(CallbackHandler)
        handler.blackjack_service = SimpleNamespace(action=AsyncMock(return_value={'terminal': True, 'text': 'Pes ettin.'}))
        handler._game_action_id = lambda *_: 'callback'
        query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())
        update = SimpleNamespace(effective_user=SimpleNamespace(id=7), effective_chat=SimpleNamespace(id=-10))
        await handler._handle_blackjack_callback(update, query, SimpleNamespace(), 'bj:7:abc123:surrender')
        handler.blackjack_service.action.assert_awaited_once_with(chat_id=-10, user_id=7, action='surrender', action_id='callback', round_token='abc123')
        assert query.edit_message_text.await_args.kwargs['reply_markup'] is None
    asyncio.run(scenario())


def test_real_economy_retries_pending_surrender_without_full_cancel_refund():
    from src.services.economy_service import EconomyService
    async def scenario():
        database, economy = SharedDatabase(), EconomyService()
        await economy.credit(7, -10, 1000, reason='test_setup', event_id='seed')
        service = _service(database, economy)
        started = await service.start(chat_id=-10, user_id=7, user_name='Oyuncu', bet=101, event_id='real-economy')
        database.fail_delete = True
        with pytest.raises(RuntimeError, match='delete_interrupted'):
            await service.action(chat_id=-10, user_id=7, action='surrender', action_id='real', round_token=started['state']['round_token'])
        assert await economy.balance(7, -10) == 949
        restarted = _service(database, economy)
        result = await restarted.cancel(chat_id=-10, user_id=7)
        assert result['result']['result'] == 'surrender'
        assert await economy.balance(7, -10) == 949
        assert len(economy._transactions) == 3  # seed, initial stake, half refund
        assert database.states == {}
    asyncio.run(scenario())


@pytest.mark.parametrize('user_id,data', [(8, 'bj:7:abc123:surrender'), (7, 'bj:7:surrender')])
def test_other_players_and_unscoped_panels_cannot_surrender(user_id, data):
    from src.handlers.callback_handler import CallbackHandler
    async def scenario():
        handler = CallbackHandler.__new__(CallbackHandler)
        handler.blackjack_service = SimpleNamespace(action=AsyncMock())
        query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())
        update = SimpleNamespace(effective_user=SimpleNamespace(id=user_id), effective_chat=SimpleNamespace(id=-10))
        await handler._handle_blackjack_callback(update, query, SimpleNamespace(), data)
        handler.blackjack_service.action.assert_not_awaited()
        query.edit_message_text.assert_not_awaited()
    asyncio.run(scenario())
