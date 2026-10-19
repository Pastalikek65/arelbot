"""Turkish Telegram presentation for the shared UNO domain state."""

from __future__ import annotations

from html import escape
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from src.games.uno import COLOR_LABELS, SPECIAL_LABELS, can_play

HAND_PAGE_SIZE = 5


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
        lines.append("<b>Masa açık</b> · 2–8 oyuncu\nKatıl düğmesine dokunun. Masayı kuran oyuncu maçı başlatır.")
    elif status == "playing":
        current = next((p for p in players if int(p.get("user_id", 0)) == int(state.get("current_player_id", 0) or 0)), None)
        current_name = escape(str((current or {}).get("name") or "oyuncu"))
        top = state.get("discard", [])[-1] if state.get("discard") else {}
        color = COLOR_LABELS.get(str(state.get("current_color") or top.get("color")), "—")
        lines.append(f"▶️ Sıra: <b>{current_name}</b>\nÜst kart: <b>{escape(card_label(top))}</b>")
        lines.append(f"Aktif renk: <b>{escape(color)}</b> · Hamle süresi {int(state.get('turn_seconds', 60))} sn")
        if int(state.get("pending_draw", 0) or 0):
            lines.append(f"+{int(state['pending_draw'])} kart cezası bekliyor")
        penalty = state.get("last_penalty") or {}
        if penalty:
            target = next((p for p in players if int(p.get("user_id", 0)) == int(penalty.get("user_id", 0))), None)
            lines.append(f"{escape(str((target or {}).get('name') or 'oyuncu'))} +{int(penalty.get('cards', 0))} kart çekti ve turu geçti")
        if state.get("last_timeout_user_id"):
            timed_out = next((p for p in players if int(p.get("user_id", 0)) == int(state["last_timeout_user_id"])), None)
            lines.append(f"⏱ {escape(str((timed_out or {}).get('name') or 'oyuncu'))} için süre doldu; sıra ilerledi")
    elif status == "finished":
        winner = next((p for p in players if int(p.get("user_id", 0)) == int(state.get("winner_id", 0) or 0)), None)
        title = "Maçın kazananı" if state.get("match_complete") else "Turun kazananı"
        lines.append(f"🏆 {title}: <b>{escape(str((winner or {}).get('name') or 'Oyuncu'))}</b>")
        lines.append(f"Bu tur +{int(state.get('round_points', 0))} puan")
        if not state.get("match_complete"):
            lines.append("Puanlar korunur. Masayı kuran oyuncu sonraki turu başlatabilir.")
    elif status == "expired":
        lines.append("⏱ Masa 15 dakika hareketsiz kaldığı için kapatıldı.")
    else:
        lines.append("oyun kapandı")
    lines.append("")
    lines.append("<b>Oyuncular · maç puanları</b>")
    for player in players:
        marker = " ▶️" if int(player.get("user_id", 0)) == int(state.get("current_player_id", 0) or 0) else ""
        ready = " ✅" if player.get("ready") else ""
        lines.append(f"• {escape(str(player.get('name') or 'Oyuncu'))}{marker}{ready} · {len(player.get('hand', []))} kart · {int(player.get('score', 0))} puan")
    if status == "playing":
        lines.extend(["", "Kartlarını görmek için <b>Elim</b> düğmesine dokun. Kart numarası yalnızca sana gösterilir."])
    if note:
        lines.extend(["", escape(str(note))])
    return "\n".join(lines)


def render_uno_hand_alert(state: dict[str, Any], *, user_id: int, page: int = 0) -> str:
    player = next((p for p in state.get("players", []) if int(p.get("user_id", 0)) == int(user_id)), None)
    if player is None:
        return "Bu masada oyuncu değilsin."
    hand = player.get("hand", [])
    if page < 0 or page * HAND_PAGE_SIZE >= max(1, len(hand)):
        return "Bu el sayfası yok."
    lines = [f"🃏 Elindeki kartlar ({len(hand)}):"]
    for index in range(page * HAND_PAGE_SIZE, min((page + 1) * HAND_PAGE_SIZE, len(hand))):
        card = hand[index]
        playable = (int(state.get("current_player_id", 0) or 0) == int(user_id)
                    and not state.get("awaiting_color") and can_play(state, card, player_id=user_id))
        lines.append(f"{index + 1}. {card_label(card)}{' ✓' if playable else ''}")
    if not hand:
        lines.append("Kart kalmadı.")
    return "\n".join(lines)[:200]


def uno_keyboard(state: dict[str, Any], *, user_id: int | None = None, miniapp_url: str | None = None, chat_type: str | None = None) -> InlineKeyboardMarkup:
    status = str(state.get("status") or "lobby")
    rid = str(state.get("round_id") or "legacy")
    prefix = f"uno:{rid}:{int(state.get('revision', 0))}"
    rows: list[list[InlineKeyboardButton]] = []
    if status == "lobby":
        rows.append([InlineKeyboardButton("➕ Katıl", callback_data=f"{prefix}:join")])
        rows.append([InlineKeyboardButton("▶️ Maçı başlat", callback_data=f"{prefix}:start")])
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
                playable = [(index, card) for index, card in enumerate(me.get("hand", []), 1)
                            if can_play(state, card, player_id=current_id)]
                for index in range(0, len(playable), 2):
                    rows.append([
                        InlineKeyboardButton(f"🃏 #{position}", callback_data=f"{prefix}:playpos:{position}")
                        for position, card in playable[index:index + 2]
                    ])
            if state.get("awaiting_color"):
                rows.append([InlineKeyboardButton(label, callback_data=f"{prefix}:color:{color}") for color, label in COLOR_LABELS.items()])
            else:
                rows.append([InlineKeyboardButton("🃏 Kart çek", callback_data=f"{prefix}:draw")])
                if next((p for p in state.get("players", []) if int(p.get("user_id", 0)) == current_id), {}).get("drawn_card_id"):
                    rows.append([InlineKeyboardButton("⏭️ Pas geç", callback_data=f"{prefix}:skip")])
        rows.append([InlineKeyboardButton("📣 UNO de", callback_data=f"{prefix}:call")])
        missed = [p for p in state.get("players", []) if len(p.get("hand", [])) == 1 and not p.get("uno_called") and state.get("uno_vulnerable_id") == p.get("user_id")]
        if missed:
            rows.append([
                InlineKeyboardButton(
                    f"⚠️ {str(player.get('name') or 'oyuncu')[:18]} UNO demedi",
                    callback_data=f"{prefix}:challenge:{int(player['user_id'])}",
                )
                for player in missed[:2]
            ])
        rows.append([InlineKeyboardButton("🛑 Masayı kapat", callback_data=f"{prefix}:stop")])
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
    return InlineKeyboardMarkup(rows)


__all__ = ["card_label", "render_uno_text", "render_uno_hand_alert", "uno_keyboard"]
