"""Formatting helpers for user-facing Turkish local times."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


TURKEY_TIMEZONE = ZoneInfo("Europe/Istanbul")


def turkey_now(value: datetime | None = None) -> datetime:
    """Return an aware instant in Turkey time, independent of host TZ."""
    current = value if value is not None else datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(TURKEY_TIMEZONE)


def format_turkey_datetime(value: datetime, pattern: str = "%d.%m.%Y %H:%M") -> str:
    """Format a timestamp in the product's Turkey timezone, independent of host TZ."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(TURKEY_TIMEZONE).strftime(pattern)
