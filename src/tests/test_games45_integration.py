"""The new mechanics must be reachable through the actual shared surfaces."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.games.catalog import GAME_DEFINITIONS
from src.games.puzzle_catalog import PUZZLES, SECRET_GAME_KEYS, puzzle_key_from_storage
from src.handlers.puzzle_suite import PuzzleSuite
from src.services.menu_service import MenuService
from src.telegram.command_registry import EXPLICIT_HANDLER_MAP

NEW_KEYS = ('pisti', 'papaz_kimde', '2048', 'bes_ipucu', 'emoji_bilmece')


def test_new_games_have_distinct_persistent_definitions_visible_menu_and_commands():
    assert len(GAME_DEFINITIONS) >= 45
    actions = {button.callback_data for category in ('cards', 'words', 'boards', 'guess', 'social')
               for row in MenuService().games_menu(category=category)[1].inline_keyboard for button in row}
    for key in NEW_KEYS:
        entry = next(entry for entry in PUZZLES if entry.key == key)
        assert f'game_start_{key}' in actions
        assert EXPLICIT_HANDLER_MAP[entry.command] == 'handle_puzzle_command'
        assert GAME_DEFINITIONS[key].state_key == key
        assert puzzle_key_from_storage(key + '_t42') == key
        assert puzzle_key_from_storage(key + 'evil') is None
    assert {'pisti', 'papaz_kimde'} <= SECRET_GAME_KEYS


@pytest.mark.parametrize('key', NEW_KEYS)
@pytest.mark.asyncio
async def test_each_command_and_runtime_topic_routes_to_its_adapter(key):
    from src.handlers.game_runtime import GameRuntime

    adapter = SimpleNamespace(command=AsyncMock(), process=AsyncMock())
    suite = PuzzleSuite({key: adapter})
    entry = next(entry for entry in PUZZLES if entry.key == key)
    update = SimpleNamespace(effective_message=SimpleNamespace(text='/' + entry.command + '@SyntheticBot'))
    context = SimpleNamespace()
    assert await suite.command(update, context)
    adapter.command.assert_awaited_once_with(update, context, game_key=key)
    runtime = GameRuntime(SimpleNamespace())
    runtime.puzzle_suite = suite
    row = {'chat_id': -100, 'game_type': key + '_t42', 'state': {'game_key': key, 'thread_id': 42}}
    await runtime._process(SimpleNamespace(), row)
    adapter.process.assert_awaited_once()


@pytest.mark.parametrize('key,prefix', [('pisti', 'ct:'), ('papaz_kimde', 'ct:'),
                                      ('2048', 't2:'), ('bes_ipucu', 'ca:'), ('emoji_bilmece', 'ca:')])
@pytest.mark.asyncio
async def test_each_new_callback_family_keeps_its_own_validation_ack(key, prefix):
    adapter = SimpleNamespace(callback=AsyncMock())
    query = SimpleNamespace(data=prefix + 'synthetic', answer=AsyncMock())
    update = SimpleNamespace(callback_query=query)
    assert await PuzzleSuite({key: adapter}).callback(update, SimpleNamespace())
    adapter.callback.assert_awaited_once()
    query.answer.assert_not_called()


@pytest.mark.parametrize('key', NEW_KEYS)
@pytest.mark.asyncio
async def test_public_delivery_lease_does_not_block_private_timer_or_score_work(key):
    import time
    from src.handlers.game_runtime import GameRuntime

    adapter = SimpleNamespace(process=AsyncMock())
    runtime = GameRuntime(SimpleNamespace())
    runtime.puzzle_suite = PuzzleSuite({key: adapter})
    row = {'chat_id': -100, 'game_type': key + '_t42', 'state': {
        'game_key': key, 'thread_id': 42, 'delivery_retry_at': time.time() + 60,
        'private_dirty': True, 'private_retry_at': 0}}
    await runtime._process(SimpleNamespace(), row)
    adapter.process.assert_awaited_once()
