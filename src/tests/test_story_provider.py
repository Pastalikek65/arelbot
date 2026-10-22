import pytest

from src.features.contracts import ProviderRequest
from src.features.contracts import FeatureDefinition
from src.features.executor import FeatureExecutor
from src.features.registry import FeatureRegistry
from src.features.providers.story import StoryProvider
from src.tools.spatula_tool import StoryTool


@pytest.mark.asyncio
async def test_story_provider_bounds_and_escapes_user_topic():
    provider = StoryProvider()
    result = await provider.fetch(
        ProviderRequest("story", {"topic": "<script>kayıp kedi</script>", "style": "gizem"})
    )
    assert result.metadata["prompt_injection_safe"] is True
    assert "&lt;script&gt;" in result.data["content"]
    assert "external_data" in result.metadata and result.metadata["external_data"] is False


@pytest.mark.asyncio
async def test_story_provider_rejects_invalid_input_with_stable_codes():
    provider = StoryProvider()
    with pytest.raises(ValueError, match="story_topic_required"):
        await provider.fetch(ProviderRequest("story", {"topic": " "}))
    with pytest.raises(ValueError, match="story_style_invalid"):
        await provider.fetch(ProviderRequest("story", {"topic": "orman", "style": "unknown"}))


@pytest.mark.asyncio
async def test_legacy_ai_story_tool_delegates_to_typed_provider():
    result = await StoryTool().execute("uzay macerası", style="bilimkurgu")
    assert "result" in result
    assert result["metadata"]["provider_kind"] == "typed_generation_brief"


@pytest.mark.asyncio
async def test_feature_executor_exposes_story_validation_error_without_retry():
    provider = StoryProvider()
    registry = FeatureRegistry()
    registry.register_provider(provider)
    registry.register_feature(
        FeatureDefinition(
            key="story",
            category="ai",
            commands=("hikaye",),
            provider_key=provider.name,
            cache_ttl_seconds=0,
            freshness_sla_seconds=0,
            timeout_ms=1000,
            async_mode=False,
            cost_class="local",
            feature_flag=None,
            audit_policy="provider_call",
            source_name="Arelbot AI story provider",
            source_url=None,
            source_license="Arelbot generated",
            source_terms_url=None,
            attribution_required=False,
            rate_limit_per_minute=30,
        ),
        provider,
    )
    result = await FeatureExecutor(registry).execute("story", {"topic": "x", "style": "masal"})
    assert result.ok is False
    assert result.error_code == "STORY_TOPIC_REQUIRED"
