"""One group panel and protected actor-bound hand panels for card games."""
from __future__ import annotations

from html import escape
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from src.games import card_table as rules
from src.services.card_table_service import b36

CODE = {"pisti": "p", "papaz_kimde": "k"}
DECODE = {value: key for key, value in CODE.items()}
TITLE = {"pisti": "Pişti", "papaz_kimde": "Papaz Kimde?"}
SUIT = {"S": "♠", "H": "♥", "D": "♦", "C": "♣"}
RANK = {"A": "A", "J": "V", "Q": "Kız", "K": "Papaz"}
SUIT_NAME = {"S": "Maça", "H": "Kupa", "D": "Karo", "C": "Sinek"}


def card_label(card):
    return f"{RANK.get(rules.rank(card), rules.rank(card))}{SUIT[card[-1]]}"


def card_name(card):
    return f"{RANK.get(rules.rank(card), rules.rank(card))} {SUIT_NAME[card[-1]]}"


def name(player):
    return escape(str(player["name"])[:20])


def _utc_end(timestamp):
    return datetime.fromtimestamp(float(timestamp), timezone.utc).strftime("%d.%m %H:%M:%S UTC")


def callback_data(state, action):
    if not action or len(action) > 7:
        raise ValueError("ct_invalid_action")
    data = (f"ct:{CODE[state['game_key']]}:{b36(abs(state['chat_id']))}:"
            f"{b36(state.get('thread_id') or 0)}:{state['round_id']}:"
            f"{b36(state['phase_seq'])}:{action}")
    if len(data.encode()) > 64:
        raise ValueError("ct_callback_too_long")
    return data


def parse_callback(data):
    parts = str(data).split(":")
    if len(parts) != 7 or parts[0] != "ct" or parts[1] not in DECODE or len(str(data).encode()) > 64:
        raise ValueError("ct_invalid_action")
    _, code, chat, topic, round_id, sequence, action = parts
    if not round_id or not action:
        raise ValueError("ct_invalid_action")
    return DECODE[code], -int(chat, 36), int(topic, 36) or None, round_id, int(sequence, 36), action


def _button(state, label, action):
    return InlineKeyboardButton(label, callback_data=callback_data(state, action))


def group_text(state):
    view = rules.public_state(state)
    game, phase = view["game_key"], view["phase"]
    lines = [f"🃏 <b>{TITLE[game]}</b>"]
    if phase == "lobby":
        ready = sum(bool(p["ready"]) for p in view["players"])
        lines.append(f"<b>Oyuncular hazırlanıyor · {ready}/{len(view['players'])} hazır</b>")
        lines.append("Katıl → Özel sohbeti hazırla → kurucu Başlat'a basar.")
        lines.append("Oyuncular: " + ", ".join(name(p) + (" ✓ hazır" if p["ready"] else " · bekliyor")
                                          for p in view["players"]))
        if view.get("lobby_expires_at"):
            lines.append(f"Katılım bitişi: {_utc_end(view['lobby_expires_at'])}.")
        if game == "pisti":
            lines.append("2 veya 4 kişi · 4 kişide karşılıklı oturanlar takım olur.")
        else:
            lines.append("3–8 kişi · Son tek papazı tutan kaybeder.")
    elif game == "pisti":
        if phase == "playing":
            actor = view["players"][view["turn_index"]]
            lines.append(f"<b>Sıra: {name(actor)}</b> · {view['turn_no'] + 1}/48. hamle · dağıtım {view['deal_no']}")
            lines.append("Özel elindeki kartlardan birini seçip oyna." if view.get("phase_ready") else
                         "Kartlar özel sohbete gönderiliyor; herkesin eli ulaşınca sıra başlar.")
        top = view.get("top_card")
        lines.append(f"Masa: {view.get('table_count', 0)} kart · üstte {card_name(top) if top else 'boş'}.")
        if phase == "playing":
            lines.append("Aynı değer veya vale masayı alır. Tek kartta aynı değer pişti +10; vale/vale +20.")
        event = view.get("last_event") or {}
        if event.get("card"):
            actor = next((p for p in view["players"] if p["id"] == event.get("actor")), None)
            result = "pişti yaptı" if event.get("pisti") else ("masayı aldı" if event.get("capture") else "masaya attı")
            lines.append(f"Son hamle: {name(actor) if actor else 'Oyuncu'} {card_name(event['card'])} ile {result}." +
                         (" Süre dolunca otomatik oynandı." if event.get("timeout") else ""))
        for side in (0, 1):
            members = [name(p) for index, p in enumerate(view["players"]) if index % 2 == side]
            lines.append(f"{' / '.join(members)}: {len(state.get('captured', {}).get(str(side), []))} alınan kart · "
                         f"{state.get('pisti_points', [0, 0])[side]} pişti puanı")
        if phase == "finished":
            lines.append(f"Son puan: {view['team_scores'][0]} – {view['team_scores'][1]} (kart ve pişti puanları).")
            for side, points in enumerate(view.get("score_breakdown", [])):
                members = " / ".join(name(p) for index, p in enumerate(view["players"]) if index % 2 == side)
                lines.append(
                    f"{members}: pişti {points['pisti']} + as/vale {points['aces_jacks']} "
                    f"+ özel kart {points['special_cards']} + çok kart {points['card_majority']} "
                    f"= <b>{view['team_scores'][side]}</b>"
                )
    else:
        if phase == "playing":
            actor = view["players"][view["turn_index"]]
            target_id = rules.draw_target(state)
            target = next((p for p in view["players"] if p["id"] == target_id), None)
            lines.append(f"<b>Sıra: {name(actor)}</b> · {view.get('turn_no', 0) + 1}. çekiş")
            lines.append((f"{name(actor)}, özel sohbetteki düğmeyle {name(target)} elinden rastgele kart çek." if target else
                          "Çekilecek oyuncu bekleniyor.") if view.get("phase_ready") else
                         "Kartlar özel sohbete gönderiliyor; herkesin eli ulaşınca sıra başlar.")
        if phase == "playing":
            lines.append("Eş değerli kartlar otomatik atılır; son tek papazı tutan kaybeder.")
        counts = view.get("hand_counts", {})
        lines.append("Eldeki kart: " + " · ".join(f"{name(p)} {counts.get(str(p['id']), 0)}"
                                                   for p in view["players"]))
        lines.append(f"Gizli çekiş: {view.get('draw_count', 0)} · atılan çift: {len(state.get('discarded', [])) // 2}.")
        event = view.get("last_event") or {}
        if event.get("target"):
            actor = next((p for p in view["players"] if p["id"] == event.get("actor")), None)
            lines.append(f"Son çekiş: {name(actor) if actor else 'Oyuncu'} · {event.get('pairs_removed', 0)} çift atıldı" +
                         (" · süre dolunca otomatik çekildi." if event.get("timeout") else "."))
    if phase == "playing":
        if view.get("deadline") is not None:
            lines.append(f"Sıra bitişi: {_utc_end(view['deadline'])}.")
        else:
            lines.append("Süre başlamadı; grup paneli ve zorunlu özel eller hazırlanıyor.")
        lines.append("Üç ardışık sıra süresi aşımı hükmen yenilgi getirir.")
    elif phase == "finished":
        winners = ", ".join(name(p) for p in view["players"] if p["id"] in view["winner_ids"])
        if game == "pisti" and view.get("reason") == "cards_exhausted" and view["team_scores"][0] == view["team_scores"][1]:
            lines.append("🏁 Berabere; iki tarafın puanı eşit.")
        else:
            lines.append(f"🏁 Kazanan: {winners or 'Yok'}.")
        if view.get("loser_id"):
            loser = next(p for p in view["players"] if p["id"] == view["loser_id"])
            lines.append(f"Kaybeden: {name(loser)}.")
        if view.get("reason") in {"forfeit", "three_timeouts"}:
            lines.append("Hükmen sonuç; tamamlanmamış maça puan veya coin yazılmaz.")
        elif view.get("score_pending"):
            lines.append("Tamamlanan maçın puan ve ödül kaydı bekleniyor.")
        else:
            lines.append("Puan kaydı tamamlandı." if view.get("reason") in {"cards_exhausted", "single_king"} else
                         "Bu sonuç için puan yazılmadı.")
        if game == "papaz_kimde":
            lines.append("Puanlar: " + " · ".join(f"{name(p)} {view.get('scores', {}).get(str(p['id']), 0)}"
                                              for p in view["players"]))
    elif phase == "cancelled":
        lines.append("Oyun ödülsüz iptal edildi.")
    return "\n".join(lines)


def group_keyboard(state):
    rows = []
    if state["phase"] == "lobby":
        rows.append([_button(state, "Katıl", "join"), _button(state, "Özel sohbeti hazırla", "ready")])
        rows.append([_button(state, "Başlat", "start")])
    if state["phase"] not in rules.TERMINAL:
        rows.append([_button(state, "Ayrıl", "leave"), _button(state, "Bitir", "stop")])
    else:
        rows.append([InlineKeyboardButton("Yeniden oyna", callback_data=f"game_start_{state['game_key']}"),
                     InlineKeyboardButton("Oyun salonu", callback_data="menu_games")])
    panel = InlineKeyboardMarkup(rows)
    from src.telegram.game_controls import with_game_help
    return with_game_help(panel, state["game_key"])


def private_text(state, user_id):
    if state["phase"] != "playing" or str(user_id) not in state["hands"]:
        raise ValueError("ct_stale_hand")
    player = next(p for p in state["players"] if p["id"] == int(user_id))
    actor = state["players"][state["turn_index"]]["id"]
    cards = state["hands"][str(user_id)]
    lines = [f"🔐 <b>{TITLE[state['game_key']]}</b> · {name(player)}",
             f"Elin: ({len(cards)} kart) " + (" ".join(card_label(card) for card in cards) if cards else "boş"),
             "♠ Maça · ♥ Kupa · ♦ Karo · ♣ Sinek"]
    if state["game_key"] == "pisti":
        top = state.get("table", [])[-1] if state.get("table") else None
        lines.append(f"Masada: {card_name(top) if top else 'boş'} · {len(state.get('table', []))} kart.")
        if actor == int(user_id):
            lines.append("Sıran geldi: bir kart seç. Aynı değer veya vale masayı alır.")
        else:
            lines.append("Sıranı bekle; elin yalnız sana görünür.")
    else:
        if (state.get("last_draw") or {}).get("actor") == int(user_id):
            lines.append(f"Son çektiğin kart: {card_label(state['last_draw']['card'])}.")
        if actor == int(user_id):
            target = rules.draw_target(state)
            target_player = next(p for p in state["players"] if p["id"] == target)
            lines.append(f"{name(target_player)} elindeki kapalı kartlardan birini galeriden seç. İstersen rastgele çekebilirsin.")
        else:
            lines.append("Sıran geldiğinde gizli çekiş düğmesi açılır.")
    return "\n".join(lines)


def private_keyboard(state, user_id):
    if state["phase"] != "playing" or state["players"][state["turn_index"]]["id"] != int(user_id):
        return None
    if state["game_key"] == "pisti":
        buttons = [_button(state, card_label(card), f"p{index}")
                   for index, card in enumerate(state["hands"][str(user_id)])]
    else:
        buttons = [_button(state, "Rastgele kart çek", "draw")]
    return InlineKeyboardMarkup([buttons])


ERRORS = {"ct_dm_not_ready": "Herkes özel sohbetini hazırlamadan oyun başlayamaz.",
          "ct_player_count": "Pişti 2 veya 4; Papaz Kimde 3–8 kişiyle oynanır.",
          "ct_not_turn": "Şu anda sıra sende değil.",
          "ct_stale_hand": "Eski el panelinden işlem yapılamaz.",
          "ct_stale_phase": "Bu tur kapandı; güncel paneli kullan.",
          "ct_wrong_phase": "Şu anda bu işlem yapılamaz.",
          "ct_not_owner": "Bu işlemi kurucu veya grup yöneticisi yapabilir.",
          "ct_invalid_invite": "Özel sohbet bağlantısının süresi doldu."}


def error_message(code):
    return ERRORS.get(str(code), "İşlem yapılamadı. Güncel oyun panelini kullan.")
