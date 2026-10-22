from types import SimpleNamespace

import pytest

from src.ai.router import AIProviderRouter
from src.ai.provider_base import AIProviderRequestError, CompletionRequest, CompletionResponse
from src.chat_engine import ChatEngine
from src.key_manager import ServiceType
from src.ai.chatgpt_plan import PLAN_SCOPE, RESOURCE_SCOPE, _atomic_write_record
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
        "freemodel", "groq", "gemini", "cerebras", "iamhc", "evren", "nvidia", "ninerouter", "cloudflare"
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


def test_chatgpt_plan_is_first_for_text_and_excluded_from_media_routes(monkeypatch, tmp_path):
    credentials = tmp_path / "credentials.json"
    _atomic_write_record(credentials, {
        "client_id": "oaiapp_test", "access_token": "access", "refresh_token": "refresh",
        "scopes": [PLAN_SCOPE, RESOURCE_SCOPE], "expires_at": 2_000_000_000,
    })
    monkeypatch.setattr(Config, "OPENAI_CHATGPT_ENABLED", True)
    monkeypatch.setattr(Config, "OPENAI_CHATGPT_MODE", "siwc")
    monkeypatch.setattr(Config, "OPENAI_CHATGPT_MODEL", "gpt-5.6-luna")
    monkeypatch.setattr(Config, "OPENAI_CHATGPT_CREDENTIALS_FILE", str(credentials))
    monkeypatch.setattr(Config, "AI_PROVIDER_ROUTES", {
        "chat": ["openai", "evren", "nvidia", "cloudflare"],
        "fast": ["openai", "evren", "nvidia", "cloudflare"],
        "vision": ["openai", "evren", "nvidia"],
    })
    engine = ChatEngine.__new__(ChatEngine)
    engine.key_manager = SimpleNamespace(keys={
        service: (["test-key"] if service in {ServiceType.EVREN, ServiceType.NVIDIA} else [])
        for service in ChatEngine.SERVICE_PRIORITY + ChatEngine.SECONDARY_PROVIDERS
    })
    engine.http_client = SimpleNamespace()

    router = engine._build_ai_router()

    assert router.routes["chat"] == ["openai", "evren", "nvidia"]
    assert router.routes["fast"] == ["openai", "evren", "nvidia"]
    assert router.routes["vision"] == ["evren", "nvidia"]
    assert router.providers["openai"].model == "gpt-5.6-luna"


def test_codex_cli_is_primary_but_keeps_provider_fallback(monkeypatch):
    monkeypatch.setattr(Config, "OPENAI_CHATGPT_ENABLED", True)
    monkeypatch.setattr(Config, "OPENAI_CHATGPT_MODE", "cli")
    monkeypatch.setattr(Config, "OPENAI_CHATGPT_CLI_BIN", "codex")
    monkeypatch.setattr(Config, "OPENAI_CHATGPT_CLI_MODEL", "")
    monkeypatch.setattr(Config, "AI_PROVIDER_ROUTES", {"chat": ["openai"], "fast": ["openai"]})
    engine = ChatEngine.__new__(ChatEngine)
    engine.key_manager = SimpleNamespace(keys={
        service: (["test-key"] if service in {ServiceType.EVREN, ServiceType.NVIDIA} else [])
        for service in ChatEngine.SERVICE_PRIORITY + ChatEngine.SECONDARY_PROVIDERS
    })
    engine.http_client = SimpleNamespace()

    router = engine._build_ai_router()

    assert router.routes["chat"][:2] == ["openai", "evren"]
    assert router.routes["fast"][:2] == ["openai", "evren"]
    assert router.providers["openai"].__class__.__name__ == "ChatGPTCliProvider"


def test_chat_engine_defaults_text_tasks_to_ordered_provider_fallback_when_route_is_empty(monkeypatch):
    monkeypatch.setattr(Config, "AI_PROVIDER_ROUTES", {})
    engine = ChatEngine.__new__(ChatEngine)
    engine.key_manager = SimpleNamespace(
        keys={service: ["test-key"] for service in ChatEngine.SERVICE_PRIORITY + ChatEngine.SECONDARY_PROVIDERS}
    )
    engine.http_client = SimpleNamespace()

    router = engine._build_ai_router()

    assert router.routes["chat"] == ["evren", "nvidia", "cloudflare"]
    assert router.routes["fast"] == ["evren", "nvidia", "cloudflare"]
    assert router.routes["analysis"] == ["evren", "nvidia", "cloudflare"]
    assert router.routes["summary"] == ["evren", "nvidia", "cloudflare"]
    assert router.routes["sticker_tagging"] == ["nvidia", "evren"]


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


def test_chat_engine_retires_cleanapis_route_without_registering_provider(monkeypatch):
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

    assert "cleanapis" not in router.routes["chat"]
    assert "cleanapis" not in router.providers
    assert router.routes["chat"] == ["evren"]


def test_chat_engine_exposes_nvidia_nim_provider_without_relabeling_evren(monkeypatch):
    monkeypatch.setattr(Config, "NVIDIA_URL", "https://integrate.api.nvidia.com/v1")
    monkeypatch.setattr(Config, "NVIDIA_MODEL", "deepseek-ai/deepseek-v4.1-flash")
    monkeypatch.setattr(Config, "NVIDIA_CHAT_MODEL", "deepseek-ai/deepseek-v4.1-flash")
    monkeypatch.setattr(Config, "NVIDIA_VISION_MODEL", "nvidia-vision-fixture")
    monkeypatch.setattr(Config, "AI_PROVIDER_ROUTES", {"chat": ["nvidia"], "vision": ["nvidia"]})
    engine = ChatEngine.__new__(ChatEngine)
    engine.key_manager = SimpleNamespace(
        keys={service: (["test-key"] if service is ServiceType.NVIDIA else [])
              for service in ChatEngine.SERVICE_PRIORITY + ChatEngine.SECONDARY_PROVIDERS}
    )
    engine.http_client = SimpleNamespace()

    router = engine._build_ai_router()

    assert router.routes["chat"] == ["nvidia"]
    assert router.routes["vision"] == ["nvidia"]
    provider = router.providers["nvidia"]
    assert provider.base_url == "https://integrate.api.nvidia.com/v1"
    assert provider.model == "deepseek-ai/deepseek-v4.1-flash"
    assert provider.vision_model == "nvidia-vision-fixture"
    assert router.providers["nvidia"].name == "nvidia"


def test_chat_engine_uses_nvidia_as_safe_default_when_evren_is_unconfigured(monkeypatch):
    monkeypatch.setattr(Config, "AI_PROVIDER_ROUTES", {})
    monkeypatch.setattr(Config, "EVREN_KEY", None)
    monkeypatch.setattr(Config, "NVIDIA_KEY", "fixture-nvidia-key")
    engine = ChatEngine.__new__(ChatEngine)
    engine.key_manager = SimpleNamespace(
        keys={service: (["fixture-nvidia-key"] if service is ServiceType.NVIDIA else [])
              for service in ChatEngine.SERVICE_PRIORITY + ChatEngine.SECONDARY_PROVIDERS}
    )
    engine.http_client = SimpleNamespace()

    router = engine._build_ai_router()

    assert router.routes["chat"] == ["nvidia"]
    assert router.routes["fast"] == ["nvidia"]


def test_chat_engine_rotates_evren_and_nvidia_when_cloudflare_has_no_key(monkeypatch):
    monkeypatch.setattr(Config, "AI_PROVIDER_ROUTES", {})
    monkeypatch.setattr(Config, "EVREN_KEY", "fixture-evren-key")
    monkeypatch.setattr(Config, "NVIDIA_KEY", "fixture-nvidia-key")
    engine = ChatEngine.__new__(ChatEngine)
    engine.key_manager = SimpleNamespace(
        keys={
            service: (["fixture-nvidia-key"] if service is ServiceType.NVIDIA
                      else (["fixture-evren-key"] if service is ServiceType.EVREN else []))
            for service in ChatEngine.SERVICE_PRIORITY + ChatEngine.SECONDARY_PROVIDERS
        }
    )
    engine.http_client = SimpleNamespace()

    router = engine._build_ai_router()

    assert router.routes["chat"] == ["evren", "nvidia"]
    assert router.routes["fast"] == ["evren", "nvidia"]


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
