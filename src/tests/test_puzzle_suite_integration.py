"""Public routes and ambiguous normal-message boundaries of the puzzle wave."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

KEYS = ('wordle_tr', 'karsi_kelime', 'anagram', 'hafiza_eslestirme', 'mayin_tarlasi', 'sudoku', 'sayisal_tahmin', 'hizli_buton')


@pytest.mark.parametrize('key', KEYS)
def test_each_puzzle_has_a_visible_category_button(key):
    from src.services.menu_service import MenuService
    actions = {b.callback_data for category in ('words', 'boards', 'guess', 'social')
               for row in MenuService().games_menu(category=category)[1].inline_keyboard for b in row}
    assert f'game_start_{key}' in actions


@pytest.mark.parametrize('prefix', ('wp:', 'gp:', 'lp:'))
@pytest.mark.asyncio
async def test_puzzle_callback_owns_ack_before_generic_dispatch(prefix):
    from src.handlers.callback_handler import CallbackHandler
    handler = CallbackHandler.__new__(CallbackHandler)
    handler.puzzle_suite = SimpleNamespace(callback=AsyncMock(return_value=True))
    query = SimpleNamespace(data=prefix+'bound', answer=AsyncMock())
    update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=3))
    await handler.handle_callback_query(update, SimpleNamespace())
    handler.puzzle_suite.callback.assert_awaited_once()
    query.answer.assert_not_awaited()


@pytest.mark.parametrize('key', KEYS)
@pytest.mark.asyncio
async def test_runtime_resumes_each_puzzle_topic(key):
    from src.handlers.game_runtime import GameRuntime
    runtime = GameRuntime(SimpleNamespace())
    runtime.puzzle_suite = SimpleNamespace(process=AsyncMock())
    row = {'chat_id': -100, 'game_type':key+'_t42', 'state':{'game_key':key, 'thread_id':42}}
    await runtime._process(SimpleNamespace(), row)
    runtime.puzzle_suite.process.assert_awaited_once()


@pytest.mark.asyncio
async def test_unique_plain_input_is_routed_and_wrong_panel_is_ignored():
    from src.handlers.puzzle_suite import PuzzleSuite
    adapter=SimpleNamespace(service=SimpleNamespace(current=AsyncMock(return_value={
        'phase':'playing','panel_message_id':42,'owner_id':7,'players':[{'id':7}], 'game_key':'wordle_tr'})),
        on_text=AsyncMock(return_value=True))
    suite=PuzzleSuite({'wordle_tr':adapter})
    message=SimpleNamespace(reply_to_message=None,reply_text=AsyncMock())
    update=SimpleNamespace(effective_chat=SimpleNamespace(id=-100,type='supergroup'), effective_user=SimpleNamespace(id=7), effective_message=message)
    context=SimpleNamespace(chat_data={})
    assert await suite.on_text(update,context,'kalem')
    adapter.on_text.reset_mock()
    message.reply_to_message=SimpleNamespace(message_id=99)
    assert not await suite.on_text(update,context,'kalem')
    adapter.on_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_ambiguous_plain_text_never_awards_to_the_first_registered_game():
    from src.handlers.puzzle_suite import PuzzleSuite
    adapters={key:SimpleNamespace(service=SimpleNamespace(current=AsyncMock(return_value={
        'phase':'playing','panel_message_id':i,'players':[{'id':7}], 'game_key':key})), on_text=AsyncMock())
        for i,key in enumerate(('wordle_tr','anagram'),start=40)}
    suite=PuzzleSuite(adapters)
    message=SimpleNamespace(reply_to_message=None,reply_text=AsyncMock())
    update=SimpleNamespace(effective_chat=SimpleNamespace(id=-100,type='supergroup'),effective_user=SimpleNamespace(id=7),effective_message=message)
    assert await suite.on_text(update,SimpleNamespace(chat_data={}), 'kalem')
    assert all(not a.on_text.called for a in adapters.values())
    message.reply_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_age_or_chain_running_requires_explicit_panel_reply_for_number_game():
    from src.handlers.puzzle_suite import PuzzleSuite
    adapter = SimpleNamespace(service=SimpleNamespace(current=AsyncMock(return_value={
        'phase':'turn','panel_message_id':42,'owner_id':7,'players':[{'id':7}]})),
        on_text=AsyncMock(return_value=True))
    async def legacy(chat_id, key):
        return {'status':'playing', 'thread_id':None} if key == 'guess_age' else None
    suite = PuzzleSuite({'sayisal_tahmin':adapter}, repository=SimpleNamespace(get_game_state=legacy))
    message = SimpleNamespace(reply_to_message=None, reply_text=AsyncMock())
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=-100),effective_user=SimpleNamespace(id=7),effective_message=message)
    assert await suite.on_text(update,SimpleNamespace(chat_data={}), '50')
    adapter.on_text.assert_not_awaited()
    message.reply_to_message=SimpleNamespace(message_id=42)
    assert await suite.on_text(update,SimpleNamespace(chat_data={}), '50')
    adapter.on_text.assert_awaited_once()


def test_existing_private_memory_consent_command_is_not_repurposed_for_a_game():
    from src.games.puzzle_catalog import PUZZLES
    commands = {entry.command for entry in PUZZLES}
    assert 'hafiza' not in commands
    assert 'eslestirme' in commands
