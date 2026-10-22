"""Turkish Telegram presentation for the shared UNO domain state."""

from __future__ import annotations

from html import escape
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from src.games.uno import COLOR_LABELS, SPECIAL_LABELS, can_play as legacy_can_play


def can_play(state, card, *, player_id=None):
    if state.get("engine") == "maildian":
        from src.games.uno_upstream import can_play as source_can_play
        return source_can_play(state, card, player_id=player_id)
    return legacy_can_play(state, card, player_id=player_id)

HAND_PAGE_SIZE = 4
CARD_POSITION_PAGE_SIZE = 12


def _display_name(player: dict[str, Any] | None) -> str:
    return escape(str((player or {}).get("name") or "Oyuncu")[:20])


def card_label(card: dict[str, Any]) -> str:
    color = COLOR_LABELS.get(str(card.get("color")), "Siyah")
    kind = str(card.get("kind"))
    value = SPECIAL_LABELS.get(kind, kind)
    return f"{color} {value}"


def render_uno_text(state: dict[str, Any], *, user_id: int | None = None, note: str | None = None) -> str:
    status = str(state.get("status") or "lobby")
    players = state.get("players") or []
    lines = ["🃏 <b>UNO</b>", f"Tur {max(1, int(state.get('round_number', 1)))} · Maç hedefi {int(state.get('match_target', 500))} puan", ""]
    if status == "lobby":
        lines.append("<b>Oyuncular bekleniyor</b> · 2–8 kişi\nKatıl düğmesine bas. En az iki kişi olduğunda oyunu kuran kişi maçı başlatabilir.")
    elif status == "playing":
        current = next((p for p in players if int(p.get("user_id", 0)) == int(state.get("current_player_id", 0) or 0)), None)
        current_name = _display_name(current)
        top = state.get("discard", [])[-1] if state.get("discard") else {}
        color = COLOR_LABELS.get(str(state.get("current_color") or top.get("color")), "—")
        lines.append(f"▶️ Sıra: <b>{current_name}</b> · Yön: {'ileri' if int(state.get('direction', 1)) == 1 else 'geri'}")
        lines.append(f"Üst kart: <b>{escape(card_label(top))}</b> · Aktif renk: <b>{escape(color)}</b>")
        lines.append(f"Hamle süresi: {int(state.get('turn_seconds', 60))} sn")
        if state.get("awaiting_color"):
            lines.append(f"<b>{current_name} renk seçmeli.</b> Seçimden sonra oyun ilerler.")
        elif (current or {}).get("drawn_card_id"):
            lines.append(f"<b>{current_name}</b> yeni çektiği kartı oynayabilir veya pas geçebilir.")
        elif (current or {}).get("draw_attempted"):
            lines.append(f"<b>{current_name}</b> kart çekmeyi denedi; pas geçebilir.")
        else:
            lines.append(f"<b>{current_name}</b> uygun kartını oynamalı veya kart çekmeli.")
        if int(state.get("pending_draw", 0) or 0):
            count = int(state["pending_draw"])
            if state.get("engine") == "maildian":
                lines.append(f"Bekleyen ceza: +{count}. " + (
                    "Sıradaki oyuncu +4 için blöf itirazı yapabilir veya cezayı çekebilir."
                    if top.get("kind") == "draw4" else
                    "Sıradaki oyuncu +2 ekleyebilir veya cezayı çekip sırasını geçebilir."
                ))
            else:
                lines.append(f"Renk seçiminden sonra sıradaki oyuncu +{count} kart çekecek ve sırası geçecek.")
        penalty = state.get("last_penalty") or {}
        if penalty:
            target = next((p for p in players if int(p.get("user_id", 0)) == int(penalty.get("user_id", 0))), None)
            lines.append(f"{_display_name(target)} +{int(penalty.get('cards', 0))} kart çekti ve turu geçti")
        if state.get("last_timeout_user_id"):
            timed_out = next((p for p in players if int(p.get("user_id", 0)) == int(state["last_timeout_user_id"])), None)
            lines.append(f"⏱ {_display_name(timed_out)} için süre doldu; sıra ilerledi")
    elif status == "finished":
        winner = next((p for p in players if int(p.get("user_id", 0)) == int(state.get("winner_id", 0) or 0)), None)
        title = "Maçın kazananı" if state.get("match_complete") else "Turun kazananı"
        lines.append(f"🏆 {title}: <b>{_display_name(winner)}</b>")
        lines.append(f"Bu tur +{int(state.get('round_points', 0))} puan")
        if not state.get("match_complete"):
            lines.append("Puanlar korunur. Masayı kuran oyuncu sonraki turu başlatabilir.")
    elif status == "expired":
        lines.append("⏱ Masa 15 dakika hareketsiz kaldığı için kapatıldı.")
    else:
        lines.append("Oyun sona erdi.")
    lines.append("")
    lines.append("<b>Oyuncular · maç puanları</b>")
    for player in players:
        marker = " ▶️" if int(player.get("user_id", 0)) == int(state.get("current_player_id", 0) or 0) else ""
        ready = " ✅" if player.get("ready") else ""
        lines.append(f"• {_display_name(player)}{marker}{ready} · {len(player.get('hand', []))} kart · {int(player.get('score', 0))} puan")
    if status == "playing":
        lines.extend(["", (
            "<b>Kartlarımı aç</b> ile elini gör. Renkli kartlar oynanabilir; soluk kartlar bu sırada oynanamaz."
            if state.get("inline_query") else
            "<b>Elim</b> düğmesinden kartlarını gör. Açılan kart listesi yalnız sana gösterilir."
        )])
    if note:
        lines.extend(["", escape(str(note))])
    return "\n".join(lines)


def render_uno_hand_alert(state: dict[str, Any], *, user_id: int, page: int = 0) -> str:
    player = next((p for p in state.get("players", []) if int(p.get("user_id", 0)) == int(user_id)), None)
    if player is None:
        return "Bu oyunda yer almıyorsun. Yeni oyuna Katıl düğmesiyle katılabilirsin."
    hand = player.get("hand", [])
    if page < 0 or page * HAND_PAGE_SIZE >= max(1, len(hand)):
        return "Bu kart sayfası yok. Güncel Elim düğmesini kullan."
    current_turn = int(state.get("current_player_id", 0) or 0) == int(user_id)
    drawn_id = str(player.get("drawn_card_id") or "")
    lines = [f"🃏 Elindeki kartlar ({len(hand)}) · sayfa {page + 1}:"]
    if state.get("awaiting_color") and current_turn:
        lines.append("Önce renk seç.")
    elif drawn_id and current_turn:
        lines.append("Yeni çekilen kartı oyna veya pas geç.")
    elif player.get("draw_attempted") and current_turn:
        lines.append("Kart çekişi tamamlandı; pas geç.")
    elif current_turn:
        lines.append("✓ oynanabilir · istersen kart çek.")
    else:
        lines.append("Sıranı bekle.")
    for index in range(page * HAND_PAGE_SIZE, min((page + 1) * HAND_PAGE_SIZE, len(hand))):
        card = hand[index]
        playable = (current_turn and not state.get("awaiting_color")
                    and (not drawn_id or str(card.get("id")) == drawn_id)
                    and can_play(state, card, player_id=user_id))
        lines.append(f"{index + 1}. {card_label(card)}{' ✓' if playable else ''}{' (yeni çekilen)' if drawn_id == str(card.get('id')) else ''}")
    if not hand:
        lines.append("Kart kalmadı.")
    return "\n".join(lines)[:200]


def uno_keyboard(state: dict[str, Any], *, user_id: int | None = None, miniapp_url: str | None = None,
                 chat_type: str | None = None, card_page: int = 0) -> InlineKeyboardMarkup:
    status = str(state.get("status") or "lobby")
    rid = str(state.get("round_id") or "legacy")
    prefix = f"uno:{rid}:{int(state.get('revision', 0))}"
    rows: list[list[InlineKeyboardButton]] = []
    vulnerable = next((p for p in state.get("players", [])
                       if p.get("user_id") == state.get("uno_vulnerable_id")
                       and len(p.get("hand", [])) == 1 and not p.get("uno_called")), None)
    if status == "lobby":
        rows.append([InlineKeyboardButton("➕ Katıl", callback_data=f"{prefix}:join")])
        rows.append([InlineKeyboardButton("▶️ Maçı başlat", callback_data=f"{prefix}:start")])
        rows.append([InlineKeyboardButton("Oyunu bitir", callback_data=f"{prefix}:stop")])
    elif status == "playing" and state.get("inline_query"):
        rows.append([InlineKeyboardButton("🃏 Kartlarımı aç", switch_inline_query_current_chat=state["inline_query"])])
        rows.append([InlineKeyboardButton("📣 UNO de", callback_data=f"{prefix}:call")])
        if vulnerable:
            rows.append([InlineKeyboardButton(
                f"⚠️ {str(vulnerable.get('name') or 'oyuncu')[:18]} UNO demedi",
                callback_data=f"{prefix}:challenge:{int(vulnerable['user_id'])}")])
        rows.append([InlineKeyboardButton("🛑 Masayı kapat", callback_data=f"{prefix}:stop")])
    elif status == "playing":
        current_id = int(state.get("current_player_id", 0) or 0)
        max_hand = max((len(p.get("hand", [])) for p in state.get("players", [])), default=0)
        for start in range(0, max(1, (max_hand + HAND_PAGE_SIZE - 1) // HAND_PAGE_SIZE), 4):
            rows.append([
                InlineKeyboardButton(f"👁 Elim {page + 1}", callback_data=f"{prefix}:hand:{page}")
                for page in range(start, min(start + 4, max(1, (max_hand + HAND_PAGE_SIZE - 1) // HAND_PAGE_SIZE)))
            ])
        me = next((p for p in state.get("players", []) if int(p.get("user_id", 0)) == current_id), None)
        if me is not None:
            if not state.get("awaiting_color"):
                # Keep hand composition private; the personal Elim alert marks
                # playable cards and the domain validates each group click.
                hand_size = len(me.get("hand", []))
                card_pages = max(1, (hand_size + CARD_POSITION_PAGE_SIZE - 1) // CARD_POSITION_PAGE_SIZE)
                card_page = max(0, min(int(card_page), card_pages - 1))
                first = card_page * CARD_POSITION_PAGE_SIZE + 1
                positions = range(first, min(first + CARD_POSITION_PAGE_SIZE, hand_size + 1))
                for index in range(0, len(positions), 2):
                    rows.append([
                        InlineKeyboardButton(f"🃏 #{position}", callback_data=f"{prefix}:playpos:{position}")
                        for position in positions[index:index + 2]
                    ])
                if card_pages > 1:
                    navigation = []
                    if card_page:
                        navigation.append(InlineKeyboardButton("◀️ Önceki kartlar", callback_data=f"{prefix}:cardpage:{card_page - 1}"))
                    navigation.append(InlineKeyboardButton(f"Kartlar {card_page + 1}/{card_pages}", callback_data=f"{prefix}:cardpage:{card_page}"))
                    if card_page + 1 < card_pages:
                        navigation.append(InlineKeyboardButton("Sonraki kartlar ▶️", callback_data=f"{prefix}:cardpage:{card_page + 1}"))
                    rows.append(navigation)
            if state.get("awaiting_color"):
                rows.append([InlineKeyboardButton(label, callback_data=f"{prefix}:color:{color}") for color, label in COLOR_LABELS.items()])
            else:
                if me.get("drawn_card_id") or me.get("draw_attempted"):
                    rows.append([InlineKeyboardButton("⏭️ Pas geç", callback_data=f"{prefix}:skip")])
                else:
                    rows.append([InlineKeyboardButton("🃏 Kart çek", callback_data=f"{prefix}:draw")])
        rows.append([InlineKeyboardButton("📣 UNO de", callback_data=f"{prefix}:call")])
        if vulnerable:
            rows.append([InlineKeyboardButton(
                f"⚠️ {str(vulnerable.get('name') or 'oyuncu')[:18]} UNO demedi",
                callback_data=f"{prefix}:challenge:{int(vulnerable['user_id'])}")])
        rows.append([InlineKeyboardButton("Oyunu bitir", callback_data=f"{prefix}:stop")])
    elif status == "finished" and not state.get("match_complete"):
        rows.append([InlineKeyboardButton("▶️ Sonraki tur", callback_data=f"{prefix}:next")])
        rows.append([InlineKeyboardButton("🛑 Maçı bitir", callback_data=f"{prefix}:stop")])
    else:
        rows.append([InlineKeyboardButton("🃏 Yeni UNO", callback_data="game_start_uno")])
    rows.append([InlineKeyboardButton("📖 Nasıl oynanır?", callback_data=f"{prefix}:rules")])
    if miniapp_url:
        if chat_type == "private":
            rows.append([InlineKeyboardButton("🌐 Mini App'te aç", web_app=WebAppInfo(url=str(miniapp_url)))])
        else:
            rows.append([InlineKeyboardButton("🌐 Mini App'te aç", url=str(miniapp_url))])
    rows.append([InlineKeyboardButton("🎮 Oyun salonu", callback_data="menu_games")])
    if state.get("source_url"):
        rows.append([InlineKeyboardButton("📦 Kaynak kodu · AGPL", url=state["source_url"])])
    return InlineKeyboardMarkup(rows)


__all__ = ["card_label", "render_uno_text", "render_uno_hand_alert", "uno_keyboard"]
