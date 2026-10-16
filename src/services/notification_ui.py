"""User-facing notification center keyboards and safe row rendering.

The command surface keeps legacy aliases such as ``/takip`` for compatibility,
but the primary interaction is a category-based inline panel. This module is
pure presentation code: it does not read Mongo/Redis and it never decides
whether a group user is allowed to change a schedule.
"""

from __future__ import annotations

import html
from typing import Any, Iterable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


FEATURE_LABELS = {
    "weather": "Hava durumu",
    "air_quality": "Hava kalitesi",
    "earthquakes": "Deprem",
    "currency": "DÃ¶viz / altÄ±n",
    "crypto": "Kripto fiyatÄ±",
    "fuel": "AkaryakÄ±t",
    "breaking_news": "Son dakika haberleri",
    "bim_deals": "AktÃ¼el Ã¼rÃ¼nler",
    "prayer_times": "Namaz vakitleri",
    "group_announcement": "Grup duyurusu",
}

# Keep the list intentionally curated.  Telegram callback payloads are short,
# and exposing arbitrary IANA names through a callback would create an
# unnecessary input surface.  The store still validates the final value.
TIMEZONE_PRESETS = (
    ("Europe/Istanbul", "ğŸ‡¹ğŸ‡· TÃ¼rkiye (Ä°stanbul)"),
    ("Europe/Berlin", "ğŸ‡©ğŸ‡ª Orta Avrupa (Berlin)"),
    ("Europe/London", "ğŸ‡¬ğŸ‡§ BirleÅŸik KrallÄ±k (Londra)"),
    ("Asia/Dubai", "ğŸ‡¦ğŸ‡ª KÃ¶rfez (Dubai)"),
    ("America/New_York", "ğŸ‡ºğŸ‡¸ DoÄŸu ABD (New York)"),
    ("Asia/Tokyo", "ğŸ‡¯ğŸ‡µ Japonya (Tokyo)"),
)


def notification_timezone_panel(user_id: int, current_timezone: str) -> tuple[str, InlineKeyboardMarkup]:
    """Render the bounded timezone picker used by command and callback flows."""

    owner = int(user_id)
    current = str(current_timezone or "Europe/Istanbul")
    rows: list[list[InlineKeyboardButton]] = []
    for zone, label in TIMEZONE_PRESETS:
        marker = " âœ…" if zone == current else ""
        token = zone.replace("/", "~")
        rows.append([
            InlineKeyboardButton(
                f"{label}{marker}",
                callback_data=f"notify_pref:tz:{owner}:{token}",
            )
        ])
    rows.append([InlineKeyboardButton("ğŸ”” Bildirim ayarlarÄ±na dÃ¶n", callback_data=f"notify_pref:show:{owner}")])
    return (
        "ğŸŒ <b>Saat dilimi</b>\n\n"
        "HatÄ±rlatÄ±cÄ± ve kiÅŸisel bildirim saatleri bu seÃ§ime gÃ¶re hesaplanÄ±r.\n"
        f"Åu an: <code>{html.escape(current)}</code>",
        InlineKeyboardMarkup(rows),
    )


def notification_preferences_panel(user_id: int, preferences: dict[str, Any]) -> tuple[str, InlineKeyboardMarkup]:
    """Render the complete personal notification preference panel."""

    owner = int(user_id)
    state = "aÃ§Ä±k" if preferences.get("quiet_hours_enabled") else "kapalÄ±"
    start = html.escape(str(preferences.get("quiet_start", "23:00")))
    end = html.escape(str(preferences.get("quiet_end", "08:00")))
    timezone_name = html.escape(str(preferences.get("timezone", "Europe/Istanbul")))
    keyboard = [
        [InlineKeyboardButton("ğŸ”• Sessiz saatleri aÃ§/kapat", callback_data=f"notify_pref:toggle:{owner}")],
        [
            InlineKeyboardButton("ğŸŒ™ 23:00â€“08:00", callback_data=f"notify_pref:preset:{owner}:night"),
            InlineKeyboardButton("ğŸŒƒ 22:00â€“07:00", callback_data=f"notify_pref:preset:{owner}:late"),
        ],
        [InlineKeyboardButton("ğŸŒ Saat dilimini deÄŸiÅŸtir", callback_data=f"notify_pref:timezone:{owner}")],
        [InlineKeyboardButton("ğŸ”” Bildirim merkezine dÃ¶n", callback_data=f"notify_center:{owner}")],
    ]
    return (
        "<b>ğŸ”” Bildirim ayarlarÄ±n</b>\n\n"
        f"Sessiz saatler: <b>{state}</b>\n"
        f"AralÄ±k: <code>{start}-{end}</code>\n"
        f"Saat dilimi: <code>{timezone_name}</code>",
        InlineKeyboardMarkup(keyboard),
    )


def personal_center(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Return the private-chat notification center."""
    owner = int(user_id)
    text = (
        "ğŸ”” <b>Bildirim merkezi</b>\n\n"
        "Arel'i kÃ¼Ã§Ã¼k bir Ã§aÄŸrÄ± merkezi gibi Ã§alÄ±ÅŸtÄ±rmak istersen buradan "
        "kaynak seÃ§ebilirsin. Spam yapmam; aynÄ± veri deÄŸiÅŸmedikÃ§e seni dÃ¼rtmem.\n\n"
        "Bir kategori seÃ§ veya mevcut bildirimlerini yÃ¶net:"
    )
    keyboard = [
        [
            InlineKeyboardButton("\U0001f327 Hava", callback_data=f"notify_weather:{owner}"),
            InlineKeyboardButton("\U0001f30d Deprem", callback_data=f"notify_earthquake:{owner}"),
        ],
        [InlineKeyboardButton("\U0001f33f Hava kalitesi", callback_data=f"notify_air_quality:{owner}")],
        [
            InlineKeyboardButton("\U0001f4b1 Kur / yak\u0131t alarm\u0131", callback_data=f"notify_prices:{owner}"),
            InlineKeyboardButton("\u20bf Kripto alarm\u0131", callback_data=f"notify_crypto:{owner}"),
        ],
        [
            InlineKeyboardButton("\U0001f4f0 Haber / akt\u00fcel", callback_data=f"notify_feeds:{owner}"),
        ],
        [
            InlineKeyboardButton("\U0001f4cb Bildirimlerim", callback_data=f"notify_list:{owner}"),
            InlineKeyboardButton("\U0001f319 Sessiz saatler", callback_data=f"notify_pref:show:{owner}"),
        ],
        [InlineKeyboardButton("\U0001f4c5 G\u00fcnl\u00fck \u00f6zet", callback_data=f"daily_brief:{owner}")],
    ]
    return text, InlineKeyboardMarkup(keyboard)


def group_center(user_id: int, *, is_admin: bool) -> tuple[str, InlineKeyboardMarkup]:
    """Return the group panel while keeping mutation rights admin-only."""
    owner = int(user_id)
    if not is_admin:
        return (
            "ğŸ“£ <b>Grup bildirim merkezi</b>\n\n"
            "Bu grubun ortak bildirimlerini yalnÄ±zca grup yÃ¶neticileri yÃ¶netebilir. "
            "KiÅŸisel bildirimler iÃ§in Ã¶zel sohbette /bildirim yazabilirsin.",
            InlineKeyboardMarkup(
                [[InlineKeyboardButton("ğŸ“‹ Aktif grup bildirimleri", callback_data=f"notify_group_list:{owner}")]]
            ),
        )
    text = (
        "ğŸ“£ <b>Grup bildirim merkezi</b>\n\n"
        "Bu panelde kurulan bildirimler gruptaki herkese gÃ¶nderilir. "
        "DolayÄ±sÄ±yla butonlar sadece yÃ¶neticilere aÃ§Ä±k; demokrasi gÃ¼zel ÅŸey, "
        "ama yanlÄ±ÅŸlÄ±kla 15 dakikada bir deprem spam'i de gÃ¼zel deÄŸil. "
        "PlanlÄ± duyuruyu da aynÄ± panelden yÃ¶netebilirsin."
    )
    keyboard = [
        [InlineKeyboardButton("ğŸŒ¤ Grup hava bildirimi", callback_data=f"notify_group_weather:{owner}")],
        [InlineKeyboardButton("ğŸŒ¿ Grup hava kalitesi", callback_data=f"notify_group_air_quality:{owner}")],
        [InlineKeyboardButton("ğŸŒ Grup deprem bildirimi", callback_data=f"notify_group_earthquake:{owner}")],
        [InlineKeyboardButton("ğŸ“° Grup haber Ã¶zeti", callback_data=f"notify_group_news:{owner}")],
        [InlineKeyboardButton("ğŸ“£ PlanlÄ± duyuru", callback_data=f"notify_group_announcement:{owner}")],
        [InlineKeyboardButton("ğŸ“‹ Aktif grup bildirimleri", callback_data=f"notify_group_list:{owner}")],
    ]
    return text, InlineKeyboardMarkup(keyboard)


def category_help(category: str, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Explain command-based setup when a category needs free-form input."""
    owner = int(user_id)
    category = str(category)
    if category == "prices":
        text = (
            "ğŸ’± <b>Kur ve yakÄ±t alarmÄ±</b>\n\n"
            "Bir eÅŸik aÅŸÄ±ldÄ±ÄŸÄ±nda tek bildirim alÄ±rsÄ±n; deÄŸer aynÄ± kaldÄ±kÃ§a tekrar "
            "gÃ¶ndermem.\n\n"
            "<code>/alarm kur usd 40 Ã¼stÃ¼</code>\n"
            "<code>/alarm kur kripto btc 100000 Ã¼stÃ¼</code>\n"
            "<code>/takip kripto btc 15dk</code>\n"
            "<code>/alarm kur benzin 50 altÄ±</code>\n\n"
            "Mevcut alarmlarÄ±nÄ± <code>/alarm liste</code> ile gÃ¶rebilirsin."
        )
    elif category == "crypto":
        text = (
            "\u20bf <b>Kripto alarm\u0131</b>\n\n"
            "Coin fiyat\u0131 e\u015fi\u011fi a\u015ft\u0131\u011f\u0131nda tek bildirim al\u0131rs\u0131n.\n\n"
            "<code>/alarm kur kripto btc 100000 \u00fcst\u00fc</code>\n"
            "<code>/takip kripto btc 15dk</code>"
        )
    elif category == "feeds":
        text = (
            "ğŸ“° <b>Haber ve aktÃ¼el takipleri</b>\n\n"
            "Yeni iÃ§erik geldiÄŸinde Ã¶zet gÃ¶nderirim; aynÄ± baÅŸlÄ±ÄŸÄ± tekrar tekrar "
            "Ä±sÄ±tmam.\n\n"
            "<code>/takip haber 2sa</code>\n"
            "<code>/takip bim 12sa</code>"
        )
    else:
        text = "Bildirim kategorisi bulunamadÄ±."
    return text, InlineKeyboardMarkup(
        [[InlineKeyboardButton("ğŸ”” Bildirim merkezine dÃ¶n", callback_data=f"notify_center:{owner}")]]
    )


def price_tracking_panel(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Render common currency and fuel tracking choices."""

    owner = int(user_id)
    text = (
        "ğŸ’± <b>Kur ve yakÄ±t takibi</b>\n\n"
        "Sabit aralÄ±klarla gÃ¼ncel deÄŸeri gÃ¶nderirim. DeÄŸer deÄŸiÅŸmedikÃ§e aynÄ± "
        "iÃ§eriÄŸi tekrar yollamam; spam makinesi olmak iÃ§in yeterince baÅŸka "
        "uygulama var.\n\n"
        "Belirli bir seviyeye ulaÅŸÄ±nca tek bildirim istiyorsan aÅŸaÄŸÄ±dan eÅŸik "
        "alarmÄ± yardÄ±mÄ±nÄ± aÃ§."
    )
    keyboard = [
        [
            InlineKeyboardButton("ğŸ’µ Kur Â· 1 saat", callback_data=f"notify_price_add:{owner}:currency:3600"),
            InlineKeyboardButton("ğŸ’µ Kur Â· 6 saat", callback_data=f"notify_price_add:{owner}:currency:21600"),
        ],
        [InlineKeyboardButton("â›½ YakÄ±t Â· 6 saat", callback_data=f"notify_price_add:{owner}:fuel:21600")],
        [InlineKeyboardButton("âš ï¸ EÅŸik alarmÄ± nasÄ±l kurulur?", callback_data=f"notify_threshold_help:{owner}")],
        [InlineKeyboardButton("ğŸ”” Bildirim merkezine dÃ¶n", callback_data=f"notify_center:{owner}")],
    ]
    return text, InlineKeyboardMarkup(keyboard)


def crypto_tracking_panel(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Render popular crypto tracking choices with bounded callback payloads."""

    owner = int(user_id)
    text = (
        "â‚¿ <b>Kripto fiyat takibi</b>\n\n"
        "Bir coin seÃ§; varsayÄ±lan olarak saatte bir kontrol ederim. "
        "Ä°stersen sonrasÄ±nda bildirimi tek tuÅŸla durdurabilirsin."
    )
    keyboard = [
        [
            InlineKeyboardButton("â‚¿ Bitcoin Â· 1 saat", callback_data=f"notify_crypto_add:{owner}:bitcoin:3600"),
            InlineKeyboardButton("Î Ethereum Â· 1 saat", callback_data=f"notify_crypto_add:{owner}:ethereum:3600"),
        ],
        [
            InlineKeyboardButton("â— Solana Â· 1 saat", callback_data=f"notify_crypto_add:{owner}:solana:3600"),
            InlineKeyboardButton("â‚¿ Bitcoin Â· 1 gÃ¼n", callback_data=f"notify_crypto_add:{owner}:bitcoin:86400"),
        ],
        [InlineKeyboardButton("âš ï¸ EÅŸik alarmÄ± yardÄ±mÄ±nÄ± aÃ§", callback_data=f"notify_threshold_help:{owner}")],
        [InlineKeyboardButton("ğŸ”” Bildirim merkezine dÃ¶n", callback_data=f"notify_center:{owner}")],
    ]
    return text, InlineKeyboardMarkup(keyboard)


def feed_tracking_panel(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Render news and retail-feed tracking choices."""

    owner = int(user_id)
    text = (
        "ğŸ“° <b>Haber ve aktÃ¼el takipleri</b>\n\n"
        "Yeni iÃ§erik geldiÄŸinde Ã¶zet yollarÄ±m; aynÄ± baÅŸlÄ±ÄŸÄ± tekrar Ä±sÄ±tmam. "
        "GÃ¼ndem zaten yeterince kendini tekrar ediyor."
    )
    keyboard = [
        [InlineKeyboardButton("ğŸ“° Haber Â· 2 saat", callback_data=f"notify_feed_add:{owner}:breaking_news:7200")],
        [InlineKeyboardButton("ğŸ›’ BÄ°M aktÃ¼el Â· 12 saat", callback_data=f"notify_feed_add:{owner}:bim_deals:43200")],
        [InlineKeyboardButton("ğŸ”” Bildirim merkezine dÃ¶n", callback_data=f"notify_center:{owner}")],
    ]
    return text, InlineKeyboardMarkup(keyboard)


def render_rows(
    rows: Iterable[dict[str, Any]],
    *,
    group: bool = False,
    owner_id: int | None = None,
) -> tuple[str, InlineKeyboardMarkup | None]:
    """Render a bounded, escaped list of active schedules."""
    active = [row for row in rows if row.get("enabled")]
    title = "Grubun aktif bildirimleri" if group else "Aktif bildirimlerin"
    lines = [f"<b>ğŸ“‹ {title}</b>", ""]
    keyboard: list[list[InlineKeyboardButton]] = []
    for row in active[:50]:
        feature = FEATURE_LABELS.get(str(row.get("feature_key")), str(row.get("feature_key", "Bildirim")))
        params = row.get("params") or {}
        city = f" Â· {html.escape(str(params.get('city')))}" if params.get("city") else ""
        schedule = row.get("schedule_kind")
        if schedule == "daily":
            cadence = "her gÃ¼n"
        elif schedule == "weekly":
            cadence = "her hafta"
        else:
            cadence = f"{max(1, int(row.get('interval_seconds', 0)) // 60)} dk aralÄ±k"
        lines.append(
            f"â€¢ <b>{html.escape(feature)}</b>{city} Â· {html.escape(cadence)} "
            f"Â· <code>{html.escape(str(row.get('subscription_id', ''))[:8])}</code>"
        )
        if group:
            legacy_prefix = {
                "weather": "weather_group_stop",
                "earthquakes": "earthquake_group_stop",
            }.get(str(row.get("feature_key")))
            callback = (
                f"{legacy_prefix}:{int(owner_id or 0)}:{row.get('subscription_id', '')}"
                if legacy_prefix
                else f"notify_group_stop:{int(owner_id or 0)}:{row.get('subscription_id', '')}"
            )
        else:
            callback = f"notify_stop:{int(owner_id or 0)}:{row.get('subscription_id', '')}"
        keyboard.append([InlineKeyboardButton("ğŸ›‘ Durdur", callback_data=callback)])
    if not active:
        lines.append("HenÃ¼z aktif bildirim yok.")
    return "\n".join(lines), InlineKeyboardMarkup(keyboard) if keyboard else None
