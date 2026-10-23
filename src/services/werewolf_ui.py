# SPDX-License-Identifier: GPL-3.0-only
"""Turkish GreyWolf panels. Only private views receive the full state."""
from __future__ import annotations

from html import escape
import math
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from src.games.werewolf import engine
from src.games.werewolf.choices import day_kind, night_kind, target_count, targets
from src.games.werewolf.roles import PACK, ROLES
from src.services.vampire_ui import callback_data as cb

TITLE = '🐺 <b>Kurt Adam · Vampir Köylü</b>'
PHASES = {'lobby': 'Lobi', 'role_delivery': 'Gizli rol onayı', 'night': 'Gece',
          'discussion': 'Gündüz · tartışma ve yetenekler', 'vote': 'Gizli oylama',
          'runoff': 'Eşit adaylarla rövanş', 'hunter': 'Avcının son atışı',
          'finished': 'Maç tamamlandı', 'cancelled': 'Maç iptal edildi'}
WINNERS = {'wolves': 'Kurt sürüsü', 'village': 'Köylüler', 'cult': 'Tarikat',
           'serial_killer': 'Seri Katil', 'arsonist': 'Kundakçı', 'tanner': 'Darağacı Yolcusu',
           'lovers': 'Âşıklar', 'hunter_killer': 'Avcı ve Seri Katil', 'draw': 'Kimse'}
KINDS = {'hunt': 'Sürünün avı için hedef seç', 'freeze': 'Bu gece donduracağın kişiyi seç',
         'inspect': 'Kimliğini göreceğin kişiyi seç', 'false_vision': 'Kimliğini göreceğin kişiyi seç',
         'protect': 'Bu gece koruyacağın kişiyi seç', 'visit': 'Bu gece ziyaret edeceğin kişiyi seç',
         'convert': 'Tarikata çağıracağın kişiyi seç', 'hunt_cult': 'Tarikatçı olduğundan kuşkulandığın kişiyi seç',
         'kill': 'Bu geceki hedefini seç', 'scry': 'Kurt veya Kâhin aramak için bir kişi seç',
         'exclude': 'Olmadığı rolü öğreneceğin kişiyi seç', 'potion': 'İksir içeceğin kişiyi seç',
         'douse': 'Yakıtla işaretleyeceğin kişiyi seç ya da önceden işaretlediklerini yak',
         'model': 'Rol modelini seç', 'lovers': 'Birbirine bağlayacağın iki farklı kişiyi seç',
         'steal': 'Rolünü çalacağın kişiyi seç', 'investigate': 'Gizlice araştıracağın kişiyi seç',
         'shoot': 'Vuracağın kişiyi seç', 'detonate': 'Patlama girişiminin hedefini seç',
         'rice': 'Kapısına pirinç serpeceğin kişiyi seç', 'reveal': 'Kimliğini açıkla; verdiğin oy iki sayılmaya başlasın',
         'silver': 'Gelecek gece sürü saldırısını gümüşle engelle', 'peace': 'Bugünkü eleme oylamasını durdur',
         'sleep': 'Gelecek gece tüm yetenekleri uyut', 'double_lynch': 'Bugün iki eleme oylaması yapılmasını sağla'}
ERRORS = {'werewolf_player_count': 'Maç için 5–35 oyuncu gerekir.',
          'werewolf_not_owner': 'Lobi ayarlarını ve maçı yalnız kurucu başlatabilir.',
          'werewolf_invalid_role': 'Bu rol tanımlı değil.',
          'werewolf_invalid_role_config': 'Bu rol seçimiyle oynanabilir bir maç kurulamıyor. Kurt ve Köylü açık kalmalı.',
          'werewolf_cannot_balance': 'Bu rollerle dengeli dağıtım bulunamadı. Birkaç rolü yeniden aç veya Kaos modunu seç.',
          'werewolf_invalid_ruleset': 'Mod için normal, kaos veya klasik seç.'}


def render_werewolf(state, *, now=None):
    view = engine.public_state(state)
    mode = 'Kaos' if view['settings']['chaos'] else 'Normal'
    stage = view['phase']
    alive = sum(p['alive'] and p['active'] for p in view['players'])
    lines = [TITLE, f"{mode} mod · <b>{PHASES.get(stage, 'Oyun')}</b>", '']
    if stage == 'lobby':
        ready = sum(p['ready'] for p in view['players'])
        lines += [f"👥 {len(view['players'])}/5–35 oyuncu · 🔐 {ready} özel sohbet hazır",
                  '1. Katıl → 2. Özel sohbetini hazırla → 3. Kurucu başlatsın.',
                  '45 rollü katalogdan oyuncu sayısına uygun dağıtım yapılır.',
                  'Normal dengeli; Kaos daha öngörülemez bir rol karışımıdır.',
                  'Gece 90 sn · ilk seçimler 120 sn · tartışma 90 sn · oy 60 sn.', '']
        disabled = len(view['settings'].get('disabled_roles') or [])
        lines += [f"🎭 45 rol katalogda · {45-disabled} rol grup ayarında açık; Balkabağı mevsimsel.",
                  'Ayarları yalnız kurucu; maç bitirmeyi kurucu veya grup yöneticisi yapabilir.']
    else:
        lines.append(f"📅 Gün {view['day']} · 👥 {alive}/{len(view['players'])} hayatta")
        if stage == 'role_delivery':
            lines.append(f"🔐 Rol onayı: {sum(p['role_ack'] for p in view['players'])}/{len(view['players'])}")
            lines.append('Rolünü özelde onayla. Herkes hazır olmadan gece başlamaz.')
        elif stage in {'night', 'discussion', 'vote', 'runoff', 'hunter'}:
            if view['phase_ready']:
                left = max(0, math.ceil(view['deadline'] - (time.time() if now is None else now)))
                lines.append(f"⏳ {left} sn kaldı")
            else:
                lines.append('Özel kontroller ve bilgiler teslim ediliyor; süre henüz başlamadı.')
            if stage == 'discussion':
                lines.append('Grupta tartışın. Gündüz yeteneğin varsa özel sohbetteki düğmeleri kullan.')
            elif stage == 'hunter':
                lines.append('Avcı son atışını özelden seçecek. Diğer eylemler kısa süreli beklemede.')
            else:
                lines.append('Seçimlerin özel sohbette. Süre dolunca eksik seçim pas sayılır.')
            if stage in {'vote', 'runoff'}:
                lines.append(f"🗳 {view['ballots_cast']}/{alive} oy kullanıldı")
        elif stage == 'finished':
            lines.append(f"🏆 Kazanan: <b>{WINNERS.get(view['winner'], 'Sonuç')}</b>")
            lines.append('Puan kaydı tamamlanıyor.' if view['score_pending'] else 'Sonuç kesinleşti.')
        else:
            lines.append('Maç ödülsüz kapandı. Yeni bir lobi kurabilirsin.')
        events = view.get('events') or []
        if events:
            lines += ['', '📜 <b>Son olaylar</b>']
            lines += ['• ' + escape(str(item['text'])[:130]) for item in events[-3:]]
    lines += ['', '👥 <b>Oyuncular</b>']
    for person in view['players']:
        status = ('✅' if person['ready'] else '🔐') if stage == 'lobby' else ('🟢' if person['alive'] and person['active'] else '⚫')
        label = f"{status} {escape(person['name'][:24])}"
        if 'role' in person:
            role = ROLES[person['role']]
            label += f" · {role.emoji} {escape(role.name)}"
        lines.append(label)
    return '\n'.join(lines)


def group_controls(state):
    stage = state['phase']
    if stage == 'lobby':
        rows = [[_button(state, '👋 Katıl', 'join'), _button(state, '🔐 Özel sohbeti hazırla', 'ready')],
                [_button(state, '▶️ Maçı başlat', 'start')],
                [_button(state, '📖 Kurallar', 'rules'), _button(state, '🎭 Roller ve seçimler', 'roles_page')]]
        for key, label in [('chaos', 'Kaos modu'), ('full_thief', 'Hırsız her gece deneyebilir'),
                           ('reveal_on_death', 'Ölümde rolü göster'), ('self_heal', 'Melek kendini koruyabilir'),
                           ('repeat_protection', 'Aynı kişiyi art arda koruma'),
                           ('burning_overkill', 'Kundakçı ve Seri Katil aynı maçta')]:
            enabled = bool(state['settings'][key])
            rows.append([_button(state, f"{'✅' if enabled else '▫️'} {label}", key, 2 if enabled else 1)])
        rows.append([_button(state, '↩️ Ayrıl', 'leave'), _button(state, '⏹ Lobiyi kapat', 'stop')])
    elif stage not in engine.TERMINAL:
        rows = [[_button(state, '🔄 Güncelle', 'refresh'), _button(state, '📖 Kurallar', 'rules')],
                [_button(state, '↩️ Ayrıl', 'leave'), _button(state, '⏹ Maçı bitir', 'stop')]]
    else:
        rows = [[InlineKeyboardButton('🔁 Yeni maç', callback_data='game_start_vampire'),
                 InlineKeyboardButton('🎮 Oyun salonu', callback_data='menu_games:social')]]
    return InlineKeyboardMarkup(rows)


def _button(state, label, action, target=None):
    return InlineKeyboardButton(label, callback_data=cb(state, action, target))


def private_card(state, user_id):
    person = next(p for p in state['players'] if p['id'] == int(user_id))
    role = ROLES['seer' if person['role'] == 'fool' else person['role']]
    if state['phase'] == 'role_delivery':
        description = (role.description_full if role.key == 'thief' and
                       state['settings'].get('full_thief') else role.description)
        lines = [f"{role.emoji} <b>{escape(role.name)}</b>", '', escape(description), '']
        if person['role'] in PACK:
            allies = [p['name'] for p in state['players'] if p['role'] in PACK and p['id'] != user_id]
            if allies:
                lines.append('🐺 Sürü arkadaşların: ' + escape(', '.join(allies)))
        elif person['role'] == 'mason':
            allies = [p['name'] for p in state['players'] if p['role'] == 'mason' and p['id'] != user_id]
            lines.append('👷 Diğer Masonlar: ' + escape(', '.join(allies) or 'yok'))
        elif person['role'] == 'beholder':
            seers = [p['name'] for p in state['players'] if p['role'] == 'seer']
            lines.append('👁 Gerçek Kâhin: ' + escape(', '.join(seers) or 'şu an yok'))
        lines += ['', 'Bu kart sana özel. Gördüysen rolünü onayla; ilk gece herkes onaylayınca açılır.']
        return '\n'.join(lines)
    stage = state['phase']
    if (stage == 'night' and state.get('actions', {}).get(str(user_id), {}).get('done') or
            stage == 'discussion' and person.get('acted_day') == state['day']):
        return f"{role.emoji} <b>{escape(role.name)}</b>\n\n✅ Bu aşamadaki seçimin tamamlandı. Sonuç aşama kapanınca işlenecek."
    if stage == 'hunter':
        return '🎯 <b>Avcının son atışı</b>\nOyun dışı kaldın; son hedefini seç veya pas geç. Sonuç kesinleşmeden önce atışın çözülecek.'
    if stage in {'vote', 'runoff'}:
        if str(user_id) in state.get('votes', {}):
            return '🗳 <b>Oy verildi</b>\nSonuç aşama kapanınca açıklanacak. Verdiğin oy gruba gösterilmez.'
        return ('🗳 <b>Gizli oylama</b>\n' + ('Yalnız eşit adaylardan birini seç.' if stage == 'runoff' else 'Eleme için bir kişiyi seç veya pas geç.') +
                '\nVerdiğin oy değiştirilemez. Seçtiğin kişi grup panelinde açıklanmaz.')
    kind = day_kind(state, person) if stage == 'discussion' else night_kind(state, person)
    lines = [f"{role.emoji} <b>{escape(role.name)}</b> · Gün {state['day']}", '']
    if kind is None:
        lines.append('Bu aşamada kullanabileceğin bir yetenek kalmadı.')
    else:
        lines.append(escape(KINDS[kind]) + '.')
        if stage == 'night':
            chosen = state.get('actions', {}).get(str(user_id), {}).get('targets', [])
            maximum = target_count(state, person)
            if maximum > 1:
                lines.append(f"Seçim: {len(chosen)}/{maximum}")
                names = [p['name'] for p in state['players'] if p['id'] in chosen]
                if names:
                    lines.append('Seçtiklerin: ' + escape(', '.join(names)))
        if kind == 'shoot':
            lines.append(f"Kalan mermi: {person.get('bullets', 0)}")
        if kind == 'douse':
            marked = [p['name'] for p in state['players'] if p['active'] and p['alive'] and p.get('doused')]
            lines.append('İşaretliler: ' + escape(', '.join(marked) or 'yok'))
    lines += ['', 'Süre, bu aşamanın özel kontrolleri tamamlanınca başlar. Eksik seçim pas sayılır.']
    return '\n'.join(lines)


def private_controls(state, user_id):
    if state['phase'] == 'role_delivery':
        return InlineKeyboardMarkup([[_button(state, '✅ Rolümü gördüm', 'ack')],
            [InlineKeyboardButton('📚 Kaynak kodu', url='https://github.com/Pastalikek65/arelbot')]])
    person = next(p for p in state['players'] if p['id'] == int(user_id))
    if state['phase'] == 'night' and state.get('actions', {}).get(str(user_id), {}).get('done'):
        return None
    if state['phase'] in {'vote', 'runoff'} and str(user_id) in state.get('votes', {}):
        return None
    action = {'night': 'night', 'discussion': 'day', 'vote': 'vote', 'runoff': 'vote', 'hunter': 'hunter'}.get(state['phase'])
    if action is None:
        return None
    kind = day_kind(state, person) if action == 'day' else night_kind(state, person) if action == 'night' else action
    if kind is None:
        return None
    selected = targets(state, user_id)
    if action == 'day' and kind in {'reveal', 'silver', 'peace', 'sleep', 'double_lynch'}:
        labels = {'reveal': '🎖 Kimliğimi açıkla', 'silver': '⚒ Gümüşü kullan', 'peace': '☮️ Bugün eleme olmasın',
                  'sleep': '💤 Gelecek geceyi uyut', 'double_lynch': '🤯 İki oylama yapılsın'}
        rows = [[_button(state, labels[kind], action, int(user_id))]]
    else:
        buttons = [_button(state, p['name'][:24], action, p['id']) for p in selected]
        rows = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
    if kind == 'douse' and any(p.get('doused') and p['alive'] and p['active'] for p in state['players']):
        rows.append([_button(state, '🔥 İşaretlileri yak', 'ignite')])
    rows.append([_button(state, '⏭ Pas geç', action)])
    return InlineKeyboardMarkup(rows)


def guide(state, *, page=0, role_key=None, rules=False):
    if rules:
        text = (TITLE + '\n\n<b>Nasıl oynanır?</b>\n'
                '• 5–35 kişi katılır; özel sohbet ve rol onayı gerekir.\n'
                '• Gece yetenekler özelden seçilir. Gündüz grupta tartışılır; gündüz yetenekleri yine özelde kullanılır.\n'
                '• Gizli oylamada en çok oy alan elenir. Eşitlikte tek rövanş, yine eşitse eleme yoktur.\n'
                '• Sürü, köylüler, tarikat ve bağımsız rollerin farklı kazanma yolları var. Rol kartını oku.\n'
                '• Normal güç dengesi gözetir; Kaos güç farkını serbest bırakır. Roller ve mod yalnız lobide değişir.\n'
                '• Tek kullanımlık güç, mermi, aşk, rol modeli ve dönüşümler maç boyunca korunur.\n'
                '• Eski düğme başka aşamada işlemez; oylar ve gece hedefleri gruba açılmaz.\n'
                '• Maçı terk etmek sonuç ödüllerini kapatır. En fazla 24 gün oynanır.\n'
                '• Roller GreyWolfDev/Werewolf GPL-3.0 kaynağından uyarlanmıştır.')
        rows = [[_button(state, '🎭 Rolleri incele', 'roles_page')]]
    else:
        keys = list(ROLES)
        page = max(0, min(int(page or 0), (len(keys)-1)//6))
        if role_key is not None:
            role = ROLES[role_key]
            page = keys.index(role_key)//6
            description = (role.description_full if role.key == 'thief' and
                           state['settings'].get('full_thief') else role.description)
            text = f"{role.emoji} <b>{escape(role.name)}</b>\n\n{escape(description)}"
            rows = []
            if state['phase'] == 'lobby' and role_key not in {'villager', 'wolf', 'spumpkin'}:
                enabled = role_key not in state['settings']['disabled_roles']
                rows.append([_button(state, '▫️ Rolü kapat' if enabled else '✅ Rolü aç', 'disable_role', role.source_id)])
            rows.append([_button(state, '◀️ Rol listesi', 'roles_page', page)])
        else:
            text = TITLE + f"\n\n🎭 <b>45 rol · {page+1}/8</b>\nBir kartı açıp yeteneğini ve kazanma yolunu oku. Lobi kurucusu karttan rolü açıp kapatabilir."
            rows = [[_button(state, f"{'▫️' if key in state['settings']['disabled_roles'] else '✅'} {ROLES[key].emoji} {ROLES[key].name}", 'role_info', ROLES[key].source_id)]
                    for key in keys[page*6:page*6+6]]
            navigation = []
            if page:
                navigation.append(_button(state, '◀️ Önceki', 'roles_page', page-1))
            if page < 7:
                navigation.append(_button(state, 'Sonraki ▶️', 'roles_page', page+1))
            if navigation:
                rows.append(navigation)
    rows.append([_button(state, '🎮 Oyuna dön', 'refresh')])
    return text, InlineKeyboardMarkup(rows)
