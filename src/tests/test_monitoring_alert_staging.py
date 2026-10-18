from __future__ import annotations

import json

import httpx
import pytest

from scripts.monitoring_alert_staging import (
    MonitoringProbeError,
    _safe_report,
    parse_notification_counter,
    validate_live_gate,
    validate_local_base_url,
    validate_monitoring_inputs,
)


def test_monitoring_delivery_probe_requires_all_live_signals():
    with pytest.raises(MonitoringProbeError, match="monitoring_live_gate_required"):
        validate_live_gate(
            live=True,
            confirm_live=True,
            confirm_notification=False,
            env_value="1",
        )


@pytest.mark.parametrize(
    "url",
    [
        "https://alerts.example.com",
        "http://192.168.1.1:9093",
        "http://127.0.0.1:9093/other",
        "http://user:secret@127.0.0.1:9093",
        "http://127.0.0.1:9093?token=secret",
    ],
)
def test_monitoring_delivery_probe_only_targets_loopback_alertmanager(url: str):
    with pytest.raises(MonitoringProbeError, match="alertmanager_url_invalid"):
        validate_local_base_url(url)


def test_monitoring_validation_checks_target_and_bounds_before_live_gate():
    assert validate_monitoring_inputs("http://127.0.0.1:9093", timeout_seconds=1, poll_interval_seconds=0, max_attempts=1)
    with pytest.raises(MonitoringProbeError, match="alertmanager_url_invalid"):
        validate_monitoring_inputs("https://alerts.example.com")
    with pytest.raises(MonitoringProbeError, match="monitoring_attempts_invalid"):
        validate_monitoring_inputs("http://127.0.0.1:9093", max_attempts=6)


def test_monitoring_report_rejects_secret_material(tmp_path):
    with pytest.raises(MonitoringProbeError, match="report_secret_material"):
        _safe_report(tmp_path / "monitoring.json", {"scope": "x", "authorization": "Bearer secret"})


def test_notification_counter_parser_selects_webhook_and_ignores_other_integrations():
    metrics = '''
alertmanager_notifications_total{integration="email"} 9
alertmanager_notifications_total{integration="webhook"} 4
alertmanager_notifications_failed_total{integration="webhook"} 1
'''
    assert parse_notification_counter(metrics, "alertmanager_notifications_total") == 4
    assert parse_notification_counter(metrics, "alertmanager_notifications_failed_total") == 1


@pytest.mark.asyncio
async def test_live_probe_posts_canary_and_requires_success_counter_increment():
    from scripts.monitoring_alert_staging import run_live

    calls: list[tuple[str, str]] = []
    metric_reads = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal metric_reads
        calls.append((request.method, request.url.path))
        if request.url.path == "/-/ready":
            return httpx.Response(200, text="Ready")
        if request.url.path == "/metrics":
            metric_reads += 1
            total = 0 if metric_reads == 1 else 1
            return httpx.Response(
                200,
                text=(
                    f'alertmanager_notifications_total{{integration="webhook"}} {total}\n'
                    'alertmanager_notifications_failed_total{integration="webhook"} 0\n'
                ),
            )
        if request.url.path == "/api/v2/alerts":
            payload = json.loads(request.content)
            assert len(payload) == 1
            assert payload[0]["labels"]["alertname"].startswith("ArelbotStagingDeliveryCanary_")
            return httpx.Response(200)
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await run_live(
            "http://127.0.0.1:9093",
            client=client,
            timeout_seconds=1,
            poll_interval_seconds=0,
        )

    assert report["ok"] is True
    assert report["scope"] == "live-monitoring-alert-delivery"
    assert report["alerts"] == {"webhook_canary": True}
    assert report["delivered"] == 1
    assert report["failed"] == 0
    assert report["secret_free"] is True
    assert calls == [
        ("GET", "/-/ready"),
        ("GET", "/metrics"),
        ("POST", "/api/v2/alerts"),
        ("GET", "/metrics"),
    ]
