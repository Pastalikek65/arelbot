"""Provider selection, health scoring and circuit breaking."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from typing import Any

from src.media.providers.base import MediaArtifact, MediaMetadata, MediaProvider, MediaRequest, ProviderHealth, validate_media_artifact
from src.security.log_redaction import summarize_exception

logger = logging.getLogger(__name__)


class ProviderRegistry:
    def __init__(self, providers: list[MediaProvider], failure_threshold: int = 3, cooldown: float = 60.0):
        self.providers = providers
        self.failure_threshold = failure_threshold
        self.cooldown = cooldown
        self._failures: dict[str, int] = defaultdict(int)
        self._opened_until: dict[str, float] = {}
        self._samples: dict[str, deque[bool]] = defaultdict(lambda: deque(maxlen=50))
        self._latencies: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=50))
        self._last_success: dict[str, float] = {}
        self._lock = asyncio.Lock()

    def _available(self, provider: MediaProvider) -> bool:
        return self._opened_until.get(provider.name, 0.0) <= time.monotonic()

    def ordered(self, platform: str | None = None) -> list[MediaProvider]:
        available = [p for p in self.providers if self._available(p)]

        def platform_rank(provider: MediaProvider) -> int:
            """Prefer an adapter that declares support for this platform.

            A wildcard or undeclared capability remains eligible as a safe
            fallback. This keeps configured HTTP providers useful while
            ensuring platform-specific adapters win when several are healthy.
            """
            if not platform:
                return 0
            supported = getattr(provider, "platforms", None)
            if not supported or "*" in supported:
                return 1
            requested = str(platform).strip().lower()
            aliases = {"twitter": "x", "x": "x"}
            requested = aliases.get(requested, requested)
            normalized = {aliases.get(str(item).lower(), str(item).lower()) for item in supported}
            return 0 if requested in normalized else 2

        def score(provider):
            samples = self._samples[provider.name]
            failures = sum(1 for ok in samples if not ok)
            error_rate = failures / len(samples) if samples else 0.0
            latencies = self._latencies[provider.name]
            avg_latency = sum(latencies) / len(latencies) if latencies else float("inf")
            return (platform_rank(provider), error_rate, self._failures[provider.name], avg_latency, provider.name)
        return sorted(available, key=score)

    async def probe(self, request: MediaRequest) -> MediaMetadata:
        last_error: Exception | None = None
        for provider in self.ordered(request.platform):
            started = time.perf_counter()
            try:
                result = await provider.probe(request)
                await self.record_success(provider.name, time.perf_counter() - started)
                return result
            except Exception as exc:
                last_error = exc
                await self.record_failure(provider.name)
        if last_error:
            raise last_error
        raise RuntimeError("provider_unavailable")

    async def download(self, request: MediaRequest) -> tuple[MediaArtifact, str]:
        last_error: Exception | None = None
        for provider in self.ordered(request.platform):
            started = time.perf_counter()
            try:
                result = validate_media_artifact(await provider.download(request))
                await self.record_success(provider.name, time.perf_counter() - started)
                return result, provider.name
            except Exception as exc:
                last_error = exc
                await self.record_failure(provider.name)
        if last_error:
            raise last_error
        raise RuntimeError("provider_unavailable")

    async def record_success(self, name: str, elapsed: float) -> None:
        async with self._lock:
            self._failures[name] = 0
            self._samples[name].append(True)
            self._latencies[name].append(elapsed)
            self._last_success[name] = time.time()
            self._opened_until.pop(name, None)

    async def record_failure(self, name: str) -> None:
        async with self._lock:
            self._failures[name] += 1
            self._samples[name].append(False)
            if self._failures[name] >= self.failure_threshold:
                self._opened_until[name] = time.monotonic() + self.cooldown

    async def health(self) -> list[ProviderHealth]:
        result: list[ProviderHealth] = []
        for provider in self.providers:
            samples = self._samples[provider.name]
            failures = sum(1 for ok in samples if not ok)
            internal = ProviderHealth(
                name=provider.name,
                healthy=self._available(provider),
                latency_ms=(sum(self._latencies[provider.name]) / len(self._latencies[provider.name]) * 1000
                            if self._latencies[provider.name] else None),
                error_rate=(failures / len(samples) if samples else 0.0),
                last_success_at=self._last_success.get(provider.name),
                reason="circuit_open" if not self._available(provider) else None,
            )
            checker = getattr(provider, "health_check", None)
            if checker is not None and internal.healthy:
                try:
                    live = await asyncio.wait_for(checker(), timeout=3)
                    if isinstance(live, ProviderHealth) and not live.healthy:
                        internal.healthy = False
                        internal.reason = live.reason or "health_check_failed"
                        if live.latency_ms is not None:
                            internal.latency_ms = live.latency_ms
                    elif isinstance(live, dict) and not live.get("healthy", True):
                        internal.healthy = False
                        internal.reason = str(live.get("reason") or "health_check_failed")
                except Exception as exc:
                    internal.healthy = False
                    internal.reason = summarize_exception(exc, label="media_provider_health")
            result.append(internal)
        return result

    async def reset(self) -> None:
        async with self._lock:
            self._failures.clear()
            self._opened_until.clear()
            self._samples.clear()
            self._latencies.clear()


class YtDlpAdapter:
    """Adapter exposing the legacy provider through the common contract."""

    name = "yt-dlp"
    platforms = frozenset({"instagram", "tiktok", "youtube", "x", "facebook", "reddit"})

    def __init__(self, provider: Any):
        self.provider = provider

    async def probe(self, request: MediaRequest) -> MediaMetadata:
        # Keep metadata extraction behind the provider contract so platform
        # fallback implementations can supply the same fields.
        result = await self.provider.probe(request.url)
        return MediaMetadata(
            title=result.get("title", ""),
            platform=result.get("platform", request.platform),
            duration=float(result.get("duration") or 0),
            size=result.get("size"),
        )

    async def download(self, request: MediaRequest) -> MediaArtifact:
        return await self.provider.download(request.url, request.job_id)

    async def health_check(self) -> ProviderHealth:
        checker = getattr(self.provider, "health_check", None)
        if checker is None:
            return ProviderHealth(name=self.name, healthy=True)
        result = await checker()
        if isinstance(result, ProviderHealth):
            return ProviderHealth(
                name=self.name,
                healthy=result.healthy,
                latency_ms=result.latency_ms,
                error_rate=result.error_rate,
                last_success_at=result.last_success_at,
                reason=result.reason,
            )
        if isinstance(result, dict):
            return ProviderHealth(
                name=self.name,
                healthy=bool(result.get("healthy", False)),
                latency_ms=result.get("latency_ms"),
                reason=str(result.get("reason"))[:120] if result.get("reason") else None,
            )
        return ProviderHealth(name=self.name, healthy=bool(result))
