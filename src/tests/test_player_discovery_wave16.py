"""All games remain discoverable without exposing administrative operations."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from main import _build_runtime_command_registry, build_bot_command_menu
from src.handlers.command_handler import CommandHandler
from src.services import game_guide
from src.tests.test_main_commands import _declared_commands


def test_every_game_command_is_visible_in_bounded_public_picker():
    rows = _declared_commands()
    registry = _build_runtime_command_registry(rows)
    menu = build_bot_command_menu(rows)
    visible = {name for name, _ in menu}
    assert {'help', 'iptal'}.issubset(visible)
    assert len(menu) < 100
    assert len(menu) == len(visible)
    assert len(game_guide.COMMANDS) == 48
    for command in game_guide.COMMANDS.values():
        resolved = registry.resolve(command)
        assert resolved is not None, command
        assert resolved.name in visible, command
    assert not visible.intersection({'health', 'metrics', 'arelmerkez', 'moderasyon', 'yasakla'})


def test_complete_guide_pages_cover_each_game_once_and_safe_callback_lengths():
    from src.games.catalog import GAME_DEFINITIONS
    total = (len(GAME_DEFINITIONS) + game_guide.PAGE_SIZE - 1) // game_guide.PAGE_SIZE
    texts = []
    for index in range(total):
        text, markup = game_guide.page(index)
        assert len(text) < 4096
        texts.append(text)
        assert all(len(button.callback_data.encode()) <= 64
                   for row in markup.inline_keyboard for button in row)
    for game in GAME_DEFINITIONS.values():
        assert sum(f'<b>{game.label}</b>' in text for text in texts) == 1
    assert game_guide.page(-5)[0] == game_guide.page(0)[0]
    assert game_guide.page(999)[0] == game_guide.page(total - 1)[0]


@pytest.mark.asyncio
async def test_blackjack_menu_intents_use_callback_identity_and_retry_stays_idempotent():
    handler = CommandHandler.__new__(CommandHandler)
    handler.blackjack_service = SimpleNamespace(validate_bet=lambda raw: int(raw),
        start=AsyncMock(return_value={'terminal': False, 'text': 'El hazır', 'state': {}}))
    handler._blackjack_keyboard = lambda *args: None
    message = SimpleNamespace(message_id=55, reply_text=AsyncMock())
    update = SimpleNamespace(effective_message=message, effective_chat=SimpleNamespace(id=-100),
        effective_user=SimpleNamespace(id=7, first_name='Oyuncu'), callback_query=SimpleNamespace(id='first'))
    for query_id in ('first', 'first', 'second'):
        update.callback_query.id = query_id
        await handler.handle_blackjack_command(update, SimpleNamespace(args=['100']))
    ids = [call.kwargs['event_id'] for call in handler.blackjack_service.start.await_args_list]
    assert ids[0] == ids[1] and ids[0] != ids[2]


@pytest.mark.asyncio
async def test_inventory_clear_button_keeps_ownership_and_balance():
    from src.handlers.economy_handler import EconomyHandler
    from src.services.economy_service import EconomyService
    from src.tests.test_economy_handler_v2 import Bot, button_data, callback_update, command_update
    service, bot = EconomyService(), Bot()
    await service.credit(1, -100, 100, reason='fixture_seed', event_id='seed')
    quote = await service.prepare_purchase(1, -100, 'merakli')
    await service.purchase(1, -100, 'merakli', event_id='buy', confirmation_token=quote['token'])
    await service.equip(1, -100, 'merakli')
    before = await service.snapshot(1, -100)
    handler = EconomyHandler(service)
    update, _ = command_update()
    await handler.command(update, SimpleNamespace(bot=bot), 'inventory')
    callback, _ = callback_update(bot, -100, 1, button_data(bot.latest_markup(), 'Unvanı kaldır'))
    await handler.callback(callback, SimpleNamespace(bot=bot))
    after = await service.snapshot(1, -100)
    assert after['balance'] == before['balance']
    assert after['owned'] == before['owned']
    assert not after['equipped']
    assert 'Profil seçimi kaldırıldı' in bot.edited[-1]['text']


@pytest.mark.asyncio
async def test_real_menu_dispatch_proxy_retains_callback_intent(monkeypatch):
    from src.handlers.callback_handler import CallbackHandler
    command = CommandHandler.__new__(CommandHandler)
    command.blackjack_service = SimpleNamespace(validate_bet=lambda raw: int(raw),
        start=AsyncMock(return_value={'terminal': False, 'text': 'Hazır', 'state': {}}))
    command._blackjack_keyboard = lambda *args: None
    class Commands:
        handle_blackjack_command = command.handle_blackjack_command
        def __getattr__(self, name):
            return AsyncMock()
    monkeypatch.setattr('src.handlers.command_handler.CommandHandler', lambda *args, **kwargs: Commands())
    handler = CallbackHandler.__new__(CallbackHandler)
    for name in ('chat_engine', 'sudo_manager', 'tool_manager', 'menu_service', 'leaderboard_service',
                 'economy_service', 'blackjack_service', 'batak_service', 'roulette_service',
                 'data_port', 'authorization', 'akinator_service', 'guess_age_service', 'uno_service'):
        setattr(handler, name, None)
    message = SimpleNamespace(message_id=55, reply_text=AsyncMock())
    query = SimpleNamespace(id='first', message=message, answer=AsyncMock())
    update = SimpleNamespace(callback_query=query, effective_chat=SimpleNamespace(id=-100),
        effective_user=SimpleNamespace(id=7, first_name='Oyuncu'))
    for query_id in ('first', 'first', 'second'):
        query.id = query_id
        await handler._handle_game_start_callback(update, query, SimpleNamespace(args=['100']), 'game_start_blackjack')
    ids = [call.kwargs['event_id'] for call in command.blackjack_service.start.await_args_list]
    assert ids[0] == ids[1] and ids[0] != ids[2]
