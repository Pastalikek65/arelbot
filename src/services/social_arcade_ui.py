"""Secret-safe, compact Telegram surfaces for social arcade sessions."""
from __future__ import annotations

from html import escape

from src.games import social_arcade as game
from src.services.social_arcade_service import b36
from src.telegram.game_controls import with_game_help
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

CODE = {key: str(index) for index, key in enumerate(game.GAME_KEYS)}
DECODE = {value: key for key, value in CODE.items()}
TITLE = {"amiral_batti": "Amiral Battı", "iki_dogru_bir_yalan": "İki Doğru Bir Yalan",
         "kimim_ben": "Kimim Ben?", "takim_bilgi_ligi": "Takım Bilgi Ligi",
         "zehirli_kadeh": "Zehirli Kadeh"}
PHASE = {"lobby": "Lobi", "shot": "Atış sırası", "author": "Üç ifade hazırlanıyor",
         "vote": "Gizli oylama", "question": "Soru sırası", "answer": "Yanıt oylaması",
         "trivia": "Takım sorusu", "poison": "Kadehler hazırlanıyor",
         "serve": "Kadeh sunumu", "react": "Karar sırası", "result": "Tur sonucu",
         "finished": "Maç bitti", "cancelled": "İptal edildi"}


def name(person):
    return escape(str(person["name"])[:20])


def callback_data(state, action):
    if len(action) > 8:
        raise ValueError("sa_invalid_action")
    data = (f"sa:{CODE[state['game_key']]}:{b36(abs(state['chat_id']))}:"
            f"{b36(state.get('thread_id') or 0)}:{state['round_id']}:"
            f"{b36(state['phase_seq'])}:{action}")
    if len(data.encode()) > 64:
        raise ValueError("sa_callback_too_long")
    return data


def parse_callback(data):
    parts = str(data).split(":")
    if len(parts) != 7 or parts[0] != "sa" or parts[1] not in DECODE or len(str(data).encode()) > 64:
        raise ValueError("sa_invalid_action")
    _, code, chat, topic, round_id, phase_seq, action = parts
    if not round_id or not action:
        raise ValueError("sa_invalid_action")
    return DECODE[code], -int(chat, 36), int(topic, 36) or None, round_id, int(phase_seq, 36), action


def _button(state, label, action):
    return InlineKeyboardButton(label, callback_data=callback_data(state, action))


def group_text(state):
    view = game.public_view(state)
    key, phase = view["game_key"], view["phase"]
    names = ", ".join(name(p) + (" ✓" if p["ready"] else "") for p in view["players"])
    lines = [f"🎲 <b>{TITLE[key]}</b> · {PHASE[phase]}", f"Oyuncular: {names}"]
    if phase == "lobby":
        low, high = game.LIMITS[key]
        lines.append(f"{low}–{high} kişi. Katıl, özel sohbetini hazırla; kurucu başlatır.")
    elif key == "amiral_batti":
        actor = view["players"][view["turn_index"]]
        lines.append(f"Sıra: {name(actor)} · 6×6 sahada bir kare seç.")
        lines.append("İsabet atıcıda sırayı tutar; ıskada rakibe geçer. Üç ardışık süre aşımı hükmen kayıp.")
        if view.get("last_event"):
            event = view["last_event"]
            if "cell" in event:
                lines.append(f"Son atış {event['cell']//6+1}. satır/{event['cell']%6+1}. sütun: " +
                             ("isabet" if event["hit"] else "ıska"))
            elif "forfeit" in event:
                lines.append("Üç süre aşımı: hükmen sonuç.")
        for p in view["players"]:
            lines.append(f"{name(p)}: {len(view['shots'][str(p['id'])])} atış")
    elif key == "iki_dogru_bir_yalan":
        author = view["author_order"][view["author_index"]] if phase != "finished" else None
        if author is not None:
            person = next(p for p in view["players"] if p["id"] == author)
            lines.append(f"Anlatıcı: {name(person)} · bölüm {view['author_index']+1}/{len(view['author_order'])}.")
        if phase == "author":
            lines.append("Anlatıcı üç kısa ifadeyi özelden yazar ve yalanı özelden işaretler.")
        elif phase == "vote":
            lines.append("Hangi ifade yalan? Oyunu oynayanlar oylarını özelden verir; anlatıcı oy vermez.")
            lines.extend(f"{i+1}. {escape(claim)}" for i, claim in enumerate(view["claims"]))
            lines.append(f"Oy sayısı: {len(state['votes'])}/{len(view['players'])-1}")
        elif phase == "result":
            if view.get("last_event", {}).get("skipped"):
                lines.append("Anlatıcı zamanında üç ifade hazırlayamadı; bölüm atlandı.")
            elif view.get("last_event", {}).get("lie_index") is not None:
                lines.extend(f"{i+1}. {escape(claim)}" for i, claim in enumerate(view["claims"]))
                lines.append(f"Yalan: {view['last_event']['lie_index']+1}. ifade. Doğruluk anlatıcının beyanına dayanır.")
    elif key == "kimim_ben":
        subject = view["players"][view["subject_index"]] if phase != "finished" else None
        if subject:
            lines.append(f"Sıradaki özne: {name(subject)} · soru {view['question_count']}/8.")
        if phase == "question":
            lines.append("Özne özelden evet/hayır sorusu veya kimlik tahmini yazar. Yanlış tahmin bir hak götürür.")
            if view["question_count"] >= 2:
                identity = state["identities"][str(subject["id"])]
                lines.append(f"İpucu · kategori: {escape(game.IDENTITY_CATEGORY[identity])}.")
        elif phase == "answer":
            lines.append(f"Soru: {escape(view['question_text'] or '')}")
            lines.append(f"Diğer oyuncular özelden evet/hayır oyu verir: {len(state['answers'])}/{len(view['players'])-1}.")
        elif phase == "result" and view.get("last_event"):
            event = view["last_event"]
            if "answer" in event:
                lines.append(f"Oy çoğunluğu: {event['answer']}.")
            elif "correct" in event:
                lines.append("Kimlik tahmini " + ("doğru." if event["correct"] else "yanlış."))
            else:
                lines.append("Süre doldu; özne değişiyor.")
    elif key == "takim_bilgi_ligi":
        turn = view["turn_no"]
        if phase == "trivia":
            team = turn % 2
            members = [p for p in view["players"] if view["teams"][str(p["id"])] == team]
            captain = members[(turn // 2) % len(members)]
            question, options, _ = state["questions"][turn]
            lines.extend([f"Tur {turn//2+1}/5 · Takım {team+1} · Kaptan: {name(captain)}",
                          escape(question),
                          *[f"{i+1}. {escape(option)}" for i, option in enumerate(options)],
                          f"Risk: {view['wager']} · Doğru +risk, yanlış −risk · Takım pas hakkı: {view['passes'][team]}"])
        elif phase == "result" and view.get("last_event"):
            event = view["last_event"]
            answer = state["questions"][turn][1][event["answer"]]
            lines.append(f"Doğru yanıt: {escape(answer)}.")
        lines.append(f"Takım puanı: {view['team_scores'][0]} – {view['team_scores'][1]}")
    else:
        lines.append(f"Tur {view['round_no']}/5 · Kurmaca kadeh ve sağlık oyunu; gerçek içecek kullanılmaz.")
        if phase == "poison":
            lines.append("Gizli rol kadehi özelden seçiyor.")
        elif phase == "serve":
            lines.append("Sunucu özelden iki kadehten birini seçiyor.")
        elif phase == "react":
            target = game.poison_server_and_target(state)[1]
            person = next(p for p in view["players"] if p["id"] == target)
            lines.append(f"{name(person)} özelden iç, değiştir veya reddet kararını verir.")
        elif phase == "result" and view.get("last_event"):
            event = view["last_event"]
            lines.append(f"Sunulan kadeh {event['served_cup']+1}; zehirli kadeh {event['poison_cup']+1}.")
            lines.append("Bu tur " + ("sağlık kaybı var." if event["poisoned"] else "sağlık kaybı yok."))
        lines.append("Sağlık: " + " · ".join(f"{name(p)} {view['health'][str(p['id'])]}"
                                            for p in view["players"]))
    if phase == "finished":
        winners = ", ".join(name(p) for p in view["players"] if p["id"] in view["winner_ids"])
        lines.append(f"🏁 Kazanan: {winners or 'Berabere'}.")
    elif phase == "cancelled":
        lines.append("Oyun ödülsüz iptal edildi.")
    elif phase not in {"lobby", "result"}:
        lines.append("Panel ve gerekli özel mesajlar teslim edilmeden süre başlamaz.")
    if phase == "result":
        lines.append("Sonraki bölüm 5 saniye sonra otomatik açılır.")
    return "\n".join(lines)


def group_keyboard(state):
    key, phase = state["game_key"], state["phase"]
    rows = []
    if phase == "lobby":
        rows.append([_button(state, "Katıl", "j"), _button(state, "Özel sohbeti hazırla", "ready")])
        rows.append([_button(state, "Başlat", "s")])
    elif (key, phase) == ("amiral_batti", "shot") and state.get("action_panel_pending"):
        for row in range(6):
            rows.append([_button(state, str(row * 6 + col + 1), f"f{b36(row*6+col)}")
                         for col in range(6)])
    elif (key, phase) == ("takim_bilgi_ligi", "trivia") and state.get("action_panel_pending"):
        rows.append([_button(state, str(index+1), f"a{index}") for index in range(4)])
        rows.append([_button(state, "Risk 1", "w1"), _button(state, "Risk 2", "w2"),
                     _button(state, "Pas", "pass")])
    elif key == "kimim_ben" and phase in {"question", "answer"}:
        rows.append([_button(state, "Kimlik kartımı gör", "card")])
    if phase not in game.TERMINAL:
        rows.append([_button(state, "Ayrıl", "leave"), _button(state, "Oyunu bitir", "x")])
    else:
        rows.append([InlineKeyboardButton("Yeniden oyna", callback_data=f"game_start_{key}"),
                     InlineKeyboardButton("Oyun salonu", callback_data="menu_games")])
    return with_game_help(InlineKeyboardMarkup(rows), state['game_key'])


def private_text(state, user_id):
    key, phase = state["game_key"], state["phase"]
    person = next(p for p in state["players"] if p["id"] == int(user_id))
    lines = [f"🔐 <b>{TITLE[key]}</b> · {PHASE[phase]} · {name(person)}"]
    if key == "amiral_batti":
        ships = {cell for ship in state["ships"][str(user_id)] for cell in ship}
        opponent = next(p["id"] for p in state["players"] if p["id"] != int(user_id))
        incoming = set(state["shots"][str(opponent)])
        lines.append("Kendi gemilerin: ■ sağlam, ✕ vurulmuş, · boş. Bu pano yalnız sana görünür.")
        for row in range(6):
            lines.append(" ".join("✕" if row*6+col in ships & incoming else
                                  "■" if row*6+col in ships else "·" for col in range(6)))
    elif key == "iki_dogru_bir_yalan":
        author = state["author_order"][state["author_index"]]
        if phase == "author" and int(user_id) == author:
            lines.append("Üç kısa ifadeyi ayrı mesajlarla gönder. İkisi doğru, biri yalan olduğunu sen belirlersin.")
            lines.extend(f"{i+1}. {escape(claim)}" for i, claim in enumerate(state["claims"]))
            if len(state["claims"]) == 3:
                lines.append("Yalan olan ifadenin numarasına bas.")
        elif phase == "vote" and int(user_id) != author:
            lines.extend(f"{i+1}. {escape(claim)}" for i, claim in enumerate(state["claims"]))
            lines.append("Yalan olduğunu düşündüğün ifadeyi seç. Oyun gizlidir.")
    elif key == "kimim_ben":
        subject = state["players"][state["subject_index"]]["id"]
        if int(user_id) == subject:
            lines.append("Kimliğin sana gösterilmez. Özelden '? ...?' ile evet/hayır sorusu,")
            lines.append("'= kimlik' ile doğrudan tahmin yaz. Yanlış tahmin de bir hak tüketir; toplam sekiz hakkın var.")
            if phase == "question" and state["question_count"] >= 2:
                identity = state["identities"][str(subject)]
                lines.append(f"İpucu · kategori: {escape(game.IDENTITY_CATEGORY[identity])}.")
        else:
            lines.append(f"Öznenin kimliği: <b>{escape(state['identities'][str(subject)])}</b>.")
            if phase == "answer":
                lines.append(f"Sorusu: {escape(state['question_text'] or '')}")
                lines.append("Kimliği dikkate alıp Evet veya Hayır seç.")
    elif key == "zehirli_kadeh":
        if phase == "poison" and int(user_id) == state["poisoner_id"]:
            lines.append("Bu tur zehirli olacak kurmaca kadehi seç. Rolün gizli kalır.")
        elif phase == "serve":
            lines.append("Sunulacak kadehi seç. Zehirli kadehin yerini bilmiyorsun.")
        elif phase == "react":
            lines.append(f"Sunulan kadeh {state['served_cup']+1}. İç, bir kere değiştir veya bir kere reddet.")
            lines.append(f"Kalan değiştir: {state['swaps'][str(user_id)]}; ret: {state['refuses'][str(user_id)]}.")
    return "\n".join(lines)


def private_keyboard(state, user_id):
    key, phase = state["game_key"], state["phase"]
    rows = []
    if key == "iki_dogru_bir_yalan":
        author = state["author_order"][state["author_index"]]
        if phase == "author" and int(user_id) == author and len(state["claims"]) == 3:
            rows.append([_button(state, f"Yalan {i+1}", f"l{i}") for i in range(3)])
        elif phase == "vote" and int(user_id) != author:
            rows.append([_button(state, f"{i+1}. ifade", f"v{i}") for i in range(3)])
    elif key == "kimim_ben" and phase == "answer":
        subject = state["players"][state["subject_index"]]["id"]
        if int(user_id) != subject:
            rows.append([_button(state, "Evet", "yes"), _button(state, "Hayır", "no")])
    elif key == "zehirli_kadeh":
        if phase == "poison" and int(user_id) == state["poisoner_id"]:
            rows.append([_button(state, "Kadeh 1", "p0"), _button(state, "Kadeh 2", "p1")])
        elif phase == "serve":
            rows.append([_button(state, "Kadeh 1", "c0"), _button(state, "Kadeh 2", "c1")])
        elif phase == "react":
            rows.append([_button(state, "İç", "drink"), _button(state, "Değiştir", "swap"),
                         _button(state, "Reddet", "refuse")])
    return InlineKeyboardMarkup(rows) if rows else None


ERRORS = {"sa_not_owner": "Bu işlemi kurucu veya grup yöneticisi yapabilir.",
          "sa_dm_not_ready": "Herkes özel sohbetini hazırlamadan oyun başlayamaz.",
          "sa_player_count": "Bu oyun için oyuncu sayısı uygun değil.",
          "sa_not_turn": "Şu anda sıra sende değil.",
          "sa_not_captain": "Son yanıtı yalnız bu turun kaptanı verebilir.",
          "sa_wrong_phase": "Bu aşama kapandı. Güncel paneli bekle.",
          "sa_stale_private_panel": "Eski özel panelden işlem yapılamaz.",
          "sa_invalid_invite": "Bu özel sohbet bağlantısının süresi doldu.",
          "sa_invalid_claim": "İfade 5–100 karakter olmalı; bu bölümde üç ifade var.",
          "sa_duplicate_claim": "Aynı ifadeyi iki kez yazamazsın.",
          "sa_invalid_question": "Evet/hayır sorusu 5–120 karakter olmalı ve ? ile bitmeli."}


def error_message(code):
    return ERRORS.get(str(code), "İşlem yapılamadı. Güncel oyun panelini kullan.")
