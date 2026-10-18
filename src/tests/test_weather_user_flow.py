from __future__ import annotations

from config import Config
from src.features.city_directory import CityDirectory, normalize_text
from src.services.subscription_service import SubscriptionService
from src.services.weather_ui import (
    earthquake_actions,
    earthquake_group_frequencies,
    earthquake_group_times,
    earthquake_intervals,
    format_interval,
    weather_actions,
    weather_city_choices,
    weather_intervals,
)
from src.storage.subscription_store import SubscriptionStore


def test_turkish_city_normalization_supports_short_forms_and_diacritics():
    directory = CityDirectory(Config.CITY_DATA_PATH)
    assert directory.search("ist", limit=1)[0].name == "İstanbul"
    assert directory.by_name("Şanlıurfa").name == "Şanlıurfa"
    assert normalize_text("Çeşme") == "cesme"


def test_weather_keyboard_callbacks_are_owner_scoped_and_compact():
    markup = weather_actions(123456, 34)
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert "weather_opts:123456:34" in callbacks
    assert "weather_add:123456:34:86400" in callbacks
    assert "weather_forecast:123456:34:7" in callbacks
    assert "air_quality:123456:34" in callbacks
    assert all(len(value.encode("utf-8")) <= 64 for value in callbacks)
    assert weather_intervals(123456, 34).inline_keyboard
    assert weather_city_choices(123456, [CityDirectory(Config.CITY_DATA_PATH).by_plate(34)]).inline_keyboard


def test_tracking_without_interval_uses_feature_default():
    request = SubscriptionService(SubscriptionStore()).parse(["hava", "ist"])
    assert request.interval_seconds == 3 * 60 * 60
    assert request.params == {"city": "ist"}
    assert SubscriptionService(SubscriptionStore()).parse(["hava", "ist", "1hafta"]).interval_seconds == 604800
    assert format_interval(604800) == "1 hafta"


def test_group_weather_and_earthquake_controls_are_admin_scoped_and_compact():
    weather = weather_actions(123456, 34, is_group=True, can_manage=True)
    earthquake = earthquake_actions(123456, is_group=True, can_manage=True)
    callbacks = [
        button.callback_data
        for markup in [
            weather,
            earthquake,
            earthquake_group_frequencies(123456),
            earthquake_group_times(123456, "daily"),
            earthquake_intervals(123456),
        ]
        for row in markup.inline_keyboard
        for button in row
    ]
    assert any(value.startswith("weather_group_opts:") for value in callbacks)
    assert any(value.startswith("weather_forecast:") for value in callbacks)
    assert any(value.startswith("earthquake_group_opts:") for value in callbacks)
    assert all(len(value.encode("utf-8")) <= 64 for value in callbacks)

    member_markup = earthquake_actions(123456, is_group=True, can_manage=False)
    assert member_markup.inline_keyboard[0][0].callback_data == "earthquake_admin_only"
