from types import SimpleNamespace

from src.observability.provider_report import (
    format_ai_provider_health,
    format_provider_governance,
    format_provider_health,
)


def test_provider_health_report_escapes_detail_and_bounds_rows():
    rows = [
        SimpleNamespace(name="typed<one>", healthy=True, error_rate=0.0, detail="safe & detail"),
        SimpleNamespace(name="legacy", healthy=False, error_rate=1.2, detail="failure <raw>"),
    ]

    text = format_provider_health(rows, title="Providers", limit=1)

    assert "typed&lt;one&gt;" in text
    assert "safe &amp; detail" in text
    assert "legacy" not in text
    assert "sağlayıcı" in text


def test_provider_health_report_handles_missing_metrics():
    text = format_provider_health([SimpleNamespace(name="x", healthy=True)], title="Health")

    assert "hata —" in text


def test_ai_provider_health_report_is_bounded_and_safe():
    text = format_ai_provider_health(
        [{"name": "groq<one>", "healthy": True, "error_rate": 0.25, "latency_ms": 42}],
    )

    assert "groq&lt;one&gt;" in text
    assert "%25.0" in text
    assert "42ms" in text


def test_provider_governance_formatter_is_bounded_and_secret_free():
    report = {
        "pending_count": 1,
        "disabled_count": 0,
        "errors": [],
        "features": [
            {
                "feature_key": "weather",
                "provider_key": "get_weather",
                "status": "pending",
                "robots_policy": "review_required",
                "terms_review": "review_required",
                "production_signoff": "required",
                "source_url": "https://secret.example/private?token=never-log",
            }
        ],
    }

    rendered = format_provider_governance(report)

    assert "weather" in rendered
    assert "get_weather" in rendered
    assert "review_required" in rendered
    assert "secret.example" not in rendered
    assert "never-log" not in rendered


def test_provider_governance_formatter_handles_invalid_rows_without_crashing():
    rendered = format_provider_governance({"features": [None, "bad"], "errors": ["hidden"]})

    assert "Kaynak yönetişimi" in rendered
    assert "hata" in rendered
