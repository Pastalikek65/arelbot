"""Durable send-boundary ledger for worker-produced Telegram outputs."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any


def _now() -> datetime:
    return datetime.now(timezone.utc)


class JobDeliveryLedger:
    """Claim each job output once and make uncertain network outcomes explicit."""

    def __init__(self, database: Any | None = None, *, required: bool = False):
        self.database = database
        self.required = required
        self._items: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    @property
    def collection(self):
        return getattr(self.database, "job_deliveries", None) if self.database is not None else None

    async def ensure_indexes(self) -> None:
        collection = self.collection
        if collection is None:
            return
        try:
            await collection.create_index("delivery_key", unique=True, name="job_delivery_key_unique")
            await collection.create_index("created_at", expireAfterSeconds=7 * 24 * 60 * 60, name="job_delivery_created_ttl")
        except Exception as exc:
            if self.required:
                raise RuntimeError("MongoDB job delivery ledger is unavailable") from exc

    async def claim(self, delivery_key: str, *, job_id: str, chat_id: int) -> bool:
        key = str(delivery_key)[:256]
        now = _now()
        cutoff = now - timedelta(minutes=5)
        collection = self.collection
        if collection is not None:
            existing = await collection.find_one({"delivery_key": key}, {"_id": 0})
            if existing and existing.get("status") in {"sent", "uncertain", "skipped"}:
                return False
            if existing and existing.get("status") == "sending" and existing.get("claimed_at") and existing["claimed_at"] > cutoff:
                return False
            if existing and existing.get("status") == "sending":
                await collection.update_one({"delivery_key": key, "status": "sending"}, {"$set": {"status": "uncertain", "updated_at": now, "claimed_at": None}})
                return False
            query = {
                "delivery_key": key,
                "status": {"$nin": ["sent", "uncertain", "skipped", "sending"]},
                "$or": [{"claimed_at": {"$exists": False}}, {"claimed_at": None}, {"claimed_at": {"$lte": cutoff}}],
            }
            try:
                result = await collection.update_one(
                    query,
                    {"$set": {"job_id": str(job_id), "chat_id": int(chat_id), "status": "pending", "claimed_at": now, "updated_at": now}, "$setOnInsert": {"delivery_key": key, "created_at": now}},
                    upsert=True,
                )
                return bool(result.modified_count or result.upserted_id)
            except Exception as exc:
                if getattr(exc, "code", None) == 11000:
                    return False
                if self.required:
                    raise
        async with self._lock:
            item = self._items.get(key)
            if item and item.get("status") in {"sent", "uncertain", "skipped"}:
                return False
            if item and item.get("status") == "sending" and item.get("claimed_at") and item["claimed_at"] > cutoff:
                return False
            if item and item.get("status") == "sending":
                item.update(status="uncertain", claimed_at=None, updated_at=now)
                return False
            self._items[key] = {"delivery_key": key, "job_id": str(job_id), "chat_id": int(chat_id), "status": "pending", "claimed_at": now, "created_at": item.get("created_at", now) if item else now, "updated_at": now}
            return True

    async def begin_send(self, delivery_key: str) -> bool:
        key = str(delivery_key)
        now = _now()
        collection = self.collection
        if collection is not None:
            result = await collection.update_one({"delivery_key": key, "status": "pending"}, {"$set": {"status": "sending", "send_started_at": now, "updated_at": now}})
            return bool(result.modified_count)
        async with self._lock:
            item = self._items.get(key)
            if not item or item.get("status") != "pending":
                return False
            item.update(status="sending", send_started_at=now, updated_at=now)
            return True

    async def mark_sent(self, delivery_key: str, telegram_message_id: int | None = None) -> bool:
        key = str(delivery_key)
        now = _now()
        update = {"status": "sent", "sent_at": now, "claimed_at": None, "updated_at": now}
        if telegram_message_id is not None:
            update["telegram_message_id"] = int(telegram_message_id)
        collection = self.collection
        if collection is not None:
            result = await collection.update_one({"delivery_key": key, "status": {"$in": ["pending", "sending"]}}, {"$set": update})
            return bool(result.modified_count)
        async with self._lock:
            item = self._items.get(key)
            if not item or item.get("status") not in {"pending", "sending"}:
                return False
            item.update(update)
            return True

    async def release(self, delivery_key: str, error: str | None = None) -> None:
        key = str(delivery_key)
        update = {"status": "failed", "claimed_at": None, "updated_at": _now()}
        if error:
            update["last_error"] = str(error)[:200]
        collection = self.collection
        if collection is not None:
            await collection.update_one({"delivery_key": key, "status": "pending"}, {"$set": update})
            return
        async with self._lock:
            item = self._items.get(key)
            if item and item.get("status") == "pending":
                item.update(update)

    async def mark_uncertain(self, delivery_key: str, error: str | None = None) -> None:
        key = str(delivery_key)
        update = {"status": "uncertain", "claimed_at": None, "updated_at": _now()}
        if error:
            update["last_error"] = str(error)[:200]
        collection = self.collection
        if collection is not None:
            await collection.update_one({"delivery_key": key, "status": "sending"}, {"$set": update})
            return
        async with self._lock:
            item = self._items.get(key)
            if item and item.get("status") == "sending":
                item.update(update)
