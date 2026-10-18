"""Offline provider-pipeline load acceptance harness.

This intentionally uses a deterministic fixture provider rather than live web
sites.  It measures the feature registry/runtime/cache path for the pilot
target (100 active chats and 10 concurrent requests) without turning external
provider instability into a false green or red release result.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.features.contracts import FeatureDefinition, ProviderHealth, ProviderRequest, ProviderResponse
from src.features.executor import FeatureExecutor
from src.features.registry import FeatureRegistry


@dataclass
class FixtureProvider:
    name: str = "fixture_weather"
    delay_seconds: float = 0.002
    calls: int = 0

    capabilities: set[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.capabilities = {"weather"}

    async def fetch(self, request: ProviderRequest) -> ProviderResponse:
        await asyncio.sleep(self.delay_seconds)
        self.calls += 1
        return ProviderResponse(
            data={"content": f"fixture:{request.params.get('city', '')}"},
            source="offline-fixture",
            source_url="https://fixture.invalid/weather",
        )

    async def normalize(self, raw):
        return raw

    async def health_check(self):
        return ProviderHealth(name=self.name, healthy=True)


def build_executor(provider: FixtureProvider) -> FeatureExecutor:
    registry = FeatureRegistry()
    registry.register_provider(provider)
    registry.register_feature(
        FeatureDefinition(
            key="fixture_weather",
            category="test",
            commands=(),
            provider_key=provider.name,
            cache_ttl_seconds=30,
            freshness_sla_seconds=60,
            timeout_ms=1000,
            cost_class="local",
            audit_policy="load_test",
            source_name="Offline fixture",
            source_url="https://fixture.invalid/weather",
            source_license="Test fixture",
            source_terms_url="https://fixture.invalid/terms",
        ),
        provider,
    )
    return FeatureExecutor(registry)


async def run_load(*, requests: int = 100, concurrency: int = 10) -> dict[str, float | int]:
    if requests < 1 or concurrency < 1:
        raise ValueError("requests and concurrency must be positive")
    provider = FixtureProvider()
    executor = build_executor(provider)
    limiter = asyncio.Semaphore(concurrency)
    latencies: list[float] = []

    async def one(index: int) -> None:
        async with limiter:
            started = time.perf_counter()
            result = await executor.execute(
                "fixture_weather", {"city": f"city-{index % 100}"}
            )
            latencies.append((time.perf_counter() - started) * 1000)
            if not result.ok:
                raise RuntimeError(result.error_code or "fixture_failed")

    await asyncio.gather(*(one(index) for index in range(requests)))
    ordered = sorted(latencies)

    def percentile(value: float) -> float:
        position = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * value))))
        return ordered[position]

    return {
        "requests": requests,
        "concurrency": concurrency,
        "provider_calls": provider.calls,
        "cache_hits": requests - provider.calls,
        "p50_ms": round(percentile(0.50), 2),
        "p95_ms": round(percentile(0.95), 2),
        "p99_ms": round(percentile(0.99), 2),
        "mean_ms": round(statistics.mean(latencies), 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=10)
    args = parser.parse_args()
    result = asyncio.run(run_load(requests=args.requests, concurrency=args.concurrency))
    for key, value in result.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
