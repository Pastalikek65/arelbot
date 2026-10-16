"""Persistent notification preferences with a deterministic memory backend."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_TIMEZONE = "Europe/Istanbul"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class NotificationPreferenceStore:
    """Owns user-scoped notification policy, independent of subscriptions."""

    def __init__(self, database: Any | None = None) -> None:
        self.database = database
        self._items: dict[int, dict[str, Any]] = {}

    @property
    def persistent(self) -> bool:
        return self.database is not None and getattr(self.database, "notification_preferences", None) is not None

    @staticmethod
    def _validate_timezone(value: str) -> str:
        value = str(value or "").strip()
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("GeÃ§ersiz saat dilimi. Ã–rn: Europe/Istanbul") from exc
        return value

    @staticmethod
    def _validate_time(value: str) -> str:
        try:
            hour, minute = (int(part) for part in str(value).split(":", 1))
        except (TypeError, ValueError) as exc:
            raise ValueError("Saat HH:MM biÃ§iminde olmalÄ±.") from exc
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError("Saat 00:00 ile 23:59 arasÄ±nda olmalÄ±.")
        return f"{hour:02d}:{minute:02d}"

    @classmethod
    def _defaults(cls, user_id: int) -> dict[str, Any]:
        now = _now()
        return {
            "user_id": int(user_id),
            "timezone": DEFAULT_TIMEZONE,
            "quiet_hours_enabled": False,
            "quiet_start": "23:00",
            "quiet_end": "08:00",
            "daily_brief_enabled": False,
            "daily_brief_time": "08:00",
            "daily_brief_city": "Ä°stanbul",
            "daily_brief_last_sent_date": None,
            "updated_at": now,
        }

    async def get(self, user_id: int) -> dict[str, Any]:
        user_id = int(user_id)
        if self.persistent:
            item = await self.database.notification_preferences.find_one({"user_id": user_id}, {"_id": 0})
            return {**self._defaults(user_id), **(item or {})}
        return {**self._defaults(user_id), **self._items.get(user_id, {})}

    async def update(
        self,
        user_id: int,
        *,
        timezone_name: str | None = None,
        quiet_hours_enabled: bool | None = None,
        quiet_start: str | None = None,
        quiet_end: str | None = None,
        daily_brief_enabled: bool | None = None,
        daily_brief_time: str | None = None,
        daily_brief_city: str | None = None,
    ) -> dict[str, Any]:
        current = await self.get(user_id)
        if timezone_name is not None:
            current["timezone"] = self._validate_timezone(timezone_name)
        if quiet_start is not None:
            current["quiet_start"] = self._validate_time(quiet_start)
        if quiet_end is not None:
            current["quiet_end"] = self._validate_time(quiet_end)
        if current["quiet_start"] == current["quiet_end"]:
            raise ValueError("Sessiz saat baÅŸlangÄ±cÄ± ve bitiÅŸi aynÄ± olamaz.")
        if quiet_hours_enabled is not None:
            current["quiet_hours_enabled"] = bool(quiet_hours_enabled)
        if daily_brief_enabled is not None:
            current["daily_brief_enabled"] = bool(daily_brief_enabled)
        if daily_brief_time is not None:
            current["daily_brief_time"] = self._validate_time(daily_brief_time)
        if daily_brief_city is not None:
            city = str(daily_brief_city).strip()
            if not city or len(city) > 80:
                raise ValueError("Åehir adÄ± geÃ§ersiz.")
            current["daily_brief_city"] = city
        current["user_id"] = int(user_id)
        current["updated_at"] = _now()
        if self.persistent:
            await self.database.notification_preferences.update_one(
                {"user_id": int(user_id)}, {"$set": current}, upsert=True
            )
        else:
            self._items[int(user_id)] = dict(current)
        return dict(current)

    async def due_daily_brief(self, *, now: datetime | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Return enabled briefs whose local clock is at the configured minute."""
        current = now or _now()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        if self.persistent:
            cursor = self.database.notification_preferences.find(
                {"daily_brief_enabled": True}, {"_id": 0}
            ).limit(limit)
            candidates = await cursor.to_list(length=limit)
        else:
            candidates = list(self._items.values())
        due: list[dict[str, Any]] = []
        for item in candidates:
            try:
                zone = ZoneInfo(str(item.get("timezone", DEFAULT_TIMEZONE)))
                local = current.astimezone(zone)
                if local.strftime("%H:%M") != str(item.get("daily_brief_time", "08:00")):
                    continue
                if item.get("daily_brief_last_sent_date") == local.date().isoformat():
                    continue
                due.append({**self._defaults(int(item["user_id"])), **item})
            except (KeyError, TypeError, ValueError, ZoneInfoNotFoundError):
                continue
        return due[:limit]

    async def claim_daily_brief(self, user_id: int, local_date: str) -> bool:
        """Atomically claim a local date so restart/duplicate ticks send once."""
        user_id = int(user_id)
        if self.persistent:
            result = await self.database.notification_preferences.update_one(
                {
                    "user_id": user_id,
                    "daily_brief_enabled": True,
                    "$or": [
                        {"daily_brief_last_sent_date": {"$ne": str(local_date)}},
                        {"daily_brief_last_sent_date": {"$exists": False}},
                    ],
                },
                {"$set": {"daily_brief_last_sent_date": str(local_date), "updated_at": _now()}},
            )
            return bool(getattr(result, "modified_count", 0))
        current = self._items.get(user_id)
        if not current or not current.get("daily_brief_enabled"):
            return False
        if current.get("daily_brief_last_sent_date") == str(local_date):
            return False
        current["daily_brief_last_sent_date"] = str(local_date)
        current["updated_at"] = _now()
        return True

    async def release_daily_brief(self, user_id: int, local_date: str) -> None:
        user_id = int(user_id)
        if self.persistent:
            await self.database.notification_preferences.update_one(
                {"user_id": user_id, "daily_brief_last_sent_date": str(local_date)},
                {"$set": {"daily_brief_last_sent_date": None, "updated_at": _now()}},
            )
        elif self._items.get(user_id, {}).get("daily_brief_last_sent_date") == str(local_date):
            self._items[user_id]["daily_brief_last_sent_date"] = None

    @classmethod
    def _local_minutes(cls, value: str) -> int:
        hour, minute = (int(part) for part in value.split(":", 1))
        return hour * 60 + minute

    async def quiet_until(self, user_id: int, now: datetime | None = None) -> datetime | None:
        """Return the UTC end of the active quiet window, if one exists."""
        prefs = await self.get(user_id)
        if not prefs.get("quiet_hours_enabled"):
            return None
        zone = ZoneInfo(prefs.get("timezone", DEFAULT_TIMEZONE))
        current = (now or _now()).astimezone(zone)
        current_minutes = current.hour * 60 + current.minute
        start = self._local_minutes(prefs["quiet_start"])
        end = self._local_minutes(prefs["quiet_end"])
        if start == end:
            return current.astimezone(timezone.utc)
        active = (start <= current_minutes < end) if start < end else (
            current_minutes >= start or current_minutes < end
        )
        if not active:
            return None
        end_date = current.date()
        if start > end and current_minutes >= start:
            end_date += timedelta(days=1)
        local_end = current.replace(
            year=end_date.year,
            month=end_date.month,
            day=end_date.day,
            hour=end // 60,
            minute=end % 60,
            second=0,
            microsecond=0,
        )
        return local_end.astimezone(timezone.utc)
