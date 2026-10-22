"""Compact, escaped Telegram panels for the logic puzzle arcade."""
from __future__ import annotations

from html import escape
import math
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from src.telegram.game_controls import with_game_help

from src.games import logic_puzzles as rules

CODE = {"sudoku": "s", "sayisal_tahmin": "n", "hizli_buton": "r"}
REVERSE_CODE = {value: key for key, value in CODE.items()}
DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz"
PHASE_LABEL = {"lobby": "Lobi", "active": "Oynanıyor", "turn_pending": "Sıra hazırlanıyor",
               "turn": "Tahmin sırası", "waiting": "GO bekleniyor", "go_pending": "GO hazırlanıyor",
               "go": "GO!", "round_result": "Tur sonucu", "finished": "Maç bitti",
               "cancelled": "İptal edildi"}


def b36(value):
    value = int(value)
    if value < 0:
        raise ValueError("lp_invalid_number")
    result = ""
    while value:
        value, rem = divmod(value, 36)
        result = DIGITS[rem] + result
    return result or "0"


def callback_data(state, action):
    chat = int(state["chat_id"])
    if chat >= 0 or len(action) > 7:
        raise ValueError("lp_invalid_action")
    data = (f"lp:{CODE[state['game_key']]}:{b36(-chat)}:"
            f"{b36(state.get('thread_id') or 0)}:{state['round_id']}:"
            f"{b36(state.get('round_no', 0))}:{action}")
    if len(data.encode()) > 64:
        raise ValueError("lp_callback_too_long")
    return data


def parse_callback(data):
    parts = str(data).split(":")
    if len(parts) != 7 or parts[0] != "lp" or parts[1] not in REVERSE_CODE:
        raise ValueError("lp_invalid_action")
    _, code, chat, topic, round_id, round_no, action = parts
    if not round_id or not action or len(data.encode()) > 64:
        raise ValueError("lp_invalid_action")
    return REVERSE_CODE[code], -int(chat, 36), int(topic, 36) or None, round_id, int(round_no, 36), action


def _btn(state, label, action):
    return InlineKeyboardButton(label, callback_data=callback_data(state, action))


def _name(person):
    # Two scoreboard appearances per player must still fit Telegram's 4096 UTF-16 limit.
    return escape(str(person["name"])[:12])


def _remaining(deadline):
    return f" · kalan {max(0, math.ceil(deadline - time.time()))} sn" if deadline is not None else ""


def render(state):
    view = rules.public_state(state)
    phase, game = view["phase"], view["game_key"]
    if game == "sudoku":
        size = view["size"]
        box = int(size ** .5)
        rows = []
        for index, row in enumerate(view["board"]):
            groups = [" ".join(str(cell) if cell else "·" for cell in row[start:start + box])
                      for start in range(0, size, box)]
            rows.append(f"{index+1:>2} " + " │ ".join(groups))
            if (index + 1) % box == 0 and index + 1 < size:
                rows.append("   " + "─" * (size * 2 + box * 3 - 4))
        name = _name(view["players"][0])
        text = (f"🧩 <b>Sudoku {size}×{size}</b> · {name}\n"
                f"<pre>{escape(chr(10).join(rows))}</pre>\n"
                f"Dolu hücre: {sum(bool(cell) for row in view['board'] for cell in row)}/{size * size}"
                f" · İpucu: {view['hints_left']}/3 · Hamle: {view['moves']}")
        if phase == "active":
            selected = view.get("selected")
            if selected is not None:
                text += f"\nSeçili kare: {selected[0]+1}. satır, {selected[1]+1}. sütun"
                if view["givens"][selected[0]][selected[1]]:
                    text += " (başlangıç rakamı, değiştirilemez)"
            text += (f"\n{view['focus_band']+1}. bant · {view['focus_row']+1}. satır. "
                     "Bant → satır → sütun seç; boş karede rakam düğmeleri açılır. Çakışan rakam kabul edilmez.")
            text += "\nYalnız kurucu oynar."
            text += ("\nPanel teslimi bekleniyor; süre başlamadı." if view.get("deadline") is None
                     else "\nSüre" + _remaining(view["deadline"]))
        elif phase == "finished":
            text += "\n✅ Bulmaca tamamlandı. Yeni oyun için aşağıdaki düğmeyi kullan."
        else:
            text += "\nOyun iptal edildi; ödül verilmedi."
        return text
    if game == "sayisal_tahmin":
        names = ", ".join(_name(p) for p in view["players"])
        text = f"🔢 <b>Sayısal Tahmin</b> · {PHASE_LABEL[phase]}\nOyuncular ({len(view['players'])}/30): {names}"
        if phase == "lobby":
            text += "\nKurucu zaten katıldı; tek kişiyle de başlanabilir. Diğerleri Katıl'a basabilir, kurucu Başlat'a basar."
            text += "\nBeş tur; herkesin tur başına en çok 10 tahmini var. Doğru sayı 1 puan."
            text += "\nLobi" + _remaining(view.get("lobby_expires_at"))
        if phase != "lobby":
            text += f"\nTur {view['round_no']}/5 · Aralık: {view['low']}–{view['high']}"
            text += "\nPuan: " + ", ".join(
                f"{_name(p)} {view['scores'][str(p['id'])]}" for p in view["players"])
            if phase in {"turn", "turn_pending"}:
                actor = view["players"][view["turn_index"]]
                used = view.get("guesses_counts", {}).get(str(actor["id"]), 0)
                text += f"\nSıra: {_name(actor)} · kalan hakkı: {view['guess_budget']-used}."
                if phase == "turn":
                    text += " Süre" + _remaining(view.get("deadline"))
                    text += "\nYalnız sıradaki oyuncu aralıktaki tahminini gruba sayı olarak yazar."
                else:
                    text += "\nTahmin paneli hazırlanıyor; henüz yanıt yazma."
            if view.get("feedback"):
                text += f"\n{escape(view['feedback'])}"
            if phase == "round_result":
                text += "\nSonraki tur, sonuç paneli teslim edildikten 5 saniye sonra açılır."
        if phase == "finished":
            text += "\n🏁 Beş tur bitti. Yeniden oynamak için aşağıdaki düğmeyi kullan."
        if phase == "finished" and view.get("score_pending"):
            text += "\nPuan kaydı bekleniyor."
        if phase == "cancelled":
            text += "\nİptal edildi; ödül verilmedi."
        return text
    names = ", ".join(_name(p) for p in view["players"])
    text = f"⚡ <b>Hızlı Buton</b> · {PHASE_LABEL[phase]}\nOyuncular ({len(view['players'])}/30): {names}"
    if phase == "lobby":
        text += "\nBaşlamak için en az iki oyuncu gerekir. Katıl'a bas; kurucu Başlat'a basar."
        text += "\nBeş tur. GO'dan sonra hedefi ilk seçen +2, yanlış veya erken basan −1 puan alır."
        text += "\nLobi" + _remaining(view.get("lobby_expires_at"))
    if phase != "lobby":
        text += f"\nTur {view['round_no']}/5 · " + ", ".join(
            f"{_name(p)} {view['scores'][str(p['id'])]}" for p in view["players"])
        if phase == "waiting":
            text += "\n⏳ GO işaretini bekle. Erken basış −1 puan ve tur hakkını bitirir."
            text += "\nPanel tesliminden sonra GO birkaç saniye içinde görünür."
        if phase == "go_pending":
            text += "\nGO paneli hazırlanıyor…"
        if phase == "go":
            text += f"\n🟢 GO! Hedef simge: {escape(view['target'])}"
            text += "\nDoğru simgeyi seç; her oyuncunun tek hakkı var." + _remaining(view.get("deadline"))
            text += f"\nHakkını kullanan: {len(view.get('attempts', {}))}/{len(view['players'])}"
        if view.get("result"):
            text += f"\n{escape(view['result'])}"
        if phase == "round_result":
            text += "\nSonraki tur, sonuç paneli teslim edildikten 5 saniye sonra açılır."
        if phase == "finished":
            text += "\n🏁 Beş tur bitti. Yeniden oynamak için aşağıdaki düğmeyi kullan."
            if view.get("score_pending"):
                text += "\nPuan kaydı bekleniyor."
        if phase == "cancelled":
            text += "\nİptal edildi; ödül verilmedi."
    text += "\nTelegram teslim ve ağ gecikmesi sonucu etkiler; kronometre hassasiyeti iddia edilmez."
    return text


def keyboard(state):
    phase, game = state["phase"], state["game_key"]
    rows = []
    if game == "sudoku" and phase == "active":
        size = state["size"]
        box = int(size ** .5)
        band = int(state.get("focus_band", 0))
        focus = int(state.get("focus_row", 0))
        rows.append([_btn(state, f"Bant {b+1}" + (" ✓" if b == band else ""),
                          f"b{b36(b)}") for b in range(box)])
        rows.append([_btn(state, f"Satır {r+1}" + (" ✓" if r == focus else ""),
                          f"r{b36(r)}") for r in range(band * box, (band + 1) * box)])
        for first in range(0, size, 3):
            rows.append([_btn(state,
                f"{c+1}:" + (str(state["board"][focus][c]) if state["board"][focus][c] else "·"),
                f"c{b36(focus)}{b36(c)}") for c in range(first, min(first + 3, size))])
        selected = state.get("selected")
        if selected is not None and not state["givens"][selected[0]][selected[1]]:
            for first in range(1, size + 1, 3):
                rows.append([_btn(state, str(d),
                                  f"d{b36(d)}{b36(selected[0])}{b36(selected[1])}")
                             for d in range(first, min(first + 3, size + 1))])
        controls = [_btn(state, "↩ Geri al", "u"), _btn(state, "💡 İpucu", "h")]
        if selected is not None and not state["givens"][selected[0]][selected[1]]:
            controls.append(_btn(state, "Sil", f"d0{b36(selected[0])}{b36(selected[1])}"))
        rows.append(controls)
    elif phase == "lobby":
        rows.append([_btn(state, "Katıl", "j"), _btn(state, "Başlat", "s")])
    elif phase == "waiting":
        rows.append([_btn(state, "⚠ Erken basış", "e")])
    elif phase == "go":
        rows.append([_btn(state, symbol, f"p{b36(index)}")
                     for index, symbol in enumerate(rules.SYMBOLS)])
    elif phase == "round_result":
        rows = []
    if phase not in rules.TERMINAL:
        rows.append([_btn(state, "Oyunu bitir", "x")])
    if phase in rules.TERMINAL:
        rows.append([InlineKeyboardButton("Yeniden oyna", callback_data=f"game_start_{game}"),
                     InlineKeyboardButton("Oyun salonu", callback_data="menu_games")])
    return with_game_help(InlineKeyboardMarkup(rows), state['game_key'])


ERRORS = {
    "lp_not_owner": "Bu işlemi yalnız oyunu açan kişi yapabilir.",
    "lp_not_turn": "Şu anda sıra sende değil.",
    "lp_not_player": "Önce oyuna katıl.",
    "lp_conflict": "Bu rakam satır, sütun veya kutuda çakışıyor.",
    "lp_fixed_cell": "Başlangıç rakamı değiştirilemez.",
    "lp_guess_out_of_range": "Tahmin güncel aralığın dışında.",
    "lp_player_count": "Başlamak için en az iki oyuncu gerekli.",
    "lp_already_joined": "Zaten oyundasın; kurucunun başlatmasını bekle.",
    "lp_lobby_closed": "Lobi kapandı; yeni oyun aç.",
    "lp_full": "Lobi dolu; yeni maçı bekle.",
    "lp_expired": "Hamle süresi doldu; güncel paneli bekle.",
    "lp_already_pressed": "Bu tur hakkını kullandın.",
    "lp_wrong_phase": "Bu işlem için güncel paneli bekle.",
}


def error_message(code):
    return ERRORS.get(code, "İşlem yapılamadı. Güncel oyun panelini kullan.")
