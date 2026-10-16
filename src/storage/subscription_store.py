"""Persistent and in-memory subscription store with atomic delivery claims."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import asyncio
from typing import Any
from uuid import uuid4


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SubscriptionStore:
    MIN_INTERVAL_SECONDS = 15 * 60
    MAX_INTERVAL_SECONDS = 30 * 86400

    def __init__(self, database: Any | None = None) -> None:
        self.database = database
        self._items: dict[str, dict[str, Any]] = {}
        # The in-memory backend follows the same delivery-ledger contract as
        # Mongo.  The lock makes concurrent test workers deterministic and
        # mirrors Mongo's unique delivery-key index.
        self._deliveries: dict[str, dict[str, Any]] = {}
        self._delivery_lock = asyncio.Lock()

    @property
    def persistent(self) -> bool:
        return self.database is not None and getattr(self.database, "subscriptions", None) is not None

    async def create(
        self,
        *,
        user_id: int,
        chat_id: int,
        feature_key: str,
        params: dict[str, Any],
        interval_seconds: int,
        scope: str = "user",
        created_by: int | None = None,
        schedule_kind: str = "interval",
        local_time: str | None = None,
        weekday: int | None = None,
        timezone_name: str = "Europe/Istanbul",
        next_run_at: datetime | None = None,
        condition: dict[str, Any] | None = None,
        dedupe_policy: str = "none",
        message_thread_id: int | None = None,
    ) -> dict[str, Any]:
        try:
            interval = int(interval_seconds)
        except (TypeError, ValueError) as exc:
            raise ValueError("subscription_interval_invalid") from exc
        if not self.MIN_INTERVAL_SECONDS <= interval <= self.MAX_INTERVAL_SECONDS:
            raise ValueError("subscription_interval_out_of_range")
        item = {
            "subscription_id": uuid4().hex,
            "user_id": int(user_id),
            "chat_id": int(chat_id),
            "message_thread_id": int(message_thread_id) if message_thread_id is not None else None,
            "scope": scope,
            "created_by": int(created_by if created_by is not None else user_id),
            "feature_key": feature_key,
            "params": dict(params),
            "condition": dict(condition or {}),
            "dedupe_policy": str(dedupe_policy or "none"),
            "last_fingerprint": None,
            "interval_seconds": interval,
            "schedule_kind": schedule_kind,
            "local_time": local_time,
            "weekday": weekday,
            "timezone": timezone_name,
            "enabled": True,
            "next_run_at": next_run_at or _now(),
            "delivery_claimed": False,
            "claimed_at": None,
            "delivery_attempt": 0,
            "created_at": _now(),
            "updated_at": _now(),
        }
        if self.persistent:
            await self.database.subscriptions.insert_one(item)
        else:
            self._items[item["subscription_id"]] = item
        return dict(item)

    async def list_for_user(self, user_id: int, limit: int = 50, scope: str = "user") -> list[dict[str, Any]]:
        if self.persistent:
            cursor = self.database.subscriptions.find(
                {"user_id": int(user_id), "scope": scope}, {"_id": 0}
            ).sort("created_at", -1).limit(limit)
            return await cursor.to_list(length=limit)
        values = [
            item for item in self._items.values()
            if item["user_id"] == int(user_id) and item.get("scope", "user") == scope
        ]
        return sorted(values, key=lambda item: item["created_at"], reverse=True)[:limit]

    async def find_active(self, user_id: int, feature_key: str, params: dict[str, Any], condition: dict[str, Any] | None = None) -> dict[str, Any] | None:
        query = {"user_id": int(user_id), "scope": "user", "feature_key": feature_key, "params": dict(params), "enabled": True}
        if condition is not None:
            query["condition"] = dict(condition)
        if self.persistent:
            return await self.database.subscriptions.find_one(query, {"_id": 0})
        return next(
            (dict(item) for item in self._items.values()
             if item["user_id"] == int(user_id)
             and item.get("scope", "user") == "user"
             and item["feature_key"] == feature_key
             and item.get("params") == dict(params)
             and (condition is None or item.get("condition", {}) == dict(condition))
             and item.get("enabled")),
            None,
        )

    async def find_group_active(
        self,
        chat_id: int,
        feature_key: str,
        params: dict[str, Any],
        schedule_kind: str,
        local_time: str,
        weekday: int | None,
        message_thread_id: int | None = None,
    ) -> dict[str, Any] | None:
        query = {
            "chat_id": int(chat_id), "scope": "group", "feature_key": feature_key,
            "params": dict(params), "schedule_kind": schedule_kind,
            "local_time": local_time, "weekday": weekday, "enabled": True,
        }
        if message_thread_id is None:
            # Older rows predate topic persistence. Treat an absent field and
            # explicit null as the same general-topic scope.
            query["$or"] = [
                {"message_thread_id": None},
                {"message_thread_id": {"$exists": False}},
            ]
        else:
            query["message_thread_id"] = int(message_thread_id)
        if self.persistent:
            return await self.database.subscriptions.find_one(query, {"_id": 0})
        def matches(item: dict[str, Any]) -> bool:
            for key, value in query.items():
                if key == "$or":
                    continue
                if item.get(key) != value:
                    return False
            if message_thread_id is None:
                return item.get("message_thread_id") is None
            return item.get("message_thread_id") == int(message_thread_id)
        return next((dict(item) for item in self._items.values() if matches(item)), None)

    async def list_for_chat(self, chat_id: int, scope: str = "group", limit: int = 50) -> list[dict[str, Any]]:
        query = {"chat_id": int(chat_id), "scope": scope}
        if self.persistent:
            cursor = self.database.subscriptions.find(query, {"_id": 0}).sort("created_at", -1).limit(limit)
            return await cursor.to_list(length=limit)
        values = [dict(item) for item in self._items.values() if all(item.get(k) == v for k, v in query.items())]
        return sorted(values, key=lambda item: item["created_at"], reverse=True)[:limit]

    async def update_interval(self, subscription_id: str, interval_seconds: int) -> dict[str, Any] | None:
        update = {
            "interval_seconds": int(interval_seconds),
            "next_run_at": _now(),
            "delivery_claimed": False,
            "claimed_at": None,
            "delivery_attempt": 0,
            "updated_at": _now(),
        }
        if self.persistent:
            await self.database.subscriptions.update_one({"subscription_id": subscription_id}, {"$set": update})
            return await self.database.subscriptions.find_one({"subscription_id": subscription_id}, {"_id": 0})
        item = self._items.get(subscription_id)
        if not item:
            return None
        item.update(update)
        return dict(item)

    async def update_dedupe_policy(self, subscription_id: str, policy: str) -> dict[str, Any] | None:
        """Backfill delivery policy for subscriptions created before dedup."""
        update = {"dedupe_policy": str(policy or "none"), "updated_at": _now()}
        if self.persistent:
            await self.database.subscriptions.update_one(
                {"subscription_id": subscription_id}, {"$set": update}
            )
            return await self.database.subscriptions.find_one({"subscription_id": subscription_id}, {"_id": 0})
        item = self._items.get(subscription_id)
        if not item:
            return None
        item.update(update)
        return dict(item)

    async def update_schedule(
        self,
        subscription_id: str,
        *,
        interval_seconds: int,
        schedule_kind: str,
        local_time: str,
        weekday: int | None,
        next_run_at: datetime,
    ) -> dict[str, Any] | None:
        update = {
            "interval_seconds": int(interval_seconds),
            "schedule_kind": schedule_kind,
            "local_time": local_time,
            "weekday": weekday,
            "next_run_at": next_run_at,
            "delivery_claimed": False,
            "claimed_at": None,
            "delivery_attempt": 0,
            "updated_at": _now(),
        }
        if self.persistent:
            await self.database.subscriptions.update_one({"subscription_id": subscription_id}, {"$set": update})
            return await self.database.subscriptions.find_one({"subscription_id": subscription_id}, {"_id": 0})
        item = self._items.get(subscription_id)
        if not item:
            return None
        item.update(update)
        return dict(item)

    async def disable_for_chat(self, chat_id: int, subscription_id: str, scope: str = "group") -> bool:
        query = {"subscription_id": subscription_id, "chat_id": int(chat_id), "scope": scope, "enabled": True}
        if self.persistent:
            result = await self.database.subscriptions.update_one(
                query, {"$set": {"enabled": False, "delivery_claimed": False, "claimed_at": None, "updated_at": _now()}}
            )
            return bool(result.modified_count)
        item = self._items.get(subscription_id)
        if not item or any(item.get(k) != v for k, v in query.items() if k != "enabled") or not item["enabled"]:
            return False
        item["enabled"] = False
        item["delivery_claimed"] = False
        item["claimed_at"] = None
        item["updated_at"] = _now()
        return True

    async def due(
        self,
        now: datetime | None = None,
        limit: int = 100,
        claim_timeout_seconds: int = 300,
    ) -> list[dict[str, Any]]:
        now = now or _now()
        claim_cutoff = now - timedelta(seconds=max(30, int(claim_timeout_seconds)))
        claim_filter = {
            "$or": [
                {"delivery_claimed": {"$ne": True}},
                {"claimed_at": {"$lte": claim_cutoff}},
                {"claimed_at": {"$exists": False}},
            ]
        }
        if self.persistent:
            cursor = self.database.subscriptions.find(
                {
                    "enabled": True,
                    "next_run_at": {"$lte": now},
                    **claim_filter,
                },
                {"_id": 0},
            ).sort("next_run_at", 1).limit(limit)
            return await cursor.to_list(length=limit)
        return [
            dict(item)
            for item in sorted(self._items.values(), key=lambda value: value["next_run_at"])
            if item["enabled"]
            and item["next_run_at"] <= now
            and (
                not item.get("delivery_claimed")
                or not item.get("claimed_at")
                or item["claimed_at"] <= claim_cutoff
            )
        ][:limit]

    async def claim(self, subscription_id: str, claim_timeout_seconds: int = 300) -> bool:
        now = _now()
        claim_cutoff = now - timedelta(seconds=max(30, int(claim_timeout_seconds)))
        claim_filter = {
            "$or": [
                {"delivery_claimed": {"$ne": True}},
                {"claimed_at": {"$lte": claim_cutoff}},
                {"claimed_at": {"$exists": False}},
            ]
        }
        if self.persistent:
            result = await self.database.subscriptions.update_one(
                {
                    "subscription_id": subscription_id,
                    "enabled": True,
                    **claim_filter,
                },
                {"$set": {"delivery_claimed": True, "claimed_at": now, "updated_at": now}},
            )
            return bool(result.modified_count)
        item = self._items.get(subscription_id)
        claim_expired = bool(item and item.get("claimed_at") and item["claimed_at"] <= claim_cutoff)
        if not item or not item["enabled"] or (item.get("delivery_claimed") and not claim_expired):
            return False
        item["delivery_claimed"] = True
        item["claimed_at"] = now
        item["updated_at"] = now
        return True

    async def complete(
        self,
        subscription_id: str,
        *,
        next_run_at: datetime,
        success: bool,
        error: str | None = None,
        delivery_attempt: int = 0,
        fingerprint: str | None = None,
    ) -> None:
        update = {
            "delivery_claimed": False,
            "claimed_at": None,
            "next_run_at": next_run_at,
            "last_run_at": _now(),
            "last_success": bool(success),
            "last_error": error[:200] if error else None,
            "delivery_attempt": max(0, int(delivery_attempt)),
            "updated_at": _now(),
        }
        if fingerprint is not None:
            update["last_fingerprint"] = str(fingerprint)[:128]
        if self.persistent:
            await self.database.subscriptions.update_one(
                {"subscription_id": subscription_id}, {"$set": update}
            )
            return
        item = self._items.get(subscription_id)
        if item:
            item.update(update)

    async def claim_delivery(
        self,
        delivery_key: str,
        *,
        subscription_id: str,
        chat_id: int,
        claim_timeout_seconds: int = 300,
    ) -> bool:
        """Claim one planned notification delivery exactly once.

        Subscription claiming prevents two workers from processing the same
        row concurrently. This second ledger protects the send boundary: if a
        worker sends to Telegram and dies before advancing ``next_run_at``, a
        restarted worker sees the durable ``sent`` marker and will not send a
        duplicate message.
        """
        now = _now()
        cutoff = now - timedelta(seconds=max(30, int(claim_timeout_seconds)))
        key = str(delivery_key)
        if self.persistent:
            collection = self.database.notification_deliveries
            existing = await collection.find_one({"delivery_key": key}, {"_id": 0})
            if existing and existing.get("status") in {"sent", "skipped", "uncertain"}:
                return False
            if existing and existing.get("claimed_at") and existing["claimed_at"] > cutoff:
                return False
            if existing and existing.get("status") == "sending":
                # Telegram has no send idempotency token. A crashed request
                # across this boundary is explicitly uncertain and is never
                # silently replayed as a duplicate notification.
                await collection.update_one(
                    {"delivery_key": key, "status": "sending"},
                    {"$set": {"status": "uncertain", "claimed_at": None, "updated_at": now}},
                )
                return False
            query = {
                "delivery_key": key,
                "status": {"$nin": ["sent", "skipped", "uncertain", "sending"]},
                "$or": [
                    {"claimed_at": {"$exists": False}},
                    {"claimed_at": None},
                    {"claimed_at": {"$lte": cutoff}},
                ],
            }
            update = {
                "$set": {
                    "subscription_id": str(subscription_id),
                    "chat_id": int(chat_id),
                    "status": "pending",
                    "claimed_at": now,
                    "updated_at": now,
                },
                "$inc": {"attempt": 1},
                "$setOnInsert": {"created_at": now},
            }
            try:
                result = await collection.update_one(query, update, upsert=True)
                return bool(result.modified_count or result.upserted_id)
            except Exception as exc:
                if getattr(exc, "code", None) == 11000:
                    return False
                raise
        async with self._delivery_lock:
            item = self._deliveries.get(key)
            if item and item.get("status") in {"sent", "skipped", "uncertain"}:
                return False
            if item and item.get("claimed_at") and item["claimed_at"] > cutoff:
                return False
            if item and item.get("status") == "sending":
                item.update({"status": "uncertain", "claimed_at": None, "updated_at": now})
                return False
            self._deliveries[key] = {
                **(item or {}),
                "delivery_key": key,
                "subscription_id": str(subscription_id),
                "chat_id": int(chat_id),
                "status": "pending",
                "claimed_at": now,
                "updated_at": now,
                "created_at": (item or {}).get("created_at", now),
                "attempt": int((item or {}).get("attempt", 0)) + 1,
            }
            return True

    async def mark_delivery_sent(
        self,
        delivery_key: str,
        *,
        status: str = "sent",
        telegram_message_id: int | None = None,
    ) -> bool:
        """Persist the terminal result of a claimed delivery."""
        now = _now()
        key = str(delivery_key)
        update = {
            "status": str(status or "sent"),
            "sent_at": now,
            "updated_at": now,
            "claimed_at": None,
        }
        if telegram_message_id is not None:
            update["telegram_message_id"] = int(telegram_message_id)
        if self.persistent:
            result = await self.database.notification_deliveries.update_one(
                {"delivery_key": key, "status": {"$in": ["pending", "sending"]}},
                {"$set": update},
            )
            return bool(result.modified_count)
        async with self._delivery_lock:
            item = self._deliveries.get(key)
            if not item or item.get("status") not in {"pending", "sending"}:
                return False
            item.update(update)
            return True

    async def begin_delivery_send(self, delivery_key: str) -> bool:
        """Durably mark that the Telegram send request is about to begin."""
        now = _now()
        key = str(delivery_key)
        if self.persistent:
            result = await self.database.notification_deliveries.update_one(
                {"delivery_key": key, "status": "pending"},
                {"$set": {"status": "sending", "send_started_at": now, "updated_at": now}},
            )
            return bool(result.modified_count)
        async with self._delivery_lock:
            item = self._deliveries.get(key)
            if not item or item.get("status") != "pending":
                return False
            item.update({"status": "sending", "send_started_at": now, "updated_at": now})
            return True

    async def delivery_was_sent(self, delivery_key: str) -> bool:
        """Return whether a delivery key reached a terminal sent/skipped state."""
        key = str(delivery_key)
        if self.persistent:
            item = await self.database.notification_deliveries.find_one(
                {"delivery_key": key, "status": {"$in": ["sent", "skipped", "uncertain"]}},
                {"_id": 0, "status": 1},
            )
            return bool(item)
        async with self._delivery_lock:
            return self._deliveries.get(key, {}).get("status") in {"sent", "skipped", "uncertain"}

    async def release_delivery(self, delivery_key: str, error: str | None = None) -> None:
        """Release a failed claim so a later retry can reclaim the key."""
        update = {"status": "failed", "claimed_at": None, "updated_at": _now()}
        if error:
            update["last_error"] = str(error)[:200]
        key = str(delivery_key)
        if self.persistent:
            await self.database.notification_deliveries.update_one(
                {"delivery_key": key, "status": "pending"}, {"$set": update}
            )
            return
        async with self._delivery_lock:
            item = self._deliveries.get(key)
            if item and item.get("status") == "pending":
                item.update(update)

    async def mark_delivery_uncertain(self, delivery_key: str, error: str | None = None) -> None:
        """Close a send attempt whose remote outcome cannot be proven."""
        update = {"status": "uncertain", "claimed_at": None, "updated_at": _now()}
        if error:
            update["last_error"] = str(error)[:200]
        key = str(delivery_key)
        if self.persistent:
            await self.database.notification_deliveries.update_one(
                {"delivery_key": key, "status": "sending"}, {"$set": update}
            )
            return
        async with self._delivery_lock:
            item = self._deliveries.get(key)
            if item and item.get("status") == "sending":
                item.update(update)

    async def defer(self, subscription_id: str, next_run_at: datetime) -> bool:
        """Move a due delivery forward without claiming it.

        Used for user quiet hours.  Group schedules and provider retry state
        keep their own lifecycle and do not call this method.
        """
        update = {
            "next_run_at": next_run_at,
            "delivery_claimed": False,
            "claimed_at": None,
            "updated_at": _now(),
        }
        if self.persistent:
            result = await self.database.subscriptions.update_one(
                {"subscription_id": subscription_id, "enabled": True}, {"$set": update}
            )
            return bool(result.modified_count)
        item = self._items.get(subscription_id)
        if not item or not item.get("enabled"):
            return False
        item.update(update)
        return True

    async def disable(self, user_id: int, subscription_id: str) -> bool:
        if self.persistent:
            result = await self.database.subscriptions.update_one(
                {
                    "subscription_id": subscription_id,
                    "user_id": int(user_id),
                    "scope": "user",
                    "enabled": True,
                },
                {"$set": {"enabled": False, "delivery_claimed": False, "claimed_at": None, "updated_at": _now()}},
            )
            return bool(result.modified_count)
        item = self._items.get(subscription_id)
        if (
            not item
            or item["user_id"] != int(user_id)
            or item.get("scope", "user") != "user"
            or not item["enabled"]
        ):
            return False
        item["enabled"] = False
        item["delivery_claimed"] = False
        item["claimed_at"] = None
        item["updated_at"] = _now()
        return True
