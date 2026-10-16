"""Telegram presentation helpers for the BÄ°M aktÃ¼el catalogue."""

from __future__ import annotations

from html import escape
from math import ceil
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


PAGE_SIZE = 6


def bim_page_view(result: Any, *, owner_id: int, page: int = 0) -> tuple[str, InlineKeyboardMarkup | None]:
    """Render a bounded page from one typed provider result.

    The provider result is intentionally passed in, so callbacks can use the
    executor cache and never perform HTTP work in the Telegram layer.
    """

    if not getattr(result, "ok", False):
        return "âš ï¸ BÄ°M aktÃ¼el verileri ÅŸu an alÄ±namÄ±yor.", None
    data = getattr(result, "data", None) or {}
    deals = data.get("deals") if isinstance(data, dict) else None
    if not isinstance(deals, list) or not deals:
        return "âš ï¸ BÄ°M iÃ§in gÃ¶sterilecek Ã¼rÃ¼n bulunamadÄ±.", None
    total_pages = max(1, ceil(len(deals) / PAGE_SIZE))
    page = max(0, min(int(page), total_pages - 1))
    rows = deals[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    lines = [f"ğŸ›’ <b>BÄ°M AktÃ¼el ÃœrÃ¼nler</b> Â· Sayfa {page + 1}/{total_pages}", ""]
    for index, item in enumerate(rows, page * PAGE_SIZE + 1):
        if not isinstance(item, dict):
            continue
        title = escape(str(item.get("title") or "AktÃ¼el Ã¼rÃ¼n")[:160])
        unit = escape(str(item.get("unit") or "").strip())
        price = escape(str(item.get("price") or "Fiyat belirtilmemiÅŸ").strip())
        suffix = f" Â· {unit}" if unit else ""
        lines.append(f"<b>{index}.</b> {title}{suffix}\n   ğŸ’³ <b>{price}</b>")
    lines.extend(["", "ğŸ“Œ ResmÃ® BÄ°M kaynaÄŸÄ±ndaki Ã¼rÃ¼n kartlarÄ± ve fiyatlar gÃ¶steriliyor."])
    keyboard: list[list[InlineKeyboardButton]] = []
    navigation: list[InlineKeyboardButton] = []
    if page > 0:
        navigation.append(InlineKeyboardButton("â€¹ Ã–nceki", callback_data=f"bim_page:{int(owner_id)}:{page - 1}"))
    navigation.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="notification_noop"))
    if page + 1 < total_pages:
        navigation.append(InlineKeyboardButton("Sonraki â€º", callback_data=f"bim_page:{int(owner_id)}:{page + 1}"))
    if navigation:
        keyboard.append(navigation)
    source_url = getattr(result, "source_url", None) or "https://www.bim.com.tr/Categories/100/aktuel-urunler.aspx"
    keyboard.append([InlineKeyboardButton("ğŸ”— ResmÃ® BÄ°M sayfasÄ±", url=str(source_url))])
    return "\n".join(lines), InlineKeyboardMarkup(keyboard)

