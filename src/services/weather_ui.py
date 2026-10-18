"""Compact callback keyboards for the user-facing weather flow."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def format_interval(seconds: int) -> str:
    seconds = int(seconds)
    if seconds % 604800 == 0:
        return f"{seconds // 604800} hafta"
    if seconds % 86400 == 0:
        return f"{seconds // 86400} gün"
    if seconds % 3600 == 0:
        return f"{seconds // 3600} saat"
    return f"{max(1, seconds // 60)} dakika"


def _weather_actions_base(
    user_id: int,
    plate: int,
    *,
    is_group: bool = False,
    can_manage: bool = False,
) -> InlineKeyboardMarkup:
    """Actions shown after a weather result."""
    if is_group:
        if can_manage:
            return InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("📣 Grup bildirimi ayarla", callback_data=f"weather_group_opts:{user_id}:{plate}")],
                    [InlineKeyboardButton("📋 Grup bildirimleri", callback_data=f"weather_group_list:{user_id}:{plate}")],
                ]
            )
        return InlineKeyboardMarkup(
            [[InlineKeyboardButton("ℹ️ Bildirimleri grup yöneticileri yönetir", callback_data="weather_admin_only")]]
        )
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔔 Bildirim kur", callback_data=f"weather_opts:{user_id}:{plate}")],
            [
                InlineKeyboardButton("🌅 Günlük", callback_data=f"weather_add:{user_id}:{plate}:86400"),
                InlineKeyboardButton("📅 Haftalık", callback_data=f"weather_add:{user_id}:{plate}:604800"),
            ],
            [InlineKeyboardButton("📋 Takiplerim", callback_data=f"weather_list:{user_id}:{plate}")],
        ]
    )


def weather_actions(
    user_id: int,
    plate: int,
    *,
    is_group: bool = False,
    can_manage: bool = False,
) -> InlineKeyboardMarkup:
    """Add forecast horizons to the shared weather result keyboard."""
    markup = _weather_actions_base(user_id, plate, is_group=is_group, can_manage=can_manage)
    rows = [list(row) for row in markup.inline_keyboard]
    rows.insert(
        3 if not is_group else len(rows),
        [
            InlineKeyboardButton("1 gün", callback_data=f"weather_forecast:{user_id}:{plate}:1"),
            InlineKeyboardButton("3 gün", callback_data=f"weather_forecast:{user_id}:{plate}:3"),
            InlineKeyboardButton("7 gün", callback_data=f"weather_forecast:{user_id}:{plate}:7"),
        ],
    )
    rows.insert(
        4 if not is_group else len(rows),
        [InlineKeyboardButton("🌿 Hava kalitesi", callback_data=f"air_quality:{user_id}:{plate}")],
    )
    return InlineKeyboardMarkup(rows)


def air_quality_actions(user_id: int, plate: int | None) -> InlineKeyboardMarkup:
    """Return safe navigation after an air-quality result."""
    rows = []
    if plate is not None:
        rows.append([InlineKeyboardButton("↩️ Hava durumuna dön", callback_data=f"weather_show:{int(user_id)}:{int(plate)}")])
    rows.append([InlineKeyboardButton("🏠 Ana Menü", callback_data="menu_main")])
    return InlineKeyboardMarkup(rows)


def air_quality_intervals(user_id: int, plate: int) -> InlineKeyboardMarkup:
    """Personal air-quality tracking intervals."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("AQI 3 saat", callback_data=f"notify_air_quality_add:{user_id}:{plate}:10800"),
                InlineKeyboardButton("AQI 6 saat", callback_data=f"notify_air_quality_add:{user_id}:{plate}:21600"),
            ],
            [
                InlineKeyboardButton("Her gün", callback_data=f"notify_air_quality_add:{user_id}:{plate}:86400"),
                InlineKeyboardButton("Her hafta", callback_data=f"notify_air_quality_add:{user_id}:{plate}:604800"),
            ],
            [InlineKeyboardButton("Bildirim merkezine dön", callback_data=f"notify_center:{user_id}")],
        ]
    )


def weather_intervals(user_id: int, plate: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⏱ 3 sa", callback_data=f"weather_add:{user_id}:{plate}:10800"),
                InlineKeyboardButton("⏱ 6 sa", callback_data=f"weather_add:{user_id}:{plate}:21600"),
            ],
            [
                InlineKeyboardButton("🌅 Her gün", callback_data=f"weather_add:{user_id}:{plate}:86400"),
                InlineKeyboardButton("📅 Her hafta", callback_data=f"weather_add:{user_id}:{plate}:604800"),
            ],
            [InlineKeyboardButton("↩️ Hava durumuna dön", callback_data=f"weather_show:{user_id}:{plate}")],
        ]
    )


def weather_group_frequencies(user_id: int, plate: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🌅 Her gün", callback_data=f"weather_group_freq:{user_id}:{plate}:daily")],
            [InlineKeyboardButton("📅 Her hafta (Pazartesi)", callback_data=f"weather_group_freq:{user_id}:{plate}:weekly")],
            [InlineKeyboardButton("↩️ Hava durumuna dön", callback_data=f"weather_show:{user_id}:{plate}")],
        ]
    )


def weather_group_times(user_id: int, plate: int, schedule_kind: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("08:00", callback_data=f"weather_group_time:{user_id}:{plate}:{schedule_kind}:08:00"),
                InlineKeyboardButton("10:00", callback_data=f"weather_group_time:{user_id}:{plate}:{schedule_kind}:10:00"),
            ],
            [
                InlineKeyboardButton("18:00", callback_data=f"weather_group_time:{user_id}:{plate}:{schedule_kind}:18:00"),
                InlineKeyboardButton("22:00", callback_data=f"weather_group_time:{user_id}:{plate}:{schedule_kind}:22:00"),
            ],
            [InlineKeyboardButton("↩️ Frekansa dön", callback_data=f"weather_group_opts:{user_id}:{plate}")],
        ]
    )


def air_quality_group_frequencies(user_id: int, plate: int) -> InlineKeyboardMarkup:
    """Frequencies for a group air-quality schedule."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🌅 Her gün", callback_data=f"notify_group_air_quality_freq:{user_id}:{plate}:daily")],
            [InlineKeyboardButton("📅 Her hafta (Pazartesi)", callback_data=f"notify_group_air_quality_freq:{user_id}:{plate}:weekly")],
            [InlineKeyboardButton("↩️ Bildirim merkezine dön", callback_data=f"notify_center:{user_id}")],
        ]
    )


def air_quality_group_times(user_id: int, plate: int, schedule_kind: str) -> InlineKeyboardMarkup:
    """Time choices for a group air-quality schedule."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("08:00", callback_data=f"notify_group_air_quality_time:{user_id}:{plate}:{schedule_kind}:08:00"),
                InlineKeyboardButton("10:00", callback_data=f"notify_group_air_quality_time:{user_id}:{plate}:{schedule_kind}:10:00"),
            ],
            [
                InlineKeyboardButton("18:00", callback_data=f"notify_group_air_quality_time:{user_id}:{plate}:{schedule_kind}:18:00"),
                InlineKeyboardButton("22:00", callback_data=f"notify_group_air_quality_time:{user_id}:{plate}:{schedule_kind}:22:00"),
            ],
            [InlineKeyboardButton("↩️ Frekansa dön", callback_data=f"notify_group_air_quality:{user_id}")],
        ]
    )


def news_group_frequencies(user_id: int) -> InlineKeyboardMarkup:
    """Frequencies for a group breaking-news digest."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🌅 Her gün", callback_data=f"notify_group_news_freq:{user_id}:daily")],
            [InlineKeyboardButton("📅 Her hafta (Pazartesi)", callback_data=f"notify_group_news_freq:{user_id}:weekly")],
            [InlineKeyboardButton("↩️ Bildirim merkezine dön", callback_data=f"notify_center:{user_id}")],
        ]
    )


def news_group_times(user_id: int, schedule_kind: str) -> InlineKeyboardMarkup:
    """Time choices for a group breaking-news digest."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("08:00", callback_data=f"notify_group_news_time:{user_id}:{schedule_kind}:08:00"),
                InlineKeyboardButton("10:00", callback_data=f"notify_group_news_time:{user_id}:{schedule_kind}:10:00"),
            ],
            [
                InlineKeyboardButton("18:00", callback_data=f"notify_group_news_time:{user_id}:{schedule_kind}:18:00"),
                InlineKeyboardButton("22:00", callback_data=f"notify_group_news_time:{user_id}:{schedule_kind}:22:00"),
            ],
            [InlineKeyboardButton("↩️ Frekansa dön", callback_data=f"notify_group_news:{user_id}")],
        ]
    )


def weather_city_choices(user_id: int, cities) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(city.name, callback_data=f"weather_pick:{user_id}:{city.plate}")]
        for city in cities
    ]
    return InlineKeyboardMarkup(rows)


def earthquake_actions(
    user_id: int,
    *,
    is_group: bool = False,
    can_manage: bool = False,
) -> InlineKeyboardMarkup:
    """Actions shown after the earthquake feed.

    Group subscriptions are deliberately separated from personal tracking so a
    member cannot accidentally create a notification for the whole chat.
    """
    if is_group:
        if can_manage:
            return InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("📣 Grup deprem bildirimi ayarla", callback_data=f"earthquake_group_opts:{user_id}")],
                    [InlineKeyboardButton("📋 Grup deprem bildirimleri", callback_data=f"earthquake_group_list:{user_id}")],
                ]
            )
        return InlineKeyboardMarkup(
            [[InlineKeyboardButton("ℹ️ Bildirimleri grup yöneticileri yönetir", callback_data="earthquake_admin_only")]]
        )
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔔 Deprem bildirimi kur", callback_data=f"earthquake_opts:{user_id}")],
            [
                InlineKeyboardButton("⏱ 15 dk", callback_data=f"earthquake_add:{user_id}:900"),
                InlineKeyboardButton("⏱ 1 saat", callback_data=f"earthquake_add:{user_id}:3600"),
            ],
            [InlineKeyboardButton("📋 Deprem takiplerim", callback_data=f"earthquake_list:{user_id}")],
        ]
    )


def earthquake_intervals(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⏱ 15 dk", callback_data=f"earthquake_add:{user_id}:900"),
                InlineKeyboardButton("⏱ 1 saat", callback_data=f"earthquake_add:{user_id}:3600"),
            ],
            [
                InlineKeyboardButton("🌅 Her gün", callback_data=f"earthquake_add:{user_id}:86400"),
                InlineKeyboardButton("📅 Her hafta", callback_data=f"earthquake_add:{user_id}:604800"),
            ],
            [InlineKeyboardButton("↩️ Deprem akışına dön", callback_data=f"earthquake_show:{user_id}")],
        ]
    )


def earthquake_group_frequencies(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🌅 Her gün", callback_data=f"earthquake_group_freq:{user_id}:daily")],
            [InlineKeyboardButton("📅 Her hafta (Pazartesi)", callback_data=f"earthquake_group_freq:{user_id}:weekly")],
            [InlineKeyboardButton("↩️ Deprem akışına dön", callback_data=f"earthquake_show:{user_id}")],
        ]
    )


def earthquake_group_times(user_id: int, schedule_kind: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("08:00", callback_data=f"earthquake_group_time:{user_id}:{schedule_kind}:08:00"),
                InlineKeyboardButton("10:00", callback_data=f"earthquake_group_time:{user_id}:{schedule_kind}:10:00"),
            ],
            [
                InlineKeyboardButton("18:00", callback_data=f"earthquake_group_time:{user_id}:{schedule_kind}:18:00"),
                InlineKeyboardButton("22:00", callback_data=f"earthquake_group_time:{user_id}:{schedule_kind}:22:00"),
            ],
            [InlineKeyboardButton("↩️ Frekansa dön", callback_data=f"earthquake_group_opts:{user_id}")],
        ]
    )
