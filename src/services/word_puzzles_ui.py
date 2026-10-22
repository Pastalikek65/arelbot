"""Safe, compact group panel for the three Turkish word games."""
from __future__ import annotations

import math
import time
from html import escape

from src.games import word_puzzles as games
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from src.telegram.game_controls import with_game_help

TITLES = {"wordle_tr": "Türkçe Wordle", "karsi_kelime": "Karşı Kelime", "anagram": "Anagram"}
CODES = {"wordle_tr": "w", "karsi_kelime": "k", "anagram": "a"}
KEYS = {value: key for key, value in CODES.items()}


def _b36(value):
    value = int(value)
    if value == 0:
        return "0"
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    result = ""
    while value:
        value, rest = divmod(value, 36)
        result = digits[rest] + result
    return result


def callback_data(state, action):
    if action not in {"join", "start", "leave", "stop", "hint", "refresh"}:
        raise ValueError("word_puzzles_invalid_action")
    return (f"wp:{CODES[state['game_key']]}:{_b36(abs(state['chat_id']))}:"
            f"{_b36(state.get('thread_id') or 0)}:{state['round_id']}:"
            f"{_b36(state['phase_seq'])}:{action}")


def keyboard(state):
    phase = state["phase"]

    def button(label, action):
        return InlineKeyboardButton(label, callback_data=callback_data(state, action))

    rows = []
    if phase == "lobby":
        if state["game_key"] != "wordle_tr":
            rows.append([button("Katıl", "join"), button("Ayrıl", "leave")])
        rows.append([button("Başlat", "start"), button("İptal", "stop")])
    elif phase == "playing":
        if state["game_key"] == "anagram" and state["hint_count"] < games.MAX_HINTS:
            rows.append([button("İpucu · puan düşer", "hint")])
        rows.append([button("Ayrıl", "leave"), button("İptal", "stop")])
    elif phase == "reveal":
        rows.append([button("İptal", "stop")])
    if phase not in {"finished", "cancelled"}:
        rows.append([button("Yenile", "refresh")])
    else:
        rows.append([InlineKeyboardButton("🔄 Yeni maç", callback_data=f"game_start_{state['game_key']}"),
                     InlineKeyboardButton("🎮 Oyun salonu", callback_data="menu_games")])
    return with_game_help(InlineKeyboardMarkup(rows), state['game_key']) if rows else None


def _name(value):
    # HTML entities may expand a name fivefold; cap before escaping so a full
    # 30-player panel stays within Telegram's 4096-character message bound.
    return escape(str(value)[:20])


def render_game(state):
    view = games.public_state(state)
    game_key, phase = view["game_key"], view["phase"]
    active = [p for p in view["players"] if p["active"]]
    names = ", ".join(_name(p["name"]) for p in active)
    lines = [f"🧩 <b>{TITLES[game_key]}</b>"]
    if phase == "lobby":
        lines.append(f"Oyuncular: {len(active)}/{1 if game_key == 'wordle_tr' else games.MAX_PLAYERS} · {names}")
        lines.append(f"Lobi süresi: {max(0, math.ceil(view['lobby_expires_at'] - time.time()))} sn")
        if game_key == "wordle_tr":
            lines.append("Tek oyunculu oyun. Kurucu Başlat'a basar; beş harfli kelimeyi altı denemede bulur.")
        else:
            lines.append("Kurucu zaten katıldı. Diğerleri Katıl'a basabilir; kurucu istediği zaman başlatır.")
            lines.append("Beş tur oynanır; her tur ilk doğru yanıt puan alır.")
    elif phase in {"playing", "reveal"}:
        lines.append(f"Tur {view['round_number']}/{view['round_count']}")
        if game_key == "wordle_tr":
            lines.append("Yalnız kurucu beş harfli sözlük kelimesi yazar. 🟩 yerinde · 🟨 başka yerde · ⬛ yok")
            for attempt in view["attempts"]:
                marks = {"correct": "🟩", "present": "🟨", "absent": "⬛"}
                lines.append(f"{escape(attempt['guess'])} · {''.join(marks[m] for m in attempt['marks'])}")
            lines.append(f"Deneme: {len(view['attempts'])}/6 · kalan {6 - len(view['attempts'])}")
        elif game_key == "karsi_kelime":
            lines.append(f"Karşıt anlamını yaz: <b>{escape(view['prompt'] or '')}</b>")
            lines.append("İlk doğru yanıt: 10 puan.")
        else:
            lines.append(f"Karışık harfler: <b>{escape(' · '.join(view['scramble'] or ''))}</b>")
            if phase == "playing":
                lines.append(f"Açılan harfler: <b>{escape(' '.join(view.get('hint_pattern', '·' * len(view.get('scramble') or ''))))}</b>")
                lines.append(f"İpucu: {view['hint_count']}/{games.MAX_HINTS} · kalan {games.MAX_HINTS - view['hint_count']}")
            else:
                lines.append(f"Kullanılan ipucu: {view['hint_count']}/{games.MAX_HINTS}")
            lines.append(f"Bu tur doğru yanıt: {view['round_value']} puan")
        if phase == "playing":
            if view.get("deadline") is None:
                lines.append("Panel teslimi bekleniyor; süre ve yanıt kabulü henüz başlamadı.")
            else:
                lines.append("Aktif oyuncular yanıtı bu başlıkta normal grup mesajı olarak yazar; paneli yanıtlayabilir.")
        else:
            lines.append(f"Doğru yanıt: <b>{escape(view['target'] or '')}</b>")
            if view.get("round_winner_id"):
                winner = next((p["name"] for p in active if p["id"] == view["round_winner_id"]), "Oyuncu")
                lines.append(f"İlk doğru: {_name(winner)}")
            else:
                lines.append("Bu turda doğru yanıt gelmedi.")
            if view["round_number"] == view["round_count"]:
                lines.append("Son tur bitti; sonuç paneli hazırlanıyor." if view.get("deadline") is None
                             else "Maç sonucu otomatik açılır.")
            else:
                lines.append("Sonraki tur paneli hazırlanıyor." if view.get("deadline") is None
                             else "Sonraki tur otomatik açılır.")
        if game_key != "wordle_tr":
            leaders = sorted(active, key=lambda p: (-view["scores"].get(str(p["id"]), 0), p["id"]))[:5]
            lines.append("Puan: " + " · ".join(f"{_name(p['name'])} {view['scores'].get(str(p['id']), 0)}" for p in leaders))
    elif phase == "finished":
        lines.append("🏁 Oyun bitti.")
        if view.get("target"):
            lines.append(f"Son yanıt: <b>{escape(view['target'])}</b>")
        ranking = sorted(active, key=lambda p: (-view["scores"].get(str(p["id"]), 0), p["id"]))
        lines.extend(f"{_name(p['name'])}: {view['scores'].get(str(p['id']), 0)} puan"
                     for p in ranking[:30])
        if view.get("score_pending"):
            lines.append("Puan kaydı bekleniyor.")
        lines.append("Yeni maç için aşağıdaki düğmeyi kullan.")
    else:
        lines.append("Oyun iptal edildi. Puan verilmez; yeniden oynamak için Yeni maç'a bas.")
    if view.get("deadline") and phase in {"playing", "reveal"}:
        lines.append(f"Kalan: {max(0, math.ceil(view['deadline'] - time.time()))} saniye")
    return "\n".join(lines)


def error(code):
    messages = {
        "word_puzzles_not_found": "Bu başlıkta oyun bulunamadı.",
        "word_puzzles_not_owner": "Bu işlemi yalnızca kurucu veya yönetici yapabilir.",
        "word_puzzles_not_player": "Önce oyuna katıl.",
        "word_puzzles_solo": "Bu oyun tek kişiliktir.",
        "word_puzzles_unknown_word": "Bu kelime yerel sözlükte bulunmuyor.",
        "word_puzzles_word_length": "Beş harfli bir kelime yaz.",
        "word_puzzles_hint_unavailable": "Şu anda ipucu kullanılamaz.",
        "word_puzzles_waiting_panel": "Panel yayınlanınca tur başlayacak.",
        "word_puzzles_lobby_closed": "Katılım süresi doldu; yeni oyun aç.",
        "word_puzzles_already_joined": "Zaten bu oyundasın; kurucunun başlatmasını bekle.",
        "word_puzzles_full": "Lobi dolu; yeni maçı bekle.",
        "word_puzzles_wrong_phase": "Bu aşamada yanıt kabul edilmiyor; güncel paneli bekle.",
        "word_puzzles_expired": "Tur süresi doldu; güncel paneli bekle.",
        "word_puzzles_invalid_word": "Yalnız tek bir kelime yaz.",
    }
    return messages.get(code, "Bu işlem artık geçerli değil. Güncel paneli kullan.")
