"""User-facing subscription validation and feature parameter parsing."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from src.storage.subscription_store import SubscriptionStore
from src.security.log_redaction import summarize_exception


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SubscriptionRequest:
    feature_key: str
    params: dict[str, Any]
    interval_seconds: int
    human_interval: str


@dataclass(frozen=True)
class ThresholdRequest:
    feature_key: str
    condition: dict[str, Any]
    interval_seconds: int
    human_text: str


class SubscriptionService:
    MIN_INTERVAL_SECONDS = 15 * 60
    MAX_INTERVAL_SECONDS = 30 * 86400
    # Keep the Turkish spellings as real Unicode in the pattern.  A raw
    # string treats ``\u00fc``/``\u0131`` as literal backslash sequences,
    # which silently rejected otherwise valid input such as ``1gün``.
    DURATION_RE_TR = re.compile(
        r"^(?P<num>\d+)\s*(?P<unit>dk|dakika|m|sa|saat|h|gün|gun|günlük|gunluk|d|hf|hafta|haftalık)$",
        re.IGNORECASE,
    )
    DURATION_RE = re.compile(r"^(?P<num>\d+)\s*(?P<unit>dk|m|sa|h|gün|gun|d)$", re.IGNORECASE)
    ALLOWED_FEATURES = {
        "weather",
        "air_quality",
        "earthquakes",
        "breaking_news",
        "currency",
        "crypto",
        "fuel",
        "bim_deals",
        "prayer_times",
    }
    DEFAULT_INTERVALS = {
        "weather": 3 * 60 * 60,
        "air_quality": 3 * 60 * 60,
        "earthquakes": 15 * 60,
        "breaking_news": 2 * 60 * 60,
        "currency": 60 * 60,
        "crypto": 15 * 60,
        "fuel": 6 * 60 * 60,
        "bim_deals": 12 * 60 * 60,
        "prayer_times": 24 * 60 * 60,
    }
    ALIASES = {
        "hava": "weather",
        "hava_kalite": "air_quality",
        "hava_kalitesi": "air_quality",
        "kalite": "air_quality",
        "deprem": "earthquakes",
        "haber": "breaking_news",
        "doviz": "currency",
        "altin": "currency",
        "kripto": "crypto",
        "crypto": "crypto",
        "akaryakit": "fuel",
        "bim": "bim_deals",
        "ezan": "prayer_times",
    }

    ALERT_FEATURES = {"currency", "fuel", "crypto"}
    CONTENT_DEDUP_FEATURES = {"earthquakes", "air_quality", "breaking_news", "bim_deals"}

    @classmethod
    def dedupe_policy(cls, feature_key: str, condition: dict[str, Any] | None = None) -> str:
        """Return the delivery policy for a scheduled feature.

        Weather and prayer notifications are time-based and intentionally
        repeat. Feed and threshold notifications should advance only when the
        observed payload changes, otherwise a cache refresh would spam users.
        """
        if feature_key in cls.CONTENT_DEDUP_FEATURES:
            return "content"
        if condition and feature_key in cls.ALERT_FEATURES:
            return "condition"
        return "none"

    @classmethod
    def fingerprint(cls, item: dict[str, Any], data: Any) -> str | None:
        if item.get("dedupe_policy", "none") == "none":
            return None
        canonical = json.dumps(
            data, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @classmethod
    def should_deliver(cls, item: dict[str, Any], data: Any) -> tuple[bool, str | None]:
        fingerprint = cls.fingerprint(item, data)
        if fingerprint is None:
            return True, None
        return fingerprint != item.get("last_fingerprint"), fingerprint

    @staticmethod
    def delivery_key(item: dict[str, Any]) -> str:
        """Build a stable key for one scheduled execution window.

        ``next_run_at`` is advanced only after the current delivery is
        completed. It therefore identifies the exact notification slot even
        when a worker restarts between Telegram send and subscription update.
        """
        subscription_id = str(item.get("subscription_id") or "unknown")
        scheduled_at = item.get("next_run_at")
        if isinstance(scheduled_at, datetime):
            scheduled_value = scheduled_at.astimezone(timezone.utc).isoformat()
        else:
            scheduled_value = str(scheduled_at or "unknown")
        return f"{subscription_id}:{scheduled_value}"

    def __init__(self, store: SubscriptionStore) -> None:
        self.store = store

    @classmethod
    def _validate_request(cls, request: SubscriptionRequest) -> None:
        if request.feature_key not in cls.ALLOWED_FEATURES:
            raise ValueError("Bu bildirim özelliği desteklenmiyor.")
        try:
            interval = int(request.interval_seconds)
        except (TypeError, ValueError) as exc:
            raise ValueError("Bildirim aralığı geçersiz.") from exc
        if not cls.MIN_INTERVAL_SECONDS <= interval <= cls.MAX_INTERVAL_SECONDS:
            raise ValueError("Bildirim aralığı 15 dakika ile 30 gün arasında olmalı.")
        if not isinstance(request.params, dict) or len(request.params) > 8:
            raise ValueError("Bildirim parametreleri geçersiz.")

    async def _audit_group_schedule(
        self,
        *,
        admin_id: int,
        chat_id: int,
        item: dict[str, Any],
        action: str,
    ) -> None:
        """Write a bounded audit event when the persistent database supports it."""
        database = getattr(self.store, "database", None)
        audit = getattr(database, "add_audit_log", None)
        if not callable(audit):
            return
        try:
            await audit(
                int(admin_id),
                action,
                chat_id=int(chat_id),
                subscription_id=str(item.get("subscription_id") or "")[:64],
                feature_key=str(item.get("feature_key") or "")[:64],
                schedule_kind=str(item.get("schedule_kind") or "")[:16],
                local_time=str(item.get("local_time") or "")[:5],
                message_thread_id=item.get("message_thread_id"),
            )
        except Exception as exc:
            # Audit failure must not duplicate or block a successfully persisted
            # schedule; the bounded warning remains observable to operations.
            logger.warning(
                "Group notification audit failed action=%s feature=%s (%s)",
                str(action)[:64],
                str(item.get("feature_key") or "")[:64],
                summarize_exception(exc, label="group_notification_audit"),
            )
            return

    @staticmethod
    def _next_local_run(
        local_time: str,
        *,
        schedule_kind: str = "daily",
        weekday: int | None = None,
        timezone_name: str = "Europe/Istanbul",
        now: datetime | None = None,
    ) -> datetime:
        try:
            hour, minute = (int(value) for value in local_time.split(":", 1))
            zone = ZoneInfo(timezone_name)
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError("Geçersiz saat veya saat dilimi.") from exc
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError("Saat 00:00 ile 23:59 arasında olmalıdır.")
        current = (now or datetime.now(timezone.utc)).astimezone(zone)
        candidate = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if schedule_kind == "weekly":
            target_day = 0 if weekday is None else int(weekday)
            candidate += timedelta(days=(target_day - candidate.weekday()) % 7)
            if candidate <= current:
                candidate += timedelta(days=7)
        else:
            if candidate <= current:
                candidate += timedelta(days=1)
        return candidate.astimezone(timezone.utc)

    def next_run_for(self, item: dict[str, Any], now: datetime | None = None) -> datetime:
        if item.get("schedule_kind") in {"daily", "weekly"} and item.get("local_time"):
            return self._next_local_run(
                item["local_time"],
                schedule_kind=item.get("schedule_kind", "daily"),
                weekday=item.get("weekday"),
                timezone_name=item.get("timezone", "Europe/Istanbul"),
                now=now,
            )
        return (now or datetime.now(timezone.utc)) + timedelta(seconds=max(900, int(item.get("interval_seconds", 3600))))

    def parse(self, args: list[str]) -> SubscriptionRequest:
        if not args:
            raise ValueError("Kullanım: /takip hava istanbul")
        first = args[0].casefold()
        # Keep the natural Turkish form while routing to the typed feature.
        if first in {"hava", "weather"} and len(args) > 1 and args[1].casefold() in {
            "kalite", "kalitesi", "hava-kalite", "aqi", "air",
        }:
            feature = "air_quality"
            feature_args = args[2:]
        else:
            feature = self.ALIASES.get(first, first)
            feature_args = args[1:]
        if feature not in self.ALLOWED_FEATURES:
            raise ValueError("Bu özellik için abonelik desteklenmiyor.")
        interval_raw = "varsayılan"
        body = feature_args
        interval = self.DEFAULT_INTERVALS[feature]
        if feature_args:
            candidate = feature_args[-1].casefold()
            match = self.DURATION_RE_TR.match(candidate)
            if match:
                interval_raw = candidate
                value = int(match.group("num"))
                unit = match.group("unit")
                multiplier = (
                    60 if unit in {"dk", "dakika", "m"}
                    else 3600 if unit in {"sa", "saat", "h"}
                    else 604800 if unit in {"hf", "hafta", "haftalık"}
                    else 86400
                )
                interval = value * multiplier
                body = feature_args[:-1]
        if interval < 15 * 60 or interval > 30 * 86400:
            raise ValueError("Aralık 15dk ile 30gün arasında olmalıdır.")
        params: dict[str, Any] = {}
        if feature in {"weather", "air_quality", "prayer_times", "crypto"}:
            if not body:
                example = (
                    "/takip kripto btc 15dk" if feature == "crypto"
                    else "/takip hava kalite istanbul 3sa" if feature == "air_quality"
                    else "/takip hava istanbul 1sa"
                )
                raise ValueError(f"{'Coin' if feature == 'crypto' else 'Şehir'} belirtin. Örnek: {example}")
            params["symbol" if feature == "crypto" else "city"] = " ".join(body)[:80]
        elif body:
            raise ValueError("Bu özellik için yalnızca aralık yazılmalıdır.")
        return SubscriptionRequest(feature, params, interval, interval_raw)

    def parse_threshold(self, args: list[str]) -> ThresholdRequest:
        """Parse a currency, fuel or crypto threshold alert."""
        if len(args) < 3:
            raise ValueError("Kullanım: /alarm kur usd 40 üstü")
        first = args[0].casefold()
        feature = self.ALIASES.get(first, first)
        if feature in self.ALERT_FEATURES:
            metric = args[1].casefold()
            value_raw = args[2]
            operator_raw = args[3].casefold() if len(args) >= 4 else "üstü"
        elif first in {"usd", "dolar", "eur", "euro", "gbp", "sterlin", "altin", "altın"}:
            feature = "currency"
            metric = first
            value_raw = args[1]
            operator_raw = args[2].casefold() if len(args) >= 3 else "üstü"
        elif first in {"btc", "bitcoin", "eth", "ethereum", "sol", "solana", "xrp", "doge", "dogecoin", "avax", "dot", "link"}:
            feature = "crypto"
            metric = first
            value_raw = args[1]
            operator_raw = args[2].casefold() if len(args) >= 3 else "üstü"
        elif first in {"gasoline", "benzin", "diesel", "motorin", "lpg"}:
            feature = "fuel"
            metric = first
            value_raw = args[1]
            operator_raw = args[2].casefold() if len(args) >= 3 else "üstü"
        else:
            raise ValueError("Bu veri için eşik alarmı desteklenmiyor.")
        operators = {
            "üstü": "above", "üzeri": "above", "above": "above", ">": "above",
            "altı": "below", "altında": "below", "below": "below", "<": "below",
        }
        operator = operators.get(operator_raw)
        if operator is None:
            raise ValueError("Yön belirtin: üstü veya altı.")
        try:
            value = float(value_raw.replace(",", "."))
        except ValueError as exc:
            raise ValueError("Eşik sayısal olmalı.") from exc
        if value <= 0 or value > 1_000_000:
            raise ValueError("Eşik 0 ile 1.000.000 arasında olmalı.")
        if feature == "currency" and len(metric) < 2:
            raise ValueError("Kur kodu belirtin; örn: usd veya euro.")
        if feature == "fuel" and metric not in {"gasoline", "benzin", "diesel", "motorin", "lpg"}:
            raise ValueError("Yakıt metriği benzin, motorin veya lpg olmalı.")
        metric = {"benzin": "gasoline", "motorin": "diesel"}.get(metric, metric)
        metric = {
            "btc": "bitcoin", "eth": "ethereum", "sol": "solana", "xrp": "ripple",
            "doge": "dogecoin", "avax": "avalanche-2", "dot": "polkadot", "link": "chainlink",
        }.get(metric, metric)
        return ThresholdRequest(
            feature_key=feature,
            condition={"kind": "threshold", "metric": metric, "operator": operator, "value": value},
            interval_seconds=self.DEFAULT_INTERVALS.get(feature, 3600),
            human_text=f"{metric} {operator_raw} {value:g}",
        )

    async def create_threshold(self, user_id: int, chat_id: int, request: ThresholdRequest) -> dict[str, Any]:
        if request.feature_key not in self.ALERT_FEATURES:
            raise ValueError("Bu veri için eşik alarmı desteklenmiyor.")
        if not self.MIN_INTERVAL_SECONDS <= int(request.interval_seconds) <= self.MAX_INTERVAL_SECONDS:
            raise ValueError("Alarm aralığı 15 dakika ile 30 gün arasında olmalı.")
        params = {"symbol": request.condition.get("metric"), "vs_currency": "usd"} if request.feature_key == "crypto" else {}
        existing = await self.store.find_active(user_id, request.feature_key, params, request.condition)
        if existing:
            policy = self.dedupe_policy(request.feature_key, request.condition)
            return await self.store.update_dedupe_policy(existing["subscription_id"], policy) or existing
        return await self.store.create(
            user_id=user_id,
            chat_id=chat_id,
            feature_key=request.feature_key,
            params=params,
            condition=request.condition,
            interval_seconds=request.interval_seconds,
            dedupe_policy=self.dedupe_policy(request.feature_key, request.condition),
        )

    async def create(self, user_id: int, chat_id: int, request: SubscriptionRequest) -> dict[str, Any]:
        self._validate_request(request)
        existing = await self.store.find_active(user_id, request.feature_key, request.params)
        if existing:
            updated = await self.store.update_interval(existing["subscription_id"], request.interval_seconds)
            policy = self.dedupe_policy(request.feature_key)
            refreshed = await self.store.update_dedupe_policy(existing["subscription_id"], policy)
            return refreshed or updated or existing
        return await self.store.create(
            user_id=user_id,
            chat_id=chat_id,
            feature_key=request.feature_key,
            params=request.params,
            interval_seconds=request.interval_seconds,
            dedupe_policy=self.dedupe_policy(request.feature_key),
        )

    async def create_group_schedule(
        self,
        *,
        admin_id: int,
        chat_id: int,
        feature_key: str,
        params: dict[str, Any],
        schedule_kind: str,
        local_time: str,
        weekday: int | None = None,
        timezone_name: str = "Europe/Istanbul",
        message_thread_id: int | None = None,
    ) -> dict[str, Any]:
        if feature_key not in self.ALLOWED_FEATURES:
            raise ValueError("Bu bildirim özelliği desteklenmiyor.")
        if schedule_kind not in {"daily", "weekly"}:
            raise ValueError("Grup planı günlük veya haftalık olmalıdır.")
        interval = 86400 if schedule_kind == "daily" else 604800
        next_run = self._next_local_run(
            local_time,
            schedule_kind=schedule_kind,
            weekday=weekday,
            timezone_name=timezone_name,
        )
        existing = await self.store.find_group_active(
            chat_id, feature_key, params, schedule_kind, local_time, weekday,
            message_thread_id=message_thread_id,
        )
        if existing:
            updated = await self.store.update_schedule(
                existing["subscription_id"],
                interval_seconds=interval,
                schedule_kind=schedule_kind,
                local_time=local_time,
                weekday=weekday,
                next_run_at=next_run,
            ) or existing
            policy = self.dedupe_policy(feature_key)
            item = await self.store.update_dedupe_policy(existing["subscription_id"], policy) or updated
            await self._audit_group_schedule(
                admin_id=admin_id,
                chat_id=chat_id,
                item=item,
                action="group_notification_schedule_updated",
            )
            return item
        item = await self.store.create(
            user_id=admin_id,
            chat_id=chat_id,
            feature_key=feature_key,
            params=params,
            interval_seconds=interval,
            scope="group",
            created_by=admin_id,
            schedule_kind=schedule_kind,
            local_time=local_time,
            weekday=weekday,
            timezone_name=timezone_name,
            next_run_at=next_run,
            dedupe_policy=self.dedupe_policy(feature_key),
            message_thread_id=message_thread_id,
        )
        await self._audit_group_schedule(
            admin_id=admin_id,
            chat_id=chat_id,
            item=item,
            action="group_notification_schedule_created",
        )
        return item

