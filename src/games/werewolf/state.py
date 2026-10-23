# SPDX-License-Identifier: GPL-3.0-only
"""Replay-safe private state primitives for the GreyWolf adaptation."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import time

from .roles import ROLES

TERMINAL = frozenset({'finished', 'cancelled'})
DURATIONS = {'night': 90, 'discussion': 90, 'vote': 60, 'runoff': 30, 'hunter': 30}
LOBBY_SECONDS, DELIVERY_SECONDS, PANEL_WAIT_SECONDS = 1200, 600, 300


def clock(now=None):
    return time.time() if now is None else float(now)


def copy_state(state):
    result = deepcopy(state)
    result['version'] = int(state['version']) + 1
    return result


def player(state, user_id, *, alive=False):
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        raise ValueError('vampire_invalid_target') from None
    person = next((p for p in state['players'] if p['id'] == uid), None)
    if person is None or not person['active'] or (alive and not person['alive']):
        raise ValueError('vampire_not_player')
    return person


def living(state):
    return [p for p in state['players'] if p['active'] and p['alive']]


def guard(state, phases, now):
    if (state['phase'] not in phases or not state['phase_ready'] or
            state.get('deadline') is None or clock(now) >= state['deadline']):
        raise ValueError('vampire_stale_phase')


def number(state, purpose):
    # The seed stays in private Mongo state. Phase-independent purpose labels
    # prevent a CAS retry or unrelated player's action from rerolling a chance.
    payload = f"{state['rng_seed']}:{state['day']}:{purpose}".encode()
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], 'big')


def chance(state, purpose, percentage):
    return number(state, purpose) % 100 < max(0, min(100, percentage))


def choose(state, purpose, items):
    return items[number(state, purpose) % len(items)] if items else None


def phase(state, name, now, *, duration=None):
    state['phase'] = name
    state['phase_seq'] += 1
    state['phase_ready'] = False
    state['panel_ready'] = False
    state['deadline'] = None
    state['phase_opened_at'] = clock(now)
    state['panel_wait_until'] = clock(now) + PANEL_WAIT_SECONDS
    state['phase_duration'] = duration if duration is not None else DURATIONS.get(name)
    state['actions'] = {}
    state['votes'] = {}


def finding(state, user_id, text, tag, *, target_id=None):
    # Delivery acknowledgments must never acknowledge a newer payload under
    # an older finding ID (e.g. a Beholder's seer changes during the night).
    digest = hashlib.sha256(str(text).encode()).hexdigest()[:12]
    state.setdefault('seer_findings', {})[f"{state['day']}:{tag}:{int(user_id)}:{digest}"] = {
        'user_id': int(user_id), 'target_id': int(target_id or user_id),
        'text': str(text)[:1800], 'delivered': False, 'retry_at': 0,
    }


def event(state, text, *, kind='event', actor_id=None):
    state.setdefault('events', []).append({'day': state['day'], 'kind': kind,
                                          'text': str(text)[:600], 'actor_id': actor_id})
    state['events'] = state['events'][-80:]
    state['last_event'] = kind


def transform(state, person, role, *, reason):
    if role not in ROLES or person['role'] == role:
        return
    before = person['role']
    person.setdefault('role_history', []).append(before)
    person['role'] = role
    person['used_power'] = False
    person['bullets'] = 2 if role == 'gunner' else 0
    person['role_model'] = None
    finding(state, person['id'], f"Rolün değişti: {ROLES[role].name}. {reason}\n{ROLES[role].description}",
            f"transform:{before}:{role}")


def finish(state, team, winners):
    state['winner'] = team
    state['winner_ids'] = sorted(set(int(uid) for uid in winners))
    state['phase'] = 'finished'
    state['phase_seq'] += 1
    state['deadline'] = None
    state['panel_wait_until'] = None
    state['phase_ready'] = False
    state['score_pending'] = bool(state.get('rewardable', True) and state['winner_ids'])


def cancel(state, reason):
    state['phase'] = 'cancelled'
    state['phase_seq'] += 1
    state['deadline'] = None
    state['panel_wait_until'] = None
    state['cancel_reason'] = reason
    state['score_pending'] = False
    return state
