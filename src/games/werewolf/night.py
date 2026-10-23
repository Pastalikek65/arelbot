# SPDX-License-Identifier: GPL-3.0-only
"""Ordered GreyWolf night resolution; private results never enter public events."""
from __future__ import annotations

from .choices import night_kind
from .effects import kill, transform_dependents
from .roles import ATTACK_WOLVES, PACK, ROLES
from .state import chance, choose, event, finding, living, player, transform

CONVERSION = {
    'seer': 40, 'guardian_angel': 60, 'detective': 70, 'cursed': 60, 'harlot': 70,
    'hunter': 50, 'serial_killer': 20, 'sorcerer': 40, 'blacksmith': 75,
    'oracle': 50, 'sandman': 60, 'wise_elder': 30, 'pacifist': 80,
    'grave_digger': 30, 'augur': 40,
}


def _action(state, person):
    return state.get('actions', {}).get(str(person['id']), {})


def _target(state, person, index=0):
    selected = _action(state, person).get('targets', [])
    return next((p for p in state['players'] if index < len(selected) and p['id'] == selected[index]), None)


def _available(person):
    return bool(person and person['active'] and person['alive'])


def _vote_target(state, actors, index, purpose):
    votes = [_target(state, p, index) for p in actors]
    ids = [p['id'] for p in votes if _available(p)]
    if not ids:
        return None
    highest = max(ids.count(uid) for uid in set(ids))
    tied = sorted(uid for uid in set(ids) if ids.count(uid) == highest)
    return player(state, choose(state, purpose, tied), alive=True)


def resolve(state):
    """Mutate a detached state copy, in the original engine's night order."""
    state['last_deaths'] = []
    state['visits'] = []
    state['saved_tonight'] = []
    state['burning_tonight'] = []
    start = list(living(state))
    frozen = set()

    def visit(actor, target, *, guardian=False, permit_dead=False):
        if target is None or not target['active']:
            return False
        state['visits'].append([actor['id'], target['id']])
        if not target['alive'] and not permit_dead and target['id'] not in state['burning_tonight']:
            return False
        if actor['role'] == 'serial_killer' and target['role'] != 'grave_digger':
            return target['alive']
        if target['id'] in state['burning_tonight']:
            kill(state, actor, cause='fire_visit')
            return False
        if target['role'] == 'serial_killer':
            wolf_luck = actor['role'] in PACK and chance(state, f"sk-escape:{actor['id']}", 20)
            if not wolf_luck:
                kill(state, actor, cause='killer_visit', killer=target)
                return False
        if actor['role'] == 'thief' and not state['settings'].get('full_thief'):
            return True
        if actor['role'] == 'snow_wolf':
            return True
        if target['role'] in PACK:
            if actor['role'] == 'harlot':
                kill(state, actor, cause='wolf_visit', killer=target)
                return False
            if guardian and target['id'] not in state['saved_tonight'] and chance(state, f"guard-wolf:{actor['id']}", 50):
                kill(state, actor, cause='wolf_visit', killer=target)
                return False
            if actor['role'] == 'thief' and state['settings'].get('full_thief'):
                return False
        if target['role'] == 'grave_digger' and state.get('graves', 0):
            graves = state['graves']
            risk = 20 + 30 - 30 * (0.5 ** (graves - 1))
            if actor['role'] == 'serial_killer':
                actor['stumbled_day'] = state['day']
                return True
            if guardian and target['id'] in state['saved_tonight']:
                return True
            if ROLES[actor['role']].team == 'village':
                risk /= 2
            if chance(state, f"grave:{actor['id']}:{target['id']}", risk):
                kill(state, actor, cause='grave', killer=target, hunter_shot=False)
            return False
        if target['role'] == 'harlot' and target['id'] not in frozen and _target(state, target):
            return False
        return target['alive'] or permit_dead

    # First-night defaults must not leave Cupid/model roles unplayable when a
    # player uses pass or times out. Choices are drawn from a private seed.
    if state['day'] == 1:
        for actor in start:
            if actor['role'] in {'wild_child', 'doppelganger'}:
                target = _target(state, actor) or choose(state, f"model:{actor['id']}", [p for p in start if p['id'] != actor['id']])
                actor['role_model'] = target['id']
                finding(state, actor['id'], f"Rol modelin: {target['name']}.", 'model', target_id=target['id'])
            elif actor['role'] == 'cupid':
                selected = list(_action(state, actor).get('targets', []))
                while len(selected) < 2:
                    target = choose(state, f"lovers:{actor['id']}:{len(selected)}", [p for p in start if p['id'] not in selected])
                    selected.append(target['id'])
                first, second = (player(state, uid) for uid in selected[:2])
                first['lover_id'], second['lover_id'] = second['id'], first['id']
                for person, other in ((first, second), (second, first)):
                    finding(state, person['id'], f"Sevdiğin kişi: {other['name']}. Biriniz ölürse diğeri de ölür; son iki kişi kalırsanız birlikte kazanırsınız.", 'lover', target_id=other['id'])

    if state.get('sleep_night') == state['day']:
        event(state, 'Uyku Perisi herkesi uyuttu; bu gece hiçbir yetenek kullanılmadı.', kind='sleep')
        state['silver_night'] = None
        state['cub_rage_day'] = None
        return

    # Snow wolf acts before every other active night role.
    for actor in start:
        if actor['role'] != 'snow_wolf' or not night_kind(state, actor):
            continue
        target = _target(state, actor)
        if _available(target) and target.get('frozen_day') != state['day'] - 1 and visit(actor, target):
            frozen.add(target['id'])
            target['frozen_day'] = state['day']
            finding(state, target['id'], 'Bu gece donduruldun; gece yeteneğin çalışmayacak.', 'frozen')

    actors = [p for p in start if p['id'] not in frozen and night_kind(state, p)]
    guards = {p['id']: _target(state, p) for p in actors if p['role'] == 'guardian_angel'}
    protected = {p['id'] for p in guards.values() if _available(p)}
    for uid, target in guards.items():
        player(state, uid)['previous_protected'] = target['id'] if target else None

    def attack(actor, target, cause):
        if not _available(target):
            return
        if target['id'] in protected:
            if target['id'] not in state['saved_tonight']:
                state['saved_tonight'].append(target['id'])
            finding(state, target['id'], 'Koruyucu Melek bu gece seni bir saldırıdan kurtardı.', 'saved')
            return
        if cause == 'wolves' and target['role'] == 'wise_elder' and not target.get('elder_shield_used'):
            target['elder_shield_used'] = True
            finding(state, target['id'], 'Bilgeliğin sayesinde ilk kurt saldırısından sağ çıktın.', 'elder_shield')
            return
        if cause == 'wolves' and target['role'] == 'cursed':
            transform(state, target, 'wolf', reason='Lanetin kurt saldırısıyla uyandı.')
            return
        if cause == 'wolves' and any(p['role'] == 'alpha_wolf' and p['alive'] for p in actors):
            if chance(state, f"alpha:{target['id']}", 20):
                target['bitten'] = True
                finding(state, target['id'], 'Alfa kurt seni ısırdı; gelecek gece kurt adama dönüşeceksin.', 'bitten')
                return
        kill(state, target, cause=cause, killer=actor)

    # Arsonist burns before pack attacks. Visiting a burning home is lethal.
    for actor in actors:
        if actor['role'] != 'arsonist' or not actor['alive']:
            continue
        if _action(state, actor).get('kind') == 'ignite':
            victims = [p for p in start if p.get('doused') and p['id'] != actor['id']]
            for target in victims:
                if target['id'] not in protected:
                    state['burning_tonight'].append(target['id'])
                attack(actor, target, 'fire')
                target['doused'] = False
        else:
            target = _target(state, actor)
            if _available(target):
                target['doused'] = True
                finding(state, actor['id'], f"{target['name']} işaretlendi. Sonraki gece yakabilir veya başka birini işaretleyebilirsin.", 'douse', target_id=target['id'])

    pack = [p for p in actors if p['role'] in ATTACK_WOLVES and p['alive']]
    if state.get('silver_night') != state['day'] and state.get('pack_drunk_day') != state['day']:
        for index in range(2 if state.get('cub_rage_day') == state['day'] else 1):
            target = _vote_target(state, pack, index, f"wolf-vote:{index}")
            available_pack = [p for p in pack if p['alive']]
            attacker = choose(state, f"wolf-visitor:{index}", available_pack)
            if attacker and target and target['role'] not in PACK and visit(attacker, target):
                attack(attacker, target, 'wolves')
    elif pack:
        for actor in pack:
            finding(state, actor['id'], 'Bu gece sürü saldırısı yapılamadı: gümüş veya sarhoşluk etkisi var.', 'pack_blocked')

    for actor in actors:
        if actor['role'] != 'serial_killer' or not (actor['alive'] or actor.get('death_cause') == 'fire'):
            continue
        target = _target(state, actor)
        if visit(actor, target):
            if actor.get('stumbled_day') == state['day'] - 1 and chance(state, f"sk-stumble:{actor['id']}", 50):
                original = target
                target = choose(state, f"sk-stumble-target:{actor['id']}",
                                [p for p in living(state) if p['id'] != actor['id']])
                if target:
                    visit(actor, target)
                    finding(state, actor['id'],
                            f"Mezarlık sende iz bıraktı: {original['name']} yerine {target['name']} hedef oldu.",
                            'stumbled_grave', target_id=target['id'])
            attack(actor, target, 'serial_killer')
        diggers = [p for p in living(state) if p['role'] == 'grave_digger' and state.get('graves', 0)]
        for digger in diggers:
            risk = (20 + 30 - 30 * (0.5 ** (state['graves'] - 1))) / 2
            if chance(state, f"spot-digger:{actor['id']}:{digger['id']}", risk):
                attack(actor, digger, 'serial_killer')

    for actor in actors:
        if actor['role'] != 'cultist_hunter' or not actor['alive']:
            continue
        target = _target(state, actor)
        if visit(actor, target):
            if target['role'] == 'cultist':
                kill(state, target, cause='cult_hunter', killer=actor)
                finding(state, actor['id'], f"{target['name']} tarikatçıydı; avın başarılı.", 'cult_hunt')
            else:
                finding(state, actor['id'], f"{target['name']} tarikatçı değil.", 'cult_hunt')

    cult = [p for p in actors if p['role'] == 'cultist' and p['alive']]
    target = _vote_target(state, cult, 0, 'cult-vote')
    if cult and target:
        visitor = max(cult, key=lambda p: (p.get('cult_joined', 0), p['id']))
        if target['role'] in PACK or target['role'] == 'cultist_hunter':
            kill(state, visitor, cause='cult_visit', killer=target)
        elif target['role'] == 'hunter' and chance(state, f"hunter-cult:{target['id']}", 50):
            kill(state, visitor, cause='hunter', killer=target)
        elif target['id'] in protected:
            finding(state, visitor['id'], 'Hedef korunduğu için tarikata katılamadı.', 'convert_blocked')
        elif target['role'] in {'thief', 'doppelganger'}:
            finding(state, visitor['id'], 'Bu kimlik tarikata dönüştürülemedi.', 'convert_blocked')
        elif (target['role'] != 'grave_digger' or not state.get('graves', 0)) and not (
                target['role'] == 'harlot' and _target(state, target) and target['id'] not in frozen):
            state['visits'].append([visitor['id'], target['id']])
            if chance(state, f"convert:{target['id']}", CONVERSION.get(target['role'], 100)):
                transform(state, target, 'cultist', reason='Tarikata katıldın.')
                target['cult_joined'] = state['day']
            elif target['role'] == 'serial_killer':
                kill(state, visitor, cause='killer_visit', killer=target)

    for actor in actors:
        if actor['role'] == 'chemist' and actor['alive']:
            target = _target(state, actor)
            if visit(actor, target):
                actor['last_potion_day'] = state['day']
                victim = target if chance(state, f"potion:{actor['id']}", 50) else actor
                kill(state, victim, cause='potion', killer=actor)
                finding(state, actor['id'], 'İksir buluşması sona erdi; zehirli bardağı içen kişi oyun dışı kaldı.', 'potion')

    for barkeep in [p for p in start if p['role'] == 'barkeep' and p['id'] not in frozen and
                    (p['alive'] or p['id'] in state['burning_tonight'])]:
        guests = [p for p in living(state) if p['role'] == 'villager' and
                  chance(state, f"bar:{p['id']}", 1000 / (len(state['players']) + 5))]
        newcomers = []
        for guest in guests:
            if visit(guest, barkeep):
                guest['bar_visits'] = guest.get('bar_visits', 0) + 1
                finding(state, guest['id'], f"Barı ziyaret ettin ({guest['bar_visits']}/3).", 'bar')
                if guest['bar_visits'] >= 3:
                    transform(state, guest, 'drunk', reason='Barın müdavimi oldun.')
                    newcomers.append(guest['name'])
        finding(state, barkeep['id'], f"Bu gece {len(guests)} ziyaretçi geldi." +
                (" Yeni dostların: " + ', '.join(newcomers) if newcomers else ''), 'bar_guests')

    for actor in actors:
        if actor['role'] != 'harlot' or not actor['alive']:
            continue
        target = _target(state, actor)
        if target and not target['alive'] and target.get('death_day') == state['day'] and target.get('death_cause') == 'wolves':
            state['visits'].append([actor['id'], target['id']])
            kill(state, actor, cause='visited_victim')
        elif visit(actor, target):
            found = 'tarikatçı' if target['role'] == 'cultist' and chance(state, f"visit-cult:{actor['id']}", 50) else 'kurt saldırısına uğramamış'
            finding(state, actor['id'], f"{target['name']} ziyaretinde gözlemin: {found}.", 'visit', target_id=target['id'])

    # Information is resolved after combat, without publishing target choices.
    for actor in actors:
        if not actor['alive'] or actor['role'] not in {'seer', 'fool', 'oracle', 'sorcerer'}:
            continue
        target = _target(state, actor)
        if not target:
            continue
        role = target['role']
        if actor['role'] == 'seer':
            seen = 'villager' if role == 'lycan' else 'wolf' if role == 'wolf_man' else role
            if role == 'traitor' and chance(state, f"see-traitor:{actor['id']}:{target['id']}", 50):
                seen = 'wolf'
            if seen == 'fool':
                seen = 'seer'
            text = f"Kâhin sonucu: {target['name']} — {ROLES[seen].name}."
        elif actor['role'] == 'fool':
            fake = actor.setdefault('fake_visions', {})
            key = str(target['id'])
            if key not in fake:
                fake[key] = choose(state, f"fool:{actor['id']}:{target['id']}", sorted(ROLES))
            text = f"Kâhin sonucu: {target['name']} — {ROLES[fake[key]].name}."
        elif actor['role'] == 'oracle':
            exclusions = [p['role'] for p in living(state) if p['role'] != role]
            excluded = choose(state, f"oracle:{actor['id']}:{target['id']}", exclusions)
            text = f"Orakl sonucu: {target['name']} kesinlikle {ROLES[excluded].name} değil." if excluded else 'Bu gece ayırt edici bir işaret göremedin.'
        else:
            text = f"Büyücü sonucu: {target['name']} — " + ('sürüden biri.' if role in PACK else 'Kâhin.' if role == 'seer' else 'aradığın kişi değil.')
        finding(state, actor['id'], text, 'vision', target_id=target['id'])
    for augur in [p for p in living(state) if p['role'] == 'augur' and p['id'] not in frozen]:
        seen = augur.setdefault('absent_roles_seen', [])
        present = {p['role'] for p in state['players'] if p['alive'] or p.get('death_day') == state['day']}
        absent = sorted(set(state['role_pool']) - present - set(seen))
        role = choose(state, f"augur:{augur['id']}", absent)
        if role:
            seen.append(role)
        finding(state, augur['id'], f"Bu köyde şu rol bulunmuyor: {ROLES[role].name}." if role else 'Yeni bir yokluk işareti kalmadı.', 'augur')
    for uid, target in guards.items():
        guardian = player(state, uid)
        if target:
            visit(guardian, target, guardian=True)
            if target['id'] in state['saved_tonight']:
                target['doused'] = False
            finding(state, uid, f"Koruma sonucu: {target['name']} " +
                    ('bir saldırıdan kurtuldu.' if target['id'] in state['saved_tonight'] else 'bu gece korunma gerektiren bir saldırı almadı.'), 'protection')

    # Thief comes last in the upstream ordering, after the stolen role acted.
    for thief in [p for p in living(state) if p['role'] == 'thief' and (p['id'] not in frozen or not state['settings'].get('full_thief'))]:
        if state['day'] != 1 and not state['settings'].get('full_thief'):
            continue
        target = _target(state, thief)
        if target is None and state['day'] == 1:
            target = choose(state, f"thief-default:{thief['id']}", [p for p in start if p['id'] != thief['id']])
        if target and visit(thief, target, permit_dead=not state['settings'].get('full_thief')):
            full = state['settings'].get('full_thief')
            if not full or target['role'] not in PACK | {'cultist'} and chance(state, f"steal:{thief['id']}", 50):
                stolen, bullets, model = target['role'], target.get('bullets', 0), target.get('role_model')
                transform(state, thief, stolen, reason=f"{target['name']} kimliğini çaldın.")
                thief['bullets'], thief['role_model'] = bullets, model
                transform(state, target, 'thief' if full else 'villager', reason='Rolün çalındı.')
            else:
                finding(state, thief['id'], 'Bu gece rol çalma girişimin başarısız oldu.', 'steal_failed')

    for chef in [p for p in state['players'] if p.get('rice_target')]:
        target = next((p for p in state['players'] if p['id'] == chef['rice_target']), None)
        if target and chef['active'] and chef['id'] not in frozen:
            incoming = sum(pair[1] == target['id'] for pair in state['visits'])
            outgoing = any(pair[0] == target['id'] for pair in state['visits'])
            finding(state, chef['id'], f"Pirinç izi: {target['name']} — " +
                    ('evinden çıktı' if outgoing else 'evinden çıkmadı') + f", evine {incoming} ziyaret yapıldı.", 'rice', target_id=target['id'])
        chef['rice_target'] = None
    transform_dependents(state)
    if not state['last_deaths']:
        event(state, 'Gece sona erdi; kimse elenmedi.', kind='quiet_night')
