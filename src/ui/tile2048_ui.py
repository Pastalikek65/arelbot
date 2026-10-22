"""Compact, accessible Telegram panel for 2048."""

from __future__ import annotations

from html import escape
from datetime import datetime, timezone

from src.games import tile2048 as rules
from src.telegram.game_controls import with_game_help
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def _b36(number):
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    number = int(number)
    sign = "-" if number < 0 else ""
    number = abs(number)
    if number == 0:
        return "0"
    output = ""
    while number:
        number, remainder = divmod(number, 36)
        output = alphabet[remainder] + output
    return sign + output


def _utc_end(timestamp):
    return datetime.fromtimestamp(float(timestamp), timezone.utc).strftime("%d.%m %H:%M:%S UTC")


def callback_data(state, code):
    if code not in {"u", "d", "l", "r", "x", "c"}:
        raise ValueError("t2_invalid_action")
    result = (f"t2:{_b36(int(state['chat_id']))}:"
              f"{_b36(state.get('thread_id') or 0)}:{state['round_id']}:"
              f"{_b36(state['phase_seq'])}:{code}")
    if len(result.encode("utf-8")) > 64:
        raise ValueError("t2_callback_too_long")
    return result


def parse_callback(data):
    parts = str(data).split(":")
    if (len(parts) != 6 or parts[0] != "t2" or len(str(data).encode("utf-8")) > 64
            or not parts[3] or parts[5] not in {"u", "d", "l", "r", "x", "c"}):
        raise ValueError("t2_invalid_action")
    try:
        return (int(parts[1], 36), int(parts[2], 36) or None,
                parts[3], int(parts[4], 36), parts[5])
    except ValueError as exc:
        raise ValueError("t2_invalid_action") from exc


def render(state):
    view = rules.public_state(state)
    lines = ["🔢 <b>2048</b>",
             f"Oyuncu: {escape(view['players'][0]['name'])}",
             f"Puan: <b>{view['score']}</b> · Hamle: {view['moves']} · En büyük taş: {view['best_tile']}"]
    grid = "\n".join(" ".join(f"{value or '·':>4}" for value in row)
                     for row in view["board"])
    lines.extend([f"<pre>{grid}</pre>"])
    if view["phase"] == "cancelled":
        lines.append("Oyun iptal edildi; ödül verilmedi.")
    elif view["phase"] == "finished":
        if view["won"]:
            lines.append("🏆 2048'e ulaştın. Maç tamamlandı!")
        elif view.get("reason") == "move_limit":
            lines.append("Hamle sınırına ulaşıldı. Maç tamamlandı; zafer ödülü yok.")
        else:
            lines.append("Hamle kalmadı. Maç tamamlandı; zafer ödülü yok.")
        if view.get("score_pending"):
            lines.append("Puan ve ödül kaydı bekleniyor.")
    elif view["deadline"] is None:
        lines.append("Süre başlamadı; panel hazırlanıyor.")
    elif view["won"]:
        lines.append("2048'e ulaştın! Devam et veya kazancı alarak bitir.")
    else:
        lines.append("Taşları oklarla kaydır. Aynı değerler birleşir; yalnız geçerli hamlede yeni taş gelir.")
    if view["phase"] == "playing" and view.get("deadline") is not None:
        lines.append(f"Hamle bitişi: {_utc_end(view['deadline'])}.")
    result = "\n".join(lines)
    if len(result.encode("utf-16-le")) // 2 > 4096:
        raise ValueError("t2_panel_too_long")
    return result


def keyboard(state):
    if state["phase"] in rules.TERMINAL:
        return with_game_help(InlineKeyboardMarkup([
            [InlineKeyboardButton("Yeniden oyna", callback_data="game_start_2048")],
            [InlineKeyboardButton("Oyun salonu", callback_data="menu_games:boards")],
        ]), state["game_key"])
    rows = [
        [InlineKeyboardButton("⬆️", callback_data=callback_data(state, "u"))],
        [InlineKeyboardButton("⬅️", callback_data=callback_data(state, "l")),
         InlineKeyboardButton("⬇️", callback_data=callback_data(state, "d")),
         InlineKeyboardButton("➡️", callback_data=callback_data(state, "r"))],
    ]
    if state["won"]:
        rows.append([InlineKeyboardButton("Kazancı al", callback_data=callback_data(state, "c"))])
    rows.append([InlineKeyboardButton("Oyunu bitir", callback_data=callback_data(state, "x"))])
    return with_game_help(InlineKeyboardMarkup(rows), state["game_key"])


_ERRORS = {
    "t2_not_found": "Bu başlıkta 2048 oyunu bulunamadı.",
    "t2_not_owner": "Taşları yalnız oyunu açan kişi kaydırabilir.",
    "t2_wrong_phase": "Güncel oyun panelini bekle veya yeni maç aç.",
    "t2_no_move": "Taşlar bu yönde hareket etmiyor.",
    "t2_stale_phase": "Bu düğme önceki hamleye ait.",
    "t2_stale_panel": "Bu mesaj eski oyun paneline ait.",
}


def error_message(code):
    return _ERRORS.get(code, "İşlem yapılamadı. Güncel 2048 panelini kullan.")
