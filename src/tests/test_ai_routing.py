from types import SimpleNamespace

import pytest

from src.ai.router import AIProviderRouter
from src.ai.provider_base import AIProviderRequestError, CompletionRequest, CompletionResponse
from src.chat_engine import ChatEngine
from config import Config


class Provider:
    def __init__(self, name):
        self.name = name


class RuntimeProvider(Provider):
    def __init__(self, name, *, fail=False):
        super().__init__(name)
        self.fail = fail

    async def complete(self, request):
        if self.fail:
            raise RuntimeError("provider unavailable")
        return CompletionResponse(content="ok", provider=self.name, model="test")


@pytest.mark.asyncio
async def test_ai_router_health_calls_provider_probe_and_surfaces_failure():
    class Unhealthy(RuntimeProvider):
        async def health_check(self):
            return {"healthy": False, "detail": "missing_key"}

    router = AIProviderRouter([Unhealthy("evren")], routes={"chat": ["evren"]})

    health = await router.health()

    assert health == [
        {"name": "evren", "healthy": False, "error_rate": 0.0, "latency_ms": None, "detail": "missing_key"}
    ]


def test_explicit_task_routes_are_allowlists():
    router = AIProviderRouter(routes={"vision": ["primary"], "transcribe": ["secondary"]})
    router.register(Provider("primary"))
    router.register(Provider("secondary"))

    assert router.routes["vision"] == ["primary"]
    assert router.routes["transcribe"] == ["secondary"]
    assert "secondary" not in router.routes["vision"]


def test_chat_engine_applies_configured_task_routes(monkeypatch):
    monkeypatch.setattr(
        Config,
        "AI_PROVIDER_ROUTES",
        {"chat": ["freemodel"], "vision": ["groq"], "transcribe": ["cerebras"]},
    )
    engine = ChatEngine.__new__(ChatEngine)
    engine.key_manager = SimpleNamespace(keys={service: ["test-key"] for service in ChatEngine.SERVICE_PRIORITY + ChatEngine.SECONDARY_PROVIDERS})
    engine.http_client = SimpleNamespace()

    router = engine._build_ai_router()

    assert router.routes["chat"] == ["freemodel"]
    assert router.routes["vision"] == ["groq"]
    assert router.routes["transcribe"] == ["cerebras"]
    # Tasks without an explicit preference retain the configured provider set.
    assert set(router.routes["summary"]) == {
        "freemodel", "groq", "gemini", "cerebras", "iamhc", "evren", "cleanapis", "ninerouter"
    }


def test_chat_engine_drops_explicit_route_without_a_provider_key(monkeypatch):
    monkeypatch.setattr(Config, "AI_PROVIDER_ROUTES", {"vision": ["gemini"]})
    engine = ChatEngine.__new__(ChatEngine)
    engine.key_manager = SimpleNamespace(keys={
        service: (["test-key"] if service.value == "freemodel" else [])
        for service in ChatEngine.SERVICE_PRIORITY + ChatEngine.SECONDARY_PROVIDERS
    })
    engine.http_client = SimpleNamespace()

    router = engine._build_ai_router()

    assert router.routes["vision"] == ["freemodel"]


def test_chat_engine_defaults_text_tasks_to_evren_when_route_is_empty(monkeypatch):
    monkeypatch.setattr(Config, "AI_PROVIDER_ROUTES", {})
    engine = ChatEngine.__new__(ChatEngine)
    engine.key_manager = SimpleNamespace(
        keys={service: ["test-key"] for service in ChatEngine.SERVICE_PRIORITY + ChatEngine.SECONDARY_PROVIDERS}
    )
    engine.http_client = SimpleNamespace()

    router = engine._build_ai_router()

    assert router.routes["chat"] == ["evren"]
    assert router.routes["fast"] == ["evren"]
    assert router.routes["analysis"] == ["evren"]
    assert router.routes["summary"] == ["evren"]
    assert router.routes["sticker_tagging"] == ["evren"]


def test_chat_engine_migrates_stale_cleanapis_route_to_evren(monkeypatch):
    monkeypatch.setattr(Config, "AI_PROVIDER_ROUTES", {"chat": ["cleanapis"]})
    monkeypatch.setattr(Config, "EVREN_KEY", "fixture-evren-key")
    engine = ChatEngine.__new__(ChatEngine)
    engine.key_manager = SimpleNamespace(
        keys={service: ["test-key"] for service in ChatEngine.SERVICE_PRIORITY + ChatEngine.SECONDARY_PROVIDERS}
    )
    engine.http_client = SimpleNamespace()

    router = engine._build_ai_router()

    assert router.routes["chat"] == ["evren"]


def test_chat_engine_exposes_cleanapis_as_an_openai_compatible_provider(monkeypatch):
    monkeypatch.setattr(Config, "CLEANAPIS_URL", "https://cleanapis.com/v1")
    monkeypatch.setattr(Config, "CLEANAPIS_MODEL", "deepseek-v4-flash-0731")
    monkeypatch.setattr(Config, "CLEANAPIS_KEY", "fixture-cleanapis-key")
    monkeypatch.setattr(Config, "AI_PROVIDER_ROUTES", {"chat": ["cleanapis"]})
    engine = ChatEngine.__new__(ChatEngine)
    engine.key_manager = SimpleNamespace(
        keys={service: ["test-key"] for service in ChatEngine.SERVICE_PRIORITY + ChatEngine.SECONDARY_PROVIDERS}
    )
    engine.http_client = SimpleNamespace()

    router = engine._build_ai_router()

    assert router.routes["chat"] == ["cleanapis"]
    provider = router.providers["cleanapis"]
    assert provider.base_url == "https://cleanapis.com/v1"
    assert provider.model == "deepseek-v4-flash-0731"


@pytest.mark.asyncio
async def test_ai_router_records_bounded_attempt_and_latency_metrics():
    router = AIProviderRouter(
        providers=[RuntimeProvider("primary", fail=True), RuntimeProvider("fallback")],
        routes={"chat": ["primary", "fallback"]},
    )

    result = await router.complete(CompletionRequest(messages=[{"role": "user", "content": "test"}]))

    assert result.provider == "fallback"
    snapshot = router.metrics_snapshot()
    assert snapshot["counters"]["ai_provider_requests_total"] == 2
    assert snapshot["counters"]["ai_provider_errors_total_primary"] == 1
    assert snapshot["counters"]["ai_provider_success_total_fallback"] == 1
    assert snapshot["latency"]["ai_provider_call_seconds"]["count"] == 2
    assert snapshot["latency"]["ai_provider_call_seconds_primary"]["count"] == 1


class DeterministicInputProvider(Provider):
    def __init__(self, name):
        super().__init__(name)

    async def complete(self, request):
        raise AIProviderRequestError("AI_MESSAGE_TOO_LARGE")


@pytest.mark.asyncio
async def test_deterministic_ai_request_error_does_not_fallback_or_open_circuit():
    router = AIProviderRouter(
        providers=[DeterministicInputProvider("primary"), RuntimeProvider("fallback")],
        routes={"chat": ["primary", "fallback"]},
        failure_threshold=1,
    )

    with pytest.raises(AIProviderRequestError, match="AI_MESSAGE_TOO_LARGE"):
        await router.complete(CompletionRequest(messages=[{"role": "user", "content": "x"}]))

    health = await router.health()
    primary = next(item for item in health if item["name"] == "primary")
    assert primary["healthy"] is True
    snapshot = router.metrics_snapshot()
    assert snapshot["counters"]["ai_provider_validation_errors_total_primary"] == 1
    assert snapshot["counters"].get("ai_provider_success_total_fallback", 0) == 0
    assert snapshot["counters"].get("ai_provider_errors_total_primary", 0) == 0
