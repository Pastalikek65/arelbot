"""Bounded, idempotent group economy ledger for game rewards."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


class EconomyService:
    MAX_TRANSACTION = 10_000
    DAILY_AMOUNT = 25

    def __init__(self, db: Any | None = None):
        self.db = db
        self._accounts: dict[tuple[int, int], dict[str, Any]] = {}
        self._transactions: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    @property
    def persistent(self) -> bool:
        return bool(self.db is not None and getattr(self.db, "available", False)
                    and getattr(self.db, "economy_accounts", None) is not None)

    @staticmethod
    def _key(user_id: int, chat_id: int) -> tuple[int, int]:
        return int(user_id), int(chat_id)

    @classmethod
    def _validate_amount(cls, amount: int) -> int:
        amount = int(amount)
        if amount < 1 or amount > cls.MAX_TRANSACTION:
            raise ValueError("amount_out_of_range")
        return amount

    @staticmethod
    def _event_key(user_id: int, chat_id: int, event_id: str | None) -> str:
        return f"{int(user_id)}:{int(chat_id)}:{event_id or uuid4().hex}"

    async def balance(self, user_id: int, chat_id: int) -> int:
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
        amount = self._validate_amount(amount)
        if direction not in {"credit", "debit"}:
            raise ValueError("invalid_direction")
        key = self._event_key(user_id, chat_id, event_id)
        delta = amount if direction == "credit" else -amount
        now = datetime.now(timezone.utc)
        if self.persistent:
            try:
                await self.db.economy_transactions.insert_one({
                    "event_key": key,
                    "user_id": int(user_id),
                    "chat_id": int(chat_id),
                    "amount": amount,
                    "direction": direction,
                    "delta": delta,
                    "reason": str(reason)[:120],
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
                "reason": str(reason)[:120],
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
        if self.persistent:
            cursor = self.db.economy_transactions.find(
                {"user_id": int(user_id), "chat_id": int(chat_id)}, {"_id": 0}
            ).sort("created_at", -1).limit(limit)
            return await cursor.to_list(length=limit)
        rows = [row for row in self._transactions.values() if row["user_id"] == int(user_id) and row["chat_id"] == int(chat_id)]
        return sorted(rows, key=lambda row: row["created_at"], reverse=True)[:limit]
