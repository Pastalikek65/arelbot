from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import pytest

from src.features.contracts import FeatureDefinition, ProviderHealth, ProviderRequest, ProviderResponse
from src.features.executor import FeatureExecutor
from src.features.registry import FeatureRegistry
from src.features.providers.transport import ProviderTransportError


class FakeProvider:
    name = "fake-source"
    capabilities = {"demo"}

    def __init__(self) -> None:
        self.calls = 0
        self.fail = False

    async def fetch(self, request):
        self.calls += 1
        if self.fail:
            raise RuntimeError("upstream unavailable")
        return ProviderResponse(
            data={"content": "demo"},
            source="Fake source",
            source_url="https://example.test/data",
        )

    async def normalize(self, raw):
        return raw

    async def health_check(self):
        return ProviderHealth(name=self.name, healthy=not self.fail)


class NormalizingProvider(FakeProvider):
    async def normalize(self, raw):
        return {"content": f"normalized:{raw['content']}"}


class FallbackProvider(FakeProvider):
    async def fetch(self, request):
        return ProviderResponse(
            data={"content": "fallback"},
            source="Fallback source",
            source_url="https://fallback.example/data",
            metadata={"fallback": "duckduckgo"},
        )


class InvalidNormalizingProvider(FakeProvider):
    async def normalize(self, raw):
        raise ValueError("schema_changed")


class InvalidEnvelopeProvider(FakeProvider):
    async def fetch(self, request):
        self.calls += 1
        return ProviderResponse(
            data={"content": "demo"},
            source="",
            source_url="https://example.test/data",
        )


class ErrorPayloadProvider(FakeProvider):
    async def normalize(self, raw):
        return {
            "error": "credential=https://user:secret@example.test/private?token=leak",
            "content": "<b>upstream private response</b>",
        }


class BoundedPolicyFailureProvider(FakeProvider):
    async def fetch(self, request):
        self.calls += 1
        raise RuntimeError("provider_response_too_large")


class TransportFailureProvider(FakeProvider):
    def __init__(self, failures: list[ProviderTransportError]) -> None:
        super().__init__()
        self.failures = list(failures)

    async def fetch(self, request):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return ProviderResponse(
            data={"content": "demo"},
            source="Fake source",
            source_url="https://example.test/data",
        )


class HealthSink:
    def __init__(self) -> None:
        self.rows = []

    async def save_provider_health(self, health):
        self.rows.append(health)


class SlowHealthSink(HealthSink):
    async def save_provider_health(self, health):
        await asyncio.sleep(0.15)
        await super().save_provider_health(health)


def make_executor(provider: FakeProvider, sink: HealthSink | None = None) -> FeatureExecutor:
    registry = FeatureRegistry()
    registry.register_provider(provider)
    registry.register_feature(
        FeatureDefinition(
            key="demo",
            category="test",
            commands=("demo",),
            provider_key=provider.name,
            cache_ttl_seconds=60,
            freshness_sla_seconds=300,
            timeout_ms=1000,
            audit_policy="none",
            source_name="Configured source",
            source_url="https://example.test/configured",
            source_license="Test data",
            source_terms_url="https://example.test/terms",
        ),
        provider,
    )
    executor = FeatureExecutor(registry)
    if sink:
        executor.attach_health_store(sink)
    return executor


def make_rate_limited_executor(provider: FakeProvider) -> FeatureExecutor:
    registry = FeatureRegistry()
    registry.register_provider(provider)
    registry.register_feature(
        FeatureDefinition(
            key="rate_limited",
            category="test",
            commands=("rate_limited",),
            provider_key=provider.name,
            cache_ttl_seconds=0,
            freshness_sla_seconds=60,
            timeout_ms=50,
            rate_limit_per_minute=1,
            audit_policy="none",
            source_name="Configured source",
            source_url="https://example.test/configured",
            source_license="Test data",
            source_terms_url="https://example.test/terms",
        ),
        provider,
    )
    return FeatureExecutor(registry)


@pytest.mark.asyncio
async def test_feature_executor_emits_source_freshness_and_persists_health():
    provider = FakeProvider()
    sink = HealthSink()
    executor = make_executor(provider, sink)
    result = await executor.execute("demo")
    await executor.close()

    assert result.ok
    assert result.source == "Configured source"
    assert result.source_url == "https://example.test/configured"
    assert result.metadata["source_license"] == "Test data"
    assert result.metadata["fresh"] is True
    assert sink.rows and sink.rows[-1]["source"] == "Configured source"
    assert sink.rows[-1]["last_cache_hit"] is False


@pytest.mark.asyncio
async def test_feature_executor_preserves_runtime_fallback_attribution():
    result = await make_executor(FallbackProvider()).execute("demo")
    assert result.ok
    assert result.source == "Fallback source"
    assert result.source_url == "https://fallback.example/data"


@pytest.mark.asyncio
async def test_feature_executor_exposes_bounded_latency_percentiles():
    executor = make_executor(FakeProvider())
    first = await executor.execute("demo", {"city": "Istanbul"})
    second = await executor.execute("demo", {"city": "Istanbul"})

    assert first.ok and second.ok
    snapshot = executor.metrics_snapshot()
    assert snapshot["counters"]["feature_requests_total"] == 2
    assert snapshot["counters"]["feature_success_total"] == 2
    latency = snapshot["latency"]["feature_execution_seconds"]
    assert latency["count"] == 2
    assert 0 <= latency["p50"] <= latency["p95"] <= latency["p99"]
    assert snapshot["latency"]["feature_execution_seconds_demo"]["count"] == 2


@pytest.mark.asyncio
async def test_health_persistence_is_bounded_and_does_not_block_feature_response():
    executor = make_executor(FakeProvider(), SlowHealthSink())
    started = time.perf_counter()
    result = await executor.execute("demo")
    elapsed = time.perf_counter() - started

    assert result.ok is True
    assert elapsed < 0.10
    assert executor.persistence_metrics()["pending_health_writes"] >= 1
    await executor.close(timeout_seconds=1.0)


@pytest.mark.asyncio
async def test_feature_executor_normalizes_provider_payload_before_display():
    provider = NormalizingProvider()
    result = await make_executor(provider).execute("demo")

    assert result.ok
    assert result.data == {"content": "normalized:demo"}
    assert result.display_blocks == ("normalized:demo",)


@pytest.mark.asyncio
async def test_feature_executor_returns_safe_error_for_invalid_normalized_payload():
    result = await make_executor(InvalidNormalizingProvider()).execute("demo")

    assert result.ok is False
    assert result.error_code == "SCHEMA_CHANGED"
    assert "schema_changed" not in (result.error_message or "")


@pytest.mark.asyncio
async def test_runtime_coalesces_cache_and_reports_observed_health():
    provider = FakeProvider()
    executor = make_executor(provider)
    first = await executor.execute("demo", {"city": "Ankara"})
    second = await executor.execute("demo", {"city": "Ankara"})

    assert first.ok and second.ok
    assert provider.calls == 1
    assert second.cache_hit is True
    health = await executor.health_snapshot()
    item = next(row for row in health if row.name == provider.name)
    assert item.healthy is True
    assert item.error_rate == 0.0
    assert item.score is not None and 0 < item.score <= 100
    assert "score=" in (item.detail or "")


@pytest.mark.asyncio
async def test_runtime_serves_stale_value_when_upstream_fails():
    provider = FakeProvider()
    executor = make_executor(provider)
    first = await executor.execute("demo", {"city": "Izmir"})
    provider.fail = True
    runtime = executor._runtimes[(provider.name, 60)]
    runtime._cache[next(iter(runtime._cache))].expires_at = 0
    stale = await executor.execute("demo", {"city": "Izmir"})
    await asyncio.sleep(0.7)

    assert first.ok and stale.ok
    assert stale.stale is True
    assert stale.cache_hit is True
    assert stale.data == first.data
    assert provider.calls >= 3


@pytest.mark.asyncio
async def test_stale_fallback_does_not_expose_provider_exception_text():
    provider = FakeProvider()
    executor = make_executor(provider)
    first = await executor.execute("demo", {"city": "Izmir"})
    assert first.ok is True

    provider.fail = True
    runtime = executor._runtimes[(provider.name, 60)]
    runtime._cache[next(iter(runtime._cache))].expires_at = 0
    provider.fetch = _private_failure(provider)

    request = ProviderRequest(operation="demo", params={"city": "Izmir"})
    key = runtime._cache_key(request)
    stale = await runtime._fetch_uncached(request, key, runtime._cache[key])

    assert stale.stale is True
    assert "private user URL" not in str(stale.metadata)
    assert "https://example.test/private?token=secret" not in str(stale.metadata)
    assert stale.metadata["fallback_error_type"] == "RuntimeError"
    await executor.close()


def _private_failure(provider):
    async def fail(request):
        provider.calls += 1
        raise RuntimeError("private user URL https://example.test/private?token=secret")

    return fail


@pytest.mark.asyncio
async def test_provider_failure_metadata_contains_only_exception_type():
    provider = FakeProvider()
    provider.fail = True
    result = await make_executor(provider).execute("demo", {"city": "Ankara"})

    assert result.ok is False
    assert result.error_code == "PROVIDER_UNAVAILABLE"
    assert result.metadata["detail"] == "provider_failure type=RuntimeError"
    assert "upstream unavailable" not in str(result.metadata)


@pytest.mark.asyncio
async def test_provider_policy_failure_uses_stable_user_error_code():
    result = await make_executor(BoundedPolicyFailureProvider()).execute("demo")

    assert result.ok is False
    assert result.error_code == "PROVIDER_RESPONSE_TOO_LARGE"
    assert "provider_response_too_large" not in str(result)


@pytest.mark.asyncio
async def test_runtime_does_not_retry_deterministic_transport_policy_failures():
    provider = TransportFailureProvider([
        ProviderTransportError("provider_content_type_invalid"),
    ])
    result = await make_executor(provider).execute("demo")

    assert result.ok is False
    assert result.error_code == "PROVIDER_CONTENT_TYPE_INVALID"
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_runtime_retries_transient_upstream_status_failures():
    provider = TransportFailureProvider([
        ProviderTransportError("provider_upstream_http", status_code=503),
    ])
    result = await make_executor(provider).execute("demo")

    assert result.ok is True
    assert provider.calls == 2


@pytest.mark.asyncio
async def test_runtime_does_not_retry_non_transient_upstream_status_failures():
    provider = TransportFailureProvider([
        ProviderTransportError("provider_upstream_http", status_code=400),
    ])
    result = await make_executor(provider).execute("demo")

    assert result.ok is False
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_provider_error_code_and_message_are_bounded():
    provider = ErrorPayloadProvider()
    result = await make_executor(provider).execute("demo", {"city": "Istanbul"})

    assert result.ok is False
    assert result.error_code == "PROVIDER_ERROR"
    assert result.error_message == "Veri kaynağı güvenli bir yanıt üretemedi."
    assert "secret" not in str(result)


@pytest.mark.asyncio
async def test_runtime_enforces_provider_rate_limit_without_counting_upstream_failure():
    provider = FakeProvider()
    executor = make_rate_limited_executor(provider)

    first = await executor.execute("rate_limited", {"city": "Ankara"})
    second = await executor.execute("rate_limited", {"city": "Izmir"})

    assert first.ok is True
    assert second.ok is False
    assert second.error_code == "PROVIDER_RATE_LIMITED"
    assert second.metadata["retry_after_seconds"] > 0
    assert provider.calls == 1
    health = await executor.health_snapshot()
    item = next(row for row in health if row.name == provider.name)
    assert item.error_rate == 0.0


@pytest.mark.asyncio
async def test_runtime_rejects_invalid_provider_envelope_before_cache():
    provider = InvalidEnvelopeProvider()
    executor = make_executor(provider)

    result = await executor.execute("demo", {"city": "Ankara"})

    assert result.ok is False
    assert result.error_code == "PROVIDER_SCHEMA_INVALID"
    assert result.metadata["contract_error"] == "source_missing"
    assert provider.calls == 2
    runtime = executor._runtimes[(provider.name, 60)]
    assert runtime._cache == {}


@pytest.mark.asyncio
async def test_executor_close_cancels_detached_stale_refresh_tasks():
    provider = FakeProvider()
    executor = make_executor(provider)
    first = await executor.execute("demo", {"city": "Bursa"})
    assert first.ok is True
    runtime = executor._runtimes[(provider.name, 60)]
    key = next(iter(runtime._cache))
    entry = runtime._cache[key]
    refresh_started = asyncio.Event()
    release_refresh = asyncio.Event()

    async def blocked_fetch(request):
        refresh_started.set()
        await release_refresh.wait()
        return await provider.fetch(request)

    provider.fetch = blocked_fetch
    runtime._cache[key].expires_at = 0
    runtime._schedule_stale_refresh(
        ProviderRequest(operation="demo", params={"city": "Bursa"}),
        key,
        entry,
    )
    await asyncio.wait_for(refresh_started.wait(), timeout=1)
    await executor.close()

    assert runtime._refresh_tasks == {}
    assert runtime._inflight == {}
    assert runtime._cache == {}
