"""Fixture tests for the explicitly enabled, text-only D2API sidecar route."""

import pytest

from config import Config
from src.ai.openai_compatible import AIProviderConfigurationError, OpenAICompatibleProvider
from src.ai.provider_base import AIProviderRequestError, AudioInput, CompletionRequest, VisionRequest
from src.chat_engine import ChatEngine
from src.key_manager import KeyManager, ServiceType


class Response:
    def __init__(self, status=200, body=None, *, invalid_json=False):
        self.status_code = status
        self.body = body if body is not None else {
            "choices": [{"message": {"role": "assistant", "content": "Tamam"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
        self.invalid_json = invalid_json

    def json(self):
        if self.invalid_json:
            raise ValueError("invalid JSON")
        return self.body


class Keys:
    def __init__(self):
        self.keys = {ServiceType.D2API: ["fixture-proxy-key"], ServiceType.EVREN: ["fixture-evren-key"]}
        self.errors = []

    def get_key(self, service):
        return self.keys[service][0]

    def report_success(self, service, key):
        pass

    def report_error(self, service, key, code):
        self.errors.append((service, code))


class Client:
    def __init__(self, d2api_response):
        self.d2api_response = d2api_response
        self.calls = []

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url.startswith("http://d2api:"):
            if isinstance(self.d2api_response, Exception):
                raise self.d2api_response
            return self.d2api_response
        return Response()


def build_router(monkeypatch, *, enabled=True, key=True, client=None, legacy_adapter=True):
    monkeypatch.setattr(Config, "D2API_ENABLED", enabled)
    monkeypatch.setattr(Config, "D2API_KEY", "fixture-proxy-key" if key else None)
    monkeypatch.setattr(Config, "D2API_URL", "http://d2api:9655/v1")
    monkeypatch.setattr(Config, "D2API_MODEL", "deepseek-v4.1-flash")
    monkeypatch.setattr(Config, "AI_PROVIDER_ROUTES", {
        "chat": ["nvidia", "evren"], "fast": ["nvidia", "evren"],
        "analysis": ["nvidia", "evren"], "summary": ["nvidia", "evren"],
        "sticker_tagging": ["nvidia", "evren"], "vision": ["evren"],
        "embedding": ["evren"], "transcribe": ["evren"],
    })
    engine = ChatEngine.__new__(ChatEngine)
    engine.key_manager = Keys()
    if not key:
        engine.key_manager.keys[ServiceType.D2API] = []
    engine.http_client = client or Client(Response())
    if not legacy_adapter:
        return engine._build_ai_router(), engine.http_client
    # Preserve adapter-level rollback fixtures without re-enabling the retired
    # proxy in the product's ChatEngine route builder.
    from src.ai.router import AIProviderRouter
    providers=[OpenAICompatibleProvider(service=ServiceType.EVREN,base_url=Config.EVREN_URL,model=Config.EVREN_MODEL,key_manager=engine.key_manager,http_client=engine.http_client)]
    text=["evren"]
    if enabled and key:
        providers.append(OpenAICompatibleProvider(service=ServiceType.D2API,base_url=Config.D2API_URL,model=Config.D2API_MODEL,key_manager=engine.key_manager,http_client=engine.http_client))
        text=["d2api","evren"]
    routes={task:list(text) for task in ("chat","fast","analysis","summary")}
    routes.update({task:["evren"] for task in ("vision","embedding","transcribe","sticker_tagging")})
    return AIProviderRouter(providers,routes=routes),engine.http_client


def test_d2api_requires_both_opt_in_and_key(monkeypatch):
    for enabled, key in ((False, True), (True, False)):
        router, _ = build_router(monkeypatch, enabled=enabled, key=key,legacy_adapter=False)
        assert "d2api" not in router.providers
        assert router.routes["chat"] == ["evren"]


def test_d2api_key_inventory_requires_explicit_opt_in(monkeypatch):
    monkeypatch.setattr(Config, "D2API_KEY", "fixture-proxy-key")
    monkeypatch.setattr(Config, "D2API_ENABLED", False)
    assert KeyManager().keys[ServiceType.D2API] == []
    monkeypatch.setattr(Config, "D2API_ENABLED", True)
    assert KeyManager().keys[ServiceType.D2API] == ["fixture-proxy-key"]


def test_d2api_is_retired_from_product_text_and_media(monkeypatch):
    router, _ = build_router(monkeypatch,legacy_adapter=False)
    for task in ("chat", "fast", "analysis", "summary"):
        assert router.routes[task] == ["evren"]
    for task in ("vision", "sticker_tagging", "embedding", "transcribe"):
        assert router.routes[task] == ["evren"]


def test_d2api_only_explicit_route_keeps_existing_keyed_failover(monkeypatch):
    build_router(monkeypatch)
    monkeypatch.setattr(Config, "AI_PROVIDER_ROUTES", {"chat": ["d2api"]})
    engine = ChatEngine.__new__(ChatEngine)
    engine.key_manager = Keys()
    engine.http_client = Client(Response())
    router = engine._build_ai_router()
    assert router.routes["chat"] == ["evren"]


@pytest.mark.asyncio
async def test_sticker_tagging_image_content_skips_d2api(monkeypatch):
    router, client = build_router(monkeypatch)
    result = await router.complete(CompletionRequest(
        task="sticker_tagging",
        messages=[{"role": "user", "content": [{"type": "text", "text": "Etiketle"},
                  {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}]}],
    ))
    assert result.provider == "evren"
    assert all(not url.startswith("http://d2api:") for url, _ in client.calls)


@pytest.mark.asyncio
async def test_d2api_primary_uses_proxy_path_key_and_redirect_block(monkeypatch):
    router, client = build_router(monkeypatch)
    result = await router.complete(CompletionRequest(messages=[{"role": "user", "content": "Selam"}]))
    assert result.provider == "d2api"
    assert result.model == "deepseek-v4.1-flash"
    assert client.calls[0][0] == "http://d2api:9655/v1/chat/completions"
    assert client.calls[0][1]["headers"]["Authorization"] == "Bearer fixture-proxy-key"
    assert client.calls[0][1]["follow_redirects"] is False
    assert client.calls[0][1]["json"]["stream"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    Response(401, {"error": {"message": "denied"}}),
    Response(429, {"error": {"message": "rate limited"}}),
    Response(400, {"error": {"message": "tools unsupported"}}),
    Response(invalid_json=True),
    TimeoutError("proxy timeout"),
])
async def test_d2api_failures_fall_back_to_evren(monkeypatch, failure):
    router, client = build_router(monkeypatch, client=Client(failure))
    result = await router.complete(CompletionRequest(
        messages=[{"role": "user", "content": "Selam"}],
        tools=[{"type": "function", "function": {"name": "weather", "parameters": {"type": "object"}}}],
    ))
    assert result.provider == "evren"
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_d2api_rejects_multimodal_input_without_network(monkeypatch):
    router, client = build_router(monkeypatch)
    provider = router.providers["d2api"]
    with pytest.raises(AIProviderRequestError, match="D2API_TEXT_ONLY"):
        await provider.complete(CompletionRequest(messages=[{
            "role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}],
        }]))
    with pytest.raises(AIProviderRequestError, match="D2API_TEXT_ONLY"):
        await provider.vision(VisionRequest(prompt="describe", image_bytes=b"image"))
    with pytest.raises(AIProviderRequestError, match="D2API_TEXT_ONLY"):
        await provider.transcribe(AudioInput(audio_bytes=b"audio"))
    with pytest.raises(AIProviderRequestError, match="D2API_TEXT_ONLY"):
        await provider.embed(["test"])
    assert client.calls == []


@pytest.mark.asyncio
async def test_d2api_json_mode_prompts_and_accepts_strict_object(monkeypatch):
    client = Client(Response(body={"choices": [{"message": {"content": '{"replies":[]}'}}]}))
    router, _ = build_router(monkeypatch, client=client)
    request = CompletionRequest(
        messages=[{"role": "system", "content": "Planla"}, {"role": "user", "content": "Adaylar"}],
        task="fast", response_format={"type": "json_object"}, max_tokens=256,
    )
    result = await router.complete(request)
    assert result.provider == "d2api"
    assert result.content == '{"replies":[]}'
    payload = client.calls[0][1]["json"]
    assert "Return exactly one valid JSON object" in payload["messages"][0]["content"]
    assert "response_format" not in payload
    assert request.messages[0]["content"] == "Planla"


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["```json\n{}\n```", "Açıklama {}", "[]", "", "{broken"])
async def test_d2api_invalid_json_object_falls_back(monkeypatch, content):
    client = Client(Response(body={"choices": [{"message": {"content": content}}]}))
    router, _ = build_router(monkeypatch, client=client)
    result = await router.complete(CompletionRequest(
        messages=[{"role": "user", "content": "Planla"}], task="fast",
        response_format={"type": "json_object"},
    ))
    assert result.provider == "evren"
    assert len(client.calls) == 2
    assert client.calls[0][1]["json"]["messages"][0]["role"] == "system"


@pytest.mark.asyncio
async def test_d2api_ignored_max_tokens_gets_local_output_bound(monkeypatch):
    client = Client(Response(body={"choices": [{"message": {"content": "x" * 257}}]}))
    router, _ = build_router(monkeypatch, client=client)
    result = await router.complete(CompletionRequest(
        messages=[{"role": "user", "content": "Kısa yanıt"}], max_tokens=16,
    ))
    assert result.provider == "evren"
    assert client.calls[0][1]["json"]["max_tokens"] == 16


@pytest.mark.asyncio
async def test_d2api_json_mode_preserves_valid_tool_call_reply(monkeypatch):
    tool = {"id": "fixture-call", "type": "function", "function": {"name": "weather", "arguments": "{}"}}
    client = Client(Response(body={"choices": [{"message": {"content": None, "tool_calls": [tool]}}]}))
    router, _ = build_router(monkeypatch, client=client)
    result = await router.complete(CompletionRequest(
        messages=[{"role": "user", "content": "Hava?"}],
        response_format={"type": "json_object"},
        tools=[{"type": "function", "function": {"name": "weather", "parameters": {"type": "object"}}}],
    ))
    assert result.provider == "d2api"
    assert result.raw["message"]["tool_calls"] == [tool]


def test_d2api_exact_internal_url_allowlist():
    kwargs = {"service": ServiceType.D2API, "model": "deepseek-v4.1-flash",
              "key_manager": Keys(), "http_client": Client(Response())}
    for url in ("http://d2api:9655/v1", "http://127.0.0.1:9655/v1"):
        assert OpenAICompatibleProvider(base_url=url, **kwargs).base_url == url
    for url in ("http://d2api:9656/v1", "http://d2api:9655/v1/", "http://d2api:9655/v2",
                "http://d2api:9655/v1?x=1", "http://user@d2api:9655/v1",
                "http://localhost:9655/v1", "http://127.0.0.2:9655/v1",
                "https://d2api:9655/v1", " http://d2api:9655/v1"):
        with pytest.raises(AIProviderConfigurationError, match="AI_PROVIDER_URL_INVALID"):
            OpenAICompatibleProvider(base_url=url, **kwargs)
