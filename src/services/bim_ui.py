"""Telegram presentation helpers for the BİM aktüel catalogue."""

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
        return "⚠️ BİM aktüel verileri şu an alınamıyor.", None
    data = getattr(result, "data", None) or {}
    deals = data.get("deals") if isinstance(data, dict) else None
    if not isinstance(deals, list) or not deals:
        return "⚠️ BİM için gösterilecek ürün bulunamadı.", None
    total_pages = max(1, ceil(len(deals) / PAGE_SIZE))
    page = max(0, min(int(page), total_pages - 1))
    rows = deals[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    lines = [f"🛒 <b>BİM Aktüel Ürünler</b> · Sayfa {page + 1}/{total_pages}", ""]
    for index, item in enumerate(rows, page * PAGE_SIZE + 1):
        if not isinstance(item, dict):
            continue
        title = escape(str(item.get("title") or "Aktüel ürün")[:160])
        unit = escape(str(item.get("unit") or "").strip())
        price = escape(str(item.get("price") or "Fiyat belirtilmemiş").strip())
        suffix = f" · {unit}" if unit else ""
        lines.append(f"<b>{index}.</b> {title}{suffix}\n   💳 <b>{price}</b>")
    lines.extend(["", "📌 Resmî BİM kaynağındaki ürün kartları ve fiyatlar gösteriliyor."])
    keyboard: list[list[InlineKeyboardButton]] = []
    navigation: list[InlineKeyboardButton] = []
    if page > 0:
        navigation.append(InlineKeyboardButton("‹ Önceki", callback_data=f"bim_page:{int(owner_id)}:{page - 1}"))
    navigation.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="notification_noop"))
    if page + 1 < total_pages:
        navigation.append(InlineKeyboardButton("Sonraki ›", callback_data=f"bim_page:{int(owner_id)}:{page + 1}"))
    if navigation:
        keyboard.append(navigation)
    source_url = getattr(result, "source_url", None) or "https://www.bim.com.tr/Categories/100/aktuel-urunler.aspx"
    keyboard.append([InlineKeyboardButton("🔗 Resmî BİM sayfası", url=str(source_url))])
    return "\n".join(lines), InlineKeyboardMarkup(keyboard)

