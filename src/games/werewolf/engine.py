# SPDX-License-Identifier: GPL-3.0-only
"""Persistent, transport-free GreyWolf Werewolf adaptation for Arel.

Original rules, role strengths and locale sources are pinned in
src/vendor/greywolf. Telegram I/O, SQL Server and upstream developer shortcuts
are deliberately outside this pure state machine.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import random
import secrets

from . import night
from .balancing import MIN_PLAYERS, MAX_PLAYERS, assign_roles
from .choices import day_kind, night_kind, private_recipients, submit_night, targets
from .effects import check_win, kill, transform_dependents
from .roles import PACK, ROLES
from .state import (DELIVERY_SECONDS, DURATIONS, LOBBY_SECONDS, PANEL_WAIT_SECONDS,
                    TERMINAL, cancel as cancel_in_place, chance, choose, clock,
                    copy_state, event, finding, finish, guard, living, number,
                    phase, player, transform)


def new_lobby(*, chat_id, thread_id, owner_id, owner_name, round_id, now=None, settings=None):
    moment = clock(now)
    options = {'repeat_protection': False, 'self_heal': False, 'reveal_on_death': False,
               'chaos': False, 'full_thief': False, 'burning_overkill': False,
               'disabled_roles': [], 'max_days': 24}
    options.update(settings or {})
    return {'ruleset': 'greywolf', 'schema_version': 2, 'round_id': str(round_id),
            'chat_id': int(chat_id), 'thread_id': int(thread_id) if thread_id is not None else None,
            'owner_id': int(owner_id), 'players': [_new_player(owner_id, owner_name)],
            'phase': 'lobby', 'version': 0, 'created_at': moment, 'last_activity_at': moment,
            'phase_opened_at': moment, 'phase_seq': 0, 'phase_ready': False,
            'panel_ready': False, 'panel_wait_until': None, 'deadline': None,
            'day': 0, 'settings': options, 'actions': {}, 'votes': {},
            'runoff_candidates': [], 'seer_findings': {}, 'last_deaths': [],
            'last_event': None, 'events': [], 'winner': None, 'winner_ids': [],
            'score_pending': False, 'rewardable': True, 'cancel_reason': None,
            'rng_seed': secrets.token_hex(32), 'role_pool': [], 'revealed_roles': {},
            'pending_hunters': [], 'resume_after_hunter': None, 'buried_deaths': 0,
            'graves': 0, 'cub_rage_day': None, 'pack_drunk_day': None,
            'silver_night': None, 'sleep_night': None, 'peace_day': None,
            'double_lynch_day': None, 'lynches_today': 0, 'empty_days': 0}


def _new_player(uid, name):
    return {'id': int(uid), 'name': str(name)[:60], 'ready': False, 'active': True,
            'alive': True, 'role': None, 'original_role': None, 'role_ack': False,
            'role_history': [], 'role_model': None, 'lover_id': None,
            'used_power': False, 'bullets': 0, 'acted_day': None, 'cult_joined': 0,
            'bitten': False, 'doused': False, 'previous_protected': None}


def _lobby_guard(state, now):
    if state['phase'] != 'lobby':
        raise ValueError('vampire_lobby_closed')
    if clock(now) >= state['last_activity_at'] + LOBBY_SECONDS:
        raise ValueError('vampire_stale_phase')


def join(state, *, user_id, user_name, now=None):
    _lobby_guard(state, now)
    if any(p['id'] == int(user_id) for p in state['players']):
        raise ValueError('vampire_already_joined')
    if len(state['players']) >= MAX_PLAYERS:
        raise ValueError('werewolf_player_count')
    result = copy_state(state)
    result['players'].append(_new_player(user_id, user_name))
    result['last_activity_at'] = clock(now)
    return result


def ready(state, *, user_id, now=None):
    _lobby_guard(state, now)
    result = copy_state(state)
    player(result, user_id)['ready'] = True
    result['last_activity_at'] = clock(now)
    return result


def configure(state, *, user_id, option, enabled, now=None):
    _lobby_guard(state, now)
    if int(user_id) != state['owner_id']:
        raise ValueError('werewolf_not_owner')
    if option not in {'repeat_protection', 'self_heal', 'reveal_on_death', 'chaos', 'full_thief', 'burning_overkill'} or not isinstance(enabled, bool):
        raise ValueError('vampire_invalid_setting')
    result = copy_state(state)
    result['settings'][option] = enabled
    result['last_activity_at'] = clock(now)
    return result


def disable_role(state, *, user_id, role_key, now=None):
    _lobby_guard(state, now)
    if int(user_id) != state['owner_id']:
        raise ValueError('werewolf_not_owner')
    if role_key not in ROLES or role_key in {'villager', 'wolf', 'spumpkin'}:
        raise ValueError('werewolf_invalid_role_config')
    result = copy_state(state)
    disabled = set(result['settings']['disabled_roles'])
    disabled.symmetric_difference_update({role_key})
    # Validate the actual current lobby size, or the minimum playable size.
    assign_roles(max(MIN_PLAYERS, len(state['players'])), disabled=disabled,
                 rng=random.Random(number(state, 'validate-roles')), chaos=state['settings']['chaos'])
    result['settings']['disabled_roles'] = sorted(disabled)
    result['last_activity_at'] = clock(now)
    return result


def start(state, *, user_id, roles=None, now=None):
    _lobby_guard(state, now)
    if int(user_id) != state['owner_id']:
        raise ValueError('werewolf_not_owner')
    count = len(state['players'])
    if not MIN_PLAYERS <= count <= MAX_PLAYERS:
        raise ValueError('werewolf_player_count')
    if not all(p['ready'] for p in state['players']):
        raise ValueError('vampire_not_ready')
    if roles is None:
        moment = datetime.fromtimestamp(clock(now), timezone.utc)
        halloween = any((moment + timedelta(hours=h)).month == 10 and
                        28 <= (moment + timedelta(hours=h)).day <= 31 for h in (-11, 0, 14))
        assigned, possible = assign_roles(count, rng=random.Random(number(state, 'deal')),
            chaos=state['settings']['chaos'], disabled=state['settings']['disabled_roles'],
            halloween=halloween, burning_overkill=state['settings']['burning_overkill'])
    else:
        assigned, possible = list(roles), list(ROLES)
        if len(assigned) != count or any(role not in ROLES for role in assigned):
            raise ValueError('vampire_invalid_roles')
    result = copy_state(state)
    result['role_pool'] = possible
    for person, role in zip(result['players'], assigned):
        person['role'] = person['original_role'] = role
        person['role_ack'] = False
        person['bullets'] = 2 if role == 'gunner' else 0
    phase(result, 'role_delivery', now)
    result['last_activity_at'] = clock(now)
    return result


def role_ack(state, *, user_id, now=None):
    if state['phase'] != 'role_delivery' or clock(now) >= state['last_activity_at'] + DELIVERY_SECONDS:
        raise ValueError('vampire_stale_phase')
    result = copy_state(state)
    player(result, user_id)['role_ack'] = True
    if all(p['role_ack'] for p in result['players'] if p['active']):
        result['day'] = 1
        _start_night(result, now)
    return result


def activate(state, *, now=None):
    if state['phase'] not in DURATIONS or state['phase_ready']:
        return state
    result = copy_state(state)
    result['phase_ready'] = result['panel_ready'] = True
    result['panel_wait_until'] = None
    result['deadline'] = clock(now) + max(1, float(result.get('phase_duration') or DURATIONS[state['phase']]))
    return result


def _start_night(state, now):
    transform_dependents(state, apply_bites=True)
    dead = sum(not p['alive'] for p in state['players'])
    state['graves'] = max(0, dead - state.get('buried_deaths', 0))
    state['buried_deaths'] = dead
    phase(state, 'night', now, duration=120 if state['day'] == 1 and any(
        ROLES[p['role']].setup_action for p in living(state)) else DURATIONS['night'])
    _sync_allies(state)


def _sync_allies(state):
    for person in living(state):
        role = person['role']
        allies = []
        label = None
        if role in PACK:
            allies = [p for p in living(state) if p['role'] in PACK and p['id'] != person['id']]
            label = 'Sürü arkadaşların'
        elif role in {'cultist', 'mason'}:
            allies = [p for p in living(state) if p['role'] == role and p['id'] != person['id']]
            label = 'Tarikat arkadaşların' if role == 'cultist' else 'Mason arkadaşların'
        elif role == 'beholder':
            allies = [p for p in living(state) if p['role'] == 'seer']
            label = 'Gerçek Kâhin'
        if label:
            ids = sorted(p['id'] for p in allies)
            if person.get('known_allies') != ids:
                person['known_allies'] = ids
                finding(state, person['id'], label + ': ' + (', '.join(p['name'] for p in allies) or 'şu an kimse yok'), 'allies')


def night_action(state, *, user_id, target_id=None, now=None):
    return submit_night(state, user_id=user_id, target_id=target_id, now=now)


def ignite(state, *, user_id, now=None):
    return submit_night(state, user_id=user_id, ignite=True, now=now)


def day_action(state, *, user_id, target_id=None, now=None):
    guard(state, {'discussion'}, now)
    actor = player(state, user_id, alive=True)
    kind = day_kind(state, actor)
    if not kind:
        raise ValueError('vampire_already_acted')
    if target_id is not None and target_id not in {p['id'] for p in targets(state, user_id)}:
        raise ValueError('vampire_invalid_target')
    result = copy_state(state)
    actor = player(result, user_id, alive=True)
    actor['acted_day'] = state['day']
    if target_id is None:
        return result
    target = player(result, target_id, alive=True)
    if kind == 'investigate':
        finding(result, user_id, f"Dedektif sonucu: {target['name']} — {ROLES[target['role']].name}.", 'detect', target_id=target['id'])
        if chance(result, f"detective-caught:{user_id}", 40):
            for wolf in living(result):
                if wolf['role'] in PACK:
                    finding(result, wolf['id'], f"Dedektifin izini buldun: {actor['name']}.", 'detective_exposed')
    elif kind == 'shoot':
        actor['bullets'] -= 1
        result['revealed_roles'][str(user_id)] = 'gunner'
        event(result, f"{actor['name']} silahını kullandı.", kind='gunshot', actor_id=user_id)
        kill(result, target, cause='gunner', killer=actor)
    elif kind == 'detonate':
        if chance(result, f"detonate:{user_id}", 40):
            kill(result, target, cause='spumpkin', killer=actor)
            kill(result, actor, cause='spumpkin')
        else:
            finding(result, user_id, 'Patlama girişimin bu kez başarısız oldu; yarın tekrar deneyebilirsin.', 'detonate_failed')
    elif kind == 'rice':
        actor['rice_target'] = target['id']
        finding(result, user_id, f"{target['name']} kapısına pirinç serptin; izler gece sonunda gelecek.", 'rice_placed')
    else:
        actor['used_power'] = True
        if kind == 'reveal':
            result['revealed_roles'][str(user_id)] = 'mayor'
            event(result, f"{actor['name']} Belediye Başkanı olduğunu açıkladı; oyu artık iki sayılır.", kind='mayor')
        elif kind == 'peace':
            result['peace_day'] = state['day']
            event(result, 'Barışçı bugün linç yapılmamasını sağladı.', kind='peace')
        elif kind == 'double_lynch':
            result['double_lynch_day'] = state['day']
            event(result, 'Olay Çıkaran bugün iki eleme oylaması yapılmasını sağladı.', kind='double_lynch')
        elif kind == 'silver':
            result['silver_night'] = state['day'] + 1
            event(result, 'Demirci gelecek gece için gümüş koruması hazırladı.', kind='silver')
        elif kind == 'sleep':
            result['sleep_night'] = state['day'] + 1
            event(result, 'Uyku Perisi gelecek geceyi herkes için eylemsiz kılacak.', kind='sleep')
    _after_effects(result, now, resume='discussion', remaining=max(1, state['deadline'] - clock(now)))
    return result


def vote(state, *, user_id, target_id=None, now=None):
    guard(state, {'vote', 'runoff'}, now)
    actor = player(state, user_id, alive=True)
    if str(user_id) in state['votes']:
        raise ValueError('vampire_already_voted')
    eligible = targets(state, user_id)
    if target_id is not None and target_id not in {p['id'] for p in eligible}:
        raise ValueError('vampire_invalid_target')
    result = copy_state(state)
    actual = target_id
    if target_id is not None and actor['role'] == 'clumsy_guy' and chance(state, f"clumsy:{user_id}:{state['lynches_today']}", 50):
        actual = choose(state, f"clumsy-target:{user_id}:{state['lynches_today']}", [p['id'] for p in eligible])
    result['votes'][str(user_id)] = actual
    return result


def _voting_result(state, now):
    if state.get('peace_day') == state['day']:
        event(state, 'Barış günü: eleme yapılmadı.', kind='peace')
        _end_day(state, now)
        return
    tally = {}
    for uid, target in state['votes'].items():
        voter = next((p for p in living(state) if p['id'] == int(uid)), None)
        if voter and target in {p['id'] for p in living(state)}:
            weight = 2 if state['revealed_roles'].get(uid) == 'mayor' and voter['role'] == 'mayor' else 1
            tally[target] = tally.get(target, 0) + weight
    top = max(tally.values(), default=0)
    candidates = sorted(uid for uid, count in tally.items() if count == top)
    if len(candidates) > 1 and state['phase'] == 'vote':
        state['runoff_candidates'] = candidates
        phase(state, 'runoff', now)
        event(state, 'Oylar eşitlendi; yalnız eşit adaylarla rövanş yapılacak.', kind='runoff')
        return
    state['lynches_today'] += 1
    if len(candidates) == 1:
        victim = player(state, candidates[0], alive=True)
        if victim['role'] == 'prince' and not victim.get('prince_spared'):
            victim['prince_spared'] = True
            state['revealed_roles'][str(victim['id'])] = 'prince'
            event(state, f"{victim['name']} Prens olduğu için ilk linçten kurtuldu.", kind='prince')
        elif victim['role'] == 'tanner':
            kill(state, victim, cause='lynch')
            finish(state, 'tanner', [victim['id']])
            return
        else:
            kill(state, victim, cause='lynch')
    else:
        event(state, 'Oylama bir eleme kararı vermedi.', kind='no_lynch')
    if _after_effects(state, now, resume='next_lynch'):
        return
    _next_lynch(state, now)


def _next_lynch(state, now):
    if state.get('double_lynch_day') == state['day'] and state['lynches_today'] < 2:
        state['runoff_candidates'] = []
        phase(state, 'vote', now)
    else:
        _end_day(state, now)


def _end_day(state, now):
    if state['day'] >= state['settings'].get('max_days', 24):
        cancel_in_place(state, 'day_limit')
        return
    state['day'] += 1
    state['lynches_today'] = 0
    _start_night(state, now)


def _after_effects(state, now, *, resume, remaining=None):
    transform_dependents(state)
    _sync_allies(state)
    if state.get('pending_hunters'):
        state['resume_after_hunter'] = {'phase': resume, 'remaining': remaining}
        phase(state, 'hunter', now)
        return True
    return check_win(state)


def hunter_action(state, *, user_id, target_id=None, now=None):
    guard(state, {'hunter'}, now)
    if not state.get('pending_hunters') or state['pending_hunters'][0] != int(user_id):
        raise ValueError('vampire_no_action')
    actor = player(state, user_id)
    if target_id is not None and target_id not in {p['id'] for p in living(state)}:
        raise ValueError('vampire_invalid_target')
    result = copy_state(state)
    result['pending_hunters'].pop(0)
    if target_id is not None:
        kill(result, player(result, target_id, alive=True), cause='hunter', killer=player(result, user_id))
    _resume_hunter(result, now)
    return result


def _resume_hunter(state, now):
    transform_dependents(state)
    if state['pending_hunters']:
        phase(state, 'hunter', now)
        return
    if check_win(state):
        return
    resume = state.get('resume_after_hunter') or {'phase': 'discussion', 'remaining': None}
    state['resume_after_hunter'] = None
    if resume['phase'] == 'next_lynch':
        _next_lynch(state, now)
    else:
        phase(state, resume['phase'], now, duration=resume.get('remaining'))


def advance(state, *, now=None):
    moment = clock(now)
    if state['phase'] in TERMINAL:
        return state
    if state['phase'] == 'lobby':
        return cancel_in_place(copy_state(state), 'lobby_timeout') if moment >= state['last_activity_at'] + LOBBY_SECONDS else state
    if state['phase'] == 'role_delivery':
        return cancel_in_place(copy_state(state), 'role_delivery_timeout') if moment >= state['last_activity_at'] + DELIVERY_SECONDS else state
    if not state['phase_ready']:
        return cancel_in_place(copy_state(state), 'panel_delivery_timeout') if moment >= float(state.get('panel_wait_until') or moment + 1) else state
    all_done = False
    if state['phase'] == 'night':
        recipients = private_recipients(state)
        all_done = all(state['actions'].get(str(p['id']), {}).get('done') for p in recipients)
    elif state['phase'] in {'vote', 'runoff'}:
        all_done = all(str(p['id']) in state['votes'] for p in living(state))
    if moment < state['deadline'] and not (all_done and moment >= state['phase_opened_at'] + 3):
        return state
    result = copy_state(state)
    if state['phase'] == 'night':
        night.resolve(result)
        if not _after_effects(result, moment, resume='discussion'):
            phase(result, 'discussion', moment)
    elif state['phase'] == 'discussion':
        if state.get('peace_day') == state['day']:
            event(result, 'Barış günü: eleme oylaması yapılmadı.', kind='peace')
            _end_day(result, moment)
        else:
            phase(result, 'vote', moment)
    elif state['phase'] in {'vote', 'runoff'}:
        _voting_result(result, moment)
    elif state['phase'] == 'hunter':
        if result['pending_hunters']:
            result['pending_hunters'].pop(0)
        _resume_hunter(result, moment)
    return result


def leave(state, *, user_id, now=None):
    if state['phase'] in TERMINAL:
        raise ValueError('vampire_closed')
    if state['phase_ready'] and clock(now) >= state['deadline']:
        raise ValueError('vampire_stale_phase')
    result = copy_state(state)
    person = player(result, user_id)
    if state['phase'] == 'lobby':
        result['players'] = [p for p in result['players'] if p['id'] != int(user_id)]
        if not result['players']:
            return cancel_in_place(result, 'empty_lobby')
    elif state['phase'] == 'role_delivery':
        return cancel_in_place(result, 'player_left')
    else:
        result['rewardable'] = False
        person['active'] = False
        if person['alive']:
            kill(result, person, cause='leave', hunter_shot=False)
        result['pending_hunters'] = [uid for uid in result.get('pending_hunters', []) if uid != int(user_id)]
        if not result['pending_hunters'] and result['phase'] == 'hunter':
            _resume_hunter(result, now)
        elif result['phase'] == 'hunter':
            # A different player's departure must not replace the hunter's
            # saved return phase with another hunter phase.
            pass
        else:
            _after_effects(result, now, resume=state['phase'], remaining=max(1, (state.get('deadline') or clock(now)+1) - clock(now)))
    if result['phase'] not in TERMINAL and result['owner_id'] == int(user_id):
        result['owner_id'] = next(p['id'] for p in result['players'] if p['active'])
    result['last_activity_at'] = clock(now)
    return result


def cancel(state, *, reason='moderator', user_id=None, moderator=False):
    if user_id is not None and not moderator and int(user_id) != state['owner_id']:
        raise ValueError('vampire_not_owner')
    if state['phase'] in TERMINAL:
        raise ValueError('vampire_closed')
    return cancel_in_place(copy_state(state), reason)


def public_state(state):
    allowed = ('ruleset', 'schema_version', 'round_id', 'chat_id', 'thread_id', 'owner_id',
               'phase', 'phase_seq', 'version', 'day', 'deadline', 'phase_ready', 'panel_ready',
               'panel_wait_until', 'winner', 'cancel_reason', 'score_pending', 'last_deaths',
               'last_event', 'runoff_candidates', 'events', 'lynches_today')
    result = {key: deepcopy(state.get(key)) for key in allowed}
    # Only explicit lobby options are public; future secret settings cannot
    # leak by adding an unreviewed key to the private settings dictionary.
    result['settings'] = {key: deepcopy(state['settings'].get(key)) for key in (
        'repeat_protection', 'self_heal', 'reveal_on_death', 'chaos', 'full_thief',
        'burning_overkill', 'disabled_roles', 'max_days')}
    result['players'] = []
    for person in state['players']:
        row = {key: deepcopy(person[key]) for key in ('id', 'name', 'ready', 'active', 'alive', 'role_ack')}
        if state['phase'] == 'finished' or state['settings'].get('reveal_on_death') and not person['alive']:
            row['role'] = person['role']
        elif str(person['id']) in state.get('revealed_roles', {}):
            row['role'] = state['revealed_roles'][str(person['id'])]
        result['players'].append(row)
    result['ballots_cast'] = len(state.get('votes', {}))
    if state['phase'] == 'finished':
        result['winner_ids'] = list(state.get('winner_ids', []))
    return result
