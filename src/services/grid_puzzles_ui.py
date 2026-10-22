"""Accessible, secret-safe Telegram panels for both grid puzzles."""

from html import escape
import math
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from src.telegram.game_controls import with_game_help, disabled_button

from src.games import grid_puzzles as grid
from src.services.grid_puzzles_service import base36

GAME_CODE = {grid.MEMORY_KEY: "h", grid.MINES_KEY: "m"}
CODE_GAME = {value: key for key, value in GAME_CODE.items()}
ACTION_CODE = {"join": "j", "start": "s", "pick": "p", "open": "o",
               "flag": "f", "mode": "m", "refresh": "r", "cancel": "x"}
CODE_ACTION = {value: key for key, value in ACTION_CODE.items()}


def callback_data(state, action, index=None):
    if action not in ACTION_CODE:
        raise ValueError("grid_invalid_action")
    return (f"gp:{GAME_CODE[state['game_key']]}:{base36(abs(state['chat_id']))}:"
            f"{base36(state.get('thread_id') or 0)}:{state['round_id']}:"
            f"{base36(state['phase_seq'])}:{base36(state['turn_seq'])}:"
            f"{ACTION_CODE[action]}:{base36(index) if index is not None else '-'}")


def _time_left(view, now):
    deadline = view["deadline"]
    if deadline is None:
        return "Panel teslimi bekleniyor; süre başlamadı."
    return f"Kalan süre: {max(0, math.ceil(deadline - (time.time() if now is None else now)))} sn"


def render(state, *, now=None):
    view = grid.public_view(state)
    if view["game_key"] == grid.MEMORY_KEY:
        lines = ["🧠 <b>Hafıza Eşleştirme</b>", ""]
        if view["phase"] == "lobby":
            lines += [f"Oyuncu: {len(view['players'])}/4 · " + ", ".join(
                          escape(str(p['name'])[:24]) for p in view['players']),
                      "Kurucu zaten katıldı. Tek kişiyle başlanabilir; diğerleri Katıl'a basabilir.",
                      "Kurucu Başlat'a basar. 4×4 tabloda sekiz çifti bul.",
                      "Önizleme 8 sn; her hamle 30 sn. Eşleşen çift 1 puan ve ek hamle verir.",
                      f"Lobi: {max(0, math.ceil(state['lobby_expires_at'] - (time.time() if now is None else now)))} sn kaldı"]
        elif view["phase"] == "preview":
            lines += ["Kartları hatırla; önizleme bitince kapanacak. Sonra ilk oyuncu iki kart seçer.", _time_left(view, now)]
        elif view["phase"] in {"turn", "mismatch"}:
            actor = next(p for p in view["players"] if p["id"] == view["current_actor"])
            lines += [f"Sıra: <b>{escape(str(actor['name'])[:24])}</b> · hamle {view['turn_count'] + 1}/{grid.MAX_TURNS}",
                      f"Bulunan çift: {len(view['matched']) // 2}/8", _time_left(view, now)]
            if view["phase"] == "mismatch":
                lines.append("Yanlış çift açık. Kapanınca sıra sonraki oyuncuya geçer; şimdi düğmelere basma.")
            elif view["phase_ready"]:
                exposed = [i + 1 for i, symbol in enumerate(view["visible"])
                           if symbol and i not in view["matched"]]
                lines.append("İlk kart seçildi; şimdi ikinci kartı seç." if exposed else
                             "Sıradaki oyuncu numaralı kart düğmelerinden birini seçer.")
            else:
                lines.append("Yeni hamle paneli teslim ediliyor; süre ve seçim henüz başlamadı.")
        elif view["phase"] == "finished":
            winners = ", ".join(escape(p["name"]) for p in view["players"]
                                if p["id"] in view["winner_ids"])
            lines.append(f"🏆 Kazanan: {winners}. Yeni oyun için aşağıdaki düğmeyi kullan.")
        else:
            reason = view.get("cancel_reason")
            lines.append("64 hamle sınırına ulaşıldı; oyun ödülsüz bitti." if reason == "turn_limit"
                         else "Oyun ödülsüz iptal edildi. Yeni oyun için aşağıdaki düğmeyi kullan.")
        lines += ["", "Kartlar (1–16):"]
        lines.append("<pre>" + "\n".join(
            "  ".join(f"{i + 1:02d}:{view['visible'][i] or '■'}"
                      for i in range(row * 4, row * 4 + 4)) for row in range(4)) + "</pre>")
        standings = sorted(view["players"], key=lambda p: (-p["score"], p["id"]))
        lines += ["", "Puan sırası: " + " · ".join(
            f"{rank}. {escape(str(p['name'])[:24])} {p['score']}"
            for rank, p in enumerate(standings, 1))]
        if view["phase"] == "finished":
            lines.append("Puan kaydı bekliyor." if view["score_pending"] else "Puan kaydedildi.")
    else:
        lines = ["💣 <b>Mayın Tarlası</b>", "",
                 "6×6 · 6 mayın · ilk açılan hücre ve komşuları güvenli."]
        if view["phase"] == "playing":
            lines += [_time_left(view, now),
                      f"Açılan güvenli hücre: {len(view['visible'])}/30 · Bayrak: {len(view['flags'])}/6",
                      f"Düğme modu: {'Bayrak koy / kaldır' if view['flag_mode'] else 'Hücre aç'}",
                      "Yalnız kurucu oynar. İlk açılan hücre ve komşuları güvenlidir."]
            if not view["phase_ready"]:
                lines.append("Panel teslimi bekleniyor; süre ve seçim henüz başlamadı.")
        elif view["phase"] == "finished":
            lines.append("🏆 Bütün güvenli hücreler açıldı." if view["outcome"] == "cleared"
                         else "💥 Mayına basıldı. Oyun bitti; zafer ödülü yok.")
        else:
            lines.append("Süre doldu veya oyun iptal edildi. Ödül verilmez.")
        lines += ["", "Hücreler (1–36): ■ kapalı · ⚑ bayrak · ✖ mayın · sayı komşu mayın adedi"]
        board_rows = []
        for row in range(6):
            cells = []
            for i in range(row * 6, row * 6 + 6):
                if i in view["flags"]:
                    icon = "⚑"
                elif str(i) in view["visible"]:
                    icon = str(view["visible"][str(i)])
                elif i in view["opened"]:
                    icon = "✖"
                else:
                    icon = "■"
                cells.append(f"{i + 1:02d}:{icon}")
            board_rows.append("  ".join(cells))
        lines.append("<pre>" + "\n".join(board_rows) + "</pre>")
        lines += ["", "Aç modunda kapalı hücreyi aç. Bayrak modunda hücreye basarak bayrak koy veya kaldır."]
        if view["phase"] == "finished" and view["outcome"] == "cleared":
            lines.append("Puan kaydı bekliyor." if view["score_pending"] else "Puan kaydedildi.")
    return "\n".join(lines)


def keyboard(state):
    phase, game_key = state["phase"], state["game_key"]
    rows = []
    if phase == "lobby":
        rows = [[InlineKeyboardButton("Katıl", callback_data=callback_data(state, "join")),
                 InlineKeyboardButton("Başlat", callback_data=callback_data(state, "start"))]]
    elif phase in {"preview", "turn", "mismatch", "playing"}:
        if game_key == grid.MEMORY_KEY and phase == "turn":
            for row in range(4):
                rows.append([disabled_button(f"{i + 1:02d}") if i in state['matched'] or i == state.get('first_pick')
                             else InlineKeyboardButton(f"{i + 1:02d}", callback_data=callback_data(state, "pick", i))
                             for i in range(row * 4, row * 4 + 4)])
        elif game_key == grid.MINES_KEY:
            action = "flag" if state["flag_mode"] else "open"
            for row in range(6):
                rows.append([disabled_button(f"{i + 1:02d}") if i in state['opened']
                             else InlineKeyboardButton(f"{i + 1:02d}", callback_data=callback_data(state, action, i))
                             for i in range(row * 6, row * 6 + 6)])
            rows.append([InlineKeyboardButton("Aç moduna geç" if state["flag_mode"] else "Bayrak moduna geç", callback_data=callback_data(
                state, "mode", 0 if state["flag_mode"] else 1))])
    if phase not in grid.TERMINAL:
        rows += [[InlineKeyboardButton("Yenile", callback_data=callback_data(state, "refresh")),
                  InlineKeyboardButton("Oyunu bitir", callback_data=callback_data(state, "cancel"))]]
    else:
        rows = [[InlineKeyboardButton("Yeniden oyna", callback_data=f"game_start_{game_key}"),
                 InlineKeyboardButton("Oyun salonu", callback_data="menu_games:boards")]]
    return with_game_help(InlineKeyboardMarkup(rows), state['game_key'])


def error_text(code):
    return {
        "grid_not_found": "Bu konuda açık oyun yok.",
        "grid_stale_round": "Bu düğme eski maça ait.",
        "grid_stale_phase": "Bu aşama kapandı. Güncel paneli kullan.",
        "grid_stale_turn": "Bu hamlenin süresi doldu. Güncel paneli kullan.",
        "grid_not_actor": "Şu an hamle sırası sende değil.",
        "grid_not_player": "Önce oyuna katıl.",
        "grid_not_owner": "Bu işlemi oyunun kurucusu veya grup yöneticisi yapabilir.",
        "grid_full": "Hafıza Eşleştirme en fazla dört oyuncu alır.",
        "grid_already_open": "Bu hücre zaten açık veya işaretli.",
        "grid_flag_limit": "En fazla altı bayrak koyabilirsin.",
        "grid_rewards_pending": "Önceki maçın puan kaydı bekliyor; biraz sonra yeniden dene.",
        "grid_already_joined": "Zaten oyundasın; kurucunun başlatmasını bekle.",
        "grid_lobby_closed": "Lobi kapandı; yeni oyun aç.",
        "grid_expired": "Süre doldu; güncel paneli bekle.",
    }.get(str(code), "İşlem şu anda yapılamıyor. Güncel paneli yenile.")
