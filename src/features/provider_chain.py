"""Ordered provider fallback with circuit state and observed quality scoring."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Iterable

from .contracts import (
    DataProvider,
    ProviderHealth,
    ProviderRequest,
    ProviderResponse,
    validate_provider_response,
)
from src.security.log_redaction import summarize_exception


class ProviderChain:
    """Execute providers in quality order and fail over on transient faults.

    Provider order is deterministic: configured priority is retained until
    there are enough observations, then recent failure rate and latency inform
    the choice. A circuit-open source is skipped until its cooldown expires.
    """

    def __init__(
        self,
        providers: Iterable[DataProvider],
        *,
        name: str | None = None,
        failure_threshold: int = 3,
        cooldown_seconds: float = 30.0,
        sample_size: int = 50,
    ) -> None:
        self.providers = list(providers)
        if not self.providers:
            raise ValueError("ProviderChain requires at least one provider")
        self.failure_threshold = max(1, int(failure_threshold))
        self.name = name or self.providers[0].name
        self.capabilities = set().union(*(getattr(provider, "capabilities", set()) for provider in self.providers))
        self.cooldown_seconds = max(1.0, float(cooldown_seconds))
        self._failures: dict[str, int] = defaultdict(int)
        self._opened_until: dict[str, float] = {}
        self._samples: dict[str, deque[bool]] = defaultdict(lambda: deque(maxlen=sample_size))
        self._latencies: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=sample_size))
        self._last_success: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def _score(self, provider: DataProvider) -> tuple[float, int]:
        samples = self._samples[provider.name]
        if not samples:
            return (0.0, self.providers.index(provider))
        error_rate = sum(not item for item in samples) / len(samples)
        latencies = self._latencies[provider.name]
        avg_latency = sum(latencies) / len(latencies) if latencies else 0.0
        # Failure dominates. Latency only breaks ties among providers with
        # equivalent observed reliability; explicit registration order remains
        # the final stable tie-breaker.
        return (error_rate * 1000 + avg_latency, self.providers.index(provider))

    def ordered(self) -> list[DataProvider]:
        now = time.monotonic()
        available = [p for p in self.providers if self._opened_until.get(p.name, 0) <= now]
        half_open = [
            provider for provider in available
            if provider.name in self._opened_until and self._opened_until[provider.name] > 0
        ]
        if half_open:
            rest = [provider for provider in available if provider not in half_open]
            return sorted(half_open, key=lambda provider: self.providers.index(provider)) + rest
        # Keep configured priority during the initial evidence window. A
        # single outage must trigger request fallback without permanently
        # demoting the primary behind an untested provider.
        ranked: list[DataProvider] = []
        unproven: list[DataProvider] = []
        for provider in available:
            if len(self._samples[provider.name]) < 5:
                unproven.append(provider)
            else:
                ranked.append(provider)
        ranked.sort(key=self._score)
        available_order = {provider.name: index for index, provider in enumerate(self.providers)}
        unproven.sort(key=lambda provider: available_order[provider.name])
        return unproven + ranked

    async def _record(self, provider: DataProvider, success: bool, elapsed: float) -> None:
        async with self._locks[provider.name]:
            self._samples[provider.name].append(success)
            self._latencies[provider.name].append(max(0.0, elapsed))
            if success:
                self._failures[provider.name] = 0
                self._opened_until.pop(provider.name, None)
                self._last_success[provider.name] = time.time()
                return
            self._failures[provider.name] += 1
            if self._failures[provider.name] >= self.failure_threshold:
                self._opened_until[provider.name] = time.monotonic() + self.cooldown_seconds

    async def fetch(self, request: ProviderRequest) -> ProviderResponse[Any]:
        errors: list[str] = []
        attempted: list[str] = []
        for provider in self.ordered():
            attempted.append(provider.name)
            started = time.perf_counter()
            try:
                response = await asyncio.wait_for(
                    provider.fetch(request), timeout=max(0.1, request.timeout_seconds)
                )
                validate_provider_response(response, provider_name=provider.name)
                normalized = await provider.normalize(response.data)
                await self._record(provider, True, time.perf_counter() - started)
                return ProviderResponse(
                    data=normalized,
                    source=response.source,
                    source_url=response.source_url,
                    fetched_at=response.fetched_at,
                    stale=response.stale,
                    cache_hit=response.cache_hit,
                    metadata={
                        **dict(response.metadata),
                        "provider_selected": provider.name,
                        "provider_chain": ",".join(attempted),
                        "legacy_fallback_available": any(
                            getattr(item, "provider_kind", "typed") == "legacy"
                            for item in self.providers
                        ),
                    },
                )
            except ValueError:
                # Invalid user input is deterministic. It must not penalize
                # providers or be hidden behind another provider's response.
                raise
            except Exception as exc:
                errors.append(type(exc).__name__)
                await self._record(provider, False, time.perf_counter() - started)
        if not attempted:
            raise RuntimeError("PROVIDER_CHAIN_UNAVAILABLE")
        raise RuntimeError("PROVIDER_CHAIN_FAILED:" + ",".join(errors[-len(attempted):]))

    async def normalize(self, raw: Any) -> Any:
        return raw

    async def health_check(self) -> ProviderHealth:
        now = time.monotonic()

        async def probe(provider: DataProvider) -> tuple[bool, ProviderHealth | None, str | None]:
            opened_until = self._opened_until.get(provider.name, 0)
            if opened_until > now:
                return False, None, "circuit_open"
            try:
                report = await asyncio.wait_for(provider.health_check(), timeout=2.0)
            except Exception as exc:
                return False, None, summarize_exception(exc, label="provider_health")
            if not isinstance(report, ProviderHealth):
                return False, None, "provider_health type=invalid"
            return bool(report.healthy), report, None

        probe_results = await asyncio.gather(*(probe(provider) for provider in self.providers))
        details = []
        scores = []
        failures = 0
        observations = 0
        live_healthy = 0
        live_latencies: list[float] = []
        live_last_success: list[datetime] = []
        disabled_until_values: list[datetime] = []
        for provider, (live_ok, live_report, probe_error) in zip(self.providers, probe_results):
            samples = self._samples[provider.name]
            error_rate = sum(not item for item in samples) / len(samples) if samples else 0.0
            failures += sum(not item for item in samples)
            observations += len(samples)
            latency = sum(self._latencies[provider.name]) / len(self._latencies[provider.name]) if self._latencies[provider.name] else None
            # Request samples are stored in seconds; provider health contracts
            # report milliseconds. Normalize only the live fallback sample so
            # either source contributes the same unit and a first health probe
            # is not presented as an unearned perfect 100.
            latency_seconds = (
                latency
                if latency is not None
                else (
                    max(0.0, float(live_report.latency_ms)) / 1000.0
                    if live_report is not None and live_report.latency_ms is not None
                    else 0.0
                )
            )
            score = max(0.0, 100.0 * (1.0 - error_rate) / (1.0 + latency_seconds))
            if not live_ok:
                score = 0.0
            scores.append(score)
            opened_until = self._opened_until.get(provider.name, 0)
            state = "open" if opened_until > now else ("healthy" if live_ok else "unhealthy")
            kind = getattr(provider, "provider_kind", "typed")
            detail_suffix = f";probe={probe_error}" if probe_error else ""
            details.append(f"{provider.name}({kind})={state};error={error_rate:.2f};score={score:.2f}{detail_suffix}")
            if live_ok and opened_until <= now:
                live_healthy += 1
            if live_report is not None:
                if live_report.latency_ms is not None:
                    live_latencies.append(float(live_report.latency_ms))
                if live_report.last_success_at is not None:
                    live_last_success.append(live_report.last_success_at)
            if opened_until > now:
                disabled_until_values.append(datetime.fromtimestamp(time.time() + (opened_until - now), tz=timezone.utc))
        return ProviderHealth(
            name=self.name,
            healthy=live_healthy > 0,
            latency_ms=(sum(live_latencies) / len(live_latencies)) if live_latencies else None,
            error_rate=(failures / observations) if observations else 0.0,
            last_success_at=(
                datetime.fromtimestamp(
                    max(
                        [*(self._last_success.get(provider.name, 0) for provider in self.providers),
                         *(item.timestamp() for item in live_last_success)],
                        default=0,
                    ),
                    tz=timezone.utc,
                ) if any(self._last_success.values()) or live_last_success else None
            ),
            disabled_until=max(disabled_until_values) if disabled_until_values else None,
            detail=" | ".join(details)[:500],
            score=(sum(scores) / len(scores)) if scores else 0.0,
        )

    async def reset(self) -> None:
        self._failures.clear()
        self._opened_until.clear()
        self._samples.clear()
        self._latencies.clear()

    async def close(self) -> None:
        """Close owned provider clients without making shutdown fragile."""

        for provider in self.providers:
            close = getattr(provider, "close", None)
            if close is None:
                continue
            try:
                result = close()
                if hasattr(result, "__await__"):
                    await result
            except Exception:
                # A provider transport is best-effort during shutdown; one
                # broken upstream client must not prevent other clients or
                # the database from closing.
                continue
