"""Group-safe team Tabu panels and private narrator cards."""

from html import escape
import math
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from src.games.tabu import public_state
from src.services.tabu_service import _base36


TEAMS = {"red": "🔴 Kırmızı", "blue": "🔵 Mavi"}


def callback_data(state, action):
    return f"tb:{_base36(abs(int(state['chat_id'])))}:{_base36(int(state.get('thread_id') or 0))}:{state['round_id']}:{state.get('card_id') or '0'}:{action}"


def render_tabu(state, *, now=None, notice=None):
    view = public_state(state)
    clock = time.time() if now is None else now
    phase = view["phase"]
    lines = ["🎭 <b>Tabu</b>", "",
             f"🔴 <b>{view['team_scores']['red']}</b>  —  <b>{view['team_scores']['blue']}</b> 🔵", ""]
    for team in ("red", "blue"):
        players = [p for p in view["players"] if p["team"] == team and p.get("active", True)]
        roster = ", ".join(escape(str(p["name"])) + (" · hazır" if p.get("ready") else " · hazır değil") for p in players)
        lines.append(f"{TEAMS[team]}: {roster or 'Oyuncu bekleniyor'}")
    lines.append("")
    if phase == "lobby":
        lines.extend(["<b>Takımını seç ve özel sohbet hazırlığını tamamla.</b>",
                      "4–20 oyuncu · takım başına en az 2 kişi · takım farkı en çok 1.",
                      "Herkes hazır olduğunda oyunu kuran kişi maçı başlatabilir.",
                      "Anlatıcı sırayla değişir. Takımlar eşit sayıda kart oynar; her kart için 60 saniye ve takım başına 2 pas hakkı vardır.",
                      "Doğru tahmin +1 · Yasak ifade −1 · Pas veya süre aşımı 0 puan."])
    elif phase in {"card_delivery", "active"}:
        narrator = next((p for p in view["players"] if int(p["id"]) == int(view["narrator_id"])), {})
        lines.extend([f"Sıra: <b>{TEAMS[view['active_team']]}</b>",
                      f"Anlatıcı: <b>{escape(str(narrator.get('name', 'Oyuncu')))}</b>",
                      f"Kartlar: 🔴 {view['cards_played']['red']}/{view['quota']} · 🔵 {view['cards_played']['blue']}/{view['quota']}",
                      f"Kalan pas: 🔴 {view['passes_left']['red']} · 🔵 {view['passes_left']['blue']}"])
        if phase == "card_delivery":
            lines.extend(["", "🔐 Kart anlatıcıya özel sohbetten gönderilir.",
                          "Anlatıcı kartı gördüğünü onayladığında süre başlayacak.",
                          "Kart ulaşmadıysa anlatıcı ‘Kartımı gönder’ düğmesini kullanabilir."])
        else:
            remaining = max(0, math.ceil(float(view["deadline"]) - clock))
            lines.extend(["", f"⏱ Kalan süre: <b>{remaining} saniye</b>",
                          "Anlatıcı ipucunu, takım arkadaşları tahminlerini gruba yazar."])
    elif phase in {"appeal", "adjudication"}:
        outcomes = {"correct": "Doğru tahmin · +1", "forbidden": "Yasak ifade · −1", "pass": "Pas · 0", "timeout": "Süre doldu · 0", "neutral": "Kart puansız sayıldı · 0"}
        lines.extend([f"<b>{outcomes.get(view.get('pending_outcome'), 'Kart kapandı')}</b>",
                      "İnceleme tamamlandığında sonuç kesinleşir."])
        reason = str(view.get("pending_reason") or "")
        if ":" in reason:
            lines.append(f"Tespit edilen ifade: <code>{escape(reason.split(':', 1)[1])}</code>")
        if view.get("pending_evidence"):
            lines.append(f"İlgili mesaj: {escape(str(view['pending_evidence']))}")
        if phase == "appeal":
            lines.append(f"İtiraz için kalan süre: {max(0, math.ceil(view['appeal_until'] - clock))} saniye.")
        else:
            lines.append("Maçta oynamayan bir grup yöneticisi sonucu onaylayabilir veya kartı puansız sayabilir.")
            lines.append(f"Karar için {max(0, math.ceil(view['judge_deadline'] - clock))} saniye kaldı. Karar verilmezse ilk sonuç geçerli olur.")
    elif phase == "finished":
        winner = view.get("winner_team")
        lines.append(f"🏆 <b>{TEAMS[winner]} kazandı.</b>" if winner else "🤝 <b>Maç berabere bitti.</b>")
        lines.append("Puan kaydı henüz tamamlanmadı. Bot yeniden deneyecek." if view.get("score_pending")
                     else "Maç puanları kaydedildi.")
    else:
        lines.extend(["<b>Oyun iptal edildi.</b>", "Bu maç için puan veya jeton verilmez."])
        if view.get("pending_reason") == "card_ready_timeout":
            lines.append("Anlatıcı kartı 120 saniye içinde onaylamadı. /tabu ile yeni oyun açabilirsin.")
    if notice:
        lines.extend(["", escape(str(notice))])
    return "\n".join(lines)


def tabu_keyboard(state):
    def button(label, action):
        return InlineKeyboardButton(label, callback_data=callback_data(state, action))
    phase = state["phase"]
    rows = []
    if phase == "lobby":
        rows += [[button("🔴 Kırmızı takım", "red"), button("🔵 Mavi takım", "blue")],
                 [button("🔐 Özel sohbeti hazırla", "ready")], [button("Maçı başlat", "start")]]
    elif phase == "card_delivery":
        rows += [[button("🔐 Kartımı gönder", "card")]]
    elif phase == "active":
        rows += [[button("Pas geç", "pass")]]
    elif phase == "appeal":
        rows += [[button("İtiraz et", "appeal")]]
    elif phase == "adjudication":
        rows += [[button("Sonucu onayla", "uphold"), button("Puansız say", "neutral")]]
    if phase not in {"finished", "cancelled"}:
        rows += [[button("Yenile", "refresh"), button("Ayrıl", "leave")], [button("Oyunu bitir", "stop")]]
    else:
        rows += [[button("Yeniden oyna", "replay")]]
    return InlineKeyboardMarkup(rows)


def render_private_card(card):
    forbidden = "\n".join(f"• {escape(str(word))}" for word in card["forbidden"])
    return (f"🔐 <b>Anlatıcı kartın</b>\n\n"
            f"Hedef: <b>{escape(str(card['target']))}</b>\nKategori: {escape(str(card['category']))}\n\n"
            f"<b>Yasak ifadeler</b>\n{forbidden}\n\n"
            "Hedefi, yasaklı ifadeleri veya bunların açık harflemesini kullanma. "
            "İpucunu gruba yaz; kartı paylaşma. Kartı gördüğünü onayladığında 60 saniye başlayacak.")


def tabu_error(code):
    return {
        "tabu_unbalanced_teams": "Her takımda en az iki kişi olmalı; takım farkı en fazla bir olabilir.",
        "tabu_not_ready": "Bütün oyuncular önce özel sohbet hazırlığını tamamlamalı.",
        "tabu_not_owner": "Bu işlemi oyunu kuran kişi veya grup yöneticisi yapabilir.",
        "tabu_stale_round": "Bu düğme eski bir maça ait. /tabu ile güncel oyunu aç.",
        "tabu_stale_card": "Bu kart kapandı. Güncel oyun panelini kullan.",
        "tabu_not_narrator": "Bu kartın anlatıcısı sen değilsin.",
        "tabu_not_player": "Önce oyun panelinden kırmızı veya mavi takıma katıl.",
        "tabu_no_passes": "Takımının iki pas hakkı da kullanıldı.",
        "tabu_not_neutral_judge": "Bu kararı yalnız maçta oynamayan bir grup yöneticisi verebilir.",
        "tabu_invalid_invite": "Bu bağlantı senin için geçerli değil. Gruptaki Özel sohbeti hazırla düğmesinden yenisini al.",
        "tabu_invite_recent": "Yeni bağlantı için biraz bekle. Önceki hazırlık bağlantın varsa onu kullanabilirsin.",
        "tabu_invite_pending": "Bu istek zaten işlendi. Bağlantı görünmüyorsa gruptaki hazırlık düğmesine yeniden bas.",
        "tabu_recent_invite": "Yeni bağlantı için biraz bekle. Önceki hazırlık bağlantın varsa onu kullanabilirsin.",
        "tabu_pending_invite": "Bu istek zaten işlendi. Bağlantı görünmüyorsa gruptaki hazırlık düğmesine yeniden bas.",
        "tabu_start_pending": "Kartın başlangıç onayı işleniyor veya süre dolmuş. Güncel paneli kontrol et.",
        "tabu_already_delivered": "Kart özel sohbetine gönderildi. Oradaki onay düğmesini kullan.",
        "tabu_not_found": "Açık bir Tabu oyunu yok. Grupta /tabu ile yeni oyun aç.",
        "tabu_not_delivered": "Kart henüz teslim edilmedi. Gruptan yeniden gönderilmesini iste.",
    }.get(str(code), "Bu işlem mevcut oyun aşamasında kullanılamıyor. Güncel paneli yenile.")
