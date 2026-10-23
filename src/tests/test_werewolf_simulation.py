import random

from bson import BSON
import pytest

from src.games.werewolf import engine as game
from src.games.werewolf.choices import day_kind, targets


@pytest.mark.parametrize('count', [5,7,10,15,20,30,35])
@pytest.mark.parametrize('chaos', [False,True])
def test_seeded_full_games_finish_without_leaking_private_state(count,chaos):
    for seed in range(5):
        rng=random.Random(seed)
        state=game.new_lobby(chat_id=-100,thread_id=None,owner_id=1,owner_name='P1',round_id=f'r{seed}',now=0,settings={'chaos':chaos})
        state['rng_seed']=f'simulation-{count}-{chaos}-{seed}'
        for uid in range(2,count+1):state=game.join(state,user_id=uid,user_name=f'P{uid}',now=1)
        for uid in range(1,count+1):state=game.ready(state,user_id=uid,now=2)
        state=game.start(state,user_id=1,now=3)
        for uid in range(1,count+1):state=game.role_ack(state,user_id=uid,now=4)
        moment=5.0
        for _ in range(300):
            if state['phase'] in game.TERMINAL:break
            state=game.activate(state,now=moment)
            phase=state['phase'];seq=state['phase_seq']
            for person in game.private_recipients(state):
                if state['phase_seq']!=seq:break
                uid=person['id']
                person=next(p for p in state['players'] if p['id']==uid)
                if phase=='discussion' and day_kind(state,person) is None:continue
                for _choice in range(2):
                    available=targets(state,uid)
                    target=rng.choice(available)['id'] if available and rng.random()>0.2 else None
                    if phase=='night':
                        if person['role']=='arsonist' and rng.random()<0.3:
                            state=game.ignite(state,user_id=uid,now=moment+0.01)
                        else:state=game.night_action(state,user_id=uid,target_id=target,now=moment+0.01)
                        if state['actions'][str(uid)]['done']:break
                    elif phase=='discussion':
                        state=game.day_action(state,user_id=uid,target_id=target,now=moment+0.01);break
                    elif phase in {'vote','runoff'}:
                        state=game.vote(state,user_id=uid,target_id=target,now=moment+0.01);break
                    elif phase=='hunter':
                        state=game.hunter_action(state,user_id=uid,target_id=target,now=moment+0.01);break
            view=game.public_state(state)
            assert all(k not in view for k in ['rng_seed','role_pool','actions','votes','seer_findings','visits'])
            assert BSON.encode(state)
            if state['phase'] in game.TERMINAL:break
            if state['phase_seq']!=seq:
                moment+=1;continue
            moment=state['deadline']+0.1
            state=game.advance(state,now=moment)
            moment+=1
        assert state['phase'] in game.TERMINAL, (count,chaos,seed,state['phase'],state['day'])
        if state['phase']=='finished':
            assert set(state['winner_ids']) <= {p['id'] for p in state['players']}
