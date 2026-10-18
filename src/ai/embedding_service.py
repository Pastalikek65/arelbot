"""Bounded embedding gateway used by the document/RAG pipeline."""

from __future__ import annotations

import asyncio
import hashlib
import math
from collections import OrderedDict
from typing import Any


class EmbeddingService:
    def __init__(self, router: Any | None = None, *, enabled: bool = False,
                 max_cache_entries: int = 512, provider_names: list[str] | None = None) -> None:
        self.router = router
        self.enabled = bool(enabled)
        self.max_cache_entries = max(16, int(max_cache_entries))
        self.provider_names = list(provider_names or [])
        self._cache: OrderedDict[str, list[float]] = OrderedDict()
        self._lock = asyncio.Lock()

    @staticmethod
    def _key(text: str) -> str:
        return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()

    async def embed_one(self, text: str) -> list[float] | None:
        if not self.enabled or self.router is None:
            return None
        normalized = str(text or "").strip()
        if not normalized:
            return None
        key = self._key(normalized)
        async with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return list(cached)
        vectors = await self.router.embed([normalized], provider_names=self.provider_names or None)
        if not isinstance(vectors, list) or len(vectors) != 1:
            raise ValueError("EMBEDDING_RESPONSE_INVALID")
        vector = [float(value) for value in vectors[0]]
        if not vector or len(vector) > 8192 or any(not math.isfinite(value) for value in vector):
            raise ValueError("EMBEDDING_VECTOR_INVALID")
        async with self._lock:
            self._cache[key] = list(vector)
            self._cache.move_to_end(key)
            while len(self._cache) > self.max_cache_entries:
                self._cache.popitem(last=False)
        return vector

    async def clear(self) -> None:
        async with self._lock:
            self._cache.clear()

    async def stats(self) -> dict[str, int | bool]:
        async with self._lock:
            return {"enabled": self.enabled, "entries": len(self._cache), "max_entries": self.max_cache_entries}
