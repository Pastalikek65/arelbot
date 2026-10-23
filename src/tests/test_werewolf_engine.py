from copy import deepcopy

import pytest

from src.games.werewolf import engine as game


def started(roles):
    state = game.new_lobby(chat_id=-100, thread_id=7, owner_id=1, owner_name='P1', round_id='fixture', now=0)
    for uid in range(2, len(roles)+1):
        state = game.join(state, user_id=uid, user_name=f'P{uid}', now=1)
    for uid in range(1,len(roles)+1):
        state = game.ready(state,user_id=uid,now=2)
    state = game.start(state,user_id=1,roles=roles,now=3)
    for uid in range(1,len(roles)+1):
        state = game.role_ack(state,user_id=uid,now=4)
    return game.activate(state,now=5)


def test_roles_are_private_and_old_state_is_not_mutated():
    state = started(['wolf','seer','guardian_angel','villager','villager'])
    before=deepcopy(state)
    changed=game.night_action(state,user_id=1,target_id=4,now=6)
    assert state==before and changed['version']>state['version']
    public=game.public_state(changed)
    for key in ['rng_seed','actions','votes','role_pool','seer_findings']:
        assert key not in public
    assert all('role' not in p for p in public['players'])


def test_guardian_prevents_kill_and_vision_is_private():
    state=started(['wolf','seer','guardian_angel','villager','villager'])
    state=game.night_action(state,user_id=1,target_id=4,now=6)
    state=game.night_action(state,user_id=2,target_id=1,now=7)
    state=game.night_action(state,user_id=3,target_id=4,now=8)
    state=game.advance(state,now=200)
    assert state['phase']=='discussion'
    assert state['players'][3]['alive']
    assert any(f['user_id']==2 and 'Kurt Adam' in f['text'] for f in state['seer_findings'].values())
    assert 'seer_findings' not in game.public_state(state)


def test_stale_and_duplicate_night_choices_are_rejected():
    state=started(['wolf','seer','guardian_angel','villager','villager'])
    state=game.night_action(state,user_id=1,target_id=4,now=6)
    with pytest.raises(ValueError,match='already_acted'):
        game.night_action(state,user_id=1,target_id=5,now=7)
    with pytest.raises(ValueError,match='stale_phase'):
        game.night_action(state,user_id=2,target_id=1,now=1000)


def test_cupid_requires_two_different_targets_and_death_chains():
    state=started(['wolf','cupid','seer','villager','villager','villager','villager'])
    state=game.night_action(state,user_id=2,target_id=4,now=6)
    with pytest.raises(ValueError):
        game.night_action(state,user_id=2,target_id=4,now=7)
    state=game.night_action(state,user_id=2,target_id=5,now=8)
    state=game.night_action(state,user_id=1,target_id=4,now=9)
    state=game.advance(state,now=200)
    assert not state['players'][3]['alive'] and not state['players'][4]['alive']


def test_lone_wolf_death_transforms_traitor_before_village_win():
    state=started(['wolf','traitor','seer','villager','villager'])
    state=game.advance(state,now=200)
    state=game.activate(state,now=201)
    state=game.advance(state,now=400)
    state=game.activate(state,now=401)
    for uid in range(2,6):
        state=game.vote(state,user_id=uid,target_id=1,now=402)
    state=game.advance(state,now=500)
    assert state['players'][1]['role']=='wolf'
    assert state['phase']!='finished'


@pytest.mark.parametrize('target_role,expected', [('lycan','Köylü'),('wolf_man','Kurt Adam')])
def test_seer_preserves_greywolf_disguises(target_role, expected):
    state=started(['wolf','seer',target_role,'villager','villager','villager','villager'])
    state=game.night_action(state,user_id=2,target_id=3,now=6)
    state=game.advance(state,now=200)
    assert any(f['user_id']==2 and expected in f['text'] for f in state['seer_findings'].values())


def test_snow_wolf_blocks_guardian_before_pack_attack():
    state=started(['wolf','snow_wolf','guardian_angel','villager','seer','villager','villager'])
    state=game.night_action(state,user_id=1,target_id=4,now=6)
    state=game.night_action(state,user_id=2,target_id=3,now=7)
    state=game.night_action(state,user_id=3,target_id=4,now=8)
    state=game.advance(state,now=200)
    assert not state['players'][3]['alive']
    assert state['players'][2]['frozen_day']==1


def test_cursed_becomes_wolf_instead_of_dying():
    state=started(['wolf','cursed','seer','villager','villager'])
    state=game.night_action(state,user_id=1,target_id=2,now=6)
    state=game.advance(state,now=200)
    assert state['players'][1]['alive'] and state['players'][1]['role']=='wolf'


def test_dead_seer_promotes_apprentice_and_updates_beholder_without_overwriting_old_finding():
    state=started(['wolf','apprentice_seer','seer','beholder','villager','villager','villager'])
    before=set(state['seer_findings'])
    state=game.night_action(state,user_id=1,target_id=3,now=6)
    state=game.advance(state,now=200)
    assert state['players'][1]['role']=='seer'
    assert before <= set(state['seer_findings'])
    assert any(f['user_id']==4 and 'P2' in f['text'] for f in state['seer_findings'].values())


@pytest.mark.parametrize('visit_target,wolf_target,alive', [(1,None,False),(4,2,True),(4,4,False)])
def test_harlot_visit_and_empty_home_rules(visit_target,wolf_target,alive):
    state=started(['wolf','harlot','seer','villager','villager','villager','villager'])
    state=game.night_action(state,user_id=2,target_id=visit_target,now=6)
    if wolf_target:
        state=game.night_action(state,user_id=1,target_id=wolf_target,now=7)
    state=game.advance(state,now=200)
    assert state['players'][1]['alive'] is alive


def test_normal_thief_steals_after_the_original_seer_acts():
    state=started(['wolf','thief','seer','villager','villager','villager','villager'])
    state=game.night_action(state,user_id=2,target_id=3,now=6)
    state=game.night_action(state,user_id=3,target_id=1,now=7)
    state=game.advance(state,now=200)
    assert state['players'][1]['role']=='seer' and state['players'][2]['role']=='villager'
    assert any(f['user_id']==3 and 'Kurt Adam' in f['text'] for f in state['seer_findings'].values())


def test_doppelganger_inherits_dead_role_models_gunner_power():
    state=started(['wolf','doppelganger','gunner','seer','villager','villager','villager'])
    state=game.night_action(state,user_id=2,target_id=3,now=6)
    state=game.night_action(state,user_id=1,target_id=3,now=7)
    state=game.advance(state,now=200)
    assert state['players'][1]['role']=='gunner' and state['players'][1]['bullets']==2


def test_tanner_wins_only_from_lynching():
    state=started(['wolf','tanner','seer','villager','villager'])
    state=game.activate(game.advance(state,now=200),now=201)
    state=game.activate(game.advance(state,now=400),now=401)
    for uid in [1,3,4,5]:
        state=game.vote(state,user_id=uid,target_id=2,now=402)
    state=game.advance(state,now=500)
    assert state['winner']=='tanner' and state['winner_ids']==[2]


def test_mayor_reveal_gives_double_vote_and_winner_rewards_use_current_team():
    state=started(['wolf','mayor','seer','villager','villager'])
    state=game.activate(game.advance(state,now=200),now=201)
    state=game.day_action(state,user_id=2,target_id=2,now=202)
    assert game.public_state(state)['players'][1]['role']=='mayor'
    state=game.activate(game.advance(state,now=400),now=401)
    for uid,target in [(2,1),(3,1),(1,4),(4,5),(5,4)]:
        state=game.vote(state,user_id=uid,target_id=target,now=402)
    state=game.advance(state,now=500)
    assert state['winner']=='village' and 2 in state['winner_ids'] and 1 not in state['winner_ids']


def test_prince_survives_first_lynch_without_exposing_other_roles():
    state=started(['wolf','prince','seer','villager','villager'])
    state=game.activate(game.advance(state,now=200),now=201)
    state=game.activate(game.advance(state,now=400),now=401)
    for uid in [1,3,4,5]:
        state=game.vote(state,user_id=uid,target_id=2,now=402)
    state=game.advance(state,now=500)
    assert state['players'][1]['alive'] and state['players'][1]['prince_spared']
    assert 'role' not in game.public_state(state)['players'][0]


def test_pacifist_skips_vote_and_cannot_reuse_power():
    state=started(['wolf','pacifist','seer','villager','villager'])
    state=game.activate(game.advance(state,now=200),now=201)
    state=game.day_action(state,user_id=2,target_id=2,now=202)
    with pytest.raises(ValueError):
        game.day_action(state,user_id=2,target_id=2,now=203)
    state=game.advance(state,now=400)
    assert state['phase']=='night' and state['day']==2


def test_day_gunner_loses_remaining_power_after_shooting_wise_elder():
    state=started(['wolf','gunner','wise_elder','seer','villager','villager','villager'])
    state=game.activate(game.advance(state,now=200),now=201)
    state=game.day_action(state,user_id=2,target_id=3,now=202)
    assert not state['players'][2]['alive']
    assert state['players'][1]['role']=='villager' and state['players'][1]['bullets']==0


def test_hunter_last_shot_is_a_private_timed_phase():
    state=started(['wolf','hunter','seer','villager','villager'])
    state=game.activate(game.advance(state,now=200),now=201)
    state=game.activate(game.advance(state,now=400),now=401)
    for uid in [1,3,4,5]:
        state=game.vote(state,user_id=uid,target_id=2,now=402)
    state=game.advance(state,now=500)
    assert state['phase']=='hunter' and [p['id'] for p in game.private_recipients(state)]==[2]
    state=game.activate(state,now=501)
    state=game.hunter_action(state,user_id=2,target_id=1,now=502)
    assert state['winner']=='village'


def test_full_state_is_bson_compatible_with_string_map_keys():
    from bson import BSON
    state=started(['wolf','seer','guardian_angel','villager','villager'])
    assert BSON.encode(state)


def test_other_player_leaving_during_hunter_shot_does_not_restart_empty_hunter_phase():
    state=started(['wolf','hunter','seer','villager','villager'])
    state=game.activate(game.advance(state,now=200),now=201)
    state=game.activate(game.advance(state,now=400),now=401)
    for uid in [1,3,4,5]:state=game.vote(state,user_id=uid,target_id=2,now=402)
    state=game.advance(state,now=500)
    assert state['phase']=='hunter'
    resume=state['resume_after_hunter']
    state=game.leave(state,user_id=4,now=501)
    assert state['phase']=='hunter' and state['resume_after_hunter']==resume
    state=game.hunter_action(game.activate(state,now=502),user_id=2,target_id=None,now=503)
    assert state['phase']!='hunter'


def test_serial_killer_grave_penalty_can_retarget_next_night():
    from src.games.werewolf import night
    from src.games.werewolf.state import chance, choose
    state=started(['wolf','serial_killer','villager','villager','villager'])
    state['day']=2
    state['players'][1]['stumbled_day']=1
    state['actions']={'2':{'kind':'kill','targets':[3], 'done':True}}
    for seed in range(1000):
        state['rng_seed']=str(seed)
        pool=[p for p in state['players'] if p['id']!=2]
        if chance(state,'sk-stumble:2',50) and choose(state,'sk-stumble-target:2',pool)['id']!=3:
            break
    expected=choose(state,'sk-stumble-target:2',pool)['id']
    night.resolve(state)
    assert not state['players'][expected-1]['alive']
    assert state['players'][2]['alive']
    assert any(f['user_id']==2 and 'hedef' in f['text'].lower() for f in state['seer_findings'].values())
