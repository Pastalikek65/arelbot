"""Bounded, idempotent group economy ledger for game rewards."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import UTC, date, datetime, timedelta
from typing import Any

from src.games import economy_policy as policy
from src.observability.metrics import MetricsRegistry
from src.storage.economy_store import EconomyStore
from src.storage.production import is_production_database


class EconomyService:
    MAX_TRANSACTION = 10_000
    MAX_SETTLEMENT = 40_000
    DAILY_AMOUNT = 25
    MAX_REWARD_TRANSACTION = 1_000
    MAX_EVENT_ID = 180
    MAX_REASON = 120

    def __init__(self, db: Any | None = None, *, allow_memory_fallback: bool | None = None,
                 intent_signing_key: str | bytes | None = None, metrics=None):
        self.db = db
        production_database = is_production_database(db)
        self.allow_memory_fallback = (
            db is None
            or not hasattr(db, "available")
            or (getattr(db, "available", True) is True and not production_database)
            if allow_memory_fallback is None
            else bool(allow_memory_fallback)
        )
        # The ledger itself lives in the storage layer.  The service only
        # validates the request and formats the result.
        self._store = EconomyStore(db, required=not self.allow_memory_fallback)
        self.metrics = metrics if metrics is not None else MetricsRegistry()
        if intent_signing_key is None:
            self._intent_key = secrets.token_bytes(32) if self.allow_memory_fallback else None
        else:
            key = intent_signing_key.encode() if isinstance(intent_signing_key, str) else intent_signing_key
            self._intent_key = hashlib.sha256(b'arel:cosmetic-confirm:v1:' + key).digest()

    @property
    def persistent(self) -> bool:
        return self._store.persistent

    @staticmethod
    def _key(user_id: int, chat_id: int) -> tuple[int, int]:
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
            raise ValueError("invalid_user_id")
        if isinstance(chat_id, bool) or not isinstance(chat_id, int) or chat_id == 0:
            raise ValueError("invalid_chat_id")
        return int(user_id), int(chat_id)

    @classmethod
    def _validate_amount(cls, amount: int, *, limit: int | None = None) -> int:
        if isinstance(amount, bool) or not isinstance(amount, int):
            raise ValueError("amount_out_of_range")  # noqa: TRY004 - stable domain error contract
        amount = int(amount)
        if amount < 1 or amount > (cls.MAX_TRANSACTION if limit is None else limit):
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
        return await self._store.balance(int(user_id), int(chat_id))

    async def was_applied(self, user_id: int, chat_id: int, event_id: str) -> bool:
        self._key(user_id, chat_id)
        self._ensure_storage()
        return await self._store.event_applied(user_id, chat_id,
                                               self._event_key(user_id, chat_id, event_id))

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
        if direction not in {"credit", "debit"}:
            raise ValueError("invalid_direction")
        safe_reason = self._reason(reason)
        settlement = direction == 'credit' and safe_reason in {'blackjack_settlement', 'blackjack_cancel'}
        amount = self._validate_amount(amount, limit=self.MAX_SETTLEMENT if settlement else None)
        self._validate_reward_limit(amount, safe_reason, direction)
        key = self._event_key(user_id, chat_id, event_id)
        delta = amount if direction == "credit" else -amount
        now = datetime.now(UTC)
        self._ensure_storage()
        # The balance change and the applied-event stamp are written by the
        # store in one atomic document update, so a crash can never separate
        # them and strand a reward.
        budget = ({'reward_day': policy.local_day(now), 'reward_limit': policy.GAME_DAILY_LIMIT}
                  if direction == 'credit' and policy.game_reward(safe_reason) else {})
        accepted = await self._store.apply_event(
            user_id=int(user_id),
            chat_id=int(chat_id),
            event_key=key,
            delta=delta,
            direction=direction,
            amount=amount,
            reason=safe_reason,
            created_at=now,
            **budget,
        )
        self.metrics.inc('economy_ledger_applied_total' if accepted else 'economy_ledger_duplicate_or_refused_total')
        return accepted

    async def credit(self, user_id: int, chat_id: int, amount: int, *, reason: str, event_id: str | None = None) -> bool:
        return await self._record(user_id, chat_id, amount, reason=reason, event_id=event_id, direction="credit")

    async def debit(self, user_id: int, chat_id: int, amount: int, *, reason: str, event_id: str | None = None) -> bool:
        return await self._record(user_id, chat_id, amount, reason=reason, event_id=event_id, direction="debit")

    async def claim_daily(self, user_id: int, chat_id: int, *, now: datetime | None = None) -> dict[str, Any]:
        self._key(user_id, chat_id)
        self._ensure_storage()
        now = policy.moment(now)
        result = await self._store.daily(user_id, chat_id, policy.local_day(now),
                                         policy.DAILY_REWARDS, now)
        self.metrics.inc('economy_daily_applied_total' if result['accepted'] else 'economy_daily_duplicate_total')
        result['next_claim_at'] = policy.next_midnight(now)
        return result

    async def snapshot(self, user_id: int, chat_id: int, *, now: datetime | None = None) -> dict[str, Any]:
        self._key(user_id, chat_id)
        self._ensure_storage()
        now = policy.moment(now)
        account = await self._store.snapshot(user_id, chat_id)
        today = policy.local_day(now)
        yesterday = (date.fromisoformat(today) - timedelta(days=1)).isoformat()
        last_day = account.get('daily_day')
        streak = int(account.get('streak') or 0) if last_day in {today, yesterday} else 0
        next_streak = max(1, streak + (last_day != today))
        owned = account.get('inventory') or {}
        equipped = account.get('equipped') or {}
        earned = int(account.get('reward_earned') or 0) if account.get('reward_day') == today else 0
        balance = int(account.get('balance') or 0)
        eligibility = {
            item.key: {'owned': item.key in owned, 'affordable': balance >= item.price,
                       'missing_coins': max(0, item.price - balance),
                       'eligible': item.key not in owned and balance >= item.price}
            for item in policy.CATALOG.values()
        }
        return {'balance': balance, 'streak': streak,
                'daily_available': last_day != today,
                'daily_amount': policy.DAILY_REWARDS[min(next_streak, len(policy.DAILY_REWARDS))-1],
                'next_claim_at': policy.next_midnight(now), 'owned': list(owned), 'equipped': dict(equipped),
                'game_earned_today': earned, 'game_daily_limit': policy.GAME_DAILY_LIMIT,
                'game_reward_remaining_today': max(0, policy.GAME_DAILY_LIMIT - earned),
                'shop_eligibility': eligibility,
                'recent': await self.history(user_id, chat_id, limit=5)}

    async def catalog(self) -> list[dict[str, Any]]:
        return policy.catalog_rows()

    @staticmethod
    def _item(item_key: str):
        item = policy.CATALOG.get(str(item_key))
        if item is None:
            raise ValueError('economy_item_unknown')
        return item

    @staticmethod
    def _b36(value: int) -> str:
        result = ''
        while value:
            value, digit = divmod(value, 36)
            result = '0123456789abcdefghijklmnopqrstuvwxyz'[digit] + result
        return result or '0'

    def _signature(self, user_id, chat_id, item, expiry, price) -> str:
        if self._intent_key is None:
            raise RuntimeError('economy_confirmation_unavailable')
        payload = f'{user_id}:{chat_id}:{item}:{expiry}:{price}'.encode()
        return base64.urlsafe_b64encode(hmac.new(self._intent_key, payload, hashlib.sha256).digest()[:12]).decode().rstrip('=')

    async def prepare_purchase(self, user_id: int, chat_id: int, item_key: str, *, now=None) -> dict[str, Any]:
        self._key(user_id, chat_id)
        item = self._item(item_key)
        now = policy.moment(now)
        expiry = int(now.timestamp()) + policy.CONFIRM_SECONDS
        signature = self._signature(user_id, chat_id, item.key, expiry, item.price)
        return {'item': next(row for row in policy.catalog_rows() if row['key'] == item.key),
                'token': f'{self._b36(expiry)}.{self._b36(item.price)}.{signature}',
                'expires_at': datetime.fromtimestamp(expiry, UTC),
                'balance': await self.balance(user_id, chat_id)}

    async def purchase(self, user_id: int, chat_id: int, item_key: str, *, event_id: str,
                       confirmation_token: str, now=None) -> dict[str, Any]:
        self._key(user_id, chat_id)
        self._ensure_storage()
        item = self._item(item_key)
        now = policy.moment(now)
        try:
            expiry_text, price_text, signature = str(confirmation_token).split('.')
            expiry, price = int(expiry_text, 36), int(price_text, 36)
            expected = self._signature(user_id, chat_id, item.key, expiry, price)
            valid = (int(now.timestamp()) < expiry <= int(now.timestamp()) + policy.CONFIRM_SECONDS
                     and price == item.price and hmac.compare_digest(signature, expected))
        except (TypeError, ValueError):
            valid = False
        if not valid:
            self.metrics.inc('economy_confirmation_rejected_total')
            raise ValueError('economy_confirmation_invalid')
        accepted = await self._store.purchase(user_id, chat_id, item.key, item.price,
            self._event_key(user_id, chat_id, event_id), now)
        self.metrics.inc('economy_purchase_applied_total' if accepted else 'economy_purchase_refused_total')
        result = await self.snapshot(user_id, chat_id, now=now)
        result.update(accepted=accepted, status='purchased' if accepted else 'refused')
        return result

    async def equip(self, user_id: int, chat_id: int, item_key: str, *, event_id: str | None = None) -> dict[str, Any]:
        self._key(user_id, chat_id)
        self._ensure_storage()
        item = self._item(item_key)
        accepted = await self._store.equip(user_id, chat_id, item.key, item.slot)
        self.metrics.inc('economy_equip_applied_total' if accepted else 'economy_equip_refused_total')
        if not accepted:
            raise ValueError('economy_item_not_owned')
        result = await self.snapshot(user_id, chat_id)
        result.update(accepted=True, status='equipped')
        return result

    async def unequip(self, user_id: int, chat_id: int, slot: str) -> dict[str, Any]:
        self._key(user_id, chat_id)
        self._ensure_storage()
        if str(slot) not in {item.slot for item in policy.CATALOG.values()}:
            raise ValueError('economy_slot_unknown')
        accepted = await self._store.unequip(user_id, chat_id, str(slot))
        self.metrics.inc('economy_unequip_applied_total' if accepted else 'economy_unequip_unchanged_total')
        result = await self.snapshot(user_id, chat_id)
        result.update(accepted=accepted, status='unequipped' if accepted else 'unchanged')
        return result

    async def history(self, user_id: int, chat_id: int, limit: int = 20) -> list[dict[str, Any]]:
        self._key(user_id, chat_id)
        self._ensure_storage()
        try:
            bounded_limit = max(1, min(100, int(limit)))
        except (TypeError, ValueError, OverflowError):
            raise ValueError("history_limit_invalid")
        return await self._store.list_events(int(user_id), int(chat_id), bounded_limit)
