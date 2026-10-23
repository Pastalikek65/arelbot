"""Integration gates for the fourteen distinct mechanics completing the catalog."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest

NEW_KEYS = ('harf_maratonu','sozcuk_avi','yasak_harf','ortak_hikaye','bomba',
    'kor_siralama','kronoloji','dogru_yanlis','dedektif','amiral_batti',
    'iki_dogru_bir_yalan','kimim_ben','takim_bilgi_ligi','zehirli_kadeh')


@pytest.mark.parametrize('key',NEW_KEYS)
def test_new_mechanics_are_registered_and_reachable(key):
    from src.games.catalog import GAME_DEFINITIONS
    from src.games.puzzle_catalog import PUZZLES
    from src.services.menu_service import MenuService
    assert key in GAME_DEFINITIONS
    assert any(p.key==key for p in PUZZLES)
    actions={b.callback_data for category in ('words','boards','guess','social')
        for row in MenuService().games_menu(category=category)[1].inline_keyboard for b in row}
    assert 'game_start_'+key in actions


@pytest.mark.parametrize('prefix,key',[('wa:','bomba'),('ra:','dedektif'),('sa:','amiral_batti')])
@pytest.mark.asyncio
async def test_new_adapter_owns_callback_answer(prefix,key):
    from src.handlers.puzzle_suite import PuzzleSuite
    a=SimpleNamespace(callback=AsyncMock())
    suite=PuzzleSuite({key:a})
    query=SimpleNamespace(data=prefix+'opaque',answer=AsyncMock())
    assert await suite.callback(SimpleNamespace(callback_query=query),SimpleNamespace())
    a.callback.assert_awaited_once()
    query.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_domain_specific_text_phase_is_respected_before_dispatch():
    from src.handlers.puzzle_suite import PuzzleSuite
    a=SimpleNamespace(service=SimpleNamespace(current=AsyncMock(return_value={
        'phase':'bomb_turn','players':[{'id':7,'active':True}], 'panel_message_id':42})),
        accepts_text=lambda state:state['phase']=='bomb_turn', on_text=AsyncMock(return_value=True))
    suite=PuzzleSuite({'bomba':a})
    u=SimpleNamespace(effective_chat=SimpleNamespace(id=-100),effective_user=SimpleNamespace(id=7),
        effective_message=SimpleNamespace(reply_to_message=None))
    assert await suite.on_text(u,SimpleNamespace(),'kalem')
    a.on_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_private_claim_is_consumed_before_secret_guard_and_ai_history():
    from src.handlers.message_handler import MessageHandler
    h=MessageHandler.__new__(MessageHandler)
    h.moderation=None
    route={'chat_id':-100,'game_type':'iki_dogru_bir_yalan_t42'}
    h.data_port=SimpleNamespace(get_active_secret_game_for_user=AsyncMock(return_value=route),
        list_active_secret_game_routes=AsyncMock(return_value=[route]))
    h.puzzle_suite=SimpleNamespace(on_private_text=AsyncMock(return_value=True))
    h.chat_engine=SimpleNamespace(record_message=AsyncMock())
    msg=SimpleNamespace(text='Üç gizli iddiam',reply_text=AsyncMock())
    u=SimpleNamespace(message=msg,effective_message=msg,effective_chat=SimpleNamespace(id=7,type='private'),effective_user=SimpleNamespace(id=7))
    await h.handle_message(u,SimpleNamespace())
    h.puzzle_suite.on_private_text.assert_awaited_once()
    assert h.puzzle_suite.on_private_text.await_args.kwargs['routes']==[route]
    h.chat_engine.record_message.assert_not_awaited()
    msg.reply_text.assert_not_awaited()


def test_expanded_mechanics_does_not_count_dice_or_theme_aliases():
    from src.games.catalog import GAME_DEFINITIONS
    assert len(GAME_DEFINITIONS)==48
    assert not {'dice','zar','slot','yazitura'} & set(GAME_DEFINITIONS)


@pytest.mark.parametrize('page',['arcade','arcade_2','puzzles'])
@pytest.mark.asyncio
async def test_all_help_pages_render_within_telegram_limit(page):
    from src.handlers.callback_handler import CallbackHandler
    handler=CallbackHandler.__new__(CallbackHandler)
    query=SimpleNamespace(edit_message_text=AsyncMock())
    await handler._show_help_feature(query,'help_feat_'+page)
    text=query.edit_message_text.await_args.args[0]
    assert len(text.encode('utf-16-le'))//2 <= 4096
    assert '<b>' in text


@pytest.mark.asyncio
async def test_ambiguous_private_answer_does_not_reach_either_game():
    from src.handlers.puzzle_suite import PuzzleSuite
    adapter=SimpleNamespace(on_private_text=AsyncMock(return_value=True))
    suite=PuzzleSuite({'harf_maratonu':adapter,'iki_dogru_bir_yalan':adapter})
    message=SimpleNamespace(reply_to_message=None,reply_text=AsyncMock())
    routes=[{'chat_id':-1,'game_type':'harf_maratonu'},
            {'chat_id':-2,'game_type':'iki_dogru_bir_yalan'}]
    assert await suite.on_private_text(SimpleNamespace(effective_message=message),None,'gizli',routes=routes)
    adapter.on_private_text.assert_not_awaited()
    assert 'gizli' not in message.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_batched_safe_metadata_routes_without_loading_every_game():
    from src.handlers.puzzle_suite import PuzzleSuite
    service=SimpleNamespace(current=AsyncMock())
    adapter=SimpleNamespace(service=service,accepts_text=lambda s:s['phase']=='hunt',
                            on_text=AsyncMock(return_value=True))
    repository=SimpleNamespace(list_active_puzzle_routes=AsyncMock(return_value=[{
        'game_type':'sozcuk_avi','state':{'game_key':'sozcuk_avi','phase':'hunt',
        'panel_message_id':77,'players':[{'id':7,'active':True}]}}]))
    suite=PuzzleSuite({'sozcuk_avi':adapter},repository=repository)
    message=SimpleNamespace(reply_to_message=SimpleNamespace(message_id=77))
    update=SimpleNamespace(effective_message=message,effective_chat=SimpleNamespace(id=-1),
                            effective_user=SimpleNamespace(id=7))
    assert await suite.on_text(update,None,'kedi')
    service.current.assert_not_awaited()
    repository.list_active_puzzle_routes.assert_awaited_once_with(-1,None,7)


@pytest.mark.asyncio
async def test_edited_forbidden_clue_rechecked_without_awarding_new_guesses():
    from src.handlers.message_handler import MessageHandler
    handler=MessageHandler.__new__(MessageHandler)
    handler.word_arcade_handler=SimpleNamespace(on_clue_text=AsyncMock(return_value=True))
    handler.chat_engine=SimpleNamespace(record_message=AsyncMock())
    message=SimpleNamespace(text='yasak ipucu',caption=None)
    update=SimpleNamespace(edited_message=message)
    await handler.handle_message(update,None)
    handler.word_arcade_handler.on_clue_text.assert_awaited_once_with(update,None,'yasak ipucu')
    handler.chat_engine.record_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_narrator_caption_consumed_before_media_guess_routing():
    from src.orchestrator import Orchestrator
    handler=Orchestrator.__new__(Orchestrator)
    handler.word_arcade_handler=SimpleNamespace(on_clue_text=AsyncMock(return_value=True))
    handler.spy_handler=SimpleNamespace(on_text=AsyncMock())
    update=SimpleNamespace(effective_message=SimpleNamespace(caption='ipucu'))
    assert await handler._route_social_caption(update,None)
    handler.spy_handler.on_text.assert_not_awaited()
