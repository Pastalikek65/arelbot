from unittest.mock import AsyncMock

import pytest

from src.services.vampire_service import VampireService
from src.tests.test_vampire_service import Repository
from src.tests.test_werewolf_engine import started


@pytest.mark.asyncio
async def test_advanced_service_opens_new_ruleset_but_resumes_an_active_classic_snapshot():
    repo=Repository()
    classic=VampireService(repo)
    _,legacy=await classic.open(chat_id=-100,user_id=1,user_name='P1')
    advanced=VampireService(repo,ruleset='greywolf')
    _,resumed=await advanced.open(chat_id=-100,user_id=1,user_name='P1')
    assert resumed==legacy and resumed.get('ruleset') is None
    _,fresh=await advanced.open(chat_id=-200,user_id=1,user_name='P1')
    assert fresh['ruleset']=='greywolf'


@pytest.mark.asyncio
async def test_night_clock_waits_for_both_controls_and_private_team_information():
    repo=Repository();service=VampireService(repo,ruleset='greywolf')
    state=started(['wolf','seer','guardian_angel','villager','villager'])
    state.update(phase_ready=False,panel_ready=False,deadline=None,panel_wait_until=1000,panel_dirty=True)
    repo.rows[-100,service.key(7)]=state
    await service.acknowledge_panel(chat_id=-100,thread_id=7,round_id=state['round_id'],expected_version=state['version'],message_id=100,now=100)
    for uid in [1,2,3]:
        await service.acknowledge_private(chat_id=-100,thread_id=7,round_id=state['round_id'],phase_seq=state['phase_seq'],user_id=uid,message_id=100+uid,now=101)
    current=await service.current(-100,7)
    assert current['deadline'] is None
    for fid,f in current['seer_findings'].items():
        await service.acknowledge_finding(chat_id=-100,thread_id=7,round_id=state['round_id'],finding_id=fid,user_id=f['user_id'],now=102)
    assert (await service.current(-100,7))['deadline']==192


@pytest.mark.asyncio
async def test_advanced_winner_ids_drive_idempotent_rewards_for_non_wolf_factions():
    repo=Repository();leaderboard=AsyncMock();economy=AsyncMock()
    service=VampireService(repo,leaderboard=leaderboard,economy=economy,ruleset='greywolf')
    state=started(['wolf','tanner','seer','villager','villager'])
    state.update(phase='finished',winner='tanner',winner_ids=[2],score_pending=True)
    repo.rows[-100,service.key(7)]=state
    await service.settle_scores(chat_id=-100,thread_id=7)
    await service.settle_scores(chat_id=-100,thread_id=7)
    paid=[c for c in leaderboard.record_score.await_args_list if c.kwargs['score']>0]
    assert len(paid)==1 and paid[0].kwargs['user_id']==2
    economy.credit.assert_awaited_once()
    assert economy.credit.await_args.args[0]==2


@pytest.mark.asyncio
async def test_only_the_lobby_owner_can_change_role_configuration():
    repo=Repository();service=VampireService(repo,ruleset='greywolf')
    _,state=await service.open(chat_id=-100,user_id=1,user_name='P1')
    with pytest.raises(ValueError,match='not_owner'):
        await service.act(chat_id=-100,user_id=2,action_id='role-ban',action='disable_role',option='seer',round_id=state['round_id'],phase_seq=state['phase_seq'])


@pytest.mark.asyncio
async def test_rematch_keeps_classic_or_chaos_mode_but_resets_players_and_secrets():
    repo=Repository();advanced=VampireService(repo,ruleset='greywolf')
    _,chaos=await advanced.open(chat_id=-100,user_id=1,user_name='P1',mode='chaos')
    chaos['settings']['disabled_roles']=['seer']
    chaos.update(phase='finished',score_pending=False)
    repo.rows[-100,advanced.key(None)]=chaos
    _,rematch=await advanced.open(chat_id=-100,user_id=2,user_name='P2')
    assert rematch['ruleset']=='greywolf' and rematch['settings']['chaos'] is True
    assert rematch['settings']['disabled_roles']==['seer']
    assert rematch['round_id']!=chaos['round_id'] and rematch['rng_seed']!=chaos['rng_seed']
    assert len(rematch['players'])==1 and rematch['owner_id']==2
    classic=VampireService(repo)
    _,old=await classic.open(chat_id=-200,user_id=3,user_name='P3')
    old.update(phase='finished',score_pending=False)
    repo.rows[-200,classic.key(None)]=old
    _,again=await advanced.open(chat_id=-200,user_id=4,user_name='P4')
    assert again.get('ruleset') is None and again['owner_id']==4
