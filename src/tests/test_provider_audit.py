from types import SimpleNamespace

from src.features.contracts import FeatureDefinition, ProviderHealth, ProviderResponse
from src.features.provider_audit import audit_errors, audit_registry
from src.features.registry import FeatureRegistry


def test_public_earthquake_feature_declares_short_cache_and_freshness():
    """The AFAD feed must not be accidentally configured as an uncached poll."""

    from src.features.catalog import SPATULA_FEATURES

    assert any(key == "earthquakes" for key, *_rest in SPATULA_FEATURES)
    from src.features.catalog import FEATURE_CACHE_TTL_SECONDS, FEATURE_FRESHNESS_SLA_SECONDS

    assert FEATURE_CACHE_TTL_SECONDS["earthquakes"] == 60
    assert FEATURE_FRESHNESS_SLA_SECONDS["earthquakes"] == 300


class Provider:
    name = "fixture_provider"
    capabilities = {"fixture"}

    async def fetch(self, request):
        return ProviderResponse(data={"content": "ok"}, source="fixture", source_url="https://fixture.invalid")

    async def normalize(self, raw):
        return raw

    async def health_check(self):
        return ProviderHealth(name=self.name, healthy=True)


def _feature(**overrides):
    return FeatureDefinition(
        key=overrides.get("key", "fixture"),
        category=overrides.get("category", "test"),
        provider_key=overrides.get("provider_key", "fixture_provider"),
        provider_kind=overrides.get("provider_kind", "data"),
        timeout_ms=overrides.get("timeout_ms", 1000),
        cache_ttl_seconds=overrides.get("cache_ttl_seconds", 30),
        freshness_sla_seconds=overrides.get("freshness_sla_seconds", 60),
        cost_class=overrides.get("cost_class", "network"),
        source_name=overrides.get("source_name", "Fixture"),
        source_license=overrides.get("source_license", "Test fixture"),
        source_terms_url=overrides.get("source_terms_url", "https://fixture.invalid/terms"),
        source_url=overrides.get("source_url", "https://fixture.invalid/source"),
        source_governance=overrides.get("source_governance", "approved"),
        robots_policy=overrides.get("robots_policy", "allowed"),
        terms_review=overrides.get("terms_review", "approved"),
        production_signoff=overrides.get("production_signoff", "approved"),
        audit_policy=overrides.get("audit_policy", "provider_call"),
        feature_flag=overrides.get("feature_flag", "fixture_feature"),
    )


def test_provider_audit_accepts_typed_network_feature():
    registry = FeatureRegistry()
    registry.register_provider(Provider())
    registry.register_feature(_feature(), SimpleNamespace())

    rows = audit_registry(registry)

    assert len(rows) == 1
    assert rows[0].status == "ok"
    assert audit_errors(registry) == []


def test_provider_audit_reports_missing_contract_and_source_metadata():
    registry = FeatureRegistry()
    registry.register_feature(
        _feature(
            provider_key="missing",
            timeout_ms=0,
            source_name=None,
            source_license=None,
            source_terms_url=None,
            source_url="file:///secret",
        ),
        SimpleNamespace(),
    )

    errors = audit_errors(registry)

    assert "fixture:provider_missing" in errors
    assert "fixture:timeout_missing" in errors
    assert "fixture:source_name_missing" in errors
    assert "fixture:source_url_invalid" in errors


def test_provider_audit_keeps_ai_routes_out_of_data_provider_registry():
    registry = FeatureRegistry()
    registry.register_feature(
        _feature(
            key="group_targeted_replies",
            provider_key="evren",
            provider_kind="ai",
            cost_class="ai_fast",
            source_name="Arelbot group reply router",
            source_license="Arelbot",
            source_terms_url=None,
            source_url=None,
        ),
        SimpleNamespace(),
    )

    rows = audit_registry(registry)

    assert rows[0].status == "ok"
    assert rows[0].provider_kind == "ai"


def test_provider_audit_accepts_injected_moderation_provider_boundary():
    registry = FeatureRegistry()
    registry.register_feature(
        _feature(
            key="guardian_moderation_enforcement",
            provider_key="kek_guardian",
            provider_kind="moderation",
            cost_class="local_socket",
            source_name="Kek Guardian local moderation bridge",
            source_license="Deployment-owned service; no source copied",
            source_terms_url=None,
            source_url=None,
            feature_flag="guardian_moderation",
            audit_policy="moderation_action",
        ),
        SimpleNamespace(),
    )

    rows = audit_registry(registry)

    assert rows[0].status == "ok"
    assert rows[0].provider_kind == "moderation"
