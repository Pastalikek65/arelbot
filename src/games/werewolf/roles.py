# SPDX-License-Identifier: GPL-3.0-only
"""GreyWolf role catalog and contextual strength rules, ported from GameBalancing.cs.

Pinned source and license: src/vendor/greywolf/UPSTREAM.json.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from types import MappingProxyType
from typing import Sequence


@dataclass(frozen=True, slots=True)
class Role:
    key: str
    source_name: str
    source_id: int
    name: str
    emoji: str
    team: str
    description: str
    night_action: str | None = None
    day_action: str | None = None
    setup_action: str | None = None
    description_full: str | None = None


_DATA = json.loads((Path(__file__).resolve().parents[2] / 'data/werewolf_roles.json').read_text(encoding='utf-8'))
ROLES = MappingProxyType({row['key']: Role(**row) for row in _DATA['roles']})
ATTACK_WOLVES = frozenset({'wolf', 'alpha_wolf', 'wolf_cub', 'lycan'})
PACK = ATTACK_WOLVES | {'snow_wolf'}
NON_VILLAGE = frozenset({'cultist', 'serial_killer', 'tanner', 'wolf', 'alpha_wolf',
                       'sorcerer', 'wolf_cub', 'lycan', 'thief', 'snow_wolf', 'arsonist'})
REVEALED_VILLAGE = frozenset({'blacksmith', 'mayor', 'pacifist', 'gunner', 'sandman', 'troublemaker'})
# This is the upstream balance heuristic; actual conversion probabilities
# belong to the night resolver and are not inferred from this set.
RESISTS_CULT = frozenset({'seer', 'guardian_angel', 'detective', 'cursed', 'harlot',
                         'hunter', 'doppelganger', 'wolf', 'alpha_wolf', 'wolf_cub',
                         'serial_killer', 'lycan', 'thief', 'snow_wolf'})


def role_strength(key: str, roles: Sequence[str]) -> int:
    if key not in ROLES:
        raise ValueError('werewolf_invalid_role')
    if key == 'seer':
        return 7 - roles.count('lycan') - 2 * roles.count('wolf_man')
    if key == 'cursed':
        return 1 - sum(role in PACK for role in roles) // 2
    if key == 'tanner':
        return len(roles) // 2
    if key == 'beholder':
        return 1 + 4 * ('seer' in roles) + ('fool' in roles)
    if key == 'cultist':
        return 10 + sum(role not in RESISTS_CULT for role in roles)
    if key == 'cultist_hunter':
        return 7 if 'cultist' in roles else 1
    if key == 'mason':
        return roles.count('mason') + 3 if roles.count('mason') > 1 else 1
    if key == 'wolf_cub':
        return 12 if any(role in roles for role in (
            'alpha_wolf', 'wolf', 'lycan', 'snow_wolf', 'wild_child',
            'doppelganger', 'cursed', 'traitor')) else 10
    if key == 'guardian_angel':
        return 7 + ('arsonist' in roles)
    if key == 'barkeep':
        return 1 + roles.count('villager')
    return {
        'villager': 1, 'drunk': 3, 'harlot': 6, 'traitor': 0, 'detective': 6,
        'wolf': 10, 'gunner': 6, 'fool': 3, 'wild_child': 1, 'apprentice_seer': 6,
        'doppelganger': 2, 'cupid': 2, 'hunter': 6, 'serial_killer': 15,
        'sorcerer': 2, 'alpha_wolf': 12, 'blacksmith': 5, 'clumsy_guy': -1,
        'mayor': 4, 'prince': 3, 'wolf_man': 1, 'augur': 5, 'pacifist': 3,
        'wise_elder': 3, 'oracle': 4, 'sandman': 3, 'lycan': 10, 'thief': 0,
        'troublemaker': 5, 'chemist': 0, 'snow_wolf': 15, 'grave_digger': 5,
        'arsonist': 8, 'spumpkin': 2, 'chef': 3,
    }[key]
