"""Telegram presentation for the Arel Yaş Tahmini game."""

from __future__ import annotations

from html import escape
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def _prefix(state: dict) -> str:
    return f"guessage:{str(state.get('round_id') or '')}:{int(state.get('round', 1) or 1)}"


def guess_age_keyboard(state: dict) -> InlineKeyboardMarkup:
    prefix = _prefix(state)
    rows = [
        [InlineKeyboardButton(f"{low}–{low + 19}", callback_data=f"{prefix}:band:{low}")
         for low in range(start, start + 60, 20)]
        for start in (1, 61)
    ]
    rows.append([InlineKeyboardButton("⏹ Oyunu bitir", callback_data=f"{prefix}:stop")])
    return InlineKeyboardMarkup(rows)


def guess_age_number_keyboard(state: dict, *, user_id: int, low: int) -> InlineKeyboardMarkup:
    if low not in range(1, 121, 20) or int(user_id) <= 0:
        raise ValueError("invalid_age_band")
    prefix = _prefix(state)
    rows = [
        [InlineKeyboardButton(str(age), callback_data=f"{prefix}:pick:{int(user_id)}:{age}")
         for age in range(start, start + 4)]
        for start in range(low, low + 20, 4)
    ]
    rows.append([InlineKeyboardButton("Vazgeç", callback_data=f"{prefix}:dismiss:{int(user_id)}")])
    return InlineKeyboardMarkup(rows)


def render_guess_age_text(state: dict, *, result: list[dict] | None = None) -> str:
    round_no = int(state.get("round", 1) or 1)
    rounds = int(state.get("rounds", 5) or 5)
    players = len(state.get("players", {}) or {})
    if str(state.get("status")) == "finished":
        lines = ["🎂 <b>Yaş Tahmini · Maç sonucu</b>", ""]
        if state.get("finish_reason") == "inactive":
            lines.append("İki tur boyunca tahmin gelmediği için maç tamamlandı.")
        lines.extend([f"{len(state.get('players', {}))} oyuncu · {len(state.get('history', []))} tur", "", "<b>Genel sıralama</b>"])
        for index, row in enumerate((result or [])[:20], 1):
            lines.append(f"{index}. {escape(str(row.get('display_name') or 'Oyuncu')[:32])} · <code>{int(row.get('score', 0))}</code> puan")
        if not result:
            lines.append("Bu maçta puan alan oyuncu yok.")
        return "\n".join(lines)
    credit = escape(str(state.get("asset_credit") or ""))
    raw_source = str(state.get("asset_source_url") or "")
    source = escape(raw_source, quote=True) if raw_source.startswith("https://commons.wikimedia.org/wiki/File:") else ""
    credit_line = f'<a href="{source}">Fotoğraf: {credit}</a>' if source else f"Fotoğraf: {credit}"
    return (
        f"🎂 <b>Yaş Tahmini</b> · Tur {round_no}/{rounds}\n\n"
        "Fotoğrafın çekildiği tarihteki yaşını tahmin et.\n"
        "Aralığı seçip tam yaşa dokun veya <code>/yastahmin 27</code> yaz.\n"
        f"⏱ Tur süresi {int(state.get('round_seconds', 60))} sn · {players} oyuncu\n"
        "Tam isabet 100 puan · Her yaş farkı −5 puan\n\n"
        f"<i>{credit_line}</i>"
    )


def render_guess_age_round_result(state: dict, ranked: list[dict]) -> str:
    closed = state.get("history", [{}])[-1]
    target = int(closed.get("target_age", 0) or 0)
    person = escape(str(closed.get("person_name") or ""))
    photo_date = escape(str(closed.get("photo_date") or ""))
    raw_source = str(closed.get("asset_source_url") or "")
    source = escape(raw_source, quote=True) if raw_source.startswith("https://commons.wikimedia.org/wiki/File:") else ""
    lines = [f"🎯 <b>{person}</b> · fotoğraf tarihinde <b>{target} yaşında</b>",
             f"📅 {photo_date}" + (f" · <a href=\"{source}\">Fotoğraf ve lisans</a>" if source else ""), ""]
    if not ranked:
        lines.append("Bu turda tahmin yapılmadı.")
        return "\n".join(lines)
    lines.append("<b>Tur puanları</b>")
    for index, row in enumerate(ranked[:10], 1):
        lines.append(f"{index}. {escape(str(row.get('display_name') or 'Oyuncu')[:32])} · {int(row.get('guess', 0))} yaş · +{int(row.get('score', 0))}")
    return "\n".join(lines)

