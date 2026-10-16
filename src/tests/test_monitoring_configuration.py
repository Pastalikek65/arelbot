from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[2]


def test_monitoring_assets_are_pinned_and_secret_protected():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    prometheus = (ROOT / "deploy" / "monitoring" / "prometheus.yml").read_text(encoding="utf-8")
    alerts = (ROOT / "deploy" / "monitoring" / "alerts.yml").read_text(encoding="utf-8")
    alertmanager = (ROOT / "deploy" / "monitoring" / "alertmanager.yml").read_text(encoding="utf-8")
    dashboard_path = ROOT / "deploy" / "monitoring" / "grafana" / "dashboards" / "arelbot-overview.json"
    dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))

    assert "prom/prometheus:v3.13.4" in compose
    assert "grafana/grafana:13.0.9" in compose
    assert "prom/alertmanager:v0.34.1" in compose
    assert "alertmanager:" in prometheus
    assert "url_file: /run/secrets/arelbot_alert_webhook_url" in alertmanager
    assert "send_resolved: true" in alertmanager
    assert "authorization:" in prometheus
    assert "rule_files:" in prometheus
    assert "/etc/prometheus/alerts.yml" in prometheus
    assert "ArelbotBotScrapeDown" in alerts
    assert "ArelbotMediaQueueBacklog" in alerts
    assert "ArelbotHealthWritesDropped" in alerts
    assert "credentials_file: /run/secrets/arelbot_observability_token" in prometheus
    assert "arelbot_observability_token" in compose
    assert "grafana_admin_password" in compose
    assert dashboard["uid"] == "arelbot-ops"
    assert len(dashboard["panels"]) >= 4
    assert all(panel.get("datasource", {}).get("uid") == "arelbot-prometheus" for panel in dashboard["panels"])


def test_alert_rule_replay_fixture_covers_every_operational_alert():
    fixture = (ROOT / "deploy" / "monitoring" / "alerts.test.yml").read_text(encoding="utf-8")
    alerts = (ROOT / "deploy" / "monitoring" / "alerts.yml").read_text(encoding="utf-8")
    expected = {
        "ArelbotBotScrapeDown",
        "ArelbotWorkerScrapeDown",
        "ArelbotMediaQueueBacklog",
        "ArelbotWorkerHasNoCapacity",
        "ArelbotHealthWritesDropped",
    }
    assert "/etc/prometheus/alerts.yml" in fixture
    assert expected <= {line.split(":", 1)[1].strip() for line in fixture.splitlines() if line.startswith("        alertname:")}
    assert expected <= {line.split(":", 1)[1].strip() for line in alerts.splitlines() if line.startswith("      - alert:")}


def test_monitoring_bindings_are_loopback_only():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert '"127.0.0.1:${PROMETHEUS_PORT:-9090}:9090"' in compose
    assert '"127.0.0.1:${GRAFANA_PORT:-3000}:3000"' in compose
    assert '"127.0.0.1:${ALERTMANAGER_PORT:-9093}:9093"' in compose


def test_monitoring_profile_is_a_valid_compose_graph():
    """Prometheus/Grafana must pull in the processes they scrape.

    This is skipped on developer machines without Docker, while CI and the
    release gate validate the actual Compose parser rather than duplicating
    its profile dependency rules in Python.
    """

    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("docker is not installed")
    result = subprocess.run(
        [docker, "compose", "--profile", "monitoring", "config", "--quiet"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-1000:]
