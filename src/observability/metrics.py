from __future__ import annotations

import contextlib
import contextvars
import secrets
import time
from collections import defaultdict
from typing import Iterator


_correlation: contextvars.ContextVar[str | None] = contextvars.ContextVar("arel_correlation_id", default=None)


def current_correlation_id() -> str | None:
    return _correlation.get()


@contextlib.contextmanager
def correlation_id(value: str | None = None) -> Iterator[str]:
    token = _correlation.set(value or secrets.token_hex(8))
    try:
        yield _correlation.get()  # type: ignore[misc]
    finally:
        _correlation.reset(token)


class MetricsRegistry:
    """In-process counters and latency samples, safe to export as JSON."""

    def __init__(self, sample_limit: int = 1000):
        self.sample_limit = max(10, sample_limit)
        self.counters: dict[str, int] = defaultdict(int)
        self.gauges: dict[str, float] = {}
        self.latencies: dict[str, list[float]] = defaultdict(list)

    def inc(self, name: str, value: int = 1) -> None:
        self.counters[name] += value

    def set_gauge(self, name: str, value: float) -> None:
        self.gauges[name] = float(value)

    def observe(self, name: str, seconds: float) -> None:
        values = self.latencies[name]
        values.append(float(seconds))
        if len(values) > self.sample_limit:
            del values[:-self.sample_limit]

    @contextlib.contextmanager
    def timer(self, name: str) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            self.observe(name, time.perf_counter() - started)

    def snapshot(self) -> dict:
        latency = {}
        for name, values in self.latencies.items():
            if not values:
                continue
            ordered = sorted(values)
            latency[name] = {
                "count": len(values),
                "p50": ordered[int((len(ordered) - 1) * 0.50)],
                "p95": ordered[int((len(ordered) - 1) * 0.95)],
                "p99": ordered[int((len(ordered) - 1) * 0.99)],
            }
        return {"counters": dict(self.counters), "gauges": dict(self.gauges), "latency": latency}

