"""Compact, escaped Telegram panels for the four reasoning games."""

from __future__ import annotations

from html import escape

from src.games import reasoning_arcade as rules
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from src.telegram.game_controls import with_game_help

CODE = {rules.RANK: "r", rules.CALENDAR: "c", rules.TRUE_FALSE: "t",
        rules.DETECTIVE: "d"}
KEYS = {code: game for game, code in CODE.items()}
DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz"


def b36(value):
    value = int(value)
    if value < 0:
        raise ValueError("ra_invalid_action")
    answer = ""
    while value:
        value, remainder = divmod(value, 36)
        answer = DIGITS[remainder] + answer
    return answer or "0"


def callback_data(state, action):
    if len(action) > 7 or int(state["chat_id"]) >= 0:
        raise ValueError("ra_invalid_action")
    data = (f"ra:{CODE[state['game_key']]}:{b36(-int(state['chat_id']))}:"
            f"{b36(state.get('thread_id') or 0)}:{state['round_id']}:"
            f"{b36(state['phase_seq'])}:{action}")
    if len(data.encode("utf-8")) > 64:
        raise ValueError("ra_callback_too_long")
    return data


def parse_callback(data):
    parts = str(data).split(":")
    if len(parts) != 7 or parts[0] != "ra" or parts[1] not in KEYS or len(data.encode()) > 64:
        raise ValueError("ra_invalid_action")
    _, code, chat, topic, round_id, phase_seq, action = parts
    if not round_id or not action:
        raise ValueError("ra_invalid_action")
    return KEYS[code], -int(chat, 36), int(topic, 36) or None, round_id, int(phase_seq, 36), action


def _btn(state, label, action):
    return InlineKeyboardButton(label, callback_data=callback_data(state, action))


def _name(player):
    return escape(player["name"][:12])


def render(state):
    view = rules.public_state(state)
    game, phase = view["game_key"], view["phase"]
    title = {rules.RANK: "Kör Sıralama", rules.CALENDAR: "Kurgu Takvimi",
             rules.TRUE_FALSE: "Doğru / Yanlış", rules.DETECTIVE: "Dedektif"}[game]
    text = f"🧠 <b>{title}</b>"
    if phase == "lobby":
        roster = ", ".join(_name(player) for player in view["players"]
                           if player.get("active", True))
        return text + f"\nOyuncular: {roster}\nKurucu başlatınca oyun açılır."
    if phase == "cancelled":
        return text + "\nOyun iptal edildi; ödül verilmedi."
    if game == rules.RANK:
        if phase == "finished":
            ordered = sorted(range(5), key=lambda index: view["values"][index])
            actual = ", ".join(str(view["values"][index]) for index in ordered)
            text += (f"\n🏁 Sonuç: {view['players'][0]['score']}/100"
                     f"\nGerçek küçükten büyüğe sıra: {actual}")
        else:
            text += (f"\nDeğer {view['step'] + 1}/5: <b>{view['values'][view['step']]}</b>"
                     "\nSonraki değerleri görmeden bu değeri boş bir sıraya yerleştir."
                     "\n1 = en küçük, 5 = en büyük. Seçim geri alınamaz.")
            for index, rank in enumerate(view["assigned"]):
                if rank is not None:
                    text += f"\n{index + 1}. değer → {rank}. sıra"
        return text
    if game == rules.CALENDAR:
        if phase == "finished":
            text += "\n🏁 Beş kurgu takvimi tamamlandı."
        else:
            puzzle = view["puzzle"]
            active = [p for p in view["players"] if p.get("active", True)]
            text += (f"\nTur {view['round_no']}/5 · {len([p for p in active if p['id'] in view['confirmed']])}/{len(active)} onay"
                     "\nBu olaylar tamamen kurgudur. En erkenden en geçe beş olayı seç ve onayla.")
            for index, name in enumerate(puzzle["events"], 1):
                text += f"\n{index}. {escape(name)}"
            text += "\nİpuçları:"
            for clue in puzzle["clues"]:
                text += (f"\n• {clue['before'] + 1}. olay, {clue['after'] + 1}. olaydan "
                         f"{clue['days']} gün önce.")
        text += "\nPuan: " + ", ".join(f"{_name(p)} {p['score']}" for p in view["players"] if p.get("active", True))
        return text
    if game == rules.TRUE_FALSE:
        if phase == "finished":
            text += "\n🏁 On önerme tamamlandı."
        else:
            text += f"\nTur {view['round_no']}/10 · {escape(view['proposition']['text'])}"
            if phase == "reveal":
                verdict = "Doğru" if view["proposition"]["answer"] else "Yanlış"
                text += f"\nSonuç: {verdict}. {escape(view['proposition']['explanation'])}"
            else:
                text += ("\nBir kez oy ver. Kalkan: doğru +1, yanlış −1 can. "
                         "Risk: doğru +2, yanlış −2 can. Yanıtlar tur bitene kadar gizlidir.")
        text += "\n" + ", ".join(f"{_name(p)} {p['score']}p/{p['lives']}❤" for p in view["players"] if p.get("active", True))
        return text
    if phase == "finished":
        text += "\n🏁 Üç kurgu vaka tamamlandı."
    else:
        case = view["case"]
        text += (f"\nKurgu vaka {view['round_no']}/3. Kişi, yer ve nesneyi çıkar."
                 "\nYanıt biçimi: kişi / yer / nesne. Oyuncu başına en fazla üç suçlama."
                 "\nKişiler: " + ", ".join(escape(x) for x in case["suspects"]) +
                 "\nYerler: " + ", ".join(escape(x) for x in case["places"]) +
                 "\nNesneler: " + ", ".join(escape(x) for x in case["objects"]))
        for clue in case["clues"]:
            if clue["kind"] == "person_place":
                text += (f"\n• {escape(case['suspects'][clue['person']])}, "
                         f"{escape(case['places'][clue['place']])} yerindeydi.")
            elif clue["kind"] == "person_object":
                text += (f"\n• {escape(case['suspects'][clue['person']])}, "
                         f"{escape(case['objects'][clue['object']])} nesnesini taşıyordu.")
            else:
                text += f"\n• Suçlu {escape(case['suspects'][clue['person']])} değil."
        text += "\nEk ipucu puandan 1 düşer."
    text += "\n" + ", ".join(f"{_name(p)} {p['score']}p/{p['accusations']} deneme"
                              for p in view["players"] if p.get("active", True))
    return text


def keyboard(state):
    phase, game = state["phase"], state["game_key"]
    rows = []
    if phase == "lobby":
        rows.append([_btn(state, "Katıl", "j"), _btn(state, "Başlat", "s")])
        rows.append([_btn(state, "Ayrıl", "l")])
    elif phase == "playing":
        if game == rules.RANK:
            rows.append([_btn(state, f"{rank}. sıra", f"r{rank}")
                         for rank in range(1, 6) if rank not in state["assigned"]])
        elif game == rules.CALENDAR:
            rows.append([_btn(state, f"{index + 1}. olay", f"c{index}")
                         for index in range(5)])
            rows.append([_btn(state, "Seçimi sıfırla", "z"),
                         _btn(state, "Sırayı onayla", "o")])
        elif game == rules.TRUE_FALSE:
            rows.append([_btn(state, "Doğru · Kalkan", "tk"),
                         _btn(state, "Yanlış · Kalkan", "fk")])
            rows.append([_btn(state, "Doğru · Risk", "tr"),
                         _btn(state, "Yanlış · Risk", "fr")])
        elif game == rules.DETECTIVE and state["clues_shown"] < 6:
            rows.append([_btn(state, "Ek ipucu (−1 puan)", "h")])
    if phase not in rules.TERMINAL:
        if phase != "lobby":
            rows.append([_btn(state, "Ayrıl", "l")])
        rows.append([_btn(state, "Oyunu bitir", "x")])
    else:
        rows.append([InlineKeyboardButton("Yeniden oyna",
                                          callback_data=f"game_start_{game}")])
        rows.append([InlineKeyboardButton("Oyun salonu", callback_data="menu_games")])
    return with_game_help(InlineKeyboardMarkup(rows), state['game_key'])


ERRORS = {
    "ra_not_owner": "Bu işlemi yalnız kurucu yapabilir.",
    "ra_not_player": "Önce oyuna katıl.",
    "ra_wrong_phase": "Güncel oyun panelini bekle.",
    "ra_stale_phase": "Bu düğme önceki aşamaya ait.",
    "ra_stale_message": "Bu mesaj önceki aşamaya ait.",
    "ra_rank_taken": "Bu sıra dolu veya geçersiz.",
    "ra_incomplete_order": "Önce beş farklı olayı sıraya koy.",
    "ra_already_answered": "Bu turdaki hakkını kullandın.",
    "ra_no_accusations": "Suçlama hakkın bitti.",
    "ra_invalid_accusation": "Kişi / yer / nesne biçimini kullan.",
}


def error_message(code):
    return ERRORS.get(code, "İşlem yapılamadı. Güncel paneli kullan.")
