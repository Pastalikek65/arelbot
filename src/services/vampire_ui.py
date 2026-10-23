"""Allowlisted group panel and recipient-only controls."""

from html import escape
import math
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from src.games.vampire import public_state
from src.services.vampire_service import base36


ROLES = {"vampire": "Vampir", "villager": "Köylü", "seer": "Kâhin", "healer": "Şifacı"}
ACTION_CODES = {"join": "j", "ready": "r", "start": "s", "refresh": "f",
                "leave": "l", "stop": "x", "ack": "a", "night": "n", "vote": "v",
                "repeat_protection": "p", "self_heal": "h", "reveal_on_death": "d"}
ACTION_CODES.update({'day': 'y', 'hunter': 'u', 'ignite': 'i', 'chaos': 'c',
                     'full_thief': 't', 'burning_overkill': 'o', 'rules': 'q',
                     'roles_page': 'g', 'role_info': 'k', 'disable_role': 'b'})


def callback_data(state, action, target=None):
    if action not in ACTION_CODES:
        raise ValueError("vampire_invalid_action")
    return (f"vp:{base36(abs(state['chat_id']))}:{base36(state.get('thread_id') or 0)}:"
            f"{state['round_id']}:{base36(state['phase_seq'])}:{ACTION_CODES[action]}:"
            f"{base36(target) if target is not None else '0'}")


def render_vampire(state, *, now=None):
    if state.get('ruleset') == 'greywolf':
        from src.services.werewolf_ui import render_werewolf
        return render_werewolf(state, now=now)
    view = public_state(state)
    phase = view["phase"]
    lines = ["🧛 <b>Vampir Köylü</b>", ""]
    if phase == "lobby":
        lines += [f"Oyuncular: {len(view['players'])}/7–20",
                  "Katıl, özel sohbetini hazırla, sonra kurucu maçı başlatsın.",
                  "Rolünü özelde onaylayınca ilk gece açılır.",
                  "Gece 90 sn · tartışma 90 sn · gizli oy 60 sn · rövanş 30 sn.",
                  "Vampirler çoğunlukla hedef seçer; eşitlikte kimse ölmez.",
                  "Gündüz eleme için yaşayanların çoğunluğu gerekir. Oy eşitliğinde tek rövanş yapılır.",
                  "12 gün veya 3 elemesiz gün sonunda maç ödülsüz iptal edilir."]
        settings = view["settings"]
        lines += ["Lobi ayarları (yalnız kurucu değiştirebilir):",
                  f"• Art arda aynı kişiyi koruma: {'açık' if settings['repeat_protection'] else 'kapalı'}",
                  f"• Şifacının kendini iyileştirmesi: {'açık' if settings['self_heal'] else 'kapalı'}",
                  f"• Ölümde rol açıklama: {'açık' if settings['reveal_on_death'] else 'kapalı'}"]
    else:
        lines += [f"Gün: {view['day']} · Hayatta: {sum(p['alive'] and p['active'] for p in view['players'])}"]
        if phase == "role_delivery":
            lines.append(f"Gizli rol onayı: {sum(p['role_ack'] for p in view['players'])}/{len(view['players'])}")
            lines.append("Herkes rolünü özel sohbetinde onaylamalı. Gece henüz başlamadı.")
        elif phase in {"night", "discussion", "vote", "runoff"}:
            labels = {"night": "Gece", "discussion": "Gündüz tartışması",
                      "vote": "Gizli oylama", "runoff": "Kısa rövanş oylaması"}
            lines.append(f"Aşama: <b>{labels[phase]}</b>")
            if view["phase_ready"]:
                lines.append(f"Kalan süre: {max(0, math.ceil(view['deadline'] - (time.time() if now is None else now)))} sn")
            else:
                lines.append("Grup paneli ve özel düğmeler hazırlanıyor; süre henüz başlamadı.")
            if phase in {"night", "vote", "runoff"}:
                lines.append("Hamle düğmeleri özel sohbette. Süre bitince eksik hamle pas sayılır.")
            if phase in {"vote", "runoff"}:
                lines.append(f"Kullanılan gizli oy: {view['ballots_cast']}")
        elif phase == "finished":
            lines.append("🏆 Vampirler kazandı." if view["winner"] == "vampires" else "🏆 Köylüler kazandı.")
        else:
            lines.append("Maç ödülsüz iptal edildi.")
        if view["last_deaths"]:
            names = [escape(p["name"]) for p in view["players"] if p["id"] in view["last_deaths"]]
            lines.append("Son olay: " + ", ".join(names) + " oyun dışı kaldı.")
        elif view["last_event"]:
            lines.append("Son olay: Kimse elenmedi.")
    lines.append("")
    for person in view["players"]:
        status = "hayatta" if person["alive"] and person["active"] else "oyun dışı"
        if phase == "lobby":
            status = "hazır" if person["ready"] else "özel sohbet bekleniyor"
        line = f"• {escape(str(person['name'])[:24])} · {status}"
        if "role" in person:
            line += f" · {ROLES.get(person['role'], 'Bilinmiyor')}"
        lines.append(line)
    if phase == "finished":
        lines.append("Sonuç kesinleşti. Puan kaydı yeniden denenecek." if view["score_pending"] else "Sonuç kaydedildi.")
    return "\n".join(lines)


def group_keyboard(state):
    if state.get('ruleset') == 'greywolf':
        from src.services.werewolf_ui import group_controls
        return group_controls(state)
    def button(title, action):
        return InlineKeyboardButton(title, callback_data=callback_data(state, action))
    phase = state["phase"]
    rows = []
    if phase == "lobby":
        rows += [[button("Katıl", "join"), button("Özel sohbeti hazırla", "ready")],
                 [button("Maçı başlat", "start")]]
        for option, label in (("repeat_protection", "Tekrar koruma"),
                              ("self_heal", "Kendini iyileştirme"),
                              ("reveal_on_death", "Ölümde rol")):
            desired = 2 if state["settings"][option] else 1
            status = "açık" if state["settings"][option] else "kapalı"
            rows.append([InlineKeyboardButton(f"{label}: {status}",
                callback_data=callback_data(state, option, desired))])
    if phase not in {"finished", "cancelled"}:
        rows += [[button("Yenile", "refresh"), button("Ayrıl", "leave")],
                 [button("Oyunu bitir", "stop")]]
    else:
        rows += [[InlineKeyboardButton("Yeniden oyna", callback_data="game_start_vampire"),
                  InlineKeyboardButton("Oyun salonu", callback_data="menu_games:social")]]
    return InlineKeyboardMarkup(rows)


def private_text(state, user_id):
    if state.get('ruleset') == 'greywolf':
        from src.services.werewolf_ui import private_card
        return private_card(state, user_id)
    person = next(p for p in state["players"] if p["id"] == int(user_id))
    phase = state["phase"]
    if phase == "role_delivery":
        others = [escape(p["name"]) for p in state["players"]
                  if p["role"] == "vampire" and p["id"] != int(user_id)]
        ally = "\nDiğer vampirler: " + ", ".join(others) if person["role"] == "vampire" and others else ""
        return f"🔐 Rolün: <b>{ROLES[person['role']]}</b>{ally}\nRolünü gördüysen onayla."
    if phase == "night":
        return "🌙 Herkese özel düğmeler ulaşınca süre başlar. Ardından gece hamleni seç; tek seçim veya pas mümkündür."
    return "🗳 Herkese özel düğmeler ulaşınca süre başlar. Ardından gizli oyun için bir kişiyi seç veya pas geç."


def private_keyboard(state, user_id):
    if state.get('ruleset') == 'greywolf':
        from src.services.werewolf_ui import private_controls
        return private_controls(state, user_id)
    person = next(p for p in state["players"] if p["id"] == int(user_id))
    if state["phase"] == "role_delivery":
        return InlineKeyboardMarkup([[InlineKeyboardButton("Rolümü onaylıyorum", callback_data=callback_data(state, "ack"))]])
    action = "night" if state["phase"] == "night" else "vote"
    choices = [p for p in state["players"] if p["active"] and p["alive"]]
    if action == "night":
        if person["role"] == "vampire":
            choices = [p for p in choices if p["role"] != "vampire"]
        if person["role"] == "healer":
            if not state["settings"]["self_heal"]:
                choices = [p for p in choices if p["id"] != user_id]
            if not state["settings"]["repeat_protection"]:
                choices = [p for p in choices if p["id"] != state["previous_protected"]]
    else:
        choices = [p for p in choices if p["id"] != user_id]
        if state["phase"] == "runoff":
            choices = [p for p in choices if p["id"] in state["runoff_candidates"]]
    rows = [[InlineKeyboardButton(p["name"], callback_data=callback_data(state, action, p["id"]))]
            for p in choices]
    rows.append([InlineKeyboardButton("Pas geç", callback_data=callback_data(state, action))])
    return InlineKeyboardMarkup(rows)


def error_text(code):
    if str(code).startswith('werewolf_'):
        from src.services.werewolf_ui import ERRORS
        return ERRORS.get(str(code), 'Bu Kurt Adam işlemi şu anda kullanılamıyor.')
    return {
        "vampire_player_count": "Maç için 7–20 oyuncu gerekir.",
        "vampire_not_ready": "Herkes önce özel sohbetini hazırlamalı.",
        "vampire_not_owner": "Lobi ayarlarını ve başlangıcı kurucu yapar; oyun bitirmeyi grup yöneticisi de yapabilir.",
        "vampire_not_player": "Bu oyunda etkin oyuncu değilsin.",
        "vampire_stale_round": "Bu düğme eski maça ait.",
        "vampire_stale_phase": "Bu aşama kapandı. Güncel düğmeleri kullan.",
        "vampire_invalid_invite": "Bu özel sohbet bağlantısı sana ait değil veya süresi doldu.",
        "vampire_role_not_delivered": "Rol mesajı henüz özel sohbetine ulaşmadı.",
        "vampire_already_acted": "Bu gece hamleni zaten yaptın.",
        "vampire_already_voted": "Bu aşamada oyunu zaten kullandın.",
        "vampire_invalid_target": "Bu oyuncuyu seçemezsin.",
        "vampire_rewards_pending": "Önceki maçın puan kaydı bekliyor. Biraz sonra yeniden dene.",
        "vampire_not_found": "Açık oyun yok. Grupta /vampir yaz.",
    }.get(str(code), "Bu işlem şu anda kullanılamıyor.")
