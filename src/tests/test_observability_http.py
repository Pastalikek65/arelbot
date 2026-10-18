from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from src.observability.http_server import ObservabilityServer
from src.observability.health_summary import health_status, summarize_provider_rows
from src.orchestrator import Orchestrator
from config import Config


def test_health_summary_is_bounded_and_distinguishes_degradation():
    rows = [type("Health", (), {"name": f"provider-{i}", "healthy": i == 0})() for i in range(4)]

    summary = summarize_provider_rows(rows, limit=2)

    assert summary == {
        "total": 4,
        "healthy": 1,
        "unhealthy": ["provider-1", "provider-2"],
        "unhealthy_truncated": 1,
    }
    assert health_status({"mongodb": "healthy"}, {"features": summary}) == "degraded"
    assert health_status({"mongodb": "unavailable"}, {"features": summary}) == "unhealthy"
    assert health_status({"mongodb": "healthy"}, {"features": {"unhealthy": 0}}) == "healthy"
    failed_probe = summarize_provider_rows([], probe_error=True)
    assert failed_probe["probe_error"] is True
    assert health_status({"mongodb": "healthy"}, {"features": failed_probe}) == "degraded"
    ai_rows = [{"name": "groq", "healthy": True}, {"name": "gemini", "healthy": False}]
    assert summarize_provider_rows(ai_rows) == {
        "total": 2,
        "healthy": 1,
        "unhealthy": ["gemini"],
        "unhealthy_truncated": 0,
    }


@pytest.mark.asyncio
async def test_orchestrator_health_bounds_core_probe_and_surfaces_optional_probe_failure(monkeypatch):
    monkeypatch.setattr(Config, "MONGODB_REQUIRED", True)
    monkeypatch.setattr(Config, "MEDIA_REDIS_REQUIRED", True)
    monkeypatch.setattr(Config, "APP_ROLE", "bot")
    monkeypatch.setattr(Config, "APP_ENV", "test")

    class Redis:
        async def ping(self):
            return True

    class ProviderRegistry:
        async def health(self):
            return []

    handler = object.__new__(Orchestrator)
    handler._health_snapshot_cache = None
    handler.data_port = type("Port", (), {"ping": lambda self: asyncio.sleep(0, result=True)})()
    handler.media_service = type(
        "Media",
        (),
        {"queue": type("Queue", (), {"redis": Redis()})(), "providers": ProviderRegistry()},
    )()

    async def broken_feature_probe():
        raise TimeoutError("provider endpoint data must not be reflected")

    handler.chat_engine = SimpleNamespace(
        feature_executor=SimpleNamespace(health_snapshot=broken_feature_probe),
        ai_router=SimpleNamespace(
            health=lambda: asyncio.sleep(0, result=[{"name": "groq", "healthy": False}])
        ),
    )

    payload = await handler._observability_health()

    assert payload["status"] == "degraded"
    assert payload["ok"] is True
    assert payload["providers"]["features"]["probe_error"] is True
    assert payload["providers"]["ai"]["unhealthy"] == ["groq"]
    assert "endpoint data" not in str(payload)


async def _request(port: int, request: str) -> tuple[int, dict[str, str], bytes]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(request.encode("ascii"))
    await writer.drain()
    response = await reader.read()
    writer.close()
    await writer.wait_closed()
    header, _, body = response.partition(b"\r\n\r\n")
    lines = header.decode("ascii").split("\r\n")
    status = int(lines[0].split()[1])
    headers = {
        key.lower(): value.strip()
        for key, value in (line.split(":", 1) for line in lines[1:] if ":" in line)
    }
    return status, headers, body


@pytest.mark.asyncio
async def test_observability_server_requires_token_and_exports_prometheus():
    server = ObservabilityServer(
        host="127.0.0.1",
        port=0,
        token="metrics-token-123456",
        snapshot_factory=lambda: {
            "counters": {"jobs": 2},
            "gauges": {"queue_depth": 3},
            "latency": {"job_seconds": {"count": 4, "p50": 0.2, "p95": 0.4, "p99": 0.5}},
        },
        health_factory=lambda: {"ok": True, "role": "test"},
    )
    await server.start()
    try:
        status, _, _ = await _request(server.port, "GET /metrics HTTP/1.1\r\nHost: localhost\r\n\r\n")
        assert status == 401

        status, headers, body = await _request(
            server.port,
            "GET /metrics HTTP/1.1\r\nHost: localhost\r\nX-Arelbot-Metrics-Token: metrics-token-123456\r\n\r\n",
        )
        text = body.decode("utf-8")
        assert status == 200
        assert headers["content-type"].startswith("text/plain")
        assert "arelbot_counters_jobs 2" in text
        assert "arelbot_gauges_queue_depth 3" in text
        assert "arelbot_latency_job_seconds_p95 0.4" in text

        # Prometheus is configured with an RFC-style Bearer credential. Keep
        # that path covered separately from the raw header used by operators.
        status, _, body = await _request(
            server.port,
            "GET /metrics?probe=ignored HTTP/1.1\r\nHost: localhost\r\n"
            "Authorization: Bearer metrics-token-123456\r\n\r\n",
        )
        assert status == 200
        assert b"probe=ignored" not in body
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_observability_health_is_json_and_rejects_unsupported_routes():
    server = ObservabilityServer(
        host="127.0.0.1",
        port=0,
        snapshot_factory=lambda: {},
        health_factory=lambda: {"ok": True, "dependencies": {"redis": "healthy"}},
    )
    await server.start()
    try:
        status, headers, body = await _request(server.port, "GET /healthz HTTP/1.1\r\nHost: localhost\r\n\r\n")
        assert status == 200
        assert headers["content-type"] == "application/json; charset=utf-8"
        assert json.loads(body) == {"ok": True, "dependencies": {"redis": "healthy"}}

        status, _, _ = await _request(server.port, "POST /metrics HTTP/1.1\r\nHost: localhost\r\n\r\n")
        assert status == 405

        status, _, _ = await _request(server.port, "GET /private HTTP/1.1\r\nHost: localhost\r\n\r\n")
        assert status == 404
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_observability_health_returns_service_unavailable_for_unhealthy_snapshot():
    """Readiness consumers must not treat an unhealthy process as available."""

    server = ObservabilityServer(
        host="127.0.0.1",
        port=0,
        snapshot_factory=lambda: {},
        health_factory=lambda: {
            "ok": False,
            "status": "unhealthy",
            "dependencies": {"mongodb": "unavailable"},
        },
    )
    await server.start()
    try:
        status, headers, body = await _request(server.port, "GET /healthz HTTP/1.1\r\nHost: localhost\r\n\r\n")
        assert status == 503
        assert headers["cache-control"] == "no-store"
        assert json.loads(body)["status"] == "unhealthy"
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_observability_health_keeps_optional_degradation_available():
    """Optional provider degradation is visible but does not fail readiness."""

    server = ObservabilityServer(
        host="127.0.0.1",
        port=0,
        snapshot_factory=lambda: {},
        health_factory=lambda: {"ok": True, "status": "degraded"},
    )
    await server.start()
    try:
        status, _, body = await _request(server.port, "GET /healthz HTTP/1.1\r\nHost: localhost\r\n\r\n")
        assert status == 200
        assert json.loads(body)["status"] == "degraded"
    finally:
        await server.close()
