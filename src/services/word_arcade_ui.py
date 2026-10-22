"""Group-safe and actor-bound private presentation for Word Arcade."""
from __future__ import annotations

import math
import time
from html import escape

from src.games import word_arcade as rules
from src.services.word_arcade_service import _CODES, _KEYS, _b36
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from src.telegram.game_controls import with_game_help

TITLES = {"harf_maratonu": "Harf Maratonu", "sozcuk_avi": "Sözcük Avı",
          "yasak_harf": "Yasak Harf", "ortak_hikaye": "Ortak Hikâye", "bomba": "Bomba"}


def callback_data(state, action):
    if action not in {"join", "start", "leave", "stop", "refresh", "dm", "ack"}:
        raise ValueError("wa_invalid_action")
    data = (f"wa:{_CODES[state['game_key']]}:{_b36(abs(state['chat_id']))}:"
            f"{_b36(state.get('thread_id') or 0)}:{state['round_id']}:"
            f"{_b36(state['phase_seq'])}:{action}")
    if len(data.encode("utf-8")) > 64:
        raise ValueError("wa_callback_too_long")
    return data


def parse_callback(data):
    parts = str(data).split(":")
    if len(parts) != 7 or parts[0] != "wa" or parts[1] not in _KEYS:
        raise ValueError("wa_invalid_action")
    _, code, chat, topic, round_id, seq, action = parts
    return _KEYS[code], -int(chat, 36), int(topic, 36) or None, round_id, int(seq, 36), action


def _name(value):
    return escape(str(value)[:20], quote=False)


def _player(view, user_id):
    return _name(next((p["name"] for p in view["players"] if p["id"] == user_id), "Oyuncu"))


def keyboard(state):
    phase, game = state["phase"], state["game_key"]

    def button(label, action):
        return InlineKeyboardButton(label, callback_data=callback_data(state, action))

    rows = []
    if phase == "lobby":
        rows.append([button("Katıl", "join"), button("Başlat", "start")])
        rows.append([button("Ayrıl", "leave"), button("İptal", "stop")])
    elif phase not in rules.TERMINAL:
        if (game == "harf_maratonu" and phase == "collect") or (
                game == "yasak_harf" and phase == "secret_delivery"):
            rows.append([button("Özel sohbeti aç", "dm")])
        rows.append([button("Ayrıl", "leave"), button("İptal", "stop")])
    if phase in rules.TERMINAL:
        rows.append([InlineKeyboardButton("🔄 Yeni maç", callback_data=f"game_start_{game}"),
                     InlineKeyboardButton("🎮 Oyun salonu", callback_data="menu_games")])
    else:
        rows.append([button("Yenile", "refresh")])
    return with_game_help(InlineKeyboardMarkup(rows), state['game_key'])


def private_keyboard(state):
    return InlineKeyboardMarkup([[InlineKeyboardButton(
        "Gizli kartı aldım", callback_data=callback_data(state, "ack"))]])


def private_role(state):
    if state["game_key"] != "yasak_harf" or state["phase"] != "secret_delivery":
        raise ValueError("wa_wrong_phase")
    return ("🔐 <b>Yasak Harf · Anlatıcı kartı</b>\n"
            f"Hedef: <b>{escape(state['target'], quote=False)}</b>\n"
            f"Kullanamayacağın harf: <b>{escape(state['forbidden'], quote=False)}</b>\n"
            "Grup ipucunda bu harf ve hedef kelime geçemez. Kartı aldığını onayla.")


def render(state):
    view = rules.public_state(state)
    game, phase = view["game_key"], view["phase"]
    active = [p for p in view["players"] if p["active"]]
    low, high = rules.LIMITS[game]
    lines = [f"🎮 <b>{TITLES[game]}</b>"]
    if phase == "lobby":
        lines.append(f"Oyuncu: {len(active)}/{high} · Başlamak için en az {low} kişi")
        lines.append("Katıl, ardından kurucu başlatsın.")
        lines.append("Oyuncular: " + ", ".join(_name(p["name"]) for p in active))
    elif phase == "cancelled":
        pass
    elif game == "harf_maratonu":
        lines.append(f"Tur {view['round_no']}/5 · Harf: <b>{escape(view['letter'])}</b> · Kategori: <b>{escape(view['category'])}</b>")
        if phase == "collect":
            lines.append("Yanıtını özel sohbetten gönder. Diğerlerinin sözcükleri tur bitene kadar gizli.")
            lines.append(f"Özel sohbet hazır: {view['private_ready_count']}/{len(active)}")
            lines.append(f"Kapalı yanıt: {view['submitted_count']}/{len(active)}")
        elif phase in {"reveal", "finished"}:
            for row in view.get("round_results", []):
                lines.append(f"{_player(view, row['id'])}: {escape(row['word'])} · {row['points']} puan")
    elif game == "sozcuk_avi":
        lines.append("4×4 harf tablosu · Komşu hücrelere yatay, dikey veya çapraz ilerle; hücreyi tekrar kullanma.")
        lines.extend(" ".join(escape(letter) for letter in row) for row in view["board"])
        lines.append(f"Bulunan sözcük: {len(view['claimed'])}")
        lines.append("Geçerli sözcüğü normal grup mesajı olarak yaz. İlk bulan puan alır.")
    elif game == "yasak_harf":
        lines.append(f"Tur {view['round_no']}/{view['round_count']} · Anlatıcı: {_player(view, view['narrator_id'])}")
        if phase == "secret_delivery":
            lines.append("Anlatıcı gizli kartını özelden alıp onaylasın. Süre onaydan ve panelden sonra başlar.")
        elif phase == "clue":
            lines.append("Anlatıcı yasak harfi ve hedefi kullanmadan ipucu yazar. Diğer oyuncular hedefi tahmin eder.")
            lines.append(f"İpucu sayısı: {view['clue_count']}")
        elif phase in {"reveal", "finished"}:
            lines.append(f"Hedef: <b>{escape(view['target'])}</b> · Yasak harf: {escape(view['forbidden'])}")
            if view.get("round_winner_id"):
                lines.append(f"İlk doğru: {_player(view, view['round_winner_id'])}")
    elif game == "ortak_hikaye":
        if phase == "story_turn":
            actor = view["players"][view["turn_index"] % len(view["players"])]["id"]
            lines.append(f"Sıra: {_player(view, actor)} · {view['turn_index'] + 1}/{view['round_count']}")
            lines.append(f"İki cümle yaz; gereken sözcük: <b>{escape(view['required_word'])}</b>.")
            if view.get("continuity_word"):
                lines.append(f"Önceki bağ sözcüğü: <b>{escape(view['continuity_word'])}</b>.")
        lines.append("Hikâye:")
        lines.extend(f"{index}. {escape(text, quote=False)}" for index, text in enumerate(view.get("story", []), 1))
    elif game == "bomba":
        lines.append(f"Bomba {_player(view, view['current_id'])} kişisinde · Harf çifti: <b>{escape(view['bigram'])}</b>")
        lines.append("Harf çiftini içeren kullanılmamış bir sözlük sözcüğü yaz. Geçerli sözcük bombayı sıradaki kişiye geçirir.")
        lines.append("Canlar: " + ", ".join(f"{_name(p['name'])} {view['lives'].get(str(p['id']), 0)}"
                                      for p in active))
        lines.append(f"Geçiş: {view['passes']} · Patlama: {view['explosions']}")
    if phase == "finished":
        lines.append("🏁 Maç bitti. " + ("Kazanan: " + ", ".join(_player(view, uid) for uid in view["winner_ids"])
                                       if view["winner_ids"] else "Kazanan çıkmadı."))
    elif phase == "cancelled":
        lines.append("Oyun iptal edildi. Ödül verilmez.")
    if phase in rules.TERMINAL:
        lines.append("Puanlar: " + ", ".join(f"{_name(p['name'])} {view['scores'].get(str(p['id']), 0)}"
                                       for p in active))
    if view.get("deadline") and phase not in rules.TERMINAL:
        lines.append(f"Kalan: {max(0, math.ceil(view['deadline'] - time.time()))} saniye")
    output = "\n".join(lines)
    if len(output.encode("utf-16-le")) // 2 > 4096:
        raise ValueError("wa_panel_too_long")
    return output


def error_message(code):
    messages = {
        "wa_not_found": "Bu başlıkta oyun bulunamadı.",
        "wa_not_owner": "Yalnız kurucu veya grup yöneticisi bu işlemi yapabilir.",
        "wa_not_player": "Önce oyuna katıl.",
        "wa_player_count": "Bu oyunda yeterli katılımcı yok.",
        "wa_category_word": "Sözcük bu harf ve kategoriyle uyuşmuyor.",
        "wa_invalid_path": "Bu sözcük tabloda geçerli bir komşu yol oluşturmuyor.",
        "wa_already_claimed": "Bu sözcüğü ilk bulan başka bir oyuncu oldu.",
        "wa_forbidden_letter": "İpucunda yasak harf var.",
        "wa_target_leak": "Hedef sözcüğü ipucunda kullanamazsın.",
        "wa_two_sentences": "Tam iki cümle yaz.",
        "wa_required_word": "Gerekli sözcükleri iki cümlende kullan.",
        "wa_invalid_bomb_word": "Sözcük yerel sözlükte yok, harf çiftini içermiyor veya daha önce kullanıldı.",
        "wa_waiting_panel": "Panel yayınlanınca süre başlayacak.",
        "wa_invite_forbidden": "Bu özel davet sana ait değil veya süresi doldu.",
    }
    return messages.get(code, "Bu işlem artık geçerli değil. Güncel paneli kullan.")
