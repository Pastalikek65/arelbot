"""Provider-independent AI routing with bounded fallback and circuit breaking."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import defaultdict, deque
from typing import Any, Iterable

from .provider_base import AIProvider, AIProviderRequestError, CompletionRequest, CompletionResponse
from src.observability.metrics import MetricsRegistry

logger = logging.getLogger(__name__)


def _safe_error_code(error: BaseException) -> str:
    """Return a bounded diagnostic code without echoing provider details."""

    raw = str(error or "")
    match = re.search(r"provider_http_(\d{3})", raw, re.IGNORECASE)
    if match:
        return f"http_{match.group(1)}"
    if "timeout" in raw.casefold() or isinstance(error, TimeoutError):
        return "timeout"
    if "circuit" in raw.casefold():
        return "circuit_open"
    if "key" in raw.casefold():
        return "key_unavailable"
    return type(error).__name__[:64]


class AIProviderUnavailable(RuntimeError):
    """Raised when every provider for a task is unavailable."""


class AIProviderRouter:
    def __init__(self, providers: Iterable[AIProvider] = (), routes: dict[str, list[str]] | None = None,
                 failure_threshold: int = 3, cooldown_seconds: float = 60.0):
        self.providers = {provider.name: provider for provider in providers}
        self._locked_routes = set((routes or {}).keys())
        self.routes = routes or {
            "chat": list(self.providers), "fast": list(self.providers),
            "analysis": list(self.providers), "vision": list(self.providers),
            "summary": list(self.providers), "sticker_tagging": list(self.providers),
            "embedding": list(self.providers), "transcribe": list(self.providers),
        }
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._failures: dict[str, int] = defaultdict(int)
        self._opened_until: dict[str, float] = {}
        self._samples: dict[str, deque[bool]] = defaultdict(lambda: deque(maxlen=50))
        self._latencies: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=50))
        self._lock = asyncio.Lock()
        # Router telemetry is deliberately bounded to registered provider
        # names.  Request payloads, model names and provider errors never
        # enter this registry; operators only need aggregate and provider
        # latency/error counters for routing decisions.
        self.metrics = MetricsRegistry(sample_limit=1000)

    def register(self, provider: AIProvider) -> None:
        self.providers[provider.name] = provider
        for task in self.routes:
            # Explicit routes are an allowlist. Unconfigured/default routes
            # continue to discover newly registered providers automatically.
            if task not in self._locked_routes and provider.name not in self.routes[task]:
                self.routes[task].append(provider.name)

    def _ordered_names(self, task: str) -> list[str]:
        names = self.routes.get(task) or self.routes.get("chat", []) or list(self.providers)
        now = time.monotonic()
        return [name for name in names if name in self.providers and self._opened_until.get(name, 0) <= now]

    async def complete(
        self,
        request: CompletionRequest,
        provider_names: list[str] | None = None,
    ) -> CompletionResponse:
        return await self._call("complete", request.task, request, provider_names=provider_names)

    async def vision(self, request: Any) -> Any:
        return await self._call("vision", "vision", request)

    async def transcribe(self, audio: Any) -> Any:
        return await self._call("transcribe", "transcribe", audio)

    async def embed(self, texts: list[str], provider_names: list[str] | None = None) -> list[list[float]]:
        return await self._call("embed", "embedding", texts, provider_names=provider_names)

    async def _call(
        self,
        method: str,
        task: str,
        request: Any,
        *,
        provider_names: list[str] | None = None,
    ) -> Any:
        last_error: Exception | None = None
        names = provider_names if provider_names is not None else self._ordered_names(task)
        now = time.monotonic()
        for name in names:
            if name not in self.providers or self._opened_until.get(name, 0) > now:
                continue
            provider = self.providers[name]
            started = time.perf_counter()
            self.metrics.inc("ai_provider_requests_total")
            self.metrics.inc(f"ai_provider_requests_total_{name}")
            try:
                result = await getattr(provider, method)(request)
                elapsed = time.perf_counter() - started
                self.metrics.inc("ai_provider_success_total")
                self.metrics.inc(f"ai_provider_success_total_{name}")
                self.metrics.observe("ai_provider_call_seconds", elapsed)
                self.metrics.observe(f"ai_provider_call_seconds_{name}", elapsed)
                await self._record(name, True, elapsed)
                return result
            except AIProviderRequestError as exc:
                # Caller-input bounds are deterministic. Do not fall through to
                # another provider or poison a healthy provider circuit; a
                # fallback cannot make the same invalid request valid.
                last_error = exc
                elapsed = time.perf_counter() - started
                self.metrics.inc("ai_provider_validation_errors_total")
                self.metrics.inc(f"ai_provider_validation_errors_total_{name}")
                self.metrics.observe("ai_provider_call_seconds", elapsed)
                self.metrics.observe(f"ai_provider_call_seconds_{name}", elapsed)
                raise
            except Exception as exc:
                last_error = exc
                elapsed = time.perf_counter() - started
                self.metrics.inc("ai_provider_errors_total")
                self.metrics.inc(f"ai_provider_errors_total_{name}")
                self.metrics.observe("ai_provider_call_seconds", elapsed)
                self.metrics.observe(f"ai_provider_call_seconds_{name}", elapsed)
                logger.warning(
                    "AI provider failed provider=%s task=%s code=%s latency_ms=%d",
                    name,
                    task,
                    _safe_error_code(exc),
                    round(elapsed * 1000),
                )
                await self._record(name, False, elapsed)
        raise AIProviderUnavailable(f"No AI provider available for task: {task}") from last_error

    async def _record(self, name: str, success: bool, elapsed: float) -> None:
        async with self._lock:
            self._samples[name].append(success)
            self._latencies[name].append(elapsed)
            if success:
                self._failures[name] = 0
                self._opened_until.pop(name, None)
            else:
                self._failures[name] += 1
                if self._failures[name] >= self.failure_threshold:
                    self._opened_until[name] = time.monotonic() + self.cooldown_seconds

    async def health(self) -> list[dict[str, Any]]:
        result = []
        for name, provider in self.providers.items():
            circuit_healthy = self._opened_until.get(name, 0) <= time.monotonic()
            probe = getattr(provider, "health_check", None)
            probe_healthy: bool | None = None
            probe_detail: str | None = None
            if callable(probe):
                try:
                    report = await asyncio.wait_for(probe(), timeout=2.0)
                    if isinstance(report, dict):
                        probe_healthy = bool(report.get("healthy", False))
                        probe_detail = str(report.get("detail") or "")[:160] or None
                    else:
                        probe_healthy = bool(getattr(report, "healthy", False))
                        probe_detail = str(getattr(report, "detail", "") or "")[:160] or None
                except Exception as exc:  # health must never break admin status
                    probe_healthy = False
                    probe_detail = _safe_error_code(exc)
            samples = self._samples[name]
            result.append({
                "name": name,
                "healthy": circuit_healthy and (probe_healthy if probe_healthy is not None else True),
                "error_rate": (sum(not ok for ok in samples) / len(samples)) if samples else 0.0,
                "latency_ms": (sum(self._latencies[name]) / len(self._latencies[name]) * 1000)
                if self._latencies[name] else None,
                **({"detail": probe_detail} if probe_detail else {}),
            })
        return result

    async def reset(self) -> None:
        async with self._lock:
            self._failures.clear()
            self._opened_until.clear()
            self._samples.clear()
            self._latencies.clear()

    def metrics_snapshot(self) -> dict[str, Any]:
        """Return bounded provider call counters and p50/p95/p99 latency."""

        return self.metrics.snapshot()


# Short alias for callers that prefer the architecture document name.
AIRouter = AIProviderRouter
