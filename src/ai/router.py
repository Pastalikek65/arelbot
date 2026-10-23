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

    safe_code = getattr(error, "safe_code", None)
    if isinstance(safe_code, str) and safe_code:
        return re.sub(r"[^a-zA-Z0-9_.-]", "_", safe_code)[:96]
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
                 failure_threshold: int = 3, cooldown_seconds: float = 60.0,
                 rotation_tasks: Iterable[str] = (), request_timeout_seconds: float | None = None,
                 fast_timeout_seconds: float | None = None,
                 provider_timeout_seconds: float | None = None,
                 rotation_primary_count: int | None = None):
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
        self.rotation_tasks = frozenset(rotation_tasks)
        self.rotation_primary_count = max(1, rotation_primary_count) if rotation_primary_count is not None else None
        self._rotation_cursor: dict[str, int] = defaultdict(int)
        self.request_timeout_seconds = request_timeout_seconds
        self.fast_timeout_seconds = fast_timeout_seconds
        self.provider_timeout_seconds = provider_timeout_seconds
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
        # An explicit empty route means unsupported/unavailable, not permission
        # to reuse a text provider for media or embeddings.
        names = self.routes[task] if task in self.routes else self.routes.get("chat", list(self.providers))
        names = list(dict.fromkeys(names))
        if task in self.rotation_tasks and names:
            # Only quality-approved primary candidates rotate; the tail stays
            # available for outages without becoming a routine first choice.
            count = min(len(names), self.rotation_primary_count or len(names))
            primary, fallback = names[:count], names[count:]
            cursor = self._rotation_cursor[task] % count
            self._rotation_cursor[task] = (cursor + 1) % count
            names = primary[cursor:] + primary[:cursor] + fallback
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
        budget = self.fast_timeout_seconds if task == "fast" else self.request_timeout_seconds
        deadline = now + budget if budget is not None else None
        for index, name in enumerate(names):
            now = time.monotonic()
            if deadline is not None and now >= deadline:
                break
            if name not in self.providers or self._opened_until.get(name, 0) > now:
                continue
            provider = self.providers[name]
            started = time.perf_counter()
            self.metrics.inc("ai_provider_requests_total")
            self.metrics.inc(f"ai_provider_requests_total_{name}")
            try:
                call = getattr(provider, method)(request)
                if deadline is None:
                    result = await call
                else:
                    remaining = deadline - now
                    available_tail = sum(
                        other in self.providers and self._opened_until.get(other, 0) <= now
                        for other in names[index + 1:]
                    )
                    per_provider = getattr(provider, "fast_timeout_seconds" if task == "fast" else "timeout_seconds", remaining)
                    if method == "complete" and self.provider_timeout_seconds is not None:
                        cap = self.provider_timeout_seconds
                        if task != "fast" and available_tail and getattr(provider, "needs_startup_budget", False):
                            # A local CLI has process/login startup overhead.
                            # Give it more than the HTTP cap while retaining
                            # half of the remaining budget for HTTP fallbacks.
                            cap = max(cap, remaining / 2)
                        per_provider = min(per_provider, cap)
                    if task != "fast" and available_tail:
                        # Each earlier timeout must leave meaningful time for
                        # the remaining route. Do not let CLI + one gateway
                        # exhaust the entire chat deadline.
                        tail_slot = min(2.0, remaining / (available_tail + 1))
                        reserve = max(remaining / 2, tail_slot * available_tail)
                        per_provider = min(per_provider, remaining - reserve)
                    if task == "fast":
                        per_provider = min(per_provider, remaining/max(1,len(names)-index))
                    result = await asyncio.wait_for(call, timeout=min(float(per_provider), remaining))
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
                await self._record(
                    name,
                    False,
                    elapsed,
                    cooldown_seconds=getattr(exc, "circuit_cooldown_seconds", None),
                )
        raise AIProviderUnavailable(f"No AI provider available for task: {task}") from last_error

    async def _record(
        self,
        name: str,
        success: bool,
        elapsed: float,
        cooldown_seconds: float | None = None,
    ) -> None:
        async with self._lock:
            self._samples[name].append(success)
            self._latencies[name].append(elapsed)
            if success:
                self._failures[name] = 0
                self._opened_until.pop(name, None)
            else:
                self._failures[name] += 1
                if cooldown_seconds is not None:
                    bounded_cooldown = max(1.0, min(86_400.0, float(cooldown_seconds)))
                    self._opened_until[name] = time.monotonic() + bounded_cooldown
                elif self._failures[name] >= self.failure_threshold:
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
            self._rotation_cursor.clear()

    def metrics_snapshot(self) -> dict[str, Any]:
        """Return bounded provider call counters and p50/p95/p99 latency."""

        return self.metrics.snapshot()


# Short alias for callers that prefer the architecture document name.
AIRouter = AIProviderRouter
