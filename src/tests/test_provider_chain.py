import pytest
import time

from src.features.contracts import ProviderHealth, ProviderRequest, ProviderResponse
from src.features.provider_chain import ProviderChain
from src.features.legacy_provider import LegacyToolProvider, LocalToolProvider


class FakeProvider:
    def __init__(self, name: str, *, fail: bool = False, latency: float = 0.0):
        self.name = name
        self.capabilities = {"demo"}
        self.fail = fail
        self.latency = latency

    async def fetch(self, request):
        if self.latency:
            import asyncio
            await asyncio.sleep(self.latency)
        if self.fail:
            raise RuntimeError(self.name)
        return ProviderResponse(data={"provider": self.name}, source=self.name, source_url=None)

    async def normalize(self, raw):
        return raw

    async def health_check(self):
        return ProviderHealth(
            name=self.name,
            healthy=not self.fail,
            latency_ms=self.latency * 1000,
        )


class NormalizingProvider(FakeProvider):
    async def normalize(self, raw):
        return {"normalized": raw["provider"]}


class DummyTool:
    name = "dummy"

    async def execute(self, **kwargs):
        return {"content": "legacy"}


class BrokenHealthProvider(FakeProvider):
    async def health_check(self):
        raise RuntimeError("Authorization https://user:secret@example.test/private")


class ClosableProvider(FakeProvider):
    def __init__(self, name: str):
        super().__init__(name)
        self.closed = False

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_provider_chain_falls_back_and_exposes_selected_provider():
    primary = FakeProvider("primary", fail=True)
    fallback = FakeProvider("fallback")
    chain = ProviderChain([primary, fallback], failure_threshold=1, cooldown_seconds=60)

    response = await chain.fetch(ProviderRequest(operation="demo"))

    assert response.data == {"provider": "fallback"}
    assert response.metadata["provider_chain"] == "primary,fallback"
    assert response.metadata["provider_selected"] == "fallback"


@pytest.mark.asyncio
async def test_provider_chain_normalizes_the_selected_provider_payload():
    provider = NormalizingProvider("primary")
    chain = ProviderChain([provider])

    response = await chain.fetch(ProviderRequest(operation="demo"))

    assert response.data == {"normalized": "primary"}


@pytest.mark.asyncio
async def test_provider_chain_exposes_legacy_fallback_as_explicit_metadata():
    primary = FakeProvider("primary")
    legacy = LegacyToolProvider(DummyTool(), name="primary:legacy")
    chain = ProviderChain([primary, legacy])

    response = await chain.fetch(ProviderRequest(operation="demo"))
    health = await chain.health_check()

    assert response.metadata["legacy_fallback_available"] is True
    assert "primary(typed)" in (health.detail or "")
    assert "primary:legacy(legacy)" in (health.detail or "")


@pytest.mark.asyncio
async def test_local_tool_provider_is_not_reported_as_legacy():
    local = LocalToolProvider(DummyTool(), name="memory_tool")
    chain = ProviderChain([local])

    response = await chain.fetch(ProviderRequest(operation="memory_tool"))
    health = await chain.health_check()

    assert response.metadata["legacy_fallback_available"] is False
    assert response.metadata["provider_selected"] == "memory_tool"
    assert "memory_tool(local)" in (health.detail or "")


@pytest.mark.asyncio
async def test_provider_chain_opens_failed_circuit_and_prefers_healthy_provider():
    primary = FakeProvider("primary", fail=True)
    fallback = FakeProvider("fallback")
    chain = ProviderChain([primary, fallback], failure_threshold=1, cooldown_seconds=60)
    request = ProviderRequest(operation="demo")

    await chain.fetch(request)
    health = await chain.health_check()

    assert health.healthy is True
    assert "primary" in (health.detail or "")
    assert health.score is not None and health.score > 0
    second = await chain.fetch(request)
    assert second.metadata["provider_selected"] == "fallback"


def test_provider_chain_preserves_primary_priority_until_circuit_threshold():
    primary = FakeProvider("primary", fail=True)
    fallback = FakeProvider("fallback")
    chain = ProviderChain([primary, fallback], failure_threshold=3, cooldown_seconds=60)

    assert [p.name for p in chain.ordered()] == ["primary", "fallback"]


@pytest.mark.asyncio
async def test_provider_chain_opens_primary_circuit_after_threshold():
    primary = FakeProvider("primary", fail=True)
    fallback = FakeProvider("fallback")
    chain = ProviderChain([primary, fallback], failure_threshold=2, cooldown_seconds=60)
    request = ProviderRequest(operation="demo")

    await chain.fetch(request)
    assert [p.name for p in chain.ordered()] == ["primary", "fallback"]
    await chain.fetch(request)
    assert [p.name for p in chain.ordered()] == ["fallback"]


@pytest.mark.asyncio
async def test_provider_chain_uses_live_health_and_redacts_probe_failure():
    provider = BrokenHealthProvider("broken")
    chain = ProviderChain([provider])

    health = await chain.health_check()

    assert health.healthy is False
    assert "probe=provider_health type=RuntimeError" in (health.detail or "")
    assert "secret" not in (health.detail or "")


@pytest.mark.asyncio
async def test_provider_chain_health_score_treats_latency_as_milliseconds():
    provider = FakeProvider("slow-but-healthy", latency=0.2)
    chain = ProviderChain([provider])

    health = await chain.health_check()

    assert health.healthy is True
    # 200 ms should produce a healthy score near 83, not a near-zero score
    # caused by dividing by 1 + 200.
    assert health.score is not None
    assert 80 < health.score < 85


def test_provider_chain_prioritizes_half_open_provider_for_recovery_probe():
    primary = FakeProvider("primary")
    fallback = FakeProvider("fallback")
    chain = ProviderChain([primary, fallback], failure_threshold=1, cooldown_seconds=60)
    chain._samples[primary.name].extend([False] * 5)
    chain._latencies[primary.name].extend([0.01] * 5)
    chain._opened_until[primary.name] = time.monotonic() - 1
    chain._samples[fallback.name].extend([True] * 5)
    chain._latencies[fallback.name].extend([0.2] * 5)

    assert [p.name for p in chain.ordered()] == ["primary", "fallback"]


@pytest.mark.asyncio
async def test_provider_chain_closes_owned_provider_transports():
    provider = ClosableProvider("closable")
    chain = ProviderChain([provider])

    await chain.close()

    assert provider.closed is True
