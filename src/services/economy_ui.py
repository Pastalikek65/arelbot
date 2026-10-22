"""Small, escaped Turkish economy panels; no ledger decisions are made here."""
from __future__ import annotations

from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

ISTANBUL = ZoneInfo("Europe/Istanbul")


def _value(snapshot, key, default=None):
    return snapshot.get(key, default) if isinstance(snapshot, dict) else getattr(snapshot, key, default)


def _owned(snapshot):
    value = _value(snapshot, "owned", _value(snapshot, "owned_items", [])) or []
    if isinstance(value, dict):
        return {str(key) for key, owned in value.items() if owned}
    return {str(item.get("key", item.get("item_key"))) if isinstance(item, dict) else str(item)
            for item in value}


def _equipped(snapshot):
    value = _value(snapshot, "equipped", _value(snapshot, "equipped_items", [])) or []
    if isinstance(value, dict):
        return {str(item) for item in value.values() if item}
    return {str(item) for item in value}


def _items(catalog):
    if isinstance(catalog, dict):
        catalog = catalog.get("items", list(catalog.values()))
    return list(catalog or [])


def _item_value(item, key, default=None):
    return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)


def _item_key(item):
    return str(_item_value(item, "key", _item_value(item, "item_key", "")))


def _item_name(item):
    return escape(_item_label(item))


def _item_label(item):
    return str(_item_value(item, "name", _item_value(item, "title", _item_key(item))))


def _number(value):
    return f"{int(value or 0):,}".replace(",", ".")


def _time_label(value):
    if not value:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=ISTANBUL)
    return value.astimezone(ISTANBUL).strftime("%d.%m.%Y %H:%M")


def _reason_label(event):
    label = _item_value(event, "label")
    if label:
        return escape(str(label))
    reason = str(_item_value(event, "reason", ""))
    if (int(_item_value(event, "amount", 0) or 0) == 0
            and str(_item_value(event, "direction", "")) == "credit"
            and reason.endswith("_reward") and reason != "daily_reward"):
        return "Günlük oyun sınırı"
    names = {"daily_reward": "Günlük ödül", "shop_purchase": "Mağaza alışverişi",
             "shop_refund": "Alışveriş iadesi", "game_reward": "Oyun ödülü"}
    if reason in names:
        return names[reason]
    if reason.endswith("_reward"):
        return "Oyun ödülü"
    if reason.endswith("_prize"):
        return "Oyun kazancı"
    if reason.endswith(("_bet", "_stake")):
        return "Oyun katılımı"
    return "İşlem"


def render(view, snapshot, catalog, *, chat_name=None, notice=None, item_key=None):
    balance = _number(_value(snapshot, "balance", 0))
    scope = "Bu sohbetteki bakiyen" if not chat_name else f"{escape(str(chat_name))} bakiyen"
    lines = []
    if notice:
        lines.extend([escape(str(notice)), ""])
    if view == "wallet":
        lines.extend(["🪙 <b>Cüzdanım</b>", f"{scope}: <b>{balance} jeton</b>.",
                      "Jetonlar bu sohbete özeldir. Oyunlardan ve günlük ödülden kazanılır."])
        limit = int(_value(snapshot, "game_daily_limit", 0) or 0)
        if limit:
            earned = int(_value(snapshot, "game_earned_today", 0) or 0)
            lines.append(f"Bugünkü oyun kazancı: {_number(earned)}/{_number(limit)} jeton.")
            remaining = int(_value(snapshot, "game_reward_remaining_today", max(0, limit - earned)) or 0)
            lines.append(f"Bugün oyunlardan kazanabileceğin: {_number(remaining)} jeton.")
            lines.append("Bu sınır oyun ödülleri içindir; günlük ödül ve bahis iadeleri ayrıdır.")
        selected = _value(snapshot, "equipped", {}) or {}
        if isinstance(selected, dict):
            catalog_by_key = {_item_key(item): item for item in _items(catalog)}
            for slot, label in (("title", "Seçili unvan"), ("badge", "Seçili rozet")):
                key = selected.get(slot)
                item = catalog_by_key.get(str(key)) if key else None
                lines.append(f"{label}: {_item_name(item) if item else 'Yok'}.")
            lines.append("Profil süsleri oyun gücünü değiştirmez.")
        recent = _value(snapshot, "recent", _value(snapshot, "history", [])) or []
        if recent:
            lines.append("\nSon hareketler:")
            for event in list(recent)[:3]:
                direction = str(_item_value(event, "direction", ""))
                sign = "+" if direction == "credit" else "−"
                amount = _number(_item_value(event, "amount", 0))
                reason = _reason_label(event)
                cap_note = " (oyun ödülü sınırına ulaşıldı)" if reason == "Günlük oyun sınırı" else ""
                lines.append(f"• {reason}: {sign}{amount}{cap_note}")
    elif view == "daily":
        streak = int(_value(snapshot, "streak", 0) or 0)
        amount = int(_value(snapshot, "daily_amount", 0) or 0)
        available = bool(_value(snapshot, "daily_available", False))
        lines.extend(["🎁 <b>Günlük ödül</b>", f"{scope}: <b>{balance} jeton</b>.",
                      f"Seri: <b>{streak} gün</b>.",
                      "Ardışık günlerde 25 / 30 / 35 / 40 / 45 / 50 jeton; 6. günden sonra 50 jeton.",
                      "Bir gün atlarsan seri 1. günden başlar."])
        if available:
            lines.append(f"Bugünkü ödül hazır{f': {amount} jeton' if amount else ''}.")
        else:
            next_at = _time_label(_value(snapshot, "next_claim_at"))
            lines.append(f"Sonraki hak: {next_at} (İstanbul saati)." if next_at
                         else "Bugünkü hak kullanıldı.")
    elif view == "shop":
        lines.extend(["🛍️ <b>Mağaza</b>", f"{scope}: <b>{balance} jeton</b>.",
                      "Ürünleri inceleyip fiyat ve etkisini gördükten sonra satın alabilirsin."])
        owned, equipped = _owned(snapshot), _equipped(snapshot)
        for item in _items(catalog):
            key = _item_key(item)
            price = int(_item_value(item, "price", 0) or 0)
            status = ("Kuşanıldı" if key in equipped else "Sahipsin" if key in owned else
                      "Bakiye yetersiz" if int(_value(snapshot, "balance", 0) or 0) < price else
                      "Alınabilir")
            lines.append(f"• {_item_name(item)} · {_number(_item_value(item, 'price', 0))} jeton · {status}")
    elif view == "inventory":
        lines.extend(["🎒 <b>Envanterim</b>", f"{scope}: <b>{balance} jeton</b>."])
        owned, equipped = _owned(snapshot), _equipped(snapshot)
        mine = [item for item in _items(catalog) if _item_key(item) in owned]
        if not mine:
            lines.append("Henüz eşyan yok. Mağazaya göz atabilirsin.")
        for item in mine:
            status = "Kuşanıldı" if _item_key(item) in equipped else "Kuşanılabilir"
            lines.append(f"• {_item_name(item)} · {status}")
    elif view == "detail":
        item = next((row for row in _items(catalog) if _item_key(row) == item_key), None)
        if item is None:
            raise ValueError("eco_item_missing")
        owned, equipped = _owned(snapshot), _equipped(snapshot)
        price = int(_item_value(item, "price", 0) or 0)
        lines.extend([f"🛍️ <b>{_item_name(item)}</b>",
                      escape(str(_item_value(item, "description", ""))),
                      f"Etkisi: {escape(str(_item_value(item, 'effect', 'Görsel süsleme')))}",
                      f"Fiyat: <b>{_number(price)} jeton</b>",
                      f"{scope}: <b>{balance} jeton</b>."])
        if item_key in equipped:
            lines.append("Bu eşya kuşanılmış.")
        elif item_key in owned:
            lines.append("Bu eşyaya sahipsin.")
        elif int(_value(snapshot, "balance", 0) or 0) < price:
            missing = max(0, price - int(_value(snapshot, "balance", 0) or 0))
            lines.append(f"Bu ürün için {_number(missing)} jeton daha gerekiyor.")
        else:
            lines.append("Satın almak istersen aşağıdaki düğmeyle onayla. Güncel fiyat yeniden denetlenir.")
    else:
        raise ValueError("eco_view_invalid")
    return "\n".join(line for line in lines if line is not None)


def keyboard(view, snapshot, catalog, *, callback, item_key=None):
    def button(label, action, key=""):
        return InlineKeyboardButton(label, callback_data=callback(action, key))

    rows = []
    if view == "wallet":
        rows.append([button("Günlük ödül", "d"), button("Mağaza", "s")])
        rows.append([button("Envanter", "i"), button("Yenile", "w")])
    elif view == "daily":
        if _value(snapshot, "daily_available", False):
            rows.append([button("Ödülü al", "c")])
        rows.append([button("Cüzdana dön", "w")])
    elif view == "shop":
        owned = _owned(snapshot)
        for item in _items(catalog):
            key = _item_key(item)
            label = f"{_item_label(item)} {'✓' if key in owned else '· ' + _number(_item_value(item, 'price', 0))}"
            rows.append([button(label, "v", key)])
        rows.append([button("Cüzdan", "w"), button("Envanter", "i")])
    elif view == "inventory":
        owned, equipped = _owned(snapshot), _equipped(snapshot)
        for item in _items(catalog):
            key = _item_key(item)
            if key in owned and key not in equipped:
                rows.append([button(f"{_item_label(item)} kuşan", "e", key)])
        selected = _value(snapshot, "equipped", {}) or {}
        for slot, label in (("title", "Unvanı kaldır"), ("badge", "Rozeti kaldır")):
            if isinstance(selected, dict) and selected.get(slot):
                rows.append([button(label, "x", slot)])
        rows.append([button("Mağaza", "s"), button("Cüzdan", "w")])
    elif view == "detail":
        owned = _owned(snapshot)
        item = next((row for row in _items(catalog) if _item_key(row) == item_key), None)
        price = int(_item_value(item, "price", 0) or 0) if item else 0
        if item_key not in owned and int(_value(snapshot, "balance", 0) or 0) >= price:
            rows.append([button("Satın al", "b", item_key)])
        else:
            rows.append([button("Envanterde gör", "i")])
        rows.append([button("Mağazaya dön", "s")])
    return InlineKeyboardMarkup(rows)
