"""Player-only help and truly disabled cells never become game actions."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, TimedOut

from src.games.help_catalog import GAME_HELP
from src.games.puzzle_catalog import PUZZLES
from src.handlers.game_help import show_game_help
from src.telegram.button_theme import decorate_markup
from src.telegram.game_controls import disabled_button, with_game_help


def test_help_covers_all_persistent_puzzles_and_does_not_mutate_existing_markup():
    assert set(GAME_HELP) == {entry.key for entry in PUZZLES}
    original = InlineKeyboardMarkup([[InlineKeyboardButton('Katıl', callback_data='opaque')]],
                                    api_kwargs={'force_reply': True})
    markup = with_game_help(original, 'sudoku')
    assert len(original.inline_keyboard) == 1
    assert markup.inline_keyboard[-1][-1].callback_data == 'gh:sudoku'
    assert len(original.inline_keyboard[0]) == 1
    assert markup.to_dict()['force_reply'] is True
    assert with_game_help(markup, 'sudoku').to_dict() == markup.to_dict()


def test_disabled_button_has_exactly_one_action_and_survives_theme():
    button = disabled_button('01').to_dict()
    assert button == {'text': '01', 'disabled': {}}
    assert decorate_markup({'inline_keyboard': [[button]]}, premium=True)['inline_keyboard'][0][0] == button


def test_clue_help_includes_the_required_group_join_step():
    assert all('önce Katıl' in GAME_HELP[key] for key in ('bes_ipucu', 'emoji_bilmece'))
    assert 'başına ?' in GAME_HELP['kimim_ben'] and 'başına =' in GAME_HELP['kimim_ben']
    assert 'yanıt olarak' in GAME_HELP['harf_maratonu']


def _update(data='gh:sudoku', *, topic=17):
    query = SimpleNamespace(data=data, id='synthetic-help-query', answer=AsyncMock())
    message = SimpleNamespace(message_id=123, message_thread_id=topic, is_topic_message=topic is not None)
    return SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=7),
                           effective_chat=SimpleNamespace(id=-100123, type='supergroup'),
                           effective_message=message)


@pytest.mark.asyncio
async def test_rules_only_use_receiver_scoped_ephemeral_message_and_preserve_topic():
    update = _update()
    bot = SimpleNamespace(send_message=AsyncMock(), do_api_request=AsyncMock())
    assert await show_game_help(update, SimpleNamespace(bot=bot))
    assert bot.do_api_request.call_args.args == ('sendMessage',)
    kwargs = bot.do_api_request.call_args.kwargs['api_kwargs']
    assert kwargs['ephemeral_message_parameters'] == {
        'receiver_user_id': 7, 'callback_query_id': 'synthetic-help-query',
        'replace_callback_query_message': False}
    assert kwargs['chat_id'] == -100123 and kwargs['message_thread_id'] == 17
    assert kwargs['text'].endswith(GAME_HELP['sudoku'])
    assert 'parse_mode' not in kwargs
    bot.send_message.assert_not_called()
    update.callback_query.answer.assert_awaited_once_with(cache_time=30)


@pytest.mark.parametrize('error', [BadRequest('unsupported ephemeral'), TimedOut()])
@pytest.mark.asyncio
async def test_failure_never_sends_public_fallback_or_retries_ambiguous_delivery(error):
    update = _update()
    bot = SimpleNamespace(send_message=AsyncMock(), do_api_request=AsyncMock(side_effect=error))
    assert await show_game_help(update, SimpleNamespace(bot=bot))
    assert bot.do_api_request.await_count == 1
    bot.send_message.assert_not_called()
    args = update.callback_query.answer.call_args
    assert args.kwargs['show_alert'] is True
    assert len(args.args[0]) <= 200


@pytest.mark.asyncio
async def test_unknown_or_non_help_callback_cannot_trigger_a_message():
    bot = SimpleNamespace(send_message=AsyncMock(), do_api_request=AsyncMock())
    assert not await show_game_help(_update('opaque'), SimpleNamespace(bot=bot))
    update = _update('gh:unknown')
    assert await show_game_help(update, SimpleNamespace(bot=bot))
    bot.send_message.assert_not_called()
    bot.do_api_request.assert_not_called()


@pytest.mark.asyncio
async def test_private_rules_use_full_direct_reply_without_ephemeral_api():
    update = _update(topic=None)
    update.effective_chat = SimpleNamespace(id=7, type='private')
    bot = SimpleNamespace(send_message=AsyncMock(), do_api_request=AsyncMock())
    assert await show_game_help(update, SimpleNamespace(bot=bot))
    assert bot.send_message.call_args.kwargs['text'].endswith(GAME_HELP['sudoku'])
    assert bot.send_message.call_args.kwargs['chat_id'] == 7
    bot.do_api_request.assert_not_called()


def test_open_mines_and_matched_memory_are_disabled_but_flags_can_be_removed():
    from src.games import grid_puzzles as rules
    from src.services.grid_puzzles_ui import keyboard

    memory = rules.new_memory(chat_id=-100123, thread_id=None, owner_id=1,
                              owner_name='Oyuncu', round_id='r', now=1)
    memory.update(phase='turn', matched=[0, 1], first_pick=2)
    cells = keyboard(memory).to_dict()['inline_keyboard'][:4]
    assert all(cells[0][index]['disabled'] == {} for index in range(3))
    assert cells[0][3]['callback_data'].startswith('gp:')
    mines = rules.new_mines(chat_id=-100123, thread_id=None, owner_id=1,
                            owner_name='Oyuncu', round_id='r', now=1)
    mines.update(opened=[0], flags=[1], flag_mode=True)
    cells = keyboard(mines).to_dict()['inline_keyboard'][:6]
    assert cells[0][0]['disabled'] == {}
    assert cells[0][1]['callback_data'].startswith('gp:')
