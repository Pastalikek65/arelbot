"""Group-safe Turkish presentation for Casus."""
import math
from html import escape

from src.games import spy
from src.services.spy_service import _b36
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def callback_data(state, action):
    return (f"sp:{_b36(abs(state['chat_id']))}:{_b36(state.get('thread_id') or 0)}:"
            f"{state['round_id']}:{_b36(state['version'])}:{action}")


def _name(view, user_id):
    player = next((p for p in view["players"] if p["id"] == user_id), None)
    return escape(player["name"] if player else "Oyuncu")


def render_spy(state):
    view = spy.public_state(state)
    active = [p for p in view["players"] if p["active"]]
    lines = ["🕵️ <b>Casus</b>", f"Oyuncu: {len(active)}/16"]
    if view["phase"] == "lobby":
        lines += ["4–16 oyuncu katılır. Kurucu başlatır.",
                  f"Özel sohbeti hazır: {sum(p['ready'] for p in active)}/{len(active)}",
                  "Herkes rolünü özelden alıp onaylamadan oyun başlamaz.",
                  "Bir tur soru/yanıt, 90 saniye tartışma, gizli oy.",
                  "Katı çoğunluk gerekir; bir kısa rövanş oyu vardır. İkinci eşitlikte casus kazanır."]
    elif view["phase"] == "delivery":
        ready = sum(p["confirmed"] for p in active)
        lines += [f"🔐 Roller özelden gönderiliyor. Onay: {ready}/{len(active)}",
                  "Rol mesajındaki Onayla düğmesini kullan."]
    elif view["phase"] == "question":
        asker = view["question_order"][view["turn_index"]]
        lines += [f"Soran: <b>{_name(view, asker)}</b>"]
        if view["target_id"]:
            lines += [f"Yanıtlayan: <b>{_name(view, view['target_id'])}</b>",
                      "Sıra: yanıt" if view["question_pending"] else "Sıra: soru"]
        else:
            lines += ["Soran kişi panelden yanıtlayanı seçsin."]
    elif view["phase"] == "discussion":
        lines += ["Tartışma başladı. Süre bitince gizli oy açılır."]
    elif view["phase"] == "vote":
        lines += [f"🔒 Gizli oy {view['vote_round']}/2 · {view['vote_count']}/{len(active)} oy",
                  "Kendine oy veremezsin. Katı çoğunluk gerekir."]
    elif view["phase"] == "finished":
        lines += ["🏁 Casus kazandı." if view["winner"] == "spy" else "🏁 Oyuncular kazandı.",
                  f"Casus: {_name(view, view['spy_id'])}",
                  f"Mekân: {escape(view['location'])}"]
    else:
        lines += ["Oyun iptal edildi. Ödül verilmez."]
    if view.get("deadline") and view["phase"] not in {"finished", "cancelled"}:
        import time
        lines.append(f"Kalan: {max(0, math.ceil(view['deadline'] - time.time()))} saniye")
    lines.append("Oyuncular: " + ", ".join(_name(view, p["id"]) + (" · hazır" if view["phase"] == "lobby" and p["ready"] else "") for p in active))
    return "\n".join(lines)


def spy_keyboard(state):
    view = spy.public_state(state)
    def button(label, action):
        return InlineKeyboardButton(label, callback_data=callback_data(state, action))
    rows = []
    if view["phase"] == "lobby":
        rows += [[button("Katıl", "join"), button("Başlat", "start")],
                 [button("🔐 Özel sohbeti hazırla", "ready")]]
    elif view["phase"] == "question" and view["target_id"] is None:
        rows += [[button(p["name"][:22], f"t{_b36(p['id'])}.{_b36(view['turn_index'])}")]
                 for p in view["players"] if p["active"] and p["id"] != view["question_order"][view["turn_index"]]]
    elif view["phase"] == "vote":
        rows += [[button(p["name"][:22], f"v{_b36(p['id'])}.{_b36(view['vote_round'])}")]
                 for p in view["players"] if p["active"] and p["id"] in view["candidates"]]
    if view["phase"] not in {"finished", "cancelled"}:
        rows += [[button("Yenile", "refresh"), button("Ayrıl", "leave")],
                 [button("Oyunu bitir", "stop")]]
    else:
        rows += [[InlineKeyboardButton("Yeniden oyna", callback_data="game_start_casus"),
                  InlineKeyboardButton("Oyun salonu", callback_data="menu_games:social")]]
    return InlineKeyboardMarkup(rows)


def render_role(role, *, chat_id=None, thread_id=None):
    if role["spy"]:
        return ("🕵️ <b>Casussun.</b> Mekânı bilmiyorsun. Soruları dinle, kimliğini sakla. "
                "Oyun başladıktan sonra aşağıdaki düğmeyle tek mekân tahminini yapabilirsin.")
    return (f"🔐 <b>Rolün: {escape(role['role'])}</b>\n"
            f"Mekân: <b>{escape(role['location'])}</b>\n"
            "Mekânı ve rolünü grupta açıkça yazma; soru ve yanıtlarda dikkatli ol.")


def guess_menu(state, page=0):
    if not 0 <= page < (len(spy.LOCATIONS) + 5) // 6:
        raise ValueError("spy_invalid_action")
    rows = [[InlineKeyboardButton(place, callback_data=callback_data(state, f"s{_b36(index)}"))]
            for index, (place, _) in enumerate(spy.LOCATIONS)
            if page * 6 <= index < (page + 1) * 6]
    navigation = []
    if page:
        navigation.append(InlineKeyboardButton("◀ Önceki", callback_data=callback_data(state, f"g{_b36(page - 1)}")))
    if (page + 1) * 6 < len(spy.LOCATIONS):
        navigation.append(InlineKeyboardButton("Sonraki ▶", callback_data=callback_data(state, f"g{_b36(page + 1)}")))
    if navigation:
        rows.append(navigation)
    return InlineKeyboardMarkup(rows)


def guess_confirmation(state, index):
    place = spy.LOCATIONS[index][0]
    text = (f"🔐 <b>Tek tahmin hakkın</b>\nMekân: <b>{escape(place)}</b>\n\n"
            "Bu seçimi onaylarsan oyun hemen biter. Emin misin?")
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Evet, tahmin et", callback_data=callback_data(state, f"c{_b36(index)}")),
        InlineKeyboardButton("Geri dön", callback_data=callback_data(state, "g0"))]])
    return text, keyboard


def spy_error(code):
    return {
        "spy_player_count": "Başlamak için 4–16 oyuncu gerekir.",
        "spy_not_owner": "Bunu yalnız kurucu veya grup yöneticisi yapabilir.",
        "spy_not_player": "Önce oyuna katıl.",
        "spy_stale_round": "Bu düğme eski oyuna ait.",
        "spy_stale_panel": "Panel değişti. Güncel paneli kullan.",
        "spy_stale_message": "Bu mesaj önceki oyun aşamasına ait.",
        "spy_wrong_phase": "Bu işlem şu anda kullanılamıyor.",
        "spy_expired": "Bu aşamanın süresi doldu.",
        "spy_invalid_vote": "Kendine veya geçersiz bir adaya oy veremezsin.",
        "spy_already_voted": "Bu turdaki oyunu kullandın.",
        "spy_invalid_invite": "Bu özel sohbet bağlantısı senin için geçerli değil.",
        "spy_recent_invite": "Yeni bağlantı için biraz bekle.",
        "spy_not_found": "Açık Casus oyunu yok. Grupta /casus yaz.",
        "spy_dm_not_ready": "Herkes önce özel sohbet bağlantısını açmalı.",
        "spy_guess_forbidden": "Mekânı yalnız casus bir kez tahmin edebilir.",
        "spy_waiting_panel": "Grup paneli henüz hazır değil. Biraz sonra tekrar dene.",
        "spy_guess_not_selected": "Önce özel listeden bir mekân seçip sonra onayla.",
    }.get(str(code), "İşlem yapılamadı. Güncel oyun panelini kullan.")
