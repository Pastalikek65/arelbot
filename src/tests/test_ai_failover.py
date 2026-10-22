import time
from unittest.mock import AsyncMock

import pytest

from src.chat_engine import ChatEngine
from src.key_manager import (
    KeyManager,
    KeyStatus,
    ServiceCircuitOpenError,
    ServiceType,
)


def build_key_manager(monkeypatch, **env):
    monkeypatch.setattr("config.Config.FREEMODEL_KEY", env.get("freemodel", "free-key"))
    monkeypatch.setattr("config.Config.GROQ_KEYS", env.get("groq", []))
    monkeypatch.setattr("config.Config.IAMHC_KEY", env.get("iamhc"))
    monkeypatch.setattr("config.Config.NINEROUTER_KEY", env.get("ninerouter"))
    monkeypatch.setattr("config.Config.CLEANAPIS_KEY", None)
    return KeyManager()


def test_default_provider_priority_keeps_freemodel_primary():
    assert ChatEngine.SERVICE_PRIORITY == [
        ServiceType.OPENAI,
        ServiceType.FREEMODEL,
        ServiceType.GROQ,
    ]


def test_report_error_ignores_unknown_key(monkeypatch):
    manager = build_key_manager(monkeypatch)

    assert manager.report_error(
        ServiceType.FREEMODEL,
        None,
        "connection failed",
    ) is False
    assert manager.key_statuses[ServiceType.FREEMODEL]["free-key"] == KeyStatus.ACTIVE


def test_quota_error_opens_circuit_and_blocks_key(monkeypatch):
    manager = build_key_manager(monkeypatch)
    manager.report_error(ServiceType.FREEMODEL, "free-key", "429 quota exceeded")

    assert manager.key_statuses[ServiceType.FREEMODEL]["free-key"] == KeyStatus.QUOTA_EXHAUSTED
    with pytest.raises(ServiceCircuitOpenError):
        manager.get_key(ServiceType.FREEMODEL)


def test_circuit_recovers_after_cooldown(monkeypatch):
    manager = build_key_manager(monkeypatch)
    manager.report_error(ServiceType.FREEMODEL, "free-key", "503 unavailable")
    manager.exhaustion_times[ServiceType.FREEMODEL]["free-key"] = (
        time.time() - manager.TEMPORARY_ERROR_COOLDOWN_SECONDS - 1
    )

    assert manager.get_key(ServiceType.FREEMODEL) == "free-key"
    assert manager.key_statuses[ServiceType.FREEMODEL]["free-key"] == KeyStatus.ACTIVE


@pytest.mark.asyncio
async def test_chat_engine_fails_over_to_next_configured_provider(monkeypatch):
    manager = build_key_manager(monkeypatch, iamhc="iamhc-key")
    engine = ChatEngine.__new__(ChatEngine)
    engine.key_manager = manager
    engine.http_client = AsyncMock()

    engine.http_client.post.side_effect = [
        type("Response", (), {"status_code": 503, "text": "unavailable"})(),
        type(
            "Response",
            (),
            {
                "status_code": 200,
                "text": '{"choices":[{"message":{"content":"OK"}}]}',
            },
        )(),
    ]

    result = await engine._request_with_failover(
        messages=[{"role": "user", "content": "test"}],
        tools=[],
        services=[ServiceType.FREEMODEL, ServiceType.IAMHC],
    )

    assert result["message"]["content"] == "OK"
    assert engine.http_client.post.await_count == 2


@pytest.mark.asyncio
async def test_chat_engine_skips_provider_with_open_circuit(monkeypatch):
    manager = build_key_manager(monkeypatch, iamhc="iamhc-key")
    manager.report_error(ServiceType.FREEMODEL, "free-key", "429 quota")
    engine = ChatEngine.__new__(ChatEngine)
    engine.key_manager = manager
    engine.http_client = AsyncMock()
    engine.http_client.post.return_value = type(
        "Response",
        (),
        {
            "status_code": 200,
            "text": '{"choices":[{"message":{"content":"FALLBACK"}}]}',
        },
    )()

    result = await engine._request_with_failover(
        messages=[{"role": "user", "content": "test"}],
        tools=[],
        services=[ServiceType.FREEMODEL, ServiceType.IAMHC],
    )

    assert result["message"]["content"] == "FALLBACK"
    assert engine.http_client.post.await_count == 1


@pytest.mark.asyncio
async def test_chat_engine_returns_none_when_all_providers_fail(monkeypatch):
    manager = build_key_manager(monkeypatch)
    engine = ChatEngine.__new__(ChatEngine)
    engine.key_manager = manager
    engine.http_client = AsyncMock()
    engine.http_client.post.return_value = type(
        "Response",
        (),
        {"status_code": 503, "text": "unavailable"},
    )()

    result = await engine._request_with_failover(
        messages=[{"role": "user", "content": "test"}],
        tools=[],
        services=[ServiceType.FREEMODEL],
    )

    assert result is None


@pytest.mark.asyncio
async def test_primary_timeout_falls_back_to_groq(monkeypatch):
    monkeypatch.setattr(
        "config.Config.AI_PROVIDER_ROUTES",
        {"chat": ["freemodel", "groq"]},
    )
    manager = build_key_manager(monkeypatch, groq=["groq-key"])
    engine = ChatEngine.__new__(ChatEngine)
    engine.key_manager = manager
    engine.http_client = AsyncMock()
    engine.http_client.post.side_effect = [
        TimeoutError("primary timed out"),
        type(
            "Response",
            (),
            {
                "status_code": 200,
                "text": '{"choices":[{"message":{"content":"FAST"}}]}',
            },
        )(),
    ]

    result = await engine._request_with_failover(
        messages=[{"role": "user", "content": "test"}],
        tools=[],
    )

    assert result["service"] == ServiceType.GROQ
    assert result["message"]["content"] == "FAST"
    assert engine.http_client.post.await_count == 2
    first_timeout = engine.http_client.post.await_args_list[0].kwargs["timeout"]
    assert first_timeout == 10.0
