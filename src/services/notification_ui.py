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
    "currency": "Döviz / altın",
    "crypto": "Kripto fiyatı",
    "fuel": "Akaryakıt",
    "breaking_news": "Son dakika haberleri",
    "bim_deals": "Aktüel ürünler",
    "prayer_times": "Namaz vakitleri",
    "group_announcement": "Grup duyurusu",
}

# Keep the list intentionally curated.  Telegram callback payloads are short,
# and exposing arbitrary IANA names through a callback would create an
# unnecessary input surface.  The store still validates the final value.
TIMEZONE_PRESETS = (
    ("Europe/Istanbul", "🇹🇷 Türkiye (İstanbul)"),
    ("Europe/Berlin", "🇩🇪 Orta Avrupa (Berlin)"),
    ("Europe/London", "🇬🇧 Birleşik Krallık (Londra)"),
    ("Asia/Dubai", "🇦🇪 Körfez (Dubai)"),
    ("America/New_York", "🇺🇸 Doğu ABD (New York)"),
    ("Asia/Tokyo", "🇯🇵 Japonya (Tokyo)"),
)


def notification_timezone_panel(user_id: int, current_timezone: str) -> tuple[str, InlineKeyboardMarkup]:
    """Render the bounded timezone picker used by command and callback flows."""

    owner = int(user_id)
    current = str(current_timezone or "Europe/Istanbul")
    rows: list[list[InlineKeyboardButton]] = []
    for zone, label in TIMEZONE_PRESETS:
        marker = " ✅" if zone == current else ""
        token = zone.replace("/", "~")
        rows.append([
            InlineKeyboardButton(
                f"{label}{marker}",
                callback_data=f"notify_pref:tz:{owner}:{token}",
            )
        ])
    rows.append([InlineKeyboardButton("🔔 Bildirim ayarlarına dön", callback_data=f"notify_pref:show:{owner}")])
    return (
        "🌍 <b>Saat dilimi</b>\n\n"
        "Hatırlatıcı ve kişisel bildirim saatleri bu seçime göre hesaplanır.\n"
        f"Şu an: <code>{html.escape(current)}</code>",
        InlineKeyboardMarkup(rows),
    )


def notification_preferences_panel(user_id: int, preferences: dict[str, Any]) -> tuple[str, InlineKeyboardMarkup]:
    """Render the complete personal notification preference panel."""

    owner = int(user_id)
    state = "açık" if preferences.get("quiet_hours_enabled") else "kapalı"
    start = html.escape(str(preferences.get("quiet_start", "23:00")))
    end = html.escape(str(preferences.get("quiet_end", "08:00")))
    timezone_name = html.escape(str(preferences.get("timezone", "Europe/Istanbul")))
    keyboard = [
        [InlineKeyboardButton("🔕 Sessiz saatleri aç/kapat", callback_data=f"notify_pref:toggle:{owner}")],
        [
            InlineKeyboardButton("🌙 23:00–08:00", callback_data=f"notify_pref:preset:{owner}:night"),
            InlineKeyboardButton("🌃 22:00–07:00", callback_data=f"notify_pref:preset:{owner}:late"),
        ],
        [InlineKeyboardButton("🌍 Saat dilimini değiştir", callback_data=f"notify_pref:timezone:{owner}")],
        [InlineKeyboardButton("🔔 Bildirim merkezine dön", callback_data=f"notify_center:{owner}")],
    ]
    return (
        "<b>🔔 Bildirim ayarların</b>\n\n"
        f"Sessiz saatler: <b>{state}</b>\n"
        f"Aralık: <code>{start}-{end}</code>\n"
        f"Saat dilimi: <code>{timezone_name}</code>",
        InlineKeyboardMarkup(keyboard),
    )


def personal_center(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Return the private-chat notification center."""
    owner = int(user_id)
    text = (
        "🔔 <b>Bildirim merkezi</b>\n\n"
        "Arel'i küçük bir çağrı merkezi gibi çalıştırmak istersen buradan "
        "kaynak seçebilirsin. Spam yapmam; aynı veri değişmedikçe seni dürtmem.\n\n"
        "Bir kategori seç veya mevcut bildirimlerini yönet:"
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
            "📣 <b>Grup bildirim merkezi</b>\n\n"
            "Bu grubun ortak bildirimlerini yalnızca grup yöneticileri yönetebilir. "
            "Kişisel bildirimler için özel sohbette /bildirim yazabilirsin.",
            InlineKeyboardMarkup(
                [[InlineKeyboardButton("📋 Aktif grup bildirimleri", callback_data=f"notify_group_list:{owner}")]]
            ),
        )
    text = (
        "📣 <b>Grup bildirim merkezi</b>\n\n"
        "Bu panelde kurulan bildirimler gruptaki herkese gönderilir. "
        "Dolayısıyla butonlar sadece yöneticilere açık; demokrasi güzel şey, "
        "ama yanlışlıkla 15 dakikada bir deprem spam'i de güzel değil. "
        "Planlı duyuruyu da aynı panelden yönetebilirsin."
    )
    keyboard = [
        [InlineKeyboardButton("🌤 Grup hava bildirimi", callback_data=f"notify_group_weather:{owner}")],
        [InlineKeyboardButton("🌿 Grup hava kalitesi", callback_data=f"notify_group_air_quality:{owner}")],
        [InlineKeyboardButton("🌍 Grup deprem bildirimi", callback_data=f"notify_group_earthquake:{owner}")],
        [InlineKeyboardButton("📰 Grup haber özeti", callback_data=f"notify_group_news:{owner}")],
        [InlineKeyboardButton("📣 Planlı duyuru", callback_data=f"notify_group_announcement:{owner}")],
        [InlineKeyboardButton("📋 Aktif grup bildirimleri", callback_data=f"notify_group_list:{owner}")],
    ]
    return text, InlineKeyboardMarkup(keyboard)


def category_help(category: str, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Explain command-based setup when a category needs free-form input."""
    owner = int(user_id)
    category = str(category)
    if category == "prices":
        text = (
            "💱 <b>Kur ve yakıt alarmı</b>\n\n"
            "Bir eşik aşıldığında tek bildirim alırsın; değer aynı kaldıkça tekrar "
            "göndermem.\n\n"
            "<code>/alarm kur usd 40 üstü</code>\n"
            "<code>/alarm kur kripto btc 100000 üstü</code>\n"
            "<code>/takip kripto btc 15dk</code>\n"
            "<code>/alarm kur benzin 50 altı</code>\n\n"
            "Mevcut alarmlarını <code>/alarm liste</code> ile görebilirsin."
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
            "📰 <b>Haber ve aktüel takipleri</b>\n\n"
            "Yeni içerik geldiğinde özet gönderirim; aynı başlığı tekrar tekrar "
            "ısıtmam.\n\n"
            "<code>/takip haber 2sa</code>\n"
            "<code>/takip bim 12sa</code>"
        )
    else:
        text = "Bildirim kategorisi bulunamadı."
    return text, InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔔 Bildirim merkezine dön", callback_data=f"notify_center:{owner}")]]
    )


def price_tracking_panel(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Render common currency and fuel tracking choices."""

    owner = int(user_id)
    text = (
        "💱 <b>Kur ve yakıt takibi</b>\n\n"
        "Sabit aralıklarla güncel değeri gönderirim. Değer değişmedikçe aynı "
        "içeriği tekrar yollamam; spam makinesi olmak için yeterince başka "
        "uygulama var.\n\n"
        "Belirli bir seviyeye ulaşınca tek bildirim istiyorsan aşağıdan eşik "
        "alarmı yardımını aç."
    )
    keyboard = [
        [
            InlineKeyboardButton("💵 Kur · 1 saat", callback_data=f"notify_price_add:{owner}:currency:3600"),
            InlineKeyboardButton("💵 Kur · 6 saat", callback_data=f"notify_price_add:{owner}:currency:21600"),
        ],
        [InlineKeyboardButton("⛽ Yakıt · 6 saat", callback_data=f"notify_price_add:{owner}:fuel:21600")],
        [InlineKeyboardButton("⚠️ Eşik alarmı nasıl kurulur?", callback_data=f"notify_threshold_help:{owner}")],
        [InlineKeyboardButton("🔔 Bildirim merkezine dön", callback_data=f"notify_center:{owner}")],
    ]
    return text, InlineKeyboardMarkup(keyboard)


def crypto_tracking_panel(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Render popular crypto tracking choices with bounded callback payloads."""

    owner = int(user_id)
    text = (
        "₿ <b>Kripto fiyat takibi</b>\n\n"
        "Bir coin seç; varsayılan olarak saatte bir kontrol ederim. "
        "İstersen sonrasında bildirimi tek tuşla durdurabilirsin."
    )
    keyboard = [
        [
            InlineKeyboardButton("₿ Bitcoin · 1 saat", callback_data=f"notify_crypto_add:{owner}:bitcoin:3600"),
            InlineKeyboardButton("Ξ Ethereum · 1 saat", callback_data=f"notify_crypto_add:{owner}:ethereum:3600"),
        ],
        [
            InlineKeyboardButton("◎ Solana · 1 saat", callback_data=f"notify_crypto_add:{owner}:solana:3600"),
            InlineKeyboardButton("₿ Bitcoin · 1 gün", callback_data=f"notify_crypto_add:{owner}:bitcoin:86400"),
        ],
        [InlineKeyboardButton("⚠️ Eşik alarmı yardımını aç", callback_data=f"notify_threshold_help:{owner}")],
        [InlineKeyboardButton("🔔 Bildirim merkezine dön", callback_data=f"notify_center:{owner}")],
    ]
    return text, InlineKeyboardMarkup(keyboard)


def feed_tracking_panel(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Render news and retail-feed tracking choices."""

    owner = int(user_id)
    text = (
        "📰 <b>Haber ve aktüel takipleri</b>\n\n"
        "Yeni içerik geldiğinde özet yollarım; aynı başlığı tekrar ısıtmam. "
        "Gündem zaten yeterince kendini tekrar ediyor."
    )
    keyboard = [
        [InlineKeyboardButton("📰 Haber · 2 saat", callback_data=f"notify_feed_add:{owner}:breaking_news:7200")],
        [InlineKeyboardButton("🛒 BİM aktüel · 12 saat", callback_data=f"notify_feed_add:{owner}:bim_deals:43200")],
        [InlineKeyboardButton("🔔 Bildirim merkezine dön", callback_data=f"notify_center:{owner}")],
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
    lines = [f"<b>📋 {title}</b>", ""]
    keyboard: list[list[InlineKeyboardButton]] = []
    for row in active[:50]:
        feature = FEATURE_LABELS.get(str(row.get("feature_key")), str(row.get("feature_key", "Bildirim")))
        params = row.get("params") or {}
        city = f" · {html.escape(str(params.get('city')))}" if params.get("city") else ""
        schedule = row.get("schedule_kind")
        if schedule == "daily":
            cadence = "her gün"
        elif schedule == "weekly":
            cadence = "her hafta"
        else:
            cadence = f"{max(1, int(row.get('interval_seconds', 0)) // 60)} dk aralık"
        lines.append(
            f"• <b>{html.escape(feature)}</b>{city} · {html.escape(cadence)} "
            f"· <code>{html.escape(str(row.get('subscription_id', ''))[:8])}</code>"
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
        keyboard.append([InlineKeyboardButton("🛑 Durdur", callback_data=callback)])
    if not active:
        lines.append("Henüz aktif bildirim yok.")
    return "\n".join(lines), InlineKeyboardMarkup(keyboard) if keyboard else None
