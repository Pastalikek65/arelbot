"""Telegram presentation for the Arel Yaş Tahmini game."""

from __future__ import annotations

from html import escape
import re
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def _group_credit(value: str) -> str:
    # Some Commons author fields themselves contain external URLs. Keep the
    # original attribution in the private directory, with no auto-links here.
    text = re.sub(r"(?:https?://|www\.)[^\s<>]+", "", str(value or ""), flags=re.IGNORECASE)
    return escape(" ".join(text.split()))


def _prefix(state: dict) -> str:
    return f"guessage:{str(state.get('round_id') or '')}:{int(state.get('round', 1) or 1)}"


def guess_age_keyboard(state: dict) -> InlineKeyboardMarkup:
    prefix = _prefix(state)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Nasıl oynanır?", callback_data=f"{prefix}:help"),
         InlineKeyboardButton("Düğmeyle tahmin", callback_data=f"{prefix}:choose")],
        [InlineKeyboardButton("Kalan süre", callback_data=f"{prefix}:status"),
         InlineKeyboardButton("Oyunu bitir", callback_data=f"{prefix}:stop")],
    ])


def guess_age_band_keyboard(state: dict, *, user_id: int) -> InlineKeyboardMarkup:
    prefix = _prefix(state)
    rows = [
        [InlineKeyboardButton(f"{low}–{low + 19}", callback_data=f"{prefix}:band:{int(user_id)}:{low}")
         for low in range(start, start + 60, 20)]
        for start in (1, 61)
    ]
    rows.append([InlineKeyboardButton("Vazgeç", callback_data=f"{prefix}:dismiss:{int(user_id)}")])
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
    rows.append([
        InlineKeyboardButton("◀️ Aralıklar", callback_data=f"{prefix}:bands:{int(user_id)}"),
        InlineKeyboardButton("Vazgeç", callback_data=f"{prefix}:dismiss:{int(user_id)}"),
    ])
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
        if result and len(result) > 20:
            lines.append(f"\nİlk 20 oyuncu gösteriliyor; maçta {len(result)} oyuncu var.")
        if state.get("pending_scores"):
            lines.append("\nPuan kaydı tamamlanıyor. Bot yeniden deneyecek.")
        if not result:
            lines.append("Bu maçta puan alan oyuncu yok.")
        return "\n".join(lines)
    credit = _group_credit(state.get("asset_credit"))
    credit_line = f"Fotoğraf: {credit}"
    return (
        f"🎂 <b>Yaş Tahmini</b> · Tur {round_no}/{rounds}\n\n"
        "Fotoğraftaki kişi, fotoğraf çekildiğinde kaç yaşındaydı?\n"
        "Tahminini <b>sayı olarak gruba yaz</b>. Örneğin: <code>35</code>\n\n"
        f"⏱ {int(state.get('round_seconds', 30))} saniye · Kişi başı tek tahmin\n"
        f"Bu tur {len(state.get('answers', {}))} tahmin · Maçta {players} oyuncu\n"
        "Puanlar süre bitince açıklanacak.\n\n"
        f"<i>{credit_line}</i>\nKaynaklar: özel sohbette /yas kaynaklar"
    )


def render_guess_age_round_result(state: dict, ranked: list[dict]) -> str:
    closed = state.get("history", [{}])[-1]
    target = int(closed.get("target_age", 0) or 0)
    person = escape(str(closed.get("person_name") or ""))
    photo_date = escape(str(closed.get("photo_date") or ""))
    lines = [f"🎯 <b>{person}</b> · fotoğraf tarihinde <b>{target} yaşında</b>",
             f"📅 {photo_date}", ""]
    if not ranked:
        lines.append("Bu turda tahmin yapılmadı.")
        lines.append("\nSonraki ekran birazdan açılacak.")
        return "\n".join(lines)
    best = abs(target - int(ranked[0].get("guess", 0)))
    lines.append("<b>Tam isabet!</b>" if best == 0 else f"<b>En yakın tahmin: {best} yaş fark</b>")
    lines.append("Tam isabet 100 puan · Her yaş farkı −5 puan\n")
    for index, row in enumerate(ranked[:10], 1):
        difference = abs(target - int(row.get("guess", 0)))
        lines.append(f"{index}. {escape(str(row.get('display_name') or 'Oyuncu')[:32])} · {int(row.get('guess', 0))} yaş · {difference} fark · <b>+{int(row.get('score', 0))}</b>")
    if len(ranked) > 10:
        lines.append(f"\nİlk 10 tahmin gösteriliyor; bu tur {len(ranked)} kişi tahmin yaptı.")
    lines.append("\nSonuçları incele; sonraki ekran en az 8 saniye sonra açılacak.")
    return "\n".join(lines)


def render_guess_age_photo_answer(state: dict) -> str:
    closed = state.get("history", [{}])[-1]
    person = escape(str(closed.get("person_name") or "Fotoğraftaki kişi")[:100])
    credit = _group_credit(closed.get("asset_credit"))
    photo_date = escape(str(closed.get("photo_date") or ""))
    return (
        f"🎂 <b>Tur {int(closed.get('round', 1))} tamamlandı</b>\n\n"
        f"{person} · <b>{int(closed.get('target_age', 0))} yaşında</b>\n"
        f"Fotoğraf tarihi: {photo_date}\n<i>Fotoğraf: {credit}</i>"
        + "\nKaynaklar: özel sohbette /yas kaynaklar"
    )


def render_age_sources(assets, page: int = 0):
    """Private, paginated attribution directory; never sent automatically to groups."""
    size = 3
    pages = max(1, (len(assets) + size - 1) // size)
    page = max(0, min(int(page), pages - 1))
    lines = ["<b>Yaş Tahmini · Fotoğraf kaynakları</b>",
             "Fotoğraflar Wikimedia Commons'tan alınmıştır. Kaynak ve lisans bilgileri burada listelenir.", ""]
    for item in assets[page * size:(page + 1) * size]:
        lines.extend([f"<b>{escape(item.person_name)}</b> · {escape(item.photo_date)}",
                      escape(item.credit)])
        for label, url, prefix in (("Fotoğraf kaynağı", item.source_url, "https://commons.wikimedia.org/wiki/File:"),
                                   ("Lisans", item.license_url, "https://creativecommons.org/")):
            if url.startswith(prefix):
                lines.append(f'<a href="{escape(url, quote=True)}">{label}</a>')
        lines.append("")
    lines.append(f"Sayfa {page + 1}/{pages}")
    buttons = []
    if page:
        buttons.append(InlineKeyboardButton("Önceki", callback_data=f"guessage:sources:{page - 1}"))
    if page + 1 < pages:
        buttons.append(InlineKeyboardButton("Sonraki", callback_data=f"guessage:sources:{page + 1}"))
    return "\n".join(lines), InlineKeyboardMarkup([buttons] if buttons else [])

