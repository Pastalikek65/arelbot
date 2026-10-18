"""Bounded, idempotent group economy ledger for game rewards."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any


class EconomyService:
    MAX_TRANSACTION = 10_000
    DAILY_AMOUNT = 25
    MAX_REWARD_TRANSACTION = 1_000
    MAX_EVENT_ID = 180
    MAX_REASON = 120

    def __init__(self, db: Any | None = None, *, allow_memory_fallback: bool | None = None):
        self.db = db
        production_database = type(db).__module__ == "src.database"
        self.allow_memory_fallback = (
            db is None
            or not hasattr(db, "available")
            or (getattr(db, "available", True) is True and not production_database)
            if allow_memory_fallback is None
            else bool(allow_memory_fallback)
        )
        self._accounts: dict[tuple[int, int], dict[str, Any]] = {}
        self._transactions: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    @property
    def persistent(self) -> bool:
        return bool(self.db is not None and getattr(self.db, "available", False)
                    and getattr(self.db, "economy_accounts", None) is not None
                    and getattr(self.db, "economy_transactions", None) is not None)

    @staticmethod
    def _key(user_id: int, chat_id: int) -> tuple[int, int]:
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
            raise ValueError("invalid_user_id")
        if isinstance(chat_id, bool) or not isinstance(chat_id, int) or chat_id == 0:
            raise ValueError("invalid_chat_id")
        return int(user_id), int(chat_id)

    @classmethod
    def _validate_amount(cls, amount: int) -> int:
        if isinstance(amount, bool):
            raise ValueError("amount_out_of_range")
        amount = int(amount)
        if amount < 1 or amount > cls.MAX_TRANSACTION:
            raise ValueError("amount_out_of_range")
        return amount

    @classmethod
    def _event_key(cls, user_id: int, chat_id: int, event_id: str | None) -> str:
        cls._key(user_id, chat_id)
        if event_id is None or not str(event_id).strip():
            raise ValueError("event_id_required")
        value = str(event_id).strip()
        if len(value) > cls.MAX_EVENT_ID or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("event_id_invalid")
        return f"{int(user_id)}:{int(chat_id)}:{value}"

    @classmethod
    def _reason(cls, reason: str) -> str:
        value = str(reason or "").strip()
        if not value or len(value) > cls.MAX_REASON or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("reason_invalid")
        return value

    @classmethod
    def _validate_reward_limit(cls, amount: int, reason: str, direction: str) -> None:
        if direction != "credit":
            return
        marker = reason.casefold()
        # Regular game rewards are deliberately small.  Settlement payouts
        # (for example a bounded blackjack stake return) remain governed by
        # MAX_TRANSACTION and their game service's own bet limit.
        if any(token in marker for token in ("reward", "bonus", "daily", "prize")) and amount > cls.MAX_REWARD_TRANSACTION:
            raise ValueError("reward_amount_out_of_range")

    def _ensure_storage(self) -> None:
        if self.db is not None and not self.persistent and not self.allow_memory_fallback:
            raise RuntimeError("economy_persistence_unavailable")

    async def balance(self, user_id: int, chat_id: int) -> int:
        self._key(user_id, chat_id)
        self._ensure_storage()
        if self.persistent:
            item = await self.db.economy_accounts.find_one(
                {"user_id": int(user_id), "chat_id": int(chat_id)}, {"_id": 0, "balance": 1}
            )
            return int((item or {}).get("balance", 0))
        return int(self._accounts.get(self._key(user_id, chat_id), {}).get("balance", 0))

    async def _record(
        self,
        user_id: int,
        chat_id: int,
        amount: int,
        *,
        reason: str,
        event_id: str | None,
        direction: str,
    ) -> bool:
        self._key(user_id, chat_id)
        amount = self._validate_amount(amount)
        if direction not in {"credit", "debit"}:
            raise ValueError("invalid_direction")
        safe_reason = self._reason(reason)
        self._validate_reward_limit(amount, safe_reason, direction)
        key = self._event_key(user_id, chat_id, event_id)
        delta = amount if direction == "credit" else -amount
        now = datetime.now(timezone.utc)
        self._ensure_storage()
        if self.persistent:
            try:
                await self.db.economy_transactions.insert_one({
                    "event_key": key,
                    "user_id": int(user_id),
                    "chat_id": int(chat_id),
                    "amount": amount,
                    "direction": direction,
                    "delta": delta,
                    "reason": safe_reason,
                    "created_at": now,
                })
            except Exception as exc:
                if "duplicate" in str(exc).casefold() or "11000" in str(exc):
                    return False
                raise
            query = {"user_id": int(user_id), "chat_id": int(chat_id)}
            if direction == "debit":
                query["balance"] = {"$gte": amount}
            result = await self.db.economy_accounts.update_one(
                query,
                {
                    "$inc": {"balance": delta},
                    "$set": {"updated_at": now},
                    "$setOnInsert": {"user_id": int(user_id), "chat_id": int(chat_id)},
                },
                upsert=direction == "credit",
            )
            if not result.modified_count and not result.upserted_id:
                await self.db.economy_transactions.delete_one({"event_key": key})
                return False
            return True
        async with self._lock:
            if key in self._transactions:
                return False
            account = self._accounts.setdefault(self._key(user_id, chat_id), {"balance": 0})
            if direction == "debit" and account["balance"] < amount:
                return False
            account["balance"] += delta
            self._transactions[key] = {
                "event_key": key,
                "user_id": int(user_id),
                "chat_id": int(chat_id),
                "amount": amount,
                "direction": direction,
                "delta": delta,
                "reason": safe_reason,
                "created_at": now,
            }
            return True

    async def credit(self, user_id: int, chat_id: int, amount: int, *, reason: str, event_id: str | None = None) -> bool:
        return await self._record(user_id, chat_id, amount, reason=reason, event_id=event_id, direction="credit")

    async def debit(self, user_id: int, chat_id: int, amount: int, *, reason: str, event_id: str | None = None) -> bool:
        return await self._record(user_id, chat_id, amount, reason=reason, event_id=event_id, direction="debit")

    async def claim_daily(self, user_id: int, chat_id: int, *, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        day = now.astimezone(timezone.utc).date().isoformat()
        accepted = await self.credit(
            user_id,
            chat_id,
            self.DAILY_AMOUNT,
            reason="daily_reward",
            event_id=f"daily:{day}",
        )
        return {
            "accepted": accepted,
            "amount": self.DAILY_AMOUNT if accepted else 0,
            "balance": await self.balance(user_id, chat_id),
            "day": day,
        }

    async def history(self, user_id: int, chat_id: int, limit: int = 20) -> list[dict[str, Any]]:
        self._key(user_id, chat_id)
        self._ensure_storage()
        try:
            bounded_limit = max(1, min(100, int(limit)))
        except (TypeError, ValueError, OverflowError):
            raise ValueError("history_limit_invalid")
        if self.persistent:
            cursor = self.db.economy_transactions.find(
                {"user_id": int(user_id), "chat_id": int(chat_id)}, {"_id": 0}
            ).sort("created_at", -1).limit(bounded_limit)
            return await cursor.to_list(length=bounded_limit)
        rows = [row for row in self._transactions.values() if row["user_id"] == int(user_id) and row["chat_id"] == int(chat_id)]
        return sorted(rows, key=lambda row: row["created_at"], reverse=True)[:bounded_limit]
