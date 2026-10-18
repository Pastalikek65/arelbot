import json

import pytest

from scripts.evren_staging import (
    EvrenProbeError,
    build_failure_report,
    build_success_report,
    validate_live_gate,
    write_report,
)


def test_evren_probe_requires_three_signal_live_gate():
    with pytest.raises(EvrenProbeError):
        validate_live_gate(live=True, confirm_live=True, env_value="0")


def test_evren_success_report_is_bounded_and_secret_free():
    response = type(
        "Response",
        (),
        {"content": "4", "provider": "evren", "model": "deepseek-v4.1-flash", "usage": {"total_tokens": 1}},
    )()
    report = build_success_report(response, latency_ms=12.345)
    assert report == {
        "scope": "evren-staging",
        "ok": True,
        "provider": "evren",
        "model": "deepseek-v4.1-flash",
        "latency_ms": 12.35,
        "response_chars": 1,
        "usage_present": True,
        "secret_free": True,
    }


def test_evren_failure_report_does_not_echo_error_text(tmp_path):
    report = build_failure_report(RuntimeError("secret-token-and-upstream-url"))
    assert report["scope"] == "evren-staging"
    assert report["ok"] is False
    assert "secret-token" not in json.dumps(report)
    target = tmp_path / "evren-staging.json"
    write_report(target, report)
    assert json.loads(target.read_text(encoding="utf-8"))["secret_free"] is True
