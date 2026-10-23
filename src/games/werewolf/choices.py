# SPDX-License-Identifier: GPL-3.0-only
"""Recipient/target contracts for GreyWolf private Telegram controls."""
from __future__ import annotations

from .roles import ATTACK_WOLVES, PACK, ROLES
from .state import copy_state, guard, living, player


def night_kind(state, person):
    if not person['active'] or not person['alive'] or state.get('sleep_night') == state['day']:
        return None
    role = ROLES[person['role']]
    if person.get('drunk_until', 0) >= state['day']:
        return None
    if role.key in ATTACK_WOLVES and (state.get('pack_drunk_day') == state['day'] or
                                    state.get('silver_night') == state['day']):
        return None
    if role.setup_action and state['day'] == 1:
        return role.setup_action
    if role.key == 'thief' and state['settings'].get('full_thief'):
        return 'steal'
    if role.key == 'chemist' and person.get('last_potion_day', -10) >= state['day'] - 1:
        return None
    return role.night_action


def day_kind(state, person):
    role = ROLES[person['role']]
    if not person['active'] or not person['alive']:
        return None
    if person.get('acted_day') == state['day']:
        return None
    if role.key == 'gunner' and person.get('bullets', 0) <= 0:
        return None
    if role.key in {'mayor', 'pacifist', 'sandman', 'blacksmith', 'troublemaker'} and person.get('used_power'):
        return None
    return role.day_action


def private_recipients(state):
    phase = state['phase']
    if phase == 'role_delivery':
        return [p for p in state['players'] if p['active']]
    if phase == 'night':
        return [p for p in living(state) if night_kind(state, p)]
    if phase == 'discussion':
        # A spent one-use power should not receive a fresh private prompt on
        # every following day. The initial phase cannot start until the
        # currently eligible players have received their controls.
        return [p for p in living(state) if day_kind(state, p)]
    if phase in {'vote', 'runoff'}:
        return living(state)
    if phase == 'hunter':
        pending = state.get('pending_hunters', [])
        return [player(state, pending[0])] if pending else []
    return []


def target_count(state, person):
    if state['day'] == 1 and person['role'] == 'cupid':
        return 2
    if person['role'] in PACK - {'snow_wolf'} and state.get('cub_rage_day') == state['day']:
        return 2
    return 1


def targets(state, user_id):
    person = player(state, user_id)
    candidates = living(state)
    phase = state['phase']
    if phase == 'role_delivery':
        return []
    if phase in {'vote', 'runoff', 'hunter'}:
        candidates = [p for p in candidates if p['id'] != int(user_id)]
        if phase == 'runoff':
            candidates = [p for p in candidates if p['id'] in state['runoff_candidates']]
        return candidates
    if phase == 'discussion':
        kind = day_kind(state, person)
        if kind is None:
            return []
        if kind in {'reveal', 'silver', 'peace', 'sleep', 'double_lynch'}:
            return [person]
        return [p for p in candidates if p['id'] != int(user_id)]
    kind = night_kind(state, person)
    if kind is None:
        return []
    if kind == 'hunt' or kind == 'freeze':
        candidates = [p for p in candidates if p['role'] not in PACK]
    elif kind == 'convert':
        candidates = [p for p in candidates if p['role'] != 'cultist']
    elif kind != 'lovers':
        candidates = [p for p in candidates if p['id'] != int(user_id) or
                      kind == 'protect' and state['settings'].get('self_heal')]
    if kind == 'protect' and not state['settings'].get('repeat_protection'):
        candidates = [p for p in candidates if p['id'] != person.get('previous_protected')]
    if kind == 'freeze':
        candidates = [p for p in candidates if p.get('frozen_day') != state['day'] - 1]
    chosen = state.get('actions', {}).get(str(user_id), {}).get('targets', [])
    return [p for p in candidates if p['id'] not in chosen]


def submit_night(state, *, user_id, target_id=None, ignite=False, now=None):
    guard(state, {'night'}, now)
    person = player(state, user_id, alive=True)
    kind = night_kind(state, person)
    if not kind:
        raise ValueError('vampire_no_action')
    old = state['actions'].get(str(user_id))
    if old and old.get('done'):
        raise ValueError('vampire_already_acted')
    if ignite and kind != 'douse':
        raise ValueError('vampire_no_action')
    if target_id is not None and target_id not in {p['id'] for p in targets(state, user_id)}:
        raise ValueError('vampire_invalid_target')
    result = copy_state(state)
    choices = list(old.get('targets', [])) if old else []
    if target_id is not None:
        choices.append(int(target_id))
    result['actions'][str(user_id)] = {
        'kind': 'ignite' if ignite else kind,
        'targets': choices,
        'done': ignite or target_id is None or len(choices) >= target_count(state, person),
    }
    return result
