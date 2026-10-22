"""Persistence primitives for targeted group replies.

The handler layer only deals with these repository contracts.  A short-lived
recent-message index keeps candidate references bounded while the delivery
ledger makes each Telegram reply idempotent across retries and restarts.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

PENDING_REF_TTL_SECONDS = 24 * 60 * 60
DEFAULT_WINDOW_SECONDS = 5 * 60
MAX_KEY_LENGTH = 256
_TERMINAL_STATUSES = {"sent", "uncertain", "skipped"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_datetime(value: Any, *, fallback: datetime | None = None) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    return fallback or _now()


def _as_int(value: Any, *, default: int | None = None) -> int | None:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _thread_value(value: Any) -> int | None:
    return _as_int(value)


def _message_key(chat_id: int, thread_id: int | None, message_id: int) -> tuple[int, int | None, int]:
    return int(chat_id), _thread_value(thread_id), int(message_id)


def _safe_key_part(value: Any, *, max_length: int = 96) -> str:
    raw = str(value or "")
    if len(raw) <= max_length:
        return raw
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


class RecentMessageIndex:
    """Recent group messages with a Mongo/Redis-compatible repository API.

    The in-memory implementation is deterministic and is used in tests and
    local polling.  When a database object is supplied, writes and reads are
    mirrored to its ``messages`` collection.  A live Redis client can be
    attached after queue bootstrap for the bounded TTL hot index.
    """

    def __init__(
        self,
        database: Any | None = None,
        redis_client: Any | None = None,
        *,
        required: bool = False,
        ttl_seconds: int = PENDING_REF_TTL_SECONDS,
    ) -> None:
        self.database = database
        self.redis = redis_client
        self.required = bool(required)
        self.ttl_seconds = max(60, int(ttl_seconds))
        self._items: dict[tuple[int, int | None, int], dict[str, Any]] = {}
        self._expires: dict[tuple[int, int | None, int], datetime] = {}
        self._lock = asyncio.Lock()

    def attach_redis(self, redis_client: Any | None) -> None:
        """Attach the process' already-health-checked Redis connection."""

        self.redis = redis_client

    @property
    def collection(self) -> Any | None:
        if self.database is None:
            return None
        try:
            return self.database.messages
        except (AttributeError, RuntimeError):
            return None

    @staticmethod
    def _normalize(message: Mapping[str, Any]) -> dict[str, Any]:
        chat_id = _as_int(message.get("chat_id"))
        message_id = _as_int(message.get("telegram_message_id", message.get("message_id")))
        if chat_id is None or message_id is None:
            raise ValueError("group_reply_message_identity_required")
        thread_id = _thread_value(message.get("thread_id", message.get("message_thread_id")))
        author_id = _as_int(message.get("author_user_id", message.get("user_id")))
        if author_id is None:
            raise ValueError("group_reply_author_required")
        created_at = _as_datetime(message.get("created_at", message.get("timestamp")))
        document: dict[str, Any] = {
            "schema_version": int(message.get("schema_version", 1)),
            "chat_id": chat_id,
            "thread_id": thread_id,
            "telegram_message_id": message_id,
            "author_user_id": author_id,
            "username": str(message.get("username") or "")[:64],
            "display_name": str(message.get("display_name") or message.get("full_name") or "Kullanıcı")[:80],
            "text": str(message.get("text", message.get("content", "")))[:4096],
            "created_at": created_at,
            "reply_to_message_id": _as_int(message.get("reply_to_message_id")),
            "chat_type": str(message.get("chat_type") or "group")[:32],
            "trigger_kind": str(message.get("trigger_kind") or "mention")[:64],
        }
        if message.get("turn_id") is not None:
            document["turn_id"] = _safe_key_part(message["turn_id"])
        if message.get("target_user_id") is not None:
            document["target_user_id"] = _as_int(message.get("target_user_id"))
        if message.get("role") is not None:
            document["role"] = str(message["role"])[:32]
        return document

    async def ensure_indexes(self) -> None:
        collection = self.collection
        if collection is None:
            return
        try:
            await collection.create_index(
                [("chat_id", 1), ("thread_id", 1), ("created_at", -1)],
                name="group_reply_recent_messages",
            )
            await collection.create_index(
                [("chat_id", 1), ("thread_id", 1), ("telegram_message_id", 1)],
                unique=True,
                partialFilterExpression={"telegram_message_id": {"$exists": True}},
                name="group_reply_telegram_message_unique",
            )
        except Exception as exc:
            if self.required:
                raise RuntimeError("group_reply_message_index_unavailable") from exc

    async def add_message(self, message: Mapping[str, Any], *, ttl_seconds: int | None = None) -> dict[str, Any]:
        document = self._normalize(message)
        key = _message_key(document["chat_id"], document["thread_id"], document["telegram_message_id"])
        expires_at = _now() + timedelta(seconds=max(60, int(ttl_seconds or self.ttl_seconds)))
        async with self._lock:
            self._prune_locked()
            previous = self._items.get(key)
            if previous and previous.get("replied_at") is not None:
                document["replied_at"] = previous["replied_at"]
                if previous.get("reply_turn_id") is not None:
                    document["reply_turn_id"] = previous["reply_turn_id"]
            self._items[key] = copy.deepcopy(document)
            self._expires[key] = expires_at
        collection = self.collection
        if collection is not None:
            try:
                await collection.update_one(
                    {
                        "chat_id": document["chat_id"],
                        "thread_id": document["thread_id"],
                        "telegram_message_id": document["telegram_message_id"],
                    },
                    {"$set": document, "$setOnInsert": {"timestamp": document["created_at"]}},
                    upsert=True,
                )
            except Exception as exc:
                if self.required:
                    raise RuntimeError("group_reply_message_write_failed") from exc
        if self.redis is not None:
            try:
                await self._redis_add(document, max(60, int(ttl_seconds or self.ttl_seconds)))
            except Exception as exc:
                if self.required:
                    raise RuntimeError("group_reply_message_index_write_failed") from exc
        return copy.deepcopy(document)

    async def record(self, message: Mapping[str, Any], *, ttl_seconds: int | None = None) -> dict[str, Any]:
        return await self.add_message(message, ttl_seconds=ttl_seconds)

    async def mark_replied(self, chat_id: int, thread_id: int | None, message_id: int, *, turn_id: str | None = None) -> bool:
        key = _message_key(chat_id, thread_id, message_id)
        now = _now()
        changed = False
        async with self._lock:
            self._prune_locked()
            item = self._items.get(key)
            if item is not None:
                item["replied_at"] = now
                if turn_id is not None:
                    item["reply_turn_id"] = _safe_key_part(turn_id)
                changed = True
        collection = self.collection
        if collection is not None:
            try:
                result = await collection.update_one(
                    {
                        "chat_id": int(chat_id),
                        "thread_id": _thread_value(thread_id),
                        "telegram_message_id": int(message_id),
                    },
                    {"$set": {"replied_at": now, **({"reply_turn_id": _safe_key_part(turn_id)} if turn_id else {})}},
                )
                changed = changed or bool(getattr(result, "modified_count", 0))
            except Exception as exc:
                if self.required:
                    raise RuntimeError("group_reply_message_update_failed") from exc
        if self.redis is not None:
            try:
                redis_key = self._redis_message_key(chat_id, thread_id, message_id)
                cached = self._decode_redis(await self.redis.get(redis_key))
                if cached is not None:
                    cached["replied_at"] = now
                    if turn_id is not None:
                        cached["reply_turn_id"] = _safe_key_part(turn_id)
                    await self.redis.set(redis_key, json.dumps(cached, ensure_ascii=False, default=self._json_default), ex=self.ttl_seconds)
            except Exception as exc:
                if self.required:
                    raise RuntimeError("group_reply_message_index_update_failed") from exc
        return changed

    async def list_candidates(
        self,
        chat_id: int,
        *,
        thread_id: int | None = None,
        since: datetime | None = None,
        limit: int = 8,
        latest_per_user: bool = False,
        include_replied: bool = False,
    ) -> list[dict[str, Any]]:
        now = _now()
        since = _as_datetime(since, fallback=now - timedelta(seconds=DEFAULT_WINDOW_SECONDS))
        bounded_limit = max(1, min(int(limit), 100))
        collection = self.collection
        rows: list[dict[str, Any]] = []
        if collection is not None:
            query: dict[str, Any] = {
                "chat_id": int(chat_id),
                "created_at": {"$gte": since},
                "telegram_message_id": {"$exists": True},
            }
            if thread_id is None:
                query["$or"] = [{"thread_id": None}, {"thread_id": {"$exists": False}}]
            else:
                query["thread_id"] = int(thread_id)
            try:
                cursor = collection.find(query, {"_id": 0}).sort(
                    [("created_at", -1), ("telegram_message_id", -1)]
                ).limit(bounded_limit * 4 if latest_per_user else bounded_limit)
                rows = await cursor.to_list(length=bounded_limit * 4 if latest_per_user else bounded_limit)
            except Exception as exc:
                if self.required:
                    raise RuntimeError("group_reply_message_read_failed") from exc
        if not rows and self.redis is not None:
            try:
                rows = await self._redis_candidates(int(chat_id), _thread_value(thread_id), since)
            except Exception as exc:
                if self.required:
                    raise RuntimeError("group_reply_message_index_read_failed") from exc
        if not rows:
            async with self._lock:
                self._prune_locked(now)
                rows = [
                    copy.deepcopy(row)
                    for key, row in self._items.items()
                    if key[0] == int(chat_id)
                    and key[1] == _thread_value(thread_id)
                    and _as_datetime(row.get("created_at")) >= since
                    and (include_replied or not row.get("replied_at"))
                ]
        else:
            rows = [copy.deepcopy(row) for row in rows]
        rows = [
            row for row in rows
            if include_replied or not row.get("replied_at")
            if str(row.get("role", "user")) in {"user", ""}
        ]
        rows.sort(key=lambda row: (_as_datetime(row.get("created_at")), _as_int(row.get("telegram_message_id"), default=0) or 0))
        if latest_per_user:
            latest: dict[int, dict[str, Any]] = {}
            for row in rows:
                user_id = _as_int(row.get("author_user_id", row.get("user_id")))
                if user_id is not None:
                    latest[user_id] = row
            rows = sorted(latest.values(), key=lambda row: (_as_datetime(row.get("created_at")), row.get("telegram_message_id", 0)))
        return rows[-bounded_limit:]

    async def get_recent(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return await self.list_candidates(*args, **kwargs)

    def _prune_locked(self, now: datetime | None = None) -> None:
        now = now or _now()
        for key, expires_at in list(self._expires.items()):
            if expires_at <= now:
                self._expires.pop(key, None)
                self._items.pop(key, None)

    @staticmethod
    def _redis_index_key(chat_id: int, thread_id: int | None) -> str:
        thread = "none" if thread_id is None else str(int(thread_id))
        return f"arel:group-reply:index:{int(chat_id)}:{thread}"

    @classmethod
    def _redis_message_key(cls, chat_id: int, thread_id: int | None, message_id: int) -> str:
        return f"{cls._redis_index_key(chat_id, thread_id)}:{int(message_id)}"

    @staticmethod
    def _json_default(value: Any) -> str:
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)

    @staticmethod
    def _decode_redis(raw: Any) -> dict[str, Any] | None:
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            row = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if not isinstance(row, dict):
            return None
        for field in ("created_at", "replied_at"):
            if row.get(field):
                row[field] = _as_datetime(row[field])
        return row

    async def _redis_add(self, document: dict[str, Any], ttl_seconds: int) -> None:
        if self.redis is None:
            return
        key = self._redis_message_key(document["chat_id"], document["thread_id"], document["telegram_message_id"])
        index = self._redis_index_key(document["chat_id"], document["thread_id"])
        previous = self._decode_redis(await self.redis.get(key))
        if previous and previous.get("replied_at") is not None:
            document = dict(document)
            document["replied_at"] = previous["replied_at"]
            if previous.get("reply_turn_id") is not None:
                document["reply_turn_id"] = previous["reply_turn_id"]
        encoded = json.dumps(document, ensure_ascii=False, default=self._json_default)
        await self.redis.set(key, encoded, ex=ttl_seconds)
        await self.redis.zadd(index, {key: _as_datetime(document["created_at"]).timestamp()})
        await self.redis.expire(index, ttl_seconds)

    async def _redis_candidates(self, chat_id: int, thread_id: int | None, since: datetime) -> list[dict[str, Any]]:
        if self.redis is None:
            return []
        index = self._redis_index_key(chat_id, thread_id)
        keys = await self.redis.zrangebyscore(index, since.timestamp(), "+inf")
        rows: list[dict[str, Any]] = []
        for key in keys:
            if isinstance(key, bytes):
                key = key.decode("utf-8")
            row = self._decode_redis(await self.redis.get(key))
            if row is not None:
                rows.append(row)
        return rows


class ReplyDeliveryLedger:
    """Idempotent targeted-reply delivery state machine."""

    def __init__(
        self,
        database: Any | None = None,
        redis_client: Any | None = None,
        *,
        required: bool = False,
        claim_timeout_seconds: int = 300,
    ) -> None:
        self.database = database
        self.redis = redis_client
        self.required = bool(required)
        self.claim_timeout_seconds = max(30, int(claim_timeout_seconds))
        self._items: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    def attach_redis(self, redis_client: Any | None) -> None:
        """Attach the shared Redis connection once the queue is started."""

        self.redis = redis_client

    @property
    def collection(self) -> Any | None:
        if self.database is None:
            return None
        try:
            return self.database.group_reply_deliveries
        except (AttributeError, RuntimeError):
            return None

    @staticmethod
    def make_key(
        chat_id: int,
        thread_id: int | None,
        target_message_id: int,
        *,
        feature: str = "group_targeted_reply",
        generation_id: str = "",
    ) -> str:
        parts = [str(int(chat_id)), "" if thread_id is None else str(int(thread_id)), str(int(target_message_id)), str(feature), str(generation_id)]
        key = ":".join(_safe_key_part(part) for part in parts)
        if len(key) <= MAX_KEY_LENGTH:
            return key
        return ":".join(parts[:4] + [hashlib.sha256(parts[4].encode("utf-8")).hexdigest()[:32]])[:MAX_KEY_LENGTH]

    async def ensure_indexes(self) -> None:
        collection = self.collection
        if collection is None:
            return
        try:
            await collection.create_index("delivery_key", unique=True, name="group_reply_delivery_key_unique")
            await collection.create_index(
                "created_at",
                expireAfterSeconds=PENDING_REF_TTL_SECONDS,
                name="group_reply_delivery_created_ttl",
            )
        except Exception as exc:
            if self.required:
                raise RuntimeError("group_reply_delivery_index_unavailable") from exc

    async def claim(
        self,
        delivery_key: str,
        *,
        chat_id: int,
        target_message_id: int,
        generation_id: str,
        thread_id: int | None = None,
        feature: str = "group_targeted_reply",
    ) -> bool:
        key = str(delivery_key)[:MAX_KEY_LENGTH]
        now = _now()
        cutoff = now - timedelta(seconds=self.claim_timeout_seconds)
        document = {
            "delivery_key": key,
            "chat_id": int(chat_id),
            "thread_id": _thread_value(thread_id),
            "target_message_id": int(target_message_id),
            "generation_id": _safe_key_part(generation_id),
            "feature": _safe_key_part(feature),
            "status": "pending",
            "claimed_at": now,
            "updated_at": now,
            "created_at": now,
        }
        collection = self.collection
        if collection is not None:
            try:
                existing = await collection.find_one({"delivery_key": key}, {"_id": 0})
                if existing and existing.get("status") in _TERMINAL_STATUSES:
                    return False
                if existing and existing.get("status") == "sending" and _as_datetime(existing.get("claimed_at"), fallback=now) > cutoff:
                    return False
                if existing and existing.get("status") == "sending":
                    await collection.update_one(
                        {"delivery_key": key, "status": "sending"},
                        {"$set": {"status": "uncertain", "claimed_at": None, "updated_at": now}},
                    )
                    return False
                result = await collection.update_one(
                    {
                        "delivery_key": key,
                        "status": {"$nin": list(_TERMINAL_STATUSES | {"sending"})},
                        "$or": [
                            {"claimed_at": {"$exists": False}},
                            {"claimed_at": None},
                            {"claimed_at": {"$lte": cutoff}},
                        ],
                    },
                    # MongoDB rejects updating the same path through both
                    # operators (WriteError code 40). ``created_at`` is an
                    # insert-only field; every retry updates the remaining
                    # delivery state while preserving the original claim
                    # creation time.
                    {
                        "$set": {name: value for name, value in document.items() if name != "created_at"},
                        "$setOnInsert": {"created_at": now},
                    },
                    upsert=True,
                )
                if bool(getattr(result, "modified_count", 0) or getattr(result, "upserted_id", None)):
                    return True
                return False
            except Exception as exc:
                if getattr(exc, "code", None) == 11000:
                    return False
                if self.required:
                    raise RuntimeError("group_reply_delivery_claim_failed") from exc
        async with self._lock:
            item = self._items.get(key)
            if item and item.get("status") in _TERMINAL_STATUSES:
                return False
            if item and item.get("status") in {"pending", "sending"} and _as_datetime(item.get("claimed_at"), fallback=now) > cutoff:
                return False
            if item and item.get("status") == "sending":
                item.update({"status": "uncertain", "claimed_at": None, "updated_at": now})
                return False
            document["created_at"] = item.get("created_at", now) if item else now
            self._items[key] = document
            return True

    async def begin_send(self, delivery_key: str) -> bool:
        return await self._transition(delivery_key, {"pending"}, {"status": "sending", "send_started_at": _now()})

    async def mark_sent(self, delivery_key: str, telegram_message_id: int | None = None) -> bool:
        update: dict[str, Any] = {"status": "sent", "sent_at": _now(), "claimed_at": None, "updated_at": _now()}
        if telegram_message_id is not None:
            update["telegram_message_id"] = int(telegram_message_id)
        return await self._transition(delivery_key, {"pending", "sending"}, update)

    async def release(self, delivery_key: str, error: str | None = None) -> None:
        update: dict[str, Any] = {"status": "failed", "claimed_at": None, "updated_at": _now()}
        if error:
            update["last_error"] = str(error)[:200]
        await self._transition(delivery_key, {"pending"}, update)

    async def mark_uncertain(self, delivery_key: str, error: str | None = None) -> None:
        update: dict[str, Any] = {"status": "uncertain", "claimed_at": None, "updated_at": _now()}
        if error:
            update["last_error"] = str(error)[:200]
        await self._transition(delivery_key, {"sending"}, update)

    async def mark_skipped(self, delivery_key: str, reason: str | None = None) -> None:
        """Close a delivery when Telegram confirms the target no longer exists."""

        update: dict[str, Any] = {"status": "skipped", "claimed_at": None, "updated_at": _now()}
        if reason:
            update["last_error"] = str(reason)[:200]
        await self._transition(delivery_key, {"pending", "sending"}, update)

    async def get(self, delivery_key: str) -> dict[str, Any] | None:
        key = str(delivery_key)[:MAX_KEY_LENGTH]
        collection = self.collection
        if collection is not None:
            try:
                return await collection.find_one({"delivery_key": key}, {"_id": 0})
            except Exception as exc:
                if self.required:
                    raise RuntimeError("group_reply_delivery_read_failed") from exc
        async with self._lock:
            item = self._items.get(key)
            return copy.deepcopy(item) if item else None

    async def _transition(self, delivery_key: str, allowed: set[str], changes: dict[str, Any]) -> bool:
        key = str(delivery_key)[:MAX_KEY_LENGTH]
        collection = self.collection
        if collection is not None:
            try:
                result = await collection.update_one(
                    {"delivery_key": key, "status": {"$in": list(allowed)}},
                    {"$set": changes},
                )
                return bool(getattr(result, "modified_count", 0))
            except Exception as exc:
                if self.required:
                    raise RuntimeError("group_reply_delivery_update_failed") from exc
        async with self._lock:
            item = self._items.get(key)
            if not item or item.get("status") not in allowed:
                return False
            item.update(copy.deepcopy(changes))
            return True


__all__ = ["PENDING_REF_TTL_SECONDS", "RecentMessageIndex", "ReplyDeliveryLedger"]
