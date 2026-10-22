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
import secrets
import time
from collections import OrderedDict
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Generic, Mapping, TypeVar

from src.storage.production import is_production_database


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
    LEASE_SECONDS = 60
    LEASE_HEARTBEAT_SECONDS = 20
    LEASE_OPERATION_SECONDS = 45

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
            is_production_database(database)
            or is_production_database(wrapped)
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
        self._leases: dict[str, tuple[str, float]] = {}
        self._lock = asyncio.Lock()

    def _lease_methods(self) -> tuple[Any | None, Any | None, Any | None]:
        names = ("claim_game_lease", "renew_game_lease", "release_game_lease")
        methods = []
        supported = []
        supports = getattr(self.database, "supports", None)
        for name in names:
            method = getattr(self.database, name, None)
            methods.append(method)
            try:
                supported.append(bool(supports(name)) if callable(supports) else callable(method))
            except Exception as exc:
                raise RuntimeError("game_persistence_capability_error") from exc
        if all(supported) and getattr(self.database, "available", True) is not False:
            return tuple(methods)
        # A partially upgraded repository must never use process-local locks
        # while another worker holds the durable lease.
        if any(supported) or not self.allow_memory_fallback:
            raise RuntimeError("game_persistence_unavailable")
        return None, None, None

    async def _memory_lease_claim(self, key: str, token: str) -> bool:
        async with self._lock:
            now = time.monotonic()
            prior = self._leases.get(key)
            if prior and prior[1] > now:
                return False
            self._leases[key] = (token, now + self.LEASE_SECONDS)
            return True

    async def _memory_lease_renew(self, key: str, token: str) -> bool:
        async with self._lock:
            now = time.monotonic()
            prior = self._leases.get(key)
            if not prior or prior[0] != token or prior[1] <= now:
                return False
            self._leases[key] = (token, now + self.LEASE_SECONDS)
            return True

    async def _memory_lease_release(self, key: str, token: str) -> None:
        async with self._lock:
            if self._leases.get(key, (None,))[0] == token:
                self._leases.pop(key, None)

    @asynccontextmanager
    async def lease(self, chat_id: int, game_type: str, action_id: str, *,
                    user_id: int | None = None):
        """Hold a renewable owner-scoped lock without changing callback receipts.

        The lease expires after a process crash. A renewal failure cancels the
        operation before another owner may acquire it; repository writes should
        still use idempotent financial event ids for uncertain network results.
        """
        key = self._key(chat_id, game_type, action_id)
        if user_id is not None and (isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0):
            raise ValueError("invalid_game_user_id")
        token = secrets.token_hex(16)
        claim, renew, release = self._lease_methods()
        if claim is None:
            acquired = await self._memory_lease_claim(key, token)
        else:
            acquired = bool(await claim(key, token, self.LEASE_SECONDS, datetime.now(timezone.utc)))
        if not acquired:
            raise ValueError("game_busy")

        owner = asyncio.current_task()
        lost = asyncio.Event()

        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(self.LEASE_HEARTBEAT_SECONDS)
                try:
                    if renew is None:
                        alive = await self._memory_lease_renew(key, token)
                    else:
                        alive = bool(await renew(key, token, self.LEASE_SECONDS, datetime.now(timezone.utc)))
                except Exception:
                    alive = False
                if not alive:
                    lost.set()
                    if owner is not None:
                        owner.cancel()
                    return

        task = asyncio.create_task(heartbeat())
        try:
            try:
                async with asyncio.timeout(self.LEASE_OPERATION_SECONDS):
                    yield
                if lost.is_set():
                    raise RuntimeError("game_lease_lost")
            except asyncio.CancelledError:
                if lost.is_set():
                    raise RuntimeError("game_lease_lost") from None
                raise
            except TimeoutError:
                raise TimeoutError("game_lease_timeout") from None
        finally:
            task.cancel()
            # A driver call may ignore cancellation; do not let cleanup hold
            # the owner indefinitely. Token-matched release fences a late
            # renewal from reviving a lease after this scope ends.
            done, _ = await asyncio.wait({task}, timeout=1)
            for finished in done:
                with suppress(asyncio.CancelledError):
                    finished.result()
            if release is None:
                await self._memory_lease_release(key, token)
            else:
                await release(key, token)

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
            self._leases.clear()
