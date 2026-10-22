"""Timeout, retry, cache and circuit-breaker runtime for data providers."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .contracts import (
    DataProvider,
    ProviderContractError,
    ProviderHealth,
    ProviderRequest,
    ProviderResponse,
    validate_provider_response,
)


_NON_RETRYABLE_PROVIDER_SUFFIXES = (
    "_content_type_invalid",
    "_credentials_forbidden",
    "_host_not_allowed",
    "_invalid_json",
    "_private_host",
    "_redirect_rejected",
    "_response_too_large",
    "_schema_error",
    "_url_https_required",
)


def _provider_error_retryable(error: Exception) -> bool:
    """Avoid retrying deterministic transport/policy/schema failures.

    Transport errors expose an explicit ``retryable`` property. The suffix
    fallback also covers adapters that translate the typed error into a stable
    RuntimeError for compatibility with older callers.
    """

    explicit = getattr(error, "retryable", None)
    if explicit is not None:
        return bool(explicit)
    code = str(error).strip().split(":", 1)[0].casefold()
    return not code.endswith(_NON_RETRYABLE_PROVIDER_SUFFIXES)


class ProviderRateLimitExceeded(RuntimeError):
    """Raised when a provider's local request budget cannot fit the deadline."""

    def __init__(self, retry_after_seconds: float) -> None:
        self.retry_after_seconds = max(0.01, float(retry_after_seconds))
        super().__init__("provider_rate_limit_exceeded")


@dataclass
class _CacheEntry:
    response: ProviderResponse[Any]
    expires_at: float
    stale_until: float


class ProviderRuntime:
    def __init__(
        self,
        provider: DataProvider,
        *,
        cache_ttl_seconds: int = 0,
        attempts: int = 2,
        failure_threshold: int = 3,
        stale_ttl_seconds: int = 3600,
        rate_limit_per_minute: int | None = None,
        shared_cache: Any | None = None,
    ):
        self.provider = provider
        self.cache_ttl_seconds = max(0, cache_ttl_seconds)
        self.attempts = max(1, attempts)
        self.failure_threshold = max(1, failure_threshold)
        self.stale_ttl_seconds = max(0, stale_ttl_seconds)
        self.rate_limit_per_minute = (
            max(1, int(rate_limit_per_minute)) if rate_limit_per_minute else None
        )
        self._rate_events: deque[float] = deque()
        self._rate_lock = asyncio.Lock()
        self._cache: dict[str, _CacheEntry] = {}
        self._inflight: dict[str, asyncio.Lock] = {}
        self._refresh_tasks: dict[str, asyncio.Task[Any]] = {}
        self.shared_cache = shared_cache
        self._failures = 0
        self._disabled_until = 0.0
        self._calls = 0
        self._successes = 0
        self._errors = 0
        self._last_success_at: datetime | None = None
        self._last_error_at: datetime | None = None
        self._latencies_ms: list[float] = []

    async def close(self) -> None:
        """Stop background stale refresh work before the provider is closed.

        Refresh tasks are deliberately detached from the request that served a
        stale value.  They therefore need an explicit lifecycle boundary: a
        process shutdown must not leave a task using an HTTP client after the
        provider transport has been closed, and a restart must not inherit old
        in-memory cache entries.  Cancellation is best effort and deliberately
        does not surface as a shutdown failure.
        """

        tasks = tuple(self._refresh_tasks.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._refresh_tasks.clear()
        self._inflight.clear()
        self._cache.clear()

    def health(self) -> ProviderHealth:
        """Return runtime health from actual provider calls.

        Registry health checks answer whether a provider can be reached at the
        moment of the check.  This snapshot also includes call-level failures,
        latency and circuit state, which is what operations needs to diagnose a
        degraded source.
        """
        now = time.monotonic()
        disabled_until = None
        healthy = True
        if self._disabled_until > now:
            healthy = False
            disabled_until = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(
                seconds=self._disabled_until - now
            )
        if self._calls and self._errors >= self.failure_threshold and self._disabled_until > now:
            healthy = False
        average = sum(self._latencies_ms) / len(self._latencies_ms) if self._latencies_ms else None
        # Reliability dominates the bounded score; latency is a secondary
        # signal in seconds so healthy fast providers remain near 100.
        if not self._calls:
            score = 100.0
        else:
            reliability = max(0.0, 1.0 - (self._errors / self._calls))
            latency_seconds = (average or 0.0) / 1000.0
            score = max(0.0, min(100.0, 100.0 * reliability / (1.0 + latency_seconds)))
            if self._disabled_until > now:
                score = 0.0
        return ProviderHealth(
            name=self.provider.name,
            healthy=healthy,
            latency_ms=average,
            error_rate=(self._errors / self._calls) if self._calls else 0.0,
            last_success_at=self._last_success_at,
            disabled_until=disabled_until,
            detail=(
                f"calls={self._calls};successes={self._successes};errors={self._errors};"
                f"score={score:.2f};rate_limit={self.rate_limit_per_minute or 'unlimited'}"
            ),
            score=score,
        )

    async def _acquire_rate_slot(self, timeout_seconds: float) -> None:
        """Reserve one upstream call without waiting past the request deadline."""

        limit = self.rate_limit_per_minute
        if limit is None:
            return
        deadline = time.monotonic() + max(0.05, float(timeout_seconds))
        while True:
            async with self._rate_lock:
                now = time.monotonic()
                while self._rate_events and now - self._rate_events[0] >= 60.0:
                    self._rate_events.popleft()
                if len(self._rate_events) < limit:
                    self._rate_events.append(now)
                    return
                retry_after = max(0.01, 60.0 - (now - self._rate_events[0]))
            remaining = deadline - time.monotonic()
            if retry_after > remaining:
                raise ProviderRateLimitExceeded(retry_after)
            await asyncio.sleep(min(retry_after, max(0.01, remaining)))

    def reset_circuit(self) -> None:
        self._failures = 0
        self._disabled_until = 0.0

    @staticmethod
    def _cache_key(request: ProviderRequest) -> str:
        return json.dumps(
            {"operation": request.operation, "params": dict(request.params)},
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )

    def _shared_key(self, request: ProviderRequest) -> str:
        digest = hashlib.sha256(self._cache_key(request).encode("utf-8")).hexdigest()
        return f"arel:feature:{self.provider.name}:{digest}"

    async def _shared_get(self, request: ProviderRequest) -> ProviderResponse[Any] | None:
        if not self.shared_cache or not self.cache_ttl_seconds:
            return None
        try:
            raw = await self.shared_cache.get(self._shared_key(request))
            if not raw:
                return None
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            payload = json.loads(raw) if isinstance(raw, str) else raw
            fetched_at = datetime.fromisoformat(payload["fetched_at"])
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=timezone.utc)
            candidate = ProviderResponse(
                data=payload.get("data"),
                source=payload.get("source", self.provider.name),
                source_url=payload.get("source_url"),
                fetched_at=fetched_at,
                metadata=payload.get("metadata") or {},
            )
            validate_provider_response(candidate, provider_name=self.provider.name)
            return candidate
        except Exception:
            return None

    async def _shared_set(self, request: ProviderRequest, response: ProviderResponse[Any]) -> None:
        if not self.shared_cache or not self.cache_ttl_seconds:
            return
        payload = {
            "data": response.data,
            "source": response.source,
            "source_url": response.source_url,
            "fetched_at": response.fetched_at.isoformat(),
            "metadata": dict(response.metadata),
        }
        try:
            await self.shared_cache.set(
                self._shared_key(request),
                json.dumps(payload, ensure_ascii=False, default=str),
                ex=self.cache_ttl_seconds + self.stale_ttl_seconds,
            )
        except Exception:
            return

    @staticmethod
    def _cached_response(response: ProviderResponse[Any], *, cache: str, stale: bool = False, extra: dict[str, Any] | None = None) -> ProviderResponse[Any]:
        return ProviderResponse(
            data=response.data,
            source=response.source,
            source_url=response.source_url,
            fetched_at=response.fetched_at,
            stale=stale,
            cache_hit=True,
            metadata={**response.metadata, "cache": cache, **(extra or {})},
        )

    async def _fetch_uncached(
        self,
        request: ProviderRequest,
        key: str,
        entry: _CacheEntry | None,
    ) -> ProviderResponse[Any]:
        now = time.monotonic()
        if self._disabled_until > now:
            if entry and entry.stale_until > now:
                return self._cached_response(entry.response, cache="stale", stale=True, extra={"circuit": "open"})
            raise RuntimeError(f"provider circuit open: {self.provider.name}")

        last_error: Exception | None = None
        for attempt in range(self.attempts):
            started = time.perf_counter()
            await self._acquire_rate_slot(request.timeout_seconds)
            self._calls += 1
            try:
                response = await asyncio.wait_for(
                    self.provider.fetch(request), timeout=max(0.1, request.timeout_seconds)
                )
                validate_provider_response(response, provider_name=self.provider.name)
                self._latencies_ms.append((time.perf_counter() - started) * 1000)
                if len(self._latencies_ms) > 1000:
                    del self._latencies_ms[:-1000]
                self._failures = 0
                self._successes += 1
                self._last_success_at = datetime.now(timezone.utc)
                logical_error = isinstance(response.data, dict) and bool(response.data.get("error"))
                if self.cache_ttl_seconds and not logical_error:
                    cached_at = time.monotonic()
                    self._cache[key] = _CacheEntry(
                        response,
                        cached_at + self.cache_ttl_seconds,
                        cached_at + self.cache_ttl_seconds + self.stale_ttl_seconds,
                    )
                    await self._shared_set(request, response)
                return response
            except Exception as exc:
                if isinstance(exc, ValueError):
                    # Invalid user input is deterministic and must not count
                    # as an upstream failure or trigger provider retries.
                    raise
                self._latencies_ms.append((time.perf_counter() - started) * 1000)
                if len(self._latencies_ms) > 1000:
                    del self._latencies_ms[:-1000]
                last_error = exc
                self._failures += 1
                self._errors += 1
                self._last_error_at = datetime.now(timezone.utc)
                if attempt + 1 < self.attempts and _provider_error_retryable(exc):
                    await asyncio.sleep(min(0.25 * (attempt + 1), 1.0))
                elif not _provider_error_retryable(exc):
                    break

        if self._failures >= self.failure_threshold:
            self._disabled_until = time.monotonic() + 30.0
        if entry and entry.stale_until > time.monotonic():
            error_type = type(last_error).__name__ if last_error is not None else "Exception"
            return self._cached_response(
                entry.response,
                cache="stale",
                stale=True,
                extra={"fallback_error_type": error_type[:80]},
            )
        raise last_error or RuntimeError("provider request failed")

    def _schedule_stale_refresh(
        self,
        request: ProviderRequest,
        key: str,
        entry: _CacheEntry,
    ) -> None:
        """Refresh one stale key in the background without request fan-out."""
        existing = self._refresh_tasks.get(key)
        if existing is not None and not existing.done():
            return

        async def refresh() -> None:
            lock = self._inflight.setdefault(key, asyncio.Lock())
            try:
                async with lock:
                    current = self._cache.get(key)
                    now = time.monotonic()
                    if current and current.expires_at > now:
                        return
                    await self._fetch_uncached(request, key, current or entry)
            except Exception:
                # The stale value remains available until its stale TTL ends.
                return
            finally:
                if self._inflight.get(key) is lock and not lock.locked():
                    self._inflight.pop(key, None)

        task = asyncio.create_task(refresh(), name=f"arel-provider-refresh:{self.provider.name}")
        self._refresh_tasks[key] = task

        def cleanup(done: asyncio.Task[Any]) -> None:
            if self._refresh_tasks.get(key) is done:
                self._refresh_tasks.pop(key, None)

        task.add_done_callback(cleanup)

    async def fetch(self, request: ProviderRequest) -> ProviderResponse[Any]:
        key = self._cache_key(request)
        now = time.monotonic()
        entry = self._cache.get(key)
        if entry and entry.expires_at > now:
            return self._cached_response(entry.response, cache="hit")
        if entry and entry.stale_until > now:
            self._schedule_stale_refresh(request, key, entry)
            return self._cached_response(entry.response, cache="stale_revalidate", stale=True)

        shared = await self._shared_get(request)
        if shared is not None:
            age = max(0.0, (datetime.now(timezone.utc) - shared.fetched_at).total_seconds())
            if age <= self.cache_ttl_seconds:
                self._cache[key] = _CacheEntry(shared, now + self.cache_ttl_seconds - age, now + self.cache_ttl_seconds + self.stale_ttl_seconds - age)
                return self._cached_response(shared, cache="shared_hit")
            if age > self.cache_ttl_seconds + self.stale_ttl_seconds:
                entry = None
                self._cache.pop(key, None)
            else:
                self._cache[key] = _CacheEntry(shared, now, now + self.cache_ttl_seconds + self.stale_ttl_seconds - age)
                entry = self._cache[key]
                self._schedule_stale_refresh(request, key, entry)
                return self._cached_response(shared, cache="shared_stale_revalidate", stale=True)

        # Coalesce concurrent requests for the same provider/arguments. The
        # second caller re-checks both caches after waiting for the first one.
        lock = self._inflight.setdefault(key, asyncio.Lock())
        try:
            async with lock:
                now = time.monotonic()
                entry = self._cache.get(key)
                if entry and entry.expires_at > now:
                    return self._cached_response(entry.response, cache="coalesced_hit")
                shared = await self._shared_get(request)
                if shared is not None:
                    age = max(0.0, (datetime.now(timezone.utc) - shared.fetched_at).total_seconds())
                    if age <= self.cache_ttl_seconds:
                        self._cache[key] = _CacheEntry(shared, now + self.cache_ttl_seconds - age, now + self.cache_ttl_seconds + self.stale_ttl_seconds - age)
                        return self._cached_response(shared, cache="shared_coalesced_hit")
                    if age <= self.cache_ttl_seconds + self.stale_ttl_seconds:
                        self._cache[key] = _CacheEntry(shared, now, now + self.cache_ttl_seconds + self.stale_ttl_seconds - age)
                        entry = self._cache[key]
                        self._schedule_stale_refresh(request, key, entry)
                        return self._cached_response(shared, cache="shared_stale_revalidate", stale=True)
                    else:
                        self._cache.pop(key, None)
                        entry = None
                return await self._fetch_uncached(request, key, entry)
        finally:
            if self._inflight.get(key) is lock and not lock.locked():
                self._inflight.pop(key, None)

    def clear_cache(self) -> None:
        self._cache.clear()
