# SPDX-License-Identifier: GPL-3.0-only
"""Deaths, transformations and victory checks adapted from Werewolf.cs."""
from __future__ import annotations

from .roles import ATTACK_WOLVES, PACK, ROLES
from .state import chance, choose, event, finding, finish, living, player, transform


def kill(state, victim, *, cause, killer=None, hunter_shot=True):
    if not victim['alive']:
        return
    victim['alive'] = False
    victim['death_cause'] = cause
    victim['death_day'] = state['day']
    victim['killed_by'] = killer['id'] if killer else None
    state.setdefault('last_deaths', []).append(victim['id'])
    event(state, f"{victim['name']} oyun dışı kaldı.", kind='death')
    if victim['role'] == 'wolf_cub':
        state['cub_rage_day'] = state['day'] + 1
    if victim['role'] == 'drunk' and cause == 'wolves':
        state['pack_drunk_day'] = state['day'] + 1
    if victim['role'] == 'wise_elder' and killer and cause in {'gunner', 'hunter'}:
        transform(state, killer, 'villager', reason='Bilgeyi öldürdüğün için yeteneğini kaybettin.')
    lover_id = victim.get('lover_id')
    if lover_id is not None:
        lover = next((p for p in state['players'] if p['id'] == lover_id and p['alive'] and p['active']), None)
        if lover:
            finding(state, lover['id'], 'Sevdiğin kişi öldü; sen de aşk acısıyla oyun dışı kaldın.', 'lover_death')
            kill(state, lover, cause='lover', hunter_shot=hunter_shot)
    if victim['role'] == 'hunter' and hunter_shot and victim['active']:
        if cause == 'wolves':
            wolves = [p for p in living(state) if p['role'] in ATTACK_WOLVES]
            if wolves and chance(state, f"hunter-wolf:{victim['id']}", min(100, 30 * len(wolves))):
                target = choose(state, f"hunter-wolf-target:{victim['id']}", wolves)
                event(state, f"{victim['name']} son anda saldırganlarından birini vurdu.", kind='hunter')
                kill(state, target, cause='hunter', killer=victim)
        else:
            pending = state.setdefault('pending_hunters', [])
            if victim['id'] not in pending:
                pending.append(victim['id'])


def transform_dependents(state, *, apply_bites=False):
    # Bites are applied at the following night, as in the original engine.
    if apply_bites:
        for p in living(state):
            if p.get('bitten'):
                p['bitten'] = False
                transform(state, p, 'wolf', reason='Alfa kurdun ısırığı etkisini gösterdi.')
    for _ in range(len(state['players']) + 1):
        changed = False
        for p in living(state):
            model_id = p.get('role_model')
            model = next((q for q in state['players'] if q['id'] == model_id), None)
            if not model or model['alive']:
                continue
            if p['role'] == 'wild_child':
                transform(state, p, 'wolf', reason='Rol modelin öldü.')
                changed = True
            elif p['role'] == 'doppelganger':
                replacement = model['role']
                # A chain of shifters cannot loop forever. Preserve the
                # modeled role when it has a settled playable identity.
                if replacement == 'doppelganger':
                    replacement = next((r for r in reversed(model.get('role_history', []))
                                        if r != 'doppelganger'), 'villager')
                copied_model = model.get('role_model')
                transform(state, p, replacement, reason='Rol modelinin kimliğini devraldın.')
                if replacement in {'wild_child', 'doppelganger'}:
                    p['role_model'] = copied_model
                changed = True
        if not changed:
            break
    if not any(p['role'] == 'seer' for p in living(state)):
        apprentices = [p for p in living(state) if p['role'] == 'apprentice_seer']
        if apprentices:
            transform(state, apprentices[0], 'seer', reason='Kâhinin yerini devraldın.')
    if not any(p['role'] in PACK for p in living(state)) and not any(p.get('bitten') for p in living(state)):
        traitors = [p for p in living(state) if p['role'] == 'traitor']
        if traitors:
            transform(state, traitors[0], 'wolf', reason='Son kurt öldü; hain kimliğin ortaya çıktı.')


def check_win(state):
    transform_dependents(state)
    if state.get('pending_hunters'):
        return False
    alive = living(state)
    if not alive:
        finish(state, 'draw', [])
        return True
    if len(alive) == 2 and alive[0].get('lover_id') == alive[1]['id']:
        finish(state, 'lovers', [p['id'] for p in alive])
        return True
    if len(alive) == 1:
        team = ROLES[alive[0]['role']].team
        if alive[0]['role'] in {'tanner', 'sorcerer', 'thief', 'doppelganger'}:
            finish(state, 'draw', [])
        else:
            finish(state, team, [p['id'] for p in state['players']
                                if p['active'] and ROLES[p['role']].team == team])
        return True
    if len(alive) == 2:
        roles = {p['role'] for p in alive}
        if roles <= {'sorcerer', 'tanner', 'thief', 'doppelganger'}:
            finish(state, 'draw', [])
            return True
        if roles == {'hunter', 'serial_killer'}:
            finish(state, 'hunter_killer', [p['id'] for p in alive])
            return True
        if 'hunter' in roles and any(p['role'] in PACK for p in alive):
            hunter = next(p for p in alive if p['role'] == 'hunter')
            wolf = next(p for p in alive if p['role'] in PACK)
            if chance(state, f"last-hunter:{hunter['id']}", 30):
                kill(state, wolf, cause='hunter', killer=hunter)
            else:
                kill(state, hunter, cause='wolves', killer=wolf, hunter_shot=False)
            return check_win(state)
        if 'cultist' in roles and 'cultist_hunter' in roles:
            cult = next(p for p in alive if p['role'] == 'cultist')
            hunter = next(p for p in alive if p['role'] == 'cultist_hunter')
            kill(state, cult, cause='cult_hunter', killer=hunter)
            return check_win(state)
        if 'serial_killer' in roles:
            return _team_win(state, 'serial_killer')
        if 'arsonist' in roles and not any(p['role'] == 'gunner' and p.get('bullets', 0) > 0 for p in alive):
            return _team_win(state, 'arsonist')
    killers = [p for p in alive if p['role'] in {'serial_killer', 'arsonist'}]
    wolves = [p for p in alive if p['role'] in PACK or p.get('bitten')]
    cult = [p for p in alive if p['role'] == 'cultist']
    if len(cult) == len(alive):
        return _team_win(state, 'cult')
    if wolves and not killers and len(wolves) >= len(alive) - len(wolves):
        # A loaded gunner can still break parity; do not end their game early.
        gunner = next((p for p in alive if p['role'] == 'gunner' and p.get('bullets', 0) > 0), None)
        if gunner and (len(wolves) == len(alive) - len(wolves)):
            return False
        return _team_win(state, 'wolves')
    if not wolves and not cult and not killers:
        return _team_win(state, 'village')
    return False


def _team_win(state, team):
    finish(state, team, [p['id'] for p in state['players'] if p['active'] and
                        (ROLES[p['role']].team == team or team == 'wolves' and p.get('bitten'))])
    return True
