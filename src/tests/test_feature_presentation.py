from datetime import datetime, timezone

from src.features.contracts import FeatureResult, ProviderResponse
from src.services.feature_presentation import format_feature_result


def test_feature_result_presentation_is_safe_and_exposes_freshness_metadata():
    result = FeatureResult.success(
        {"content": "<b>Kur</b>"},
        display_blocks=("<b>Kur</b>",),
        response=ProviderResponse(
            data={"content": "<b>Kur</b>"},
            source="Fixture provider",
            source_url="javascript:alert(1)",
            fetched_at=datetime(2026, 10, 10, 7, 0, tzinfo=timezone.utc),
            stale=True,
            metadata={"fresh": False},
        ),
    )

    rendered = format_feature_result(result, "fallback")

    assert rendered.startswith("<b>Kur</b>")
    assert "Kaynak geÃ§ici olarak kullanÄ±lamadÄ±" in rendered
    assert "Kaynak: Fixture provider" in rendered
    assert "javascript:" not in rendered
    assert "Veri zamanÄ±: 10.10.2026 10:00" in rendered


def test_feature_result_presentation_uses_controlled_fallback_for_failure():
    result = FeatureResult.failure("PROVIDER_UNAVAILABLE", "private upstream detail")

    assert format_feature_result(result, "<b>Veri alÄ±namadÄ±.</b>") == "<b>Veri alÄ±namadÄ±.</b>"
