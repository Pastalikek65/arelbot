"""Escaped Turkish panels and compact callback protocol for clue arcade."""
from __future__ import annotations

from html import escape
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from src.games import clue_arcade as rules
from src.telegram.game_controls import with_game_help

CODE = {rules.BES_IPUCU: "b", rules.EMOJI_BILMECE: "e"}
KEYS = {code: key for key, code in CODE.items()}
DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz"


def _utc_end(timestamp):
    return datetime.fromtimestamp(float(timestamp), timezone.utc).strftime("%d.%m %H:%M:%S UTC")


def b36(value):
    value = int(value)
    if value < 0:
        raise ValueError("ca_invalid_action")
    result = ""
    while value:
        value, rem = divmod(value, 36)
        result = DIGITS[rem] + result
    return result or "0"


def callback_data(state, action):
    chat_id = int(state["chat_id"])
    encoded_chat = ("n" if chat_id < 0 else "p") + b36(abs(chat_id))
    data = (f"ca:{CODE[state['game_key']]}:{encoded_chat}:"
            f"{b36(state.get('thread_id') or 0)}:{state['round_id']}:"
            f"{b36(state['phase_seq'])}:{action}")
    if len(data.encode()) > 64:
        raise ValueError("ca_callback_too_long")
    return data


def parse_callback(data):
    parts = str(data).split(":")
    if len(parts) != 7 or parts[0] != "ca" or parts[1] not in KEYS or len(data.encode()) > 64:
        raise ValueError("ca_invalid_action")
    _, code, chat, topic, round_id, seq, action = parts
    if not round_id or action not in {"j", "s", "l", "x", "p", "r"}:
        raise ValueError("ca_invalid_action")
    if chat[:1] not in {"n", "p"} or not chat[1:]:
        raise ValueError("ca_invalid_action")
    chat_id = int(chat[1:], 36) * (-1 if chat[0] == "n" else 1)
    return KEYS[code], chat_id, int(topic, 36) or None, round_id, int(seq, 36), action


def _button(state, label, action):
    return InlineKeyboardButton(label, callback_data=callback_data(state, action))


def render(state):
    view = rules.public_state(state)
    title = "Beş İpucu" if view["game_key"] == rules.BES_IPUCU else "Emoji Bilmece"
    phase = view["phase"]
    text = f"🧩 <b>{title}</b>"
    if phase == "lobby":
        names = ", ".join(escape(p["name"][:16]) for p in view["players"] if p.get("active", True))
        return (text + f"\nOyuncular: {names}\nKurucu başlatınca beş tur oynanır. "
                "Önce Katıl düğmesine bas; ardından normal grup mesajıyla yanıt ver."
                + (f"\nKatılım bitişi: {_utc_end(view['lobby_expires_at'])}." if view.get("lobby_expires_at") else ""))
    if phase == "cancelled":
        return text + "\nOyun iptal edildi. Puan verilmedi."
    if phase == "finished":
        text += "\n🏁 Beş tur tamamlandı."
        if view.get("score_pending"):
            text += "\nPuan ve ödül kaydı bekleniyor."
    elif view["game_key"] == rules.BES_IPUCU:
        item = view["card"]
        text += (f"\nTur {view['round_no']}/5 · Kategori: <b>{escape(item['category'])}</b>")
        if phase == "reveal":
            text += f"\nYanıt: <b>{escape(item['answer'])}</b>"
            if view["round_winner"]:
                winner = next((p for p in view["players"] if p["id"] == view["round_winner"]), None)
                if winner:
                    text += f"\nBilen: {escape(winner['name'][:16])}"
        else:
            text += (f"\nİpucu {view['clue_no']}/5 · Bu ipucunda {rules.CLUE_POINTS[view['clue_no'] - 1]} puan"
                     "\nHer üyeye bu ipucunda bir tahmin hakkı."
                     " Pas geçerek sonraki ipucunu bekleyebilirsin.")
            for index, clue in enumerate(item["clues"], 1):
                text += f"\n{index}. {escape(clue)}"
    else:
        item = view["card"]
        lengths = "+".join(str(length) for length in item["word_lengths"])
        text += (f"\nTur {view['round_no']}/5 · {escape(item['emoji'])}"
                 f"\nKategori: {escape(item['category'])} · "
                 f"{len(item['word_lengths'])} sözcük · Harfler: {lengths}")
        if phase == "reveal":
            text += f"\nYanıt: <b>{escape(item['answer'])}</b>"
        else:
            text += "\nEmojilerin anlattığı sözcük ya da ifadeyi yaz. Tur başına iki tahmin hakkı; ilkinde 6, ikincisinde 3 puan."
    scores = ", ".join(f"{escape(p['name'][:12])} {p['score']}" for p in view["players"]
                       if p.get("active", True))
    if phase not in rules.TERMINAL:
        if view.get("deadline") is not None:
            text += f"\nAşama bitişi: {_utc_end(view['deadline'])}."
        else:
            text += "\nSüre başlamadı; güncel panel hazırlanıyor."
    return text + f"\nPuan: {scores}"


def keyboard(state):
    rows = []
    if state["phase"] == "lobby":
        rows.append([_button(state, "Katıl", "j"), _button(state, "Başlat", "s")])
        rows.append([_button(state, "Ayrıl", "l")])
    elif state["phase"] == "playing":
        if state["game_key"] == rules.BES_IPUCU:
            rows.append([_button(state, "Bu ipucunu pas geç", "p")])
        rows.append([_button(state, "Katıl", "j"), _button(state, "Ayrıl", "l")])
    if state["phase"] not in rules.TERMINAL:
        rows.append([_button(state, "Oyunu bitir", "x")])
    else:
        rows.append([InlineKeyboardButton("Yeniden oyna",
                                          callback_data=f"game_start_{state['game_key']}")])
        rows.append([InlineKeyboardButton("Oyun salonu", callback_data="menu_games")])
    return with_game_help(InlineKeyboardMarkup(rows), state["game_key"])


ERRORS = {
    "ca_not_found": "Bu konuda açık oyun bulunamadı.",
    "ca_not_owner": "Bu işlemi yalnız kurucu yapabilir.",
    "ca_not_player": "Önce oyuna katıl.",
    "ca_wrong_phase": "Güncel oyun panelini bekle.",
    "ca_stale_phase": "Bu düğme önceki aşamaya ait.",
    "ca_stale_round": "Bu düğme eski oyuna ait.",
    "ca_stale_panel": "Güncel oyun panelini kullan.",
    "ca_stale_message": "Bu mesaj önceki aşamaya ait.",
    "ca_no_attempts": "Bu ipucundaki hakkını kullandın.",
    "ca_lobby_closed": "Katılım süresi doldu.",
    "ca_full": "Oyuncu sınırına ulaşıldı.",
    "ca_already_joined": "Zaten oyundasın.",
}


def error_message(code):
    return ERRORS.get(code, "İşlem yapılamadı. Güncel paneli kullan.")
