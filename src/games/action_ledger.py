"""Idempotent action claims for interactive Telegram games.

Telegram may deliver a callback more than once (for example after a slow
network response or a client retry).  Game state mutations and rewards must
therefore have a durable, chat-scoped action key.  The ledger uses MongoDB in
production and a bounded process-local set for tests/degraded development;
production startup still requires MongoDB for game persistence.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Generic, Mapping, TypeVar


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ActionRunResult(Generic[T]):
    """Outcome of an idempotent game action execution."""

    executed: bool
    replayed: bool
    result: T | None = None


class GameActionLedger:
    """Claim/release callback actions exactly once per action id.

    Durable repository claims are mandatory whenever a production-like
    database advertises availability.  Process-local claims are a bounded
    test/degraded fallback only and expire to prevent unbounded growth.
    """

    MAX_ACTION_ID = 180
    MAX_GAME_TYPE = 64
    MAX_PAYLOAD_KEYS = 32
    MAX_PAYLOAD_BYTES = 4096
    MEMORY_CLAIM_TTL_SECONDS = 3600
    MAX_MEMORY_CLAIMS = 20_000

    def __init__(
        self,
        database: Any | None = None,
        *,
        allow_memory_fallback: bool | None = None,
        memory_claim_ttl_seconds: int = MEMORY_CLAIM_TTL_SECONDS,
    ) -> None:
        self.database = database
        test_port_fallback = (
            type(database).__name__ == "HandlerDataPort"
            and not hasattr(getattr(database, "_repository", None), "available")
        )
        wrapped = getattr(database, "_repository", None)
        production_database = (
            type(database).__module__ == "src.database"
            or type(wrapped).__module__ == "src.database"
        )
        self.allow_memory_fallback = (
            database is None
            or not hasattr(database, "available")
            or test_port_fallback
            or (getattr(database, "available", True) is True and not production_database)
            if allow_memory_fallback is None
            else bool(allow_memory_fallback)
        )
        try:
            self.memory_claim_ttl_seconds = max(60, min(24 * 60 * 60, int(memory_claim_ttl_seconds)))
        except (TypeError, ValueError, OverflowError):
            self.memory_claim_ttl_seconds = self.MEMORY_CLAIM_TTL_SECONDS
        self._claimed: OrderedDict[str, float] = OrderedDict()
        self._lock = asyncio.Lock()

    @staticmethod
    def _key(chat_id: int, game_type: str, action_id: str) -> str:
        if isinstance(chat_id, bool) or not isinstance(chat_id, int) or chat_id == 0:
            raise ValueError("invalid_game_chat_id")
        action = str(action_id).strip()
        if not action or len(action) > GameActionLedger.MAX_ACTION_ID or any(
            ord(char) < 32 or ord(char) == 127 for char in action
        ):
            raise ValueError("invalid_game_action_id")
        game = str(game_type).strip().casefold()
        if not game or len(game) > GameActionLedger.MAX_GAME_TYPE or any(
            char not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for char in game
        ):
            raise ValueError("invalid_game_type")
        return f"{int(chat_id)}:{game}:{action}"

    @classmethod
    def _payload(cls, payload: Mapping[str, Any] | None) -> dict[str, Any]:
        if payload is None:
            return {}
        if not isinstance(payload, Mapping) or len(payload) > cls.MAX_PAYLOAD_KEYS:
            raise ValueError("invalid_game_action_payload")
        value = dict(payload)
        if any(
            not isinstance(key, str)
            or not key.strip()
            or len(key) > 64
            or any(ord(char) < 32 or ord(char) == 127 for char in key)
            for key in value
        ):
            raise ValueError("invalid_game_action_payload")
        try:
            encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid_game_action_payload") from exc
        if len(encoded.encode("utf-8")) > cls.MAX_PAYLOAD_BYTES:
            raise ValueError("game_action_payload_too_large")
        return value

    def _durable_capability(self, operation: str) -> tuple[Any | None, bool]:
        method = getattr(self.database, operation, None)
        supports = getattr(self.database, "supports", None)
        try:
            durable = bool(supports(operation)) if callable(supports) else method is not None
        except Exception as exc:
            raise RuntimeError("game_persistence_capability_error") from exc
        available = getattr(self.database, "available", True)
        if method is not None and durable and available is not False:
            return method, True
        if self.database is not None and not self.allow_memory_fallback:
            raise RuntimeError("game_persistence_unavailable")
        return None, False

    async def _prune_memory(self, now: float) -> None:
        cutoff = now - self.memory_claim_ttl_seconds
        for key, claimed_at in list(self._claimed.items()):
            if claimed_at <= cutoff:
                self._claimed.pop(key, None)
        while len(self._claimed) > self.MAX_MEMORY_CLAIMS:
            self._claimed.popitem(last=False)

    async def claim(
        self,
        chat_id: int,
        game_type: str,
        action_id: str,
        *,
        user_id: int | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> bool:
        """Atomically claim an action, returning ``False`` for a retry."""
        key = self._key(chat_id, game_type, action_id)
        if user_id is not None and (isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0):
            raise ValueError("invalid_game_user_id")
        safe_payload = self._payload(payload)
        claim_method, durable = self._durable_capability("claim_game_action")
        if durable:
            return bool(
                await claim_method(
                    action_key=key,
                    chat_id=int(chat_id),
                    game_type=str(game_type),
                    action_id=str(action_id),
                    user_id=int(user_id) if user_id is not None else None,
                    payload=safe_payload,
                )
            )
        async with self._lock:
            now = time.monotonic()
            await self._prune_memory(now)
            if key in self._claimed:
                return False
            self._claimed[key] = now
            return True

    async def release(self, chat_id: int, game_type: str, action_id: str) -> None:
        """Release a claim when the mutation failed before being committed."""
        key = self._key(chat_id, game_type, action_id)
        release_method, durable = self._durable_capability("release_game_action")
        if durable:
            await release_method(key)
            return
        async with self._lock:
            self._claimed.pop(key, None)

    async def run(
        self,
        chat_id: int,
        game_type: str,
        action_id: str,
        operation: Callable[[], Awaitable[T] | T],
        *,
        user_id: int | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> ActionRunResult[T]:
        """Claim, execute and release a failed action as one safe contract.

        A callback retry returns ``replayed=True`` without invoking the
        operation.  Exceptions release the claim so transient failures can be
        retried; a successful operation remains claimed permanently.
        """

        accepted = await self.claim(
            chat_id,
            game_type,
            action_id,
            user_id=user_id,
            payload=payload,
        )
        if not accepted:
            return ActionRunResult(executed=False, replayed=True)
        try:
            value = operation()
            if inspect.isawaitable(value):
                value = await value
        except Exception:
            # Keep the operation's failure visible even if a degraded
            # repository cannot release its durable claim.  A retained claim
            # is fail-safe (it blocks a potentially duplicate mutation).
            try:
                await self.release(chat_id, game_type, action_id)
            except Exception:
                pass
            raise
        return ActionRunResult(executed=True, replayed=False, result=value)

    async def close(self) -> None:
        async with self._lock:
            self._claimed.clear()
