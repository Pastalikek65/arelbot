"""Telegram presentation helpers for Arel Tahmin."""

from __future__ import annotations

import html

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from src.games.akinator import ANSWER_CHOICES


_LABELS = {
    "0": "Evet",
    "1": "Hayır",
    "2": "Bilmiyorum",
    "3": "Muhtemelen",
    "4": "Sanmıyorum",
}


def _prefix(state: dict) -> str:
    return f"akinator:{str(state.get('round_id') or '')}:{int(state.get('revision', 0) or 0)}"


def akinator_keyboard(state: dict) -> InlineKeyboardMarkup:
    prefix = _prefix(state)
    status = str(state.get("status") or "pending")
    rows: list[list[InlineKeyboardButton]] = []
    if status == "playing":
        rows.extend(
            [
                [
                    InlineKeyboardButton(_LABELS[key], callback_data=f"{prefix}:answer:{key}")
                    for key in ANSWER_CHOICES[:3]
                ],
                [
                    InlineKeyboardButton(_LABELS[key], callback_data=f"{prefix}:answer:{key}")
                    for key in ANSWER_CHOICES[3:]
                ],
                [InlineKeyboardButton("Geri al", callback_data=f"{prefix}:back")],
            ]
        )
    elif status == "proposition":
        rows.append(
            [
                InlineKeyboardButton("✅ Bildin", callback_data=f"{prefix}:guess:yes"),
                InlineKeyboardButton("❌ Değil", callback_data=f"{prefix}:guess:no"),
            ]
        )
    if status not in {"finished", "failed"}:
        rows.append([InlineKeyboardButton("🛑 Oyunu bırak", callback_data=f"{prefix}:stop")])
    else:
        rows.append([InlineKeyboardButton("🔄 Yeni tur", callback_data="game_start_akinator")])
    return InlineKeyboardMarkup(rows)


def render_akinator_text(state: dict) -> str:
    status = str(state.get("status") or "pending")
    progress = float(state.get("progress") or 0)
    question = html.escape(str(state.get("question") or "Hazırlanıyorum…"))
    proposition = html.escape(str(state.get("proposition") or "Aklındaki kişiyi"))
    description = html.escape(str(state.get("proposition_description") or ""))
    owner = html.escape(str(state.get("owner_name") or "Oyuncu"))
    if status == "pending":
        body = "sorular hazırlanıyor…"
    elif status == "proposition":
        body = f"aklımdaki kişi: <b>{proposition}</b>\n{description}\n\nbildim mi?"
    elif status == "finished":
        body = f"tur tamamlandı · tahminim: <b>{proposition}</b>"
    elif status == "failed":
        body = "Oturum kesildi veya servis yanıt vermedi. Yeni tur başlatabilirsin."
    else:
        body = f"<b>{question}</b>\n\nSoru {int(state.get('questions', 0) or 0) + 1} · ilerleme <code>{progress:.0f}%</code>"
    return f"🧞 <b>Akinatör</b> · {owner}\n\n{body}"
