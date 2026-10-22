"""Small async state store abstraction.

FSM payloads are deliberately JSON-shaped so they can move between the local
development backend and Redis without changing handlers.  Redis is preferred
in production; the memory backend is only useful for tests and local polling.
"""

from __future__ import annotations

import asyncio
import copy
import json
import time
from collections.abc import MutableMapping
from typing import Any, Protocol


class StateStore(Protocol):
    async def get(self, key: str) -> dict[str, Any] | None: ...
    async def set(self, key: str, value: dict[str, Any], ttl: int | None = None) -> None: ...
    async def delete(self, key: str) -> None: ...


class MemoryStateStore:
    def __init__(self) -> None:
        self._values: dict[str, tuple[dict[str, Any], float | None]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> dict[str, Any] | None:
        async with self._lock:
            item = self._values.get(key)
            if not item:
                return None
            value, expires_at = item
            if expires_at is not None and expires_at <= time.time():
                self._values.pop(key, None)
                return None
            return copy.deepcopy(value)

    async def set(self, key: str, value: dict[str, Any], ttl: int | None = None) -> None:
        expires_at = time.time() + ttl if ttl else None
        async with self._lock:
            self._values[key] = (copy.deepcopy(value), expires_at)

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._values.pop(key, None)

    async def scan(self) -> list[tuple[str, dict[str, Any]]]:
        """Return live JSON-shaped records for restart/replay tests."""

        async with self._lock:
            now = time.time()
            values: list[tuple[str, dict[str, Any]]] = []
            expired: list[str] = []
            for key, (value, expires_at) in self._values.items():
                if expires_at is not None and expires_at <= now:
                    expired.append(key)
                    continue
                values.append((key, copy.deepcopy(value)))
            for key in expired:
                self._values.pop(key, None)
            return values

    async def close(self) -> None:
        async with self._lock:
            self._values.clear()


class RedisStateStore:
    def __init__(self, redis_client: Any, prefix: str = "arel:state:") -> None:
        self.redis = redis_client
        self.prefix = prefix

    def _key(self, key: str) -> str:
        return f"{self.prefix}{key}"

    async def get(self, key: str) -> dict[str, Any] | None:
        raw = await self.redis.get(self._key(key))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    async def set(self, key: str, value: dict[str, Any], ttl: int | None = None) -> None:
        encoded = json.dumps(value, ensure_ascii=False, default=str)
        if ttl:
            await self.redis.set(self._key(key), encoded, ex=ttl)
        else:
            await self.redis.set(self._key(key), encoded)

    async def delete(self, key: str) -> None:
        await self.redis.delete(self._key(key))

    async def scan(self) -> list[tuple[str, dict[str, Any]]]:
        values = []
        async for raw_key in self.redis.scan_iter(match=f"{self.prefix}*"):
            key = raw_key.decode() if isinstance(raw_key, bytes) else raw_key
            payload = await self.get(key[len(self.prefix):])
            if payload is not None:
                values.append((key[len(self.prefix):], payload))
        return values


class _TrackedDict(dict):
    def __init__(self, value: dict[str, Any], changed):
        self._changed = changed
        super().__init__({key: self._wrap(item) for key, item in value.items()})

    def _wrap(self, value):
        return _wrap_state_value(value, self._changed)

    def __setitem__(self, key, value):
        super().__setitem__(key, self._wrap(value))
        self._changed()

    def __delitem__(self, key):
        super().__delitem__(key)
        self._changed()

    def update(self, *args, **kwargs):
        values = dict(*args, **kwargs)
        for key, value in values.items():
            super().__setitem__(key, self._wrap(value))
        if values:
            self._changed()

    def pop(self, key, *default):
        if key not in self:
            if default:
                return default[0]
            raise KeyError(key)
        value = super().pop(key)
        self._changed()
        return value

    def popitem(self):
        value = super().popitem()
        self._changed()
        return value

    def clear(self):
        if self:
            super().clear()
            self._changed()

    def setdefault(self, key, default=None):
        if key in self:
            return self[key]
        value = self._wrap(default)
        super().__setitem__(key, value)
        self._changed()
        return value

    def __ior__(self, other):
        self.update(other)
        return self


class _TrackedList(list):
    """List that persists in-place FSM payload mutations."""

    def __init__(self, value: list[Any], changed):
        self._changed = changed
        super().__init__(_wrap_state_value(item, changed) for item in value)

    def _wrap(self, value):
        return _wrap_state_value(value, self._changed)

    def __setitem__(self, index, value):
        if isinstance(index, slice):
            value = [self._wrap(item) for item in value]
        else:
            value = self._wrap(value)
        super().__setitem__(index, value)
        self._changed()

    def __delitem__(self, index):
        super().__delitem__(index)
        self._changed()

    def append(self, value):
        super().append(self._wrap(value))
        self._changed()

    def extend(self, values):
        values = [self._wrap(value) for value in values]
        if values:
            super().extend(values)
            self._changed()

    def insert(self, index, value):
        super().insert(index, self._wrap(value))
        self._changed()

    def pop(self, index=-1):
        value = super().pop(index)
        self._changed()
        return value

    def remove(self, value):
        super().remove(value)
        self._changed()

    def clear(self):
        if self:
            super().clear()
            self._changed()

    def reverse(self):
        super().reverse()
        self._changed()

    def sort(self, *args, **kwargs):
        super().sort(*args, **kwargs)
        self._changed()

    def __iadd__(self, values):
        self.extend(values)
        return self

    def __imul__(self, value):
        super().__imul__(value)
        self._changed()
        return self


def _wrap_state_value(value, changed):
    if isinstance(value, _TrackedDict) or isinstance(value, _TrackedList):
        return value
    if isinstance(value, dict):
        return _TrackedDict(value, changed)
    if isinstance(value, list):
        return _TrackedList(value, changed)
    return value


class PersistentStateMap(MutableMapping):
    """Dict-compatible state map with asynchronous Redis persistence."""

    def __init__(self, ttl: int = 30 * 60):
        self._values: dict[str, _TrackedDict] = {}
        self._store: StateStore | None = None
        self.ttl = ttl
        self._pending: set[asyncio.Task] = set()

    def bind(self, store: StateStore) -> None:
        self._store = store

    @staticmethod
    def _legacy_key(user_id: int | str) -> str:
        return f"user:{int(user_id)}"

    @staticmethod
    def _scope_key(user_id: int | str, chat_id: int | str) -> str:
        return f"scope:{int(user_id)}:{int(chat_id)}"

    @staticmethod
    def _scoped_value(value: dict[str, Any], user_id: int, chat_id: int) -> dict[str, Any]:
        scoped = copy.deepcopy(value)
        scoped["_scope"] = {"user_id": int(user_id), "chat_id": int(chat_id)}
        return scoped

    def _schedule_key(self, key: str):
        if self._store is None:
            return
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(self._store.set(key, self._plain(self._values[key]), ttl=self.ttl))
            self._pending.add(task)
            task.add_done_callback(self._pending.discard)
        except RuntimeError:
            pass

    def _schedule(self, user_id: str):
        self._schedule_key(self._legacy_key(user_id))

    def _wrap(self, key: str, value: dict[str, Any]) -> _TrackedDict:
        return _TrackedDict(value, lambda: self._schedule_key(key))

    @staticmethod
    def _plain(value):
        if isinstance(value, dict):
            return {key: PersistentStateMap._plain(item) for key, item in value.items()}
        if isinstance(value, list):
            return [PersistentStateMap._plain(item) for item in value]
        return value

    async def hydrate(self) -> None:
        if self._store is None:
            return
        scan = getattr(self._store, "scan", None)
        if scan is None:
            return
        for key, value in await scan():
            if key.startswith("user:") and isinstance(value, dict):
                user_id = key.split(":", 1)[1]
                self._values[key] = self._wrap(key, value)
            elif key.startswith("scope:") and isinstance(value, dict):
                parts = key.split(":")
                if len(parts) == 3 and all(part.lstrip("-").isdigit() for part in parts[1:]):
                    self._values[key] = self._wrap(key, value)

    def __getitem__(self, key):
        return self._values[self._legacy_key(key)]

    def __setitem__(self, key, value):
        storage_key = self._legacy_key(key)
        self._values[storage_key] = self._wrap(storage_key, value)
        self._schedule_key(storage_key)

    def __delitem__(self, key):
        storage_key = self._legacy_key(key)
        del self._values[storage_key]
        if self._store:
            try:
                task = asyncio.get_running_loop().create_task(self._store.delete(storage_key))
                self._pending.add(task)
                task.add_done_callback(self._pending.discard)
            except RuntimeError:
                pass

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def pop(self, key, *default):
        storage_key = self._legacy_key(key)
        if storage_key not in self._values:
            return default[0] if default else None
        value = self._values[storage_key]
        del self._values[storage_key]
        if self._store:
            try:
                task = asyncio.get_running_loop().create_task(self._store.delete(storage_key))
                self._pending.add(task)
                task.add_done_callback(self._pending.discard)
            except RuntimeError:
                pass
        return value

    def get_for_scope(self, user_id: int, chat_id: int) -> dict[str, Any] | None:
        key = self._scope_key(user_id, chat_id)
        value = self._values.get(key)
        return value

    def set_for_scope(self, user_id: int, chat_id: int, value: dict[str, Any]) -> None:
        key = self._scope_key(user_id, chat_id)
        self._values[key] = self._wrap(key, self._scoped_value(value, user_id, chat_id))
        self._schedule_key(key)

    def pop_for_scope(self, user_id: int, chat_id: int) -> dict[str, Any] | None:
        key = self._scope_key(user_id, chat_id)
        value = self._values.pop(key, None)
        if value is not None and self._store:
            try:
                task = asyncio.get_running_loop().create_task(self._store.delete(key))
                self._pending.add(task)
                task.add_done_callback(self._pending.discard)
            except RuntimeError:
                pass
        return value

    def clear_user(self, user_id: int) -> int:
        prefix = f"scope:{int(user_id)}:"
        keys = [key for key in self._values if key == self._legacy_key(user_id) or key.startswith(prefix)]
        for key in keys:
            self._values.pop(key, None)
            if self._store:
                try:
                    task = asyncio.get_running_loop().create_task(self._store.delete(key))
                    self._pending.add(task)
                    task.add_done_callback(self._pending.discard)
                except RuntimeError:
                    pass
        return len(keys)

    async def flush(self) -> None:
        if self._pending:
            await asyncio.gather(*list(self._pending), return_exceptions=True)

    async def close(self) -> None:
        await self.flush()
        self._values.clear()


async def create_state_store(redis_client: Any | None) -> StateStore:
    """Return the production backend when Redis is available."""
    if redis_client is not None:
        return RedisStateStore(redis_client)
    return MemoryStateStore()
