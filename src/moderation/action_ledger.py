"""Idempotency ledger for destructive moderation actions.

The ledger is deliberately transport-neutral.  A small in-memory backend keeps
unit tests and development usable; production can inject a repository exposing
``moderation_actions`` and set ``required=True`` so an unavailable durable
ledger fails closed before Telegram is called.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from .contracts import ModerationAction


_TERMINAL = frozenset({"sent", "skipped", "uncertain"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ModerationActionLedger:
    """Claim and finalize one Telegram action exactly once per key."""

    def __init__(
        self,
        database: Any | None = None,
        *,
        required: bool = False,
        claim_timeout_seconds: int = 300,
    ) -> None:
        self.database = database
        self.required = bool(required)
        self.claim_timeout_seconds = max(30, int(claim_timeout_seconds))
        self._items: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    @property
    def collection(self) -> Any | None:
        if self.database is None:
            return None
        try:
            return self.database.moderation_actions
        except Exception:
            return None

    @staticmethod
    def _key(action_or_key: ModerationAction | str) -> str:
        key = action_or_key.idempotency_key if isinstance(action_or_key, ModerationAction) else str(action_or_key)
        key = str(key).strip()
        if not key or len(key) > 256:
            raise ValueError("moderation_action_key_invalid")
        return key

    @staticmethod
    def _document(action: ModerationAction, now: datetime) -> dict[str, Any]:
        return {
            # ``idempotency_key`` is the durable schema field.  Keep the
            # bounded ``action_key`` alias for older operators/replays while
            # migration v16 owns the unique index on the canonical field.
            "idempotency_key": action.idempotency_key,
            "action_key": action.idempotency_key,
            "chat_id": int(action.chat_id),
            "thread_id": int(action.thread_id) if action.thread_id is not None else None,
            "actor_id": int(action.actor_id),
            "target_user_id": int(action.target_user_id) if action.target_user_id is not None else None,
            "target_message_id": int(action.target_message_id) if action.target_message_id is not None else None,
            "action": str(action.action),
            "reason": str(action.reason or "")[:128],
            "duration_seconds": int(action.duration_seconds) if action.duration_seconds is not None else None,
            "correlation_id": str(action.correlation_id or "")[:128],
            "metadata": {
                str(key)[:64]: str(value)[:256]
                for key, value in dict(action.metadata).items()
                if str(key)[:64] and key in {"source", "provider", "policy_version", "generation_id"}
            },
            "status": "pending",
            "claimed_at": now,
            "updated_at": now,
            "created_at": now,
        }

    async def ensure_indexes(self) -> None:
        collection = self.collection
        if collection is None:
            return
        try:
            await collection.create_index(
                "idempotency_key",
                unique=True,
                name="moderation_action_idempotency_unique",
            )
            await collection.create_index(
                [("chat_id", 1), ("created_at", -1)],
                name="moderation_action_chat_time",
            )
            await collection.create_index(
                "created_at",
                expireAfterSeconds=30 * 24 * 60 * 60,
                name="moderation_action_created_ttl",
            )
        except Exception as exc:
            if self.required:
                raise RuntimeError("moderation_action_index_unavailable") from exc

    async def claim(self, action: ModerationAction) -> bool:
        """Claim an action; terminal or actively sending keys are rejected."""

        key = self._key(action)
        now = _now()
        cutoff = now - timedelta(seconds=self.claim_timeout_seconds)
        document = self._document(action, now)
        collection = self.collection
        if collection is not None:
            try:
                existing = await collection.find_one(
                    {"$or": [{"idempotency_key": key}, {"action_key": key}]},
                    {"_id": 0},
                )
                if existing and existing.get("status") in _TERMINAL:
                    return False
                claimed_at = existing.get("claimed_at") if existing else None
                if existing and existing.get("status") == "sending" and claimed_at and claimed_at > cutoff:
                    return False
                if existing and existing.get("status") == "sending":
                    await collection.update_one(
                        {"$or": [{"idempotency_key": key}, {"action_key": key}], "status": "sending"},
                        {"$set": {"status": "uncertain", "updated_at": now}},
                    )
                    return False
                query = {
                    "$or": [{"idempotency_key": key}, {"action_key": key}],
                    "status": {"$nin": ["sent", "skipped", "uncertain", "sending"]},
                }
                # MongoDB rejects an update when the same path appears in
                # both ``$set`` and ``$setOnInsert``.  This used to make
                # every first moderation action fail closed with
                # ``moderation_ledger_unavailable`` on production Mongo.
                # Fields owned by the insert branch must therefore be
                # removed from the regular update document.
                set_document = dict(document)
                set_document.pop("idempotency_key", None)
                set_document.pop("action_key", None)
                set_document.pop("created_at", None)
                result = await collection.update_one(
                    query,
                    {
                        "$set": set_document,
                        "$setOnInsert": {
                            "idempotency_key": key,
                            "action_key": key,
                            "created_at": now,
                        },
                    },
                    upsert=True,
                )
                return bool(getattr(result, "modified_count", 0) or getattr(result, "upserted_id", None))
            except Exception as exc:
                if getattr(exc, "code", None) == 11000:
                    return False
                if self.required:
                    raise RuntimeError("moderation_action_claim_failed") from exc

        async with self._lock:
            existing = self._items.get(key)
            if existing and existing.get("status") in _TERMINAL:
                return False
            if existing and existing.get("status") == "sending":
                claimed_at = existing.get("claimed_at")
                if claimed_at and claimed_at > cutoff:
                    return False
                existing.update(status="uncertain", updated_at=now)
                return False
            document["created_at"] = existing.get("created_at", now) if existing else now
            self._items[key] = document
            return True

    async def begin_send(self, action_or_key: ModerationAction | str) -> bool:
        key = self._key(action_or_key)
        now = _now()
        collection = self.collection
        if collection is not None:
            try:
                result = await collection.update_one(
                    {"$or": [{"idempotency_key": key}, {"action_key": key}], "status": "pending"},
                    {"$set": {"status": "sending", "send_started_at": now, "updated_at": now}},
                )
                return bool(getattr(result, "modified_count", 0))
            except Exception as exc:
                if self.required:
                    raise RuntimeError("moderation_action_begin_failed") from exc
        async with self._lock:
            item = self._items.get(key)
            if not item or item.get("status") != "pending":
                return False
            item.update(status="sending", send_started_at=now, updated_at=now)
            return True

    async def mark_sent(self, action_or_key: ModerationAction | str, telegram_message_id: int | None = None) -> bool:
        return await self._mark(action_or_key, "sent", telegram_message_id=telegram_message_id)

    async def mark_skipped(self, action_or_key: ModerationAction | str, reason: str = "skipped") -> bool:
        return await self._mark(action_or_key, "skipped", error_code=reason)

    async def mark_failed(self, action_or_key: ModerationAction | str, reason: str = "action_failed") -> bool:
        return await self._mark(action_or_key, "failed", error_code=reason)

    async def mark_uncertain(self, action_or_key: ModerationAction | str, reason: str = "action_uncertain") -> bool:
        return await self._mark(action_or_key, "uncertain", error_code=reason)

    async def _mark(
        self,
        action_or_key: ModerationAction | str,
        status: str,
        *,
        telegram_message_id: int | None = None,
        error_code: str | None = None,
    ) -> bool:
        key = self._key(action_or_key)
        now = _now()
        update: dict[str, Any] = {"status": status, "updated_at": now, "claimed_at": None}
        if telegram_message_id is not None:
            update["telegram_message_id"] = int(telegram_message_id)
        if error_code:
            update["error_code"] = str(error_code)[:96]
        collection = self.collection
        if collection is not None:
            try:
                result = await collection.update_one(
                    {
                        "$or": [{"idempotency_key": key}, {"action_key": key}],
                        "status": {"$in": ["pending", "sending", "failed"]},
                    },
                    {"$set": update},
                )
                return bool(getattr(result, "modified_count", 0))
            except Exception as exc:
                if self.required:
                    raise RuntimeError("moderation_action_finalize_failed") from exc
        async with self._lock:
            item = self._items.get(key)
            if not item or item.get("status") not in {"pending", "sending", "failed"}:
                return False
            item.update(update)
            return True

    async def get(self, action_or_key: ModerationAction | str) -> dict[str, Any] | None:
        key = self._key(action_or_key)
        collection = self.collection
        if collection is not None:
            try:
                row = await collection.find_one(
                    {"$or": [{"idempotency_key": key}, {"action_key": key}]},
                    {"_id": 0},
                )
                return dict(row) if row else None
            except Exception as exc:
                if self.required:
                    raise RuntimeError("moderation_action_read_failed") from exc
        async with self._lock:
            item = self._items.get(key)
            return dict(item) if item else None


__all__ = ["ModerationActionLedger"]
