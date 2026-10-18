from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts.cleanapis_staging import (
    build_failure_report,
    build_success_report,
    validate_live_gate,
)


def test_cleanapis_probe_requires_explicit_live_gate():
    with pytest.raises(ValueError, match="live_gate_required"):
        validate_live_gate(live=True, confirm_live=False, env_value="1")


def test_cleanapis_success_report_is_bounded_and_secret_free():
    response = SimpleNamespace(
        provider="cleanapis",
        model="deepseek-v4-flash-0731",
        content="fixture-private-response",
        usage={"prompt_tokens": 2, "completion_tokens": 1},
    )
    report = build_success_report(response, latency_ms=12.345)

    assert report == {
        "scope": "cleanapis-staging",
        "ok": True,
        "provider": "cleanapis",
        "model": "deepseek-v4-flash-0731",
        "latency_ms": 12.35,
        "response_chars": len(response.content),
        "usage_present": True,
        "secret_free": True,
    }
    assert response.content not in repr(report)


def test_cleanapis_failure_report_does_not_echo_exception_text():
    private_marker = "pr" + "ivate-value"
    error = RuntimeError("token=" + private_marker + " https://private.example")
    report = build_failure_report(error)

    assert report["scope"] == "cleanapis-staging"
    assert report["ok"] is False
    assert report["error"] == "PROVIDER_CALL_FAILED"
    assert report["exception_type"] == "RuntimeError"
    assert private_marker not in repr(report)
    assert "private.example" not in repr(report)


