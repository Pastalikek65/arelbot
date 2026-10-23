# SPDX-License-Identifier: GPL-3.0-only
"""Bounded port of GreyWolf GameBalancing.cs (pinned source in vendor/greywolf)."""
from __future__ import annotations

import random
import secrets
from typing import Iterable

from .roles import ATTACK_WOLVES, NON_VILLAGE, PACK, REVEALED_VILLAGE, ROLES, role_strength

MIN_PLAYERS, MAX_PLAYERS = 5, 35


def _pool(count: int, disabled: set[str], rng, halloween: bool) -> list[str]:
    wolves = [role for role in ('wolf', 'alpha_wolf', 'wolf_cub', 'lycan', 'snow_wolf') if role not in disabled]
    result = []
    for _ in range(min(max(count // 5, 1), 5)):
        if not wolves:
            break
        chosen = rng.choice(wolves)
        result.append(chosen)
        if chosen != 'wolf':
            wolves.remove(chosen)
    if result == ['snow_wolf']:
        result[0] = rng.choice(sorted(ATTACK_WOLVES - disabled))
    for key in ROLES:
        if key in PACK or key in disabled:
            continue
        if key in {'cultist', 'cultist_hunter'} and count <= 10:
            continue
        if key == 'spumpkin' and not halloween:
            continue
        if key == 'apprentice_seer' and 'seer' in disabled:
            continue
        result.append(key)
    if 'mason' not in disabled:
        result.extend(['mason'] * 2)
    if 'cultist_hunter' in result and 'cultist' not in disabled:
        result.extend(['cultist'] * 2)
    if 'villager' not in disabled:
        result.extend(['villager'] * (count // 4))
    return result


def assign_roles(count: int, *, rng: random.Random | None = None, chaos: bool = False,
                 disabled: Iterable[str] = (), halloween: bool = False,
                 burning_overkill: bool = False) -> tuple[list[str], list[str]]:
    """Return assigned roles and the upstream possible-role deck.

    Randomness is injectable for replay. Disabled-role checks also close the
    upstream empty-wolf-list / disabled-apprentice replacement edge cases.
    No partially balanced assignment is returned after the attempt limit.
    """
    if isinstance(count, bool) or not MIN_PLAYERS <= count <= MAX_PLAYERS:
        raise ValueError('werewolf_player_count')
    banned = set(disabled)
    if banned - ROLES.keys() or not ATTACK_WOLVES - banned:
        raise ValueError('werewolf_invalid_role_config')
    if not (NON_VILLAGE - banned) or not (ROLES.keys() - NON_VILLAGE - banned):
        raise ValueError('werewolf_invalid_role_config')
    rng = rng or secrets.SystemRandom()
    for _ in range(500):
        possible = _pool(count, banned, rng, halloween)
        while len(possible) < count and 'villager' not in banned:
            possible.append('villager')
        if len(possible) < count:
            raise ValueError('werewolf_invalid_role_config')
        roles = rng.sample(possible, count)
        helpers = {'sorcerer', 'traitor', 'snow_wolf'}
        if any(r in helpers for r in roles) and not any(r in ATTACK_WOLVES for r in roles):
            index = next(i for i, r in enumerate(roles) if r in helpers)
            roles[index] = rng.choice(sorted(ATTACK_WOLVES - banned))
        if 'cultist' in roles and 'cultist_hunter' not in roles and 'cultist_hunter' not in banned:
            index = next((i for i, role in enumerate(roles) if role not in NON_VILLAGE), None)
            if index is None:
                continue
            roles[index] = 'cultist_hunter'
        if 'apprentice_seer' in roles and 'seer' not in roles:
            roles[roles.index('apprentice_seer')] = 'seer'
        enemies = [r for r in roles if r in NON_VILLAGE]
        village = [r for r in roles if r not in NON_VILLAGE]
        if not enemies or len(enemies) >= len(village):
            continue
        if not any(r not in {'sorcerer', 'tanner', 'thief'} for r in enemies):
            continue
        if not burning_overkill and 'serial_killer' in roles and 'arsonist' in roles:
            continue
        if not chaos:
            variance = count // 4 + 1
            if abs(sum(role_strength(r, roles) for r in village) -
                   sum(role_strength(r, roles) for r in enemies)) > variance:
                continue
            if sum(r in REVEALED_VILLAGE for r in roles) * 3 > count:
                continue
            only_wolves = not any(r in roles for r in ('arsonist', 'serial_killer', 'cultist'))
            stoppers = {'troublemaker', 'sandman'} | ({'blacksmith'} if only_wolves else set())
            if sum(r in stoppers for r in roles) * 4 > count:
                continue
        # Repairs may promote a role not present in this sampled pool. The
        # public possible-role inventory must still describe that real role.
        possible.extend(role for role in roles if role not in possible)
        return roles, list(dict.fromkeys(possible))
    raise ValueError('werewolf_cannot_balance')
