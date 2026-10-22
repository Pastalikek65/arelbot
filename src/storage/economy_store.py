"""Atomic single-document economy ledger.

Every account owns exactly one document.  The balance and the stamp of each
applied ledger event live in that document, so MongoDB applies them with one
atomic ``update_one``.  The previous design wrote a transaction row first and
the balance second: a crash between the two writes left the reward
permanently unpaid, because the retry hit the duplicate transaction key and
was refused.  One document removes that window, because there is only one
write to lose.

The same contract is implemented by the in-memory backend, so the service
behaves identically without a database.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from bson import BSON, Binary

from src.security.log_redaction import summarize_exception
from src.storage.production import is_production_database

logger = logging.getLogger(__name__)

MAX_APPLIED_EVENTS = 500
MAX_RECEIPT_BYTES = 12 * 1024 * 1024


def _receipt_key(event_key: str) -> str:
    digest = hashlib.sha256(event_key.encode("utf-8")).digest()[:16]
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _payload_digest(direction: str, delta: int, amount: int, reason: str,
                    reward_day: str | None = None, reward_limit: int | None = None,
                    purchase_item: str | None = None, daily_amounts: tuple[int, ...] | None = None) -> bytes:
    # The day bucket and schedule are operational policy.  A delayed retry
    # must still recognize the original event after midnight or a policy edit.
    payload = [direction, int(delta), int(amount), str(reason), purchase_item]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).digest()


def _receipt_value(event_key: str, signature: bytes) -> Binary:
    if "\x00" in event_key or len(event_key.encode("utf-8")) > 512:
        raise ValueError("economy_event_key_invalid")
    return Binary(event_key.encode("utf-8") + b"\x00" + signature)


def _decode_receipt(value: Any) -> tuple[str, bytes]:
    raw, separator, signature = bytes(value).partition(b"\x00")
    if not separator or len(signature) != 32:
        raise RuntimeError("economy_receipt_corrupt")
    return raw.decode("utf-8"), signature


def _receipt_size(receipts: dict[str, Any]) -> int:
    return len(BSON.encode({"r": receipts}))


class EconomyStore:
    """Atomic single-document balance and event application."""

    MAX_APPLIED_EVENTS = MAX_APPLIED_EVENTS

    def __init__(self, database: Any | None = None, *, required: bool = False):
        self.database = database
        # A production database must never degrade to process-local state,
        # so the detection decides on its own even when the caller does not
        # say so.  The store then fails loudly instead of paying a reward
        # into memory that a restart would forget.
        self.required = bool(required) or is_production_database(database)
        self._accounts: dict[tuple[int, int], dict[str, Any]] = {}
        self._index_ready = False
        self._lock = asyncio.Lock()

    @property
    def collection(self) -> Any | None:
        """The Mongo account collection, or ``None`` without a database."""
        if self.database is None:
            return None
        try:
            return self.database.economy_accounts
        except (AttributeError, RuntimeError):
            return None

    @property
    def persistent(self) -> bool:
        """Whether the ledger is backed by MongoDB."""
        if self.database is None or not bool(getattr(self.database, "available", False)):
            return False
        return self.collection is not None

    async def ensure_indexes(self) -> None:
        """Create the unique account identity the ledger relies on.

        Two concurrent writers of the same account would otherwise be able to
        create two account documents and split one balance over two rows.
        """
        collection = self.collection
        if collection is None:
            return
        try:
            # The index is declared exactly like the base schema so a
            # database that already carries it is not rebuilt under a
            # second name.
            await collection.create_index([("user_id", 1), ("chat_id", 1)], unique=True)
            self._index_ready = True
        except Exception as exc:
            if self.required:
                raise RuntimeError("economy_account_index_unavailable") from exc

    async def _ensure_account_index(self, collection: Any) -> None:
        """Guarantee the account identity before the first production write.

        The migrations create the index at bootstrap, but the ledger must not
        depend on that having happened: without the unique account identity a
        race could split one balance over two account documents.
        """
        if self._index_ready or not self.required:
            return
        await self.ensure_indexes()

    @staticmethod
    def safe_event_key(event_key: str) -> str:
        """Return a Mongo-safe field name for one ledger event.

        MongoDB rejects ``.`` and ``$`` inside a field name.  Replacing them
        keeps the name readable, but two different events could then collapse
        into the same field name (``a.b`` and ``a_b``) and the second event
        would be mistaken for a replay of the first one, which would drop a
        reward.  A short digest of the raw key is therefore appended whenever
        the name had to be rewritten, so distinct events always keep distinct
        field names.

        Older unsafe keys used an eight-character suffix.  New unsafe keys use
        a full digest; replay checks still recognize the old spelling.
        """
        raw = str(event_key)
        safe = raw.replace(".", "_").replace("$", "_")
        if safe == raw:
            return safe
        return "h_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _legacy_safe_event_key(event_key: str) -> str:
        raw = str(event_key)
        safe = raw.replace(".", "_").replace("$", "_")
        return safe if safe == raw else f"{safe}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:8]}"

    async def apply_event(
        self,
        *,
        user_id: int,
        chat_id: int,
        event_key: str,
        delta: int,
        direction: str,
        amount: int,
        reason: str,
        created_at: datetime,
        reward_day: str | None = None,
        reward_limit: int | None = None,
        purchase_item: str | None = None,
        daily_day: str | None = None,
        daily_amounts: tuple[int, ...] | None = None,
    ) -> bool:
        """Atomically apply one ledger event to the account document.

        Returns True when the event was newly applied, False when the event
        was already applied (idempotent replay) or the debit was refused for
        insufficient balance.
        """
        if direction not in {"credit", "debit"}:
            raise ValueError("invalid_direction")
        if (int(amount) < 0 or (daily_day is None and int(amount) == 0) or
                (daily_day is None and int(delta) != (int(amount) if direction == "credit" else -int(amount))) or
                (daily_day is not None and (int(delta) != 0 or int(amount) != 0))):
            raise ValueError("economy_delta_invalid")
        uid, cid = int(user_id), int(chat_id)
        safe_key = self.safe_event_key(event_key)
        signature = _payload_digest(direction, delta, amount, reason, reward_day,
                                    reward_limit, purchase_item, daily_amounts)
        receipt_key = _receipt_key(str(event_key))
        receipt = _receipt_value(str(event_key), signature)
        if reward_day is not None and (direction != "credit" or reward_limit is None or
                int(reward_limit) < 0 or not str(reward_day)):
            raise ValueError("economy_reward_limit_invalid")
        if daily_day is not None and (direction != "credit" or not daily_amounts):
            raise ValueError("economy_daily_invalid")
        if purchase_item is not None and direction != "debit":
            raise ValueError("economy_purchase_invalid")
        record = {
            "event_key": str(event_key),
            "amount": int(amount),
            "direction": direction,
            "delta": int(delta),
            "reason": str(reason),
            "at": created_at,
        }
        collection = self.collection
        if collection is None:
            if self.required and self.database is not None:
                raise RuntimeError("economy_account_collection_unavailable")
            return await self._apply_memory(uid, cid, safe_key, int(delta), direction,
                int(amount), record, receipt_key, receipt, reward_day, reward_limit,
                purchase_item, daily_day, daily_amounts)
        await self._ensure_account_index(collection)
        return await self._apply_document(
            collection, uid, cid, safe_key, int(delta), direction, int(amount), record,
            created_at, receipt_key, receipt, reward_day, reward_limit,
            purchase_item, daily_day, daily_amounts
        )

    async def balance(self, user_id: int, chat_id: int) -> int:
        """Return the current balance of one account."""
        uid, cid = int(user_id), int(chat_id)
        collection = self.collection
        if collection is None:
            async with self._lock:
                account = self._accounts.get((uid, cid)) or {}
                return int(account.get("balance") or 0)
        document = await collection.find_one(
            {"user_id": uid, "chat_id": cid}, {"_id": 0, "balance": 1}
        )
        return int((document or {}).get("balance") or 0)

    async def event_applied(self, user_id: int, chat_id: int, event_key: str) -> bool:
        """Read whether an exact account event was paid, including pruned events.

        A receipt proves an event survived history pruning.  Legacy event rows
        are also recognized until migration or ordinary pruning stamps them.
        Hash or legacy field collisions fail closed instead of claiming that
        another event was paid.
        """
        uid, cid, raw_key = int(user_id), int(chat_id), str(event_key)
        receipt_key = _receipt_key(raw_key)
        safe_key = self.safe_event_key(raw_key)
        legacy_key = self._legacy_safe_event_key(raw_key)
        collection = self.collection
        if collection is None:
            if self.required and self.database is not None:
                raise RuntimeError("economy_account_collection_unavailable")
            async with self._lock:
                document = self._accounts.get((uid, cid))
        else:
            projection = {"_id": 0, f"receipts.{receipt_key}": 1,
                          f"applied_events.{safe_key}": 1}
            if legacy_key != safe_key:
                projection[f"applied_events.{legacy_key}"] = 1
            document = await collection.find_one(
                {"user_id": uid, "chat_id": cid}, projection)
        if not document:
            return False
        receipt = (document.get("receipts") or {}).get(receipt_key)
        if receipt is not None:
            recorded_key, _signature = _decode_receipt(receipt)
            if recorded_key != raw_key:
                raise RuntimeError("economy_receipt_collision")
            return True
        events = document.get("applied_events") or {}
        for key in {safe_key, legacy_key}:
            record = events.get(key)
            if record is None:
                continue
            if not isinstance(record, dict) or record.get("event_key") != raw_key:
                raise RuntimeError("economy_legacy_key_collision")
            return True
        return False

    @staticmethod
    def _daily_progress(document: dict[str, Any] | None) -> tuple[str | None, int]:
        if not document:
            return None, 0
        if document.get("daily_day"):
            return str(document["daily_day"]), int(document.get("streak") or 1)
        days = []
        daily_prefix = f"{document.get('user_id')}:{document.get('chat_id')}:daily:"

        def legacy_day(key):
            if not str(key).startswith(daily_prefix):
                return None
            candidate = str(key)[len(daily_prefix):]
            try:
                return date.fromisoformat(candidate).isoformat()
            except ValueError:
                return None

        for record in (document.get("applied_events") or {}).values():
            if not isinstance(record, dict) or record.get("reason") != "daily_reward":
                continue
            at = record.get("at")
            if isinstance(at, datetime):
                at = at.replace(tzinfo=UTC) if at.tzinfo is None else at
                days.append(at.astimezone(ZoneInfo("Europe/Istanbul")).date().isoformat())
            else:
                candidate = legacy_day(record.get("event_key") or "")
                if candidate:
                    days.append(candidate)
        for value in (document.get("receipts") or {}).values():
            key, _signature = _decode_receipt(value)
            candidate = legacy_day(key)
            if candidate:
                days.append(candidate)
        return (max(days), 1) if days else (None, 0)

    async def snapshot(self, user_id: int, chat_id: int) -> dict[str, Any]:
        uid, cid = int(user_id), int(chat_id)
        collection = self.collection
        if collection is None:
            if self.required and self.database is not None:
                raise RuntimeError("economy_account_collection_unavailable")
            async with self._lock:
                document = dict(self._accounts.get((uid, cid)) or {})
        else:
            document = await collection.find_one({"user_id": uid, "chat_id": cid}) or {}
        day, streak = self._daily_progress(document)
        return {"balance": int(document.get("balance") or 0),
                "inventory": list(document.get("inventory") or []),
                "equipped": dict(document.get("equipped") or {}),
                "daily_day": day, "streak": streak,
                "reward_day": document.get("reward_day"),
                "reward_earned": int(document.get("reward_earned") or 0)}

    async def daily(self, user_id: int, chat_id: int, day: str,
                    amounts: tuple[int, ...], now: datetime) -> dict[str, Any]:
        day = date.fromisoformat(str(day)).isoformat()
        schedule = tuple(int(value) for value in amounts)
        if not schedule or any(value <= 0 for value in schedule):
            raise ValueError("economy_daily_invalid")
        if now.tzinfo is None:
            raise ValueError("economy_datetime_naive")
        uid, cid = int(user_id), int(chat_id)
        accepted = await self.apply_event(user_id=uid, chat_id=cid,
            event_key=f"{uid}:{cid}:daily:{day}", delta=0, direction="credit",
            amount=0, reason="daily_reward", created_at=now,
            daily_day=day, daily_amounts=schedule)
        state = await self.snapshot(uid, cid)
        amount, streak = 0, state["streak"]
        if accepted:
            account_key = f"{uid}:{cid}:daily:{day}"
            collection = self.collection
            if collection is None:
                async with self._lock:
                    entry = (self._accounts[(uid, cid)].get("applied_events") or {}).get(account_key)
            else:
                document = await collection.find_one({"user_id": uid, "chat_id": cid}) or {}
                entry = (document.get("applied_events") or {}).get(account_key)
            if entry is None:
                raise RuntimeError("economy_daily_receipt_missing")
            amount, streak = int(entry["amount"]), int(entry["streak"])
        return {"accepted": accepted, "amount": amount, "streak": streak,
                "balance": state["balance"], "day": day}

    async def purchase(self, user_id: int, chat_id: int, item_key: str,
                       price: int, event_key: str, now: datetime) -> bool:
        if not str(item_key) or "." in str(item_key) or "$" in str(item_key) or int(price) <= 0:
            raise ValueError("economy_purchase_invalid")
        return await self.apply_event(user_id=int(user_id), chat_id=int(chat_id),
            event_key=str(event_key), delta=-int(price), direction="debit",
            amount=int(price), reason="shop_purchase", created_at=now,
            purchase_item=str(item_key))

    async def equip(self, user_id: int, chat_id: int, item_key: str, slot: str) -> bool:
        if (not str(item_key) or not str(slot) or "." in str(slot) or
                "$" in str(slot) or "\x00" in str(slot)):
            raise ValueError("economy_equip_invalid")
        uid, cid = int(user_id), int(chat_id)
        collection = self.collection
        if collection is None:
            if self.required and self.database is not None:
                raise RuntimeError("economy_account_collection_unavailable")
            async with self._lock:
                account = self._accounts.get((uid, cid))
                if not account or item_key not in account.get("inventory", []):
                    return False
                account.setdefault("equipped", {})[slot] = item_key
                return True
        result = await collection.update_one(
            {"user_id": uid, "chat_id": cid, "inventory": item_key},
            {"$set": {f"equipped.{slot}": item_key}})
        return bool(getattr(result, "modified_count", 0))

    async def unequip(self, user_id: int, chat_id: int, slot: str) -> bool:
        """Clear one selected cosmetic without changing ownership or money."""
        if not str(slot) or "." in str(slot) or "$" in str(slot) or "\x00" in str(slot):
            raise ValueError("economy_equip_invalid")
        uid, cid = int(user_id), int(chat_id)
        collection = self.collection
        if collection is None:
            if self.required and self.database is not None:
                raise RuntimeError("economy_account_collection_unavailable")
            async with self._lock:
                account = self._accounts.get((uid, cid))
                equipped = (account or {}).get("equipped") or {}
                if slot not in equipped:
                    return False
                del equipped[slot]
                return True
        result = await collection.update_one(
            {"user_id": uid, "chat_id": cid, f"equipped.{slot}": {"$exists": True}},
            {"$unset": {f"equipped.{slot}": ""}})
        return bool(getattr(result, "modified_count", 0))

    async def list_events(self, user_id: int, chat_id: int, limit: int = 20) -> list[dict[str, Any]]:
        """Return the newest applied ledger events of one account."""
        uid, cid = int(user_id), int(chat_id)
        bounded_limit = max(1, min(int(limit), 100))
        collection = self.collection
        if collection is None:
            async with self._lock:
                events = dict((self._accounts.get((uid, cid)) or {}).get("applied_events") or {})
        else:
            document = await collection.find_one(
                {"user_id": uid, "chat_id": cid}, {"_id": 0, "applied_events": 1}
            )
            events = dict((document or {}).get("applied_events") or {})
        entries = [
            (self._moment(record), record)
            for record in events.values()
            if isinstance(record, dict)
        ]
        entries.sort(key=lambda entry: entry[0], reverse=True)
        return [self._event_row(uid, cid, record) for _, record in entries[:bounded_limit]]

    async def _apply_document(
        self,
        collection: Any,
        uid: int,
        cid: int,
        safe_key: str,
        delta: int,
        direction: str,
        amount: int,
        record: dict[str, Any],
        now: datetime,
        receipt_key: str,
        receipt: Binary,
        reward_day: str | None,
        reward_limit: int | None,
        purchase_item: str | None,
        daily_day: str | None,
        daily_amounts: tuple[int, ...] | None,
    ) -> bool:
        account = {"user_id": uid, "chat_id": cid}
        for attempt in range(12):
            document = await collection.find_one(account)
            if daily_day is not None:
                last_day, last_streak = self._daily_progress(document)
                if last_day is not None and daily_day <= last_day:
                    return False
                new_streak = last_streak + 1 if last_day == (
                    date.fromisoformat(daily_day) - timedelta(days=1)).isoformat() else 1
                daily_amount = daily_amounts[min(new_streak - 1, len(daily_amounts) - 1)]
            if self._event_status(document, safe_key, record, receipt_key, receipt):
                return False
            if purchase_item is not None and purchase_item in (document or {}).get("inventory", []):
                return False
            receipts = dict((document or {}).get("receipts") or {})
            before_bytes = _receipt_size(receipts)
            entry_bytes = _receipt_size({receipt_key: receipt}) - _receipt_size({})
            if before_bytes + entry_bytes > MAX_RECEIPT_BYTES:
                raise RuntimeError("economy_receipt_capacity_reached")
            old_bytes = (document or {}).get("receipt_bytes")
            query = {**account, f"applied_events.{safe_key}": {"$exists": False},
                     f"receipts.{receipt_key}": {"$exists": False},
                     "receipt_bytes": old_bytes if old_bytes is not None else {"$exists": False}}
            actual_delta = delta
            actual_amount = amount
            update_fields = {"updated_at": now, f"receipts.{receipt_key}": receipt,
                             "receipt_bytes": before_bytes + entry_bytes}
            if reward_day is not None:
                previous_day = (document or {}).get("reward_day")
                earned = int((document or {}).get("reward_earned") or 0) if previous_day == reward_day else 0
                actual_amount = min(amount, max(0, int(reward_limit) - earned))
                actual_delta = actual_amount
                query["reward_day"] = previous_day if previous_day is not None else {"$exists": False}
                update_fields.update(reward_day=reward_day, reward_earned=earned + actual_amount)
            if daily_day is not None:
                actual_amount = int(daily_amount)
                actual_delta = actual_amount
                current_day = (document or {}).get("daily_day")
                query["daily_day"] = current_day if current_day is not None else {"$exists": False}
                update_fields.update(daily_day=daily_day, streak=new_streak)
            applied = dict(record, amount=actual_amount, delta=actual_delta)
            if daily_day is not None:
                applied["streak"] = new_streak
            update_fields[f"applied_events.{safe_key}"] = applied
            if direction == "debit":
                query["balance"] = {"$gte": amount}
            if purchase_item is not None:
                query["inventory"] = {"$ne": purchase_item}
            update = {"$inc": {"balance": actual_delta}, "$set": update_fields,
                      "$setOnInsert": {"user_id": uid, "chat_id": cid}}
            if purchase_item is not None:
                update["$addToSet"] = {"inventory": purchase_item}
            upsert = direction == "credit" and document is None
            try:
                result = await collection.update_one(query, update, upsert=upsert)
            except Exception as exc:
                if not self._is_duplicate_key(exc):
                    raise
                continue
            if getattr(result, "modified_count", 0) or getattr(result, "upserted_id", None):
                await self._prune(collection, account)
                return True
            if direction == "debit":
                current = await collection.find_one(account, {"_id": 0, "balance": 1})
                if current is not None and int(current.get("balance") or 0) < amount:
                    return False
        raise RuntimeError("economy_account_conflict")

    async def _apply_memory(
        self,
        uid: int,
        cid: int,
        safe_key: str,
        delta: int,
        direction: str,
        amount: int,
        record: dict[str, Any],
        receipt_key: str,
        receipt: Binary,
        reward_day: str | None,
        reward_limit: int | None,
        purchase_item: str | None,
        daily_day: str | None,
        daily_amounts: tuple[int, ...] | None,
    ) -> bool:
        async with self._lock:
            account = self._accounts.setdefault((uid, cid),
                {"balance": 0, "applied_events": {}, "receipts": {}})
            events = account["applied_events"]
            if daily_day is not None:
                last_day, last_streak = self._daily_progress(account)
                if last_day is not None and daily_day <= last_day:
                    return False
                new_streak = last_streak + 1 if last_day == (
                    date.fromisoformat(daily_day) - timedelta(days=1)).isoformat() else 1
                daily_amount = daily_amounts[min(new_streak - 1, len(daily_amounts) - 1)]
            if self._event_status(account, safe_key, record, receipt_key, receipt):
                return False
            if purchase_item is not None and purchase_item in account.get("inventory", []):
                return False
            receipts = account.setdefault("receipts", {})
            if (_receipt_size(receipts) + _receipt_size({receipt_key: receipt})
                    - _receipt_size({})) > MAX_RECEIPT_BYTES:
                raise RuntimeError("economy_receipt_capacity_reached")
            balance = int(account.get("balance") or 0)
            if direction == "debit" and balance < amount:
                return False
            actual_delta, actual_amount = delta, amount
            if reward_day is not None:
                earned = int(account.get("reward_earned") or 0) if account.get("reward_day") == reward_day else 0
                actual_amount = min(amount, max(0, int(reward_limit) - earned))
                actual_delta = actual_amount
                account.update(reward_day=reward_day, reward_earned=earned + actual_amount)
            if daily_day is not None:
                actual_amount = int(daily_amount)
                actual_delta = actual_amount
                account.update(daily_day=daily_day, streak=new_streak)
            account["balance"] = balance + actual_delta
            events[safe_key] = dict(record, amount=actual_amount, delta=actual_delta)
            if daily_day is not None:
                events[safe_key]["streak"] = new_streak
            receipts[receipt_key] = receipt
            account["receipt_bytes"] = _receipt_size(receipts)
            if purchase_item is not None:
                account.setdefault("inventory", []).append(purchase_item)
            self._trim(events)
            return True

    @staticmethod
    def _event_status(document: dict[str, Any] | None, safe_key: str,
                      record: dict[str, Any], receipt_key: str, receipt: Binary) -> bool:
        if not document:
            return False
        prior_receipt = (document.get("receipts") or {}).get(receipt_key)
        if prior_receipt is not None:
            old_key, old_signature = _decode_receipt(prior_receipt)
            new_key, new_signature = _decode_receipt(receipt)
            if old_key != new_key:
                raise RuntimeError("economy_receipt_collision")
            if old_signature != new_signature:
                raise ValueError("economy_event_payload_mismatch")
            return True
        prior = (document.get("applied_events") or {}).get(safe_key)
        if prior is None:
            prior = (document.get("applied_events") or {}).get(
                EconomyStore._legacy_safe_event_key(record["event_key"]))
        if prior is None:
            return False
        if prior.get("event_key") != record["event_key"]:
            raise RuntimeError("economy_legacy_key_collision")
        if any(prior.get(field) != record[field] for field in ("direction", "amount", "delta", "reason")):
            raise ValueError("economy_event_payload_mismatch")
        return True

    async def _prune(self, collection: Any, account: dict[str, int]) -> None:
        """Keep the applied-event map bounded without touching correctness.

        Pruning only limits the document size.  A failure must never turn an
        applied event into a refused one, so the error is logged and the
        event stays applied.
        """
        try:
            await self.prune_account(collection, int(account["user_id"]), int(account["chat_id"]))
        except Exception as exc:  # noqa: BLE001 - post-credit prune may retry later
            logger.warning(
                "Economy ledger pruning failed: %s",
                summarize_exception(exc, label="economy_prune"),
            )

    @classmethod
    def excess_event_keys(cls, events: dict[str, Any]) -> list[str]:
        """Return the applied-event keys that exceed the per-account cap.

        The oldest events are dropped first.  The rule is shared by the store
        and by the migration that seeds a ledger from the historical
        transaction rows, so both bound a document the same way.
        """
        if len(events) <= cls.MAX_APPLIED_EVENTS:
            return []
        ordered = sorted(events.items(), key=lambda item: (cls._moment(item[1]), item[0]))
        return [key for key, _ in ordered[: len(events) - cls.MAX_APPLIED_EVENTS]]

    @classmethod
    async def prune_account(cls, collection: Any, user_id: int, chat_id: int) -> int:
        """Bound the applied-event map of one account and report the drop count.

        Pruning only limits the document size; it never changes the balance
        and never invalidates an applied event.
        """
        account = {"user_id": int(user_id), "chat_id": int(chat_id)}
        for _ in range(8):
            document = await collection.find_one(account)
            events = (document or {}).get("applied_events") or {}
            dropped = cls.excess_event_keys(events)
            if not dropped:
                return 0
            receipts = dict(document.get("receipts") or {})
            new_receipts = {}
            legacy_daily_day = None
            for key in dropped:
                record = events[key]
                event_key = str(record.get("event_key") or key)
                digest_key = _receipt_key(event_key)
                signature = _payload_digest(str(record.get("direction") or "credit"),
                    int(record.get("delta") or 0), int(record.get("amount") or 0),
                    str(record.get("reason") or ""))
                receipt = _receipt_value(event_key, signature)
                existing = receipts.get(digest_key)
                if existing is not None and _decode_receipt(existing)[0] != event_key:
                    raise RuntimeError("economy_receipt_collision")
                if existing is None:
                    receipts[digest_key] = receipt
                    new_receipts[digest_key] = receipt
                if record.get("reason") == "daily_reward" and isinstance(record.get("at"), datetime):
                    at = record["at"]
                    at = at.replace(tzinfo=UTC) if at.tzinfo is None else at
                    local_day = at.astimezone(ZoneInfo("Europe/Istanbul")).date().isoformat()
                    legacy_daily_day = max(legacy_daily_day or local_day, local_day)
            budget = _receipt_size(receipts)
            if budget > MAX_RECEIPT_BYTES:
                raise RuntimeError("economy_receipt_capacity_reached")
            previous_budget = document.get("receipt_bytes")
            query = {**account, "receipt_bytes": previous_budget if previous_budget is not None
                     else {"$exists": False}}
            set_fields = {"receipt_bytes": budget, **{
                f"receipts.{key}": value for key, value in new_receipts.items()}}
            if not document.get("daily_day") and legacy_daily_day:
                derived_day, _ = cls._daily_progress(document)
                set_fields.update(daily_day=derived_day, streak=1)
            update = {"$unset": {f"applied_events.{key}": "" for key in dropped},
                      "$set": set_fields}
            result = await collection.update_one(query, update)
            if getattr(result, "modified_count", 0):
                return len(dropped)
        raise RuntimeError("economy_prune_conflict")

    @classmethod
    def _trim(cls, events: dict[str, Any]) -> None:
        if len(events) <= cls.MAX_APPLIED_EVENTS:
            return
        ordered = sorted(events.items(), key=lambda item: (cls._moment(item[1]), item[0]))
        for key, _ in ordered[: len(events) - cls.MAX_APPLIED_EVENTS]:
            events.pop(key, None)

    @staticmethod
    def _moment(record: Any) -> float:
        """Return the sortable timestamp of one applied-event record."""
        value = record.get("at") if isinstance(record, dict) else None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=UTC)
            return value.timestamp()
        return 0.0

    @staticmethod
    def _event_row(user_id: int, chat_id: int, record: dict[str, Any]) -> dict[str, Any]:
        return {
            "event_key": str(record.get("event_key") or ""),
            "user_id": int(user_id),
            "chat_id": int(chat_id),
            "amount": int(record.get("amount") or 0),
            "direction": str(record.get("direction") or "credit"),
            "delta": int(record.get("delta") or 0),
            "reason": str(record.get("reason") or ""),
            "created_at": record.get("at"),
        }

    @staticmethod
    def _is_duplicate_key(exc: BaseException) -> bool:
        if getattr(exc, "code", None) == 11000:
            return True
        text = str(exc).casefold()
        return "duplicate" in text or "11000" in text


__all__ = ["MAX_APPLIED_EVENTS", "EconomyStore"]
