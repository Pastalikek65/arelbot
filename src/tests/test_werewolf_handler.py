from types import SimpleNamespace
from unittest.mock import AsyncMock
import time

import pytest

from src.handlers.vampire_handler import VampireHandler
from src.services.vampire_service import VampireService, base36
from src.services.vampire_ui import callback_data
from src.games.werewolf import engine
from src.games.werewolf.roles import ROLES
from src.tests.test_vampire_service import Repository
from src.tests.test_vampire_handler import Bot,event
from src.tests.test_werewolf_engine import started


@pytest.mark.asyncio
async def test_cupid_private_callback_updates_same_message_for_second_target():
    repo=Repository();service=VampireService(repo,ruleset='greywolf')
    state=started(['wolf','cupid','seer','villager','villager'])
    state.update(deadline=time.time()+100,phase_opened_at=time.time(),panel_ready=True)
    state['private_delivery']={'1':101,'2':102,'3':103}
    for f in state['seer_findings'].values():f['delivered']=True
    repo.rows[-100,service.key(7)]=state
    handler=VampireHandler(service);handler._flush=AsyncMock();bot=Bot()
    update=event(2,2,102,callback_data(state,'night',4))
    await handler.callback(update,SimpleNamespace(bot=bot))
    assert not update.callback_query.answers[0][1].get('show_alert')
    current=await service.current(-100,7)
    assert current['actions']['2']['targets']==[4]
    assert any(edit['chat_id']==2 and edit['message_id']==102 and '1/2' in edit['text'] for edit in bot.edits)


@pytest.mark.asyncio
async def test_day_power_stays_bound_to_recipient_private_message():
    repo=Repository();service=VampireService(repo,ruleset='greywolf')
    state=engine.activate(engine.advance(started(['wolf','mayor','seer','villager','villager']),now=200),now=201)
    state.update(deadline=time.time()+100,phase_opened_at=time.time(),panel_ready=True)
    state['private_delivery']={'2':102}
    repo.rows[-100,service.key(7)]=state
    handler=VampireHandler(service);handler._flush=AsyncMock();bot=Bot()
    wrong=event(3,3,102,callback_data(state,'day',2))
    await handler.callback(wrong,SimpleNamespace(bot=bot))
    assert wrong.callback_query.answers[0][1]['show_alert']
    correct=event(2,2,102,callback_data(state,'day',2),token='correct')
    await handler.callback(correct,SimpleNamespace(bot=bot))
    assert (await service.current(-100,7))['revealed_roles']['2']=='mayor'


@pytest.mark.asyncio
async def test_public_role_guide_is_read_only_and_never_shows_assigned_roles():
    repo=Repository();service=VampireService(repo,ruleset='greywolf')
    _,state=await service.open(chat_id=-100,thread_id=7,user_id=1,user_name='Owner')
    state.update(panel_message_id=42,panel_dirty=False)
    repo.rows[-100,service.key(7)]=state
    handler=VampireHandler(service);bot=Bot()
    update=event(8,-100,42,callback_data(state,'role_info',ROLES['seer'].source_id),topic=7)
    await handler.callback(update,SimpleNamespace(bot=bot))
    assert bot.edits and 'Kâhin' in bot.edits[-1]['text']
    assert await service.current(-100,7)==state


@pytest.mark.asyncio
async def test_role_guide_during_active_phase_restores_live_panel_after_reading_time():
    repo=Repository();service=VampireService(repo,ruleset='greywolf')
    state=engine.activate(engine.advance(started(['wolf','seer','guardian_angel','villager','villager']),now=200),now=201)
    state.update(panel_message_id=42,panel_dirty=False)
    repo.rows[-100,service.key(7)]=state
    handler=VampireHandler(service);bot=Bot()
    before=time.time()
    await handler._show_guide(bot,state,rules=True)
    refreshed=await service.current(-100,7)
    assert bot.edits and refreshed['panel_dirty']
    assert before+19 <= refreshed['panel_retry_at'] <= time.time()+21
    assert refreshed['phase']==state['phase'] and refreshed['players']==state['players']


def test_negative_base36_fails_fast_instead_of_looping():
    import inspect,subprocess,sys
    program=inspect.getsource(base36)+"\ntry: base36(-1)\nexcept ValueError: print('rejected')\n"
    result=subprocess.run([sys.executable,'-'],input=program,text=True,capture_output=True,timeout=3)
    assert result.returncode==0 and result.stdout.strip()=='rejected'


@pytest.mark.asyncio
async def test_advanced_private_delivery_batches_large_lobby_without_duplicates():
    from src.games.werewolf import engine
    repo=Repository();service=VampireService(repo,ruleset='greywolf')
    state=engine.new_lobby(chat_id=-100,thread_id=7,owner_id=1,owner_name='P1',round_id='many',now=time.time())
    for uid in range(2,36):
        state=engine.join(state,user_id=uid,user_name=f'P{uid}',now=time.time())
    state['phase']='role_delivery'
    state['phase_seq']=1
    for p in state['players']:p['role']='wolf' if p['id']==1 else 'villager'
    state['private_dirty']=True
    repo.rows[-100,service.key(7)]=state
    handler=VampireHandler(service);bot=Bot()
    await handler.deliver_private(bot,chat_id=-100,thread_id=7)
    assert len(bot.sent)==8
    for _ in range(6):await handler.deliver_private(bot,chat_id=-100,thread_id=7)
    assert len(bot.sent)==35
    assert len({m['chat_id'] for m in bot.sent})==35
    assert all(m['protect_content'] is True for m in bot.sent)


@pytest.mark.asyncio
async def test_advanced_callback_flush_only_sends_public_panel_and_defers_private_work():
    repo=Repository();service=VampireService(repo,ruleset='greywolf')
    await service.open(chat_id=-100,thread_id=7,user_id=1,user_name='P1')
    handler=VampireHandler(service);handler.publish=AsyncMock();handler.process=AsyncMock()
    await handler._flush(SimpleNamespace(bot=Bot()),-100,7)
    handler.publish.assert_awaited_once()
    handler.process.assert_not_awaited()
