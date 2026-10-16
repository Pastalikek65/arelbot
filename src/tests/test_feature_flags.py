from __future__ import annotations

import pytest

from src.features.contracts import FeatureDefinition, ProviderHealth, ProviderResponse
from src.features.executor import FeatureExecutor
from src.features.registry import FeatureRegistry


class CountingProvider:
    name = "flagged-source"
    capabilities = {"flagged"}

    def __init__(self) -> None:
        self.calls = 0

    async def fetch(self, request):
        self.calls += 1
        return ProviderResponse(data={"content": "ok"}, source="fixture", source_url="https://example.test")

    async def normalize(self, raw):
        return raw

    async def health_check(self):
        return ProviderHealth(name=self.name, healthy=True)


class BrokenHealthProvider:
    name = "broken-health"
    capabilities = {"fixture"}

    async def fetch(self, request):
        return ProviderResponse(data={"content": "ok"}, source="fixture", source_url="https://example.test")

    async def normalize(self, raw):
        return raw

    async def health_check(self):
        raise RuntimeError("credential=https://user:secret@example.test/private?token=leak")


def _registry(*, flags=None, disabled=None, flag_name=None):
    provider = CountingProvider()
    registry = FeatureRegistry(feature_flags=flags, disabled_features=disabled)
    registry.register_provider(provider)
    registry.register_feature(
        FeatureDefinition(
            key="weather",
            category="test",
            commands=("hava",),
            provider_key=provider.name,
            feature_flag=flag_name,
            cache_ttl_seconds=0,
            freshness_sla_seconds=0,
            timeout_ms=1000,
            audit_policy="provider_call",
            source_name="Fixture weather",
            source_url="https://example.test/weather",
        ),
        provider,
    )
    return registry, provider


@pytest.mark.asyncio
async def test_explicit_false_flag_stops_provider_before_network_call():
    registry, provider = _registry(flags={"weather": False})
    result = await FeatureExecutor(registry).execute("weather", {"city": "Istanbul"})

    assert result.ok is False
    assert result.error_code == "FEATURE_DISABLED"
    assert result.metadata["feature_flag"] == "weather"
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_disabled_feature_list_and_named_flag_are_supported():
    registry, _ = _registry(
        flags={"public.weather": True},
        disabled={"public.weather"},
        flag_name="public.weather",
    )

    assert registry.feature_enabled("weather") is False
    assert registry.feature_status("weather") == {
        "key": "weather",
        "flag": "public.weather",
        "enabled": False,
        "reason": "kill_switch",
        "provider_approval_required": False,
    }


def test_production_registry_blocks_pending_external_provider_before_network():
    registry, provider = _registry()
    # The fixture is intentionally pending by default, matching an external
    # source that has not received terms/robots/sign-off approval.
    production = FeatureRegistry(require_provider_approval=True)
    production.register_provider(provider)
    definition = registry.get_feature("weather").definition
    production.register_feature(definition, provider)

    assert production.feature_enabled("weather") is False
    assert production.feature_status("weather")["reason"] == "provider_governance"


def test_explicit_kill_switch_reason_takes_precedence_over_pending_governance():
    registry, provider = _registry(disabled={"weather"})
    production = FeatureRegistry(require_provider_approval=True, disabled_features={"weather"})
    production.register_provider(provider)
    production.register_feature(registry.get_feature("weather").definition, provider)

    status = production.feature_status("weather")

    assert status["enabled"] is False
    assert status["reason"] == "kill_switch"


@pytest.mark.asyncio
async def test_production_governance_guard_returns_stable_disabled_result_without_provider_call():
    registry, provider = _registry()
    production = FeatureRegistry(require_provider_approval=True)
    production.register_provider(provider)
    production.register_feature(registry.get_feature("weather").definition, provider)

    result = await FeatureExecutor(production).execute("weather", {"city": "Istanbul"})

    assert result.ok is False
    assert result.error_code == "FEATURE_DISABLED"
    assert result.metadata["reason"] == "provider_governance"
    assert provider.calls == 0


def test_production_registry_accepts_only_fully_approved_external_provider():
    registry, provider = _registry()
    production = FeatureRegistry(require_provider_approval=True)
    production.register_provider(provider)
    definition = registry.get_feature("weather").definition
    approved = definition.__class__(
        **{
            **definition.__dict__,
            "source_governance": "approved",
            "robots_policy": "allowed",
            "terms_review": "approved",
            "production_signoff": "approved",
        }
    )
    production.register_feature(approved, provider)

    assert production.feature_enabled("weather") is True


def test_runtime_emergency_toggle_is_visible_in_manifest_status():
    registry, _ = _registry()

    assert registry.set_feature_enabled("WEATHER", False) is True
    assert registry.feature_statuses()[0]["enabled"] is False
    assert registry.set_feature_enabled("weather", True) is True
    assert registry.feature_enabled("weather") is True
    assert registry.set_feature_enabled("missing", False) is False


def test_runtime_toggle_only_applies_to_provider_backed_features() -> None:
    registry = FeatureRegistry()
    registry.register_feature(
        FeatureDefinition(
            key="local_game",
            category="oyun",
            commands=("oyun",),
            input_model="game_action",
            output_type="telegram_html",
            provider_key=None,
            cache_ttl_seconds=0,
            freshness_sla_seconds=0,
            timeout_ms=500,
            audit_policy="test",
        ),
        None,
    )
    registry.register_feature(
        FeatureDefinition(
            key="weather",
            category="hava",
            commands=("hava",),
            input_model="weather_request",
            output_type="telegram_html",
            provider_key="weather_provider",
            cache_ttl_seconds=60,
            freshness_sla_seconds=300,
            timeout_ms=5000,
            audit_policy="test",
        ),
        None,
    )

    assert registry.runtime_toggle_supported("local_game") is False
    assert registry.runtime_toggle_supported("weather") is True
    assert registry.runtime_toggle_supported("missing") is False


@pytest.mark.asyncio
async def test_health_snapshot_summarizes_provider_exception_without_message():
    registry = FeatureRegistry()
    provider = BrokenHealthProvider()
    registry.register_provider(provider)

    health = await registry.health_snapshot()

    assert len(health) == 1
    assert health[0].healthy is False
    assert health[0].detail == "provider_health type=RuntimeError"
    assert "secret" not in (health[0].detail or "")
