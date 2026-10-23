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
                InlineKeyboardButton("Evet, doğru", callback_data=f"{prefix}:guess:yes"),
                InlineKeyboardButton("Hayır, bu değil", callback_data=f"{prefix}:guess:no"),
            ]
        )
    if status not in {"finished", "failed"}:
        rows.append([InlineKeyboardButton("Oyunu bitir", callback_data=f"{prefix}:stop")])
    else:
        rows.append([InlineKeyboardButton("Yeni oyun", callback_data="game_start_akinator")])
    return InlineKeyboardMarkup(rows)


def render_akinator_text(state: dict) -> str:
    status = str(state.get("status") or "pending")
    progress = float(state.get("progress") or 0)
    question = html.escape(str(state.get("question") or "Hazırlanıyorum…"))
    proposition = html.escape(str(state.get("proposition") or "Aklındaki kişiyi"))
    description = html.escape(str(state.get("proposition_description") or ""))
    owner = html.escape(str(state.get("owner_name") or "Oyuncu"))
    if status == "pending":
        body = "Bir kişi düşün. Sorular hazırlanıyor…"
    elif status == "proposition":
        body = f"Tahminim: <b>{proposition}</b>\n{description}\n\nAklındaki kişi bu mu?"
    elif status == "finished":
        body = f"Oyun tamamlandı.\nSon tahminim: <b>{proposition}</b>"
    elif status == "failed":
        body = ("Bu turda bir tahmin çıkaramadım. Yeni bir kişiyle tekrar deneyebilirsin."
                if state.get("error_code") == "AKINATOR_NO_GUESS" else
                "Akinatör servisi zamanında yanıt vermedi. Yeni oyunla tekrar deneyebilirsin."
                if state.get("error_code") == "AKINATOR_TIMEOUT" else
                "Bu oyuna devam edilemiyor. Yeni oyun düğmesiyle tekrar başlayabilirsin.")
    else:
        body = f"<b>{question}</b>\n\nSoru {int(state.get('questions', 0) or 0) + 1} · İlerleme <code>{progress:.0f}%</code>"
    return f"🧞 <b>Akinatör</b> · {owner}\n\n{body}"
