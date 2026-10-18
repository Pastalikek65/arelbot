"""Admin-controlled, persistent group announcement schedules.

Announcements deliberately reuse the subscription store's atomic claim and
lease semantics.  They are not public-data subscriptions: the payload is
validated once by this service and delivered by the orchestrator without
calling an external provider.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.storage.subscription_store import SubscriptionStore
from src.services.subscription_service import SubscriptionService


@dataclass(frozen=True)
class AnnouncementRequest:
    schedule_kind: str
    local_time: str
    weekday: int | None
    text: str
    timezone_name: str = "Europe/Istanbul"


class AnnouncementService:
    FEATURE_KEY = "group_announcement"
    MAX_TEXT_LENGTH = 3500
    _TIME_RE = re.compile(r"^(?P<hour>[01]?\d|2[0-3]):(?P<minute>[0-5]\d)$")
    _WEEKDAYS = {
        "pazartesi": 0,
        "salı": 1,
        "sali": 1,
        "çarşamba": 2,
        "carsamba": 2,
        "perşembe": 3,
        "persembe": 3,
        "cuma": 4,
        "cumartesi": 5,
        "pazar": 6,
    }

    def __init__(self, store: SubscriptionStore) -> None:
        self.store = store

    @classmethod
    def parse(cls, args: list[str]) -> AnnouncementRequest:
        """Parse ``/duyuru her gün 10:00 metin`` or weekly variants."""
        if len(args) < 3:
            raise ValueError(
                "Kullanım: /duyuru her gün 10:00 Mesajın veya "
                "/duyuru her pazartesi 09:00 Mesajın"
            )
        words = [str(value).strip() for value in args]
        if words[0].casefold() != "her":
            raise ValueError("Plan yalnızca 'her gün' veya 'her <gün>' biçiminde kurulabilir.")

        schedule_kind = "daily"
        weekday: int | None = None
        time_index = 2
        frequency = words[1].casefold()
        if frequency == "gün" or frequency == "gun":
            pass
        elif frequency == "hafta":
            schedule_kind = "weekly"
            # Accept both ``her hafta 10:00`` and ``her hafta pazartesi 10:00``.
            if len(words) >= 4 and words[2].casefold() in cls._WEEKDAYS:
                weekday = cls._WEEKDAYS[words[2].casefold()]
                time_index = 3
            else:
                weekday = 0
        elif frequency in cls._WEEKDAYS:
            schedule_kind = "weekly"
            weekday = cls._WEEKDAYS[frequency]
        else:
            raise ValueError("Sıklık 'her gün', 'her hafta' veya 'her pazartesi' olmalı.")

        if len(words) <= time_index + 1:
            raise ValueError("Saat ve duyuru metni gerekli. Örn: /duyuru her gün 10:00 Toplantı")
        time_value = words[time_index]
        if not cls._TIME_RE.fullmatch(time_value):
            raise ValueError("Saat HH:MM biçiminde olmalı; örn. 10:00.")
        text = " ".join(words[time_index + 1:]).strip()
        if len(text) < 2:
            raise ValueError("Duyuru metni en az 2 karakter olmalı.")
        if len(text) > cls.MAX_TEXT_LENGTH:
            raise ValueError(f"Duyuru metni {cls.MAX_TEXT_LENGTH} karakteri geçemez.")
        return AnnouncementRequest(schedule_kind, time_value, weekday, text)

    def next_run_for(self, item: dict[str, Any], now: datetime | None = None) -> datetime:
        return SubscriptionService._next_local_run(
            str(item.get("local_time") or "10:00"),
            schedule_kind=str(item.get("schedule_kind") or "daily"),
            weekday=item.get("weekday"),
            timezone_name=str(item.get("timezone") or "Europe/Istanbul"),
            now=now,
        )

    @staticmethod
    def delivery_key(item: dict[str, Any]) -> str:
        """Use the same durable send-slot identity as provider subscriptions."""
        return SubscriptionService.delivery_key(item)

    async def create(
        self,
        *,
        admin_id: int,
        chat_id: int,
        request: AnnouncementRequest,
        message_thread_id: int | None = None,
    ) -> dict[str, Any]:
        params = {"text": request.text}
        existing = await self.store.find_group_active(
            chat_id,
            self.FEATURE_KEY,
            params,
            request.schedule_kind,
            request.local_time,
            request.weekday,
            message_thread_id=message_thread_id,
        )
        if existing:
            return existing
        return await self.store.create(
            user_id=admin_id,
            chat_id=chat_id,
            feature_key=self.FEATURE_KEY,
            params=params,
            interval_seconds=86400 if request.schedule_kind == "daily" else 604800,
            scope="group",
            created_by=admin_id,
            schedule_kind=request.schedule_kind,
            local_time=request.local_time,
            weekday=request.weekday,
            timezone_name=request.timezone_name,
            next_run_at=self.next_run_for({
                "local_time": request.local_time,
                "schedule_kind": request.schedule_kind,
                "weekday": request.weekday,
                "timezone": request.timezone_name,
            }),
            message_thread_id=message_thread_id,
        )

    async def list_for_chat(self, chat_id: int) -> list[dict[str, Any]]:
        rows = await self.store.list_for_chat(chat_id)
        return [row for row in rows if row.get("feature_key") == self.FEATURE_KEY and row.get("enabled")]

    async def disable(self, chat_id: int, subscription_id: str) -> bool:
        return await self.store.disable_for_chat(chat_id, subscription_id)
