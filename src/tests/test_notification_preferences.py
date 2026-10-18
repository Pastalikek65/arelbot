from datetime import datetime, timezone

import pytest

from src.storage.notification_preference_store import NotificationPreferenceStore


@pytest.mark.asyncio
async def test_quiet_window_supports_cross_midnight_and_timezone():
    store = NotificationPreferenceStore()
    await store.update(
        42,
        timezone_name="Europe/Istanbul",
        quiet_hours_enabled=True,
        quiet_start="23:00",
        quiet_end="08:00",
    )

    active = await store.quiet_until(42, datetime(2026, 10, 5, 22, 30, tzinfo=timezone.utc))
    assert active == datetime(2026, 10, 6, 5, 0, tzinfo=timezone.utc)

    inactive = await store.quiet_until(42, datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc))
    assert inactive is None


@pytest.mark.asyncio
async def test_preferences_validate_values_and_can_disable_quiet_hours():
    store = NotificationPreferenceStore()
    with pytest.raises(ValueError, match="Saat"):
        await store.update(7, quiet_start="25:00")
    with pytest.raises(ValueError, match="saat dilimi"):
        await store.update(7, timezone_name="Mars/Phobos")
    with pytest.raises(ValueError, match="aynı olamaz"):
        await store.update(7, quiet_start="08:00", quiet_end="08:00")

    await store.update(7, quiet_hours_enabled=True)
    await store.update(7, quiet_hours_enabled=False)
    assert await store.quiet_until(7, datetime.now(timezone.utc)) is None


@pytest.mark.asyncio
async def test_timezone_preference_is_persistent_in_memory_backend():
    store = NotificationPreferenceStore()
    saved = await store.update(7, timezone_name="Asia/Tokyo")
    assert saved["timezone"] == "Asia/Tokyo"
    assert (await store.get(7))["timezone"] == "Asia/Tokyo"
