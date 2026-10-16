"""Compatibility adapter while legacy tools migrate to typed providers."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from .contracts import ProviderHealth, ProviderRequest, ProviderResponse


class _ToolProvider:
    """Expose a bounded local tool behind the typed provider contract."""

    provider_kind = "local"

    def __init__(self, tool: Any, *, name: str | None = None):
        self.tool = tool
        self.name = name or tool.name
        self.capabilities = {tool.name}
        self._last_success = None
        self._calls = 0
        self._errors = 0
        self._latency_ms = None

    async def fetch(self, request: ProviderRequest) -> ProviderResponse[Any]:
        started = time.perf_counter()
        self._calls += 1
        try:
            value = await self.tool.execute(**dict(request.params))
            self._last_success = datetime.now(timezone.utc)
            self._latency_ms = (time.perf_counter() - started) * 1000
            return ProviderResponse(
                data=value,
                source=self.name,
                source_url=None,
                metadata={"provider_kind": self.provider_kind},
            )
        except Exception:
            self._errors += 1
            raise

    async def normalize(self, raw: Any) -> Any:
        return raw

    async def health_check(self) -> ProviderHealth:
        error_rate = self._errors / self._calls if self._calls else 0.0
        return ProviderHealth(
            name=self.name,
            healthy=error_rate < 0.5,
            latency_ms=self._latency_ms,
            error_rate=error_rate,
            last_success_at=self._last_success,
        )


class LocalToolProvider(_ToolProvider):
    """Typed adapter for local tools such as memory and game services."""

    provider_kind = "local"


class LegacyToolProvider(_ToolProvider):
    """Compatibility adapter reserved for an explicitly enabled migration fallback."""

    provider_kind = "legacy"

    async def fetch(self, request: ProviderRequest) -> ProviderResponse[Any]:
        response = await super().fetch(request)
        return ProviderResponse(
            data=response.data,
            source=response.source,
            source_url=response.source_url,
            fetched_at=response.fetched_at,
            stale=response.stale,
            cache_hit=response.cache_hit,
            metadata={**dict(response.metadata), "legacy": True},
        )
