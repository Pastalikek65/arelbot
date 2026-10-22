import pytest

from src.ai.openai_compatible import (
    AIProviderConfigurationError,
    AIProviderHTTPError,
    AIProviderProtocolError,
    OpenAICompatibleProvider,
)
from src.ai.provider_base import AudioInput, CompletionRequest, VisionRequest
from src.key_manager import ServiceType


class FakeResponse:
    status_code = 200

    def json(self):
        return {
            "choices": [{"message": {"role": "assistant", "content": "Merhaba"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        }


class RetryableResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self.payload = payload

    def json(self):
        return self.payload


class FakeClient:
    async def post(self, url, *, json, headers, timeout):
        assert url.endswith("/chat/completions")
        assert json["model"] == "demo-model"
        assert headers["Authorization"] == "Bearer secret"
        return FakeResponse()


class MultiModalResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class MultiModalClient:
    def __init__(self):
        self.calls = []

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url.endswith("/chat/completions"):
            return MultiModalResponse({"choices": [{"message": {"content": "Görselde bir kedi var."}}]})
        if url.endswith("/audio/transcriptions"):
            assert kwargs["files"]["file"][1] == b"audio-data"
            return MultiModalResponse({"text": "Türkçe ses metni"})
        return MultiModalResponse({
            "data": [
                {"index": 1, "embedding": [0.2, 0.3]},
                {"index": 0, "embedding": [0.1, 0.4]},
            ]
        })


class FakeKeys:
    keys = {ServiceType.GROQ: ["secret"]}

    def get_key(self, service):
        return "secret"

    def report_success(self, service, key):
        self.success = (service, key)

    def report_error(self, service, key, message):
        raise AssertionError(message)


@pytest.mark.asyncio
async def test_openai_compatible_provider_normalizes_completion_and_usage():
    from src.ai.usage_tracker import UsageTracker

    usage_tracker = UsageTracker(rates={"groq": {"input": 0.2, "output": 0.8}})
    provider = OpenAICompatibleProvider(
        service=ServiceType.GROQ,
        base_url="https://example.test/v1",
        model="demo-model",
        key_manager=FakeKeys(),
        http_client=FakeClient(),
        usage_tracker=usage_tracker,
    )

    response = await provider.complete(CompletionRequest(messages=[{"role": "user", "content": "Selam"}]))

    assert response.content == "Merhaba"
    assert response.provider == "groq"
    assert response.usage == {"prompt_tokens": 3, "completion_tokens": 2}
    assert response.raw["message"]["role"] == "assistant"
    assert "key" not in response.raw
    snapshot = await usage_tracker.snapshot()
    assert snapshot["groq"]["input_tokens"] == 3
    assert snapshot["groq"]["output_tokens"] == 2


@pytest.mark.asyncio
async def test_single_route_retries_transient_upstream_once():
    class FlakyClient:
        def __init__(self):
            self.calls = 0

        async def post(self, url, *, json, headers, timeout):
            self.calls += 1
            if self.calls == 1:
                return RetryableResponse(503, {"error": {"message": "temporary"}})
            return FakeResponse()

    client = FlakyClient()
    provider = OpenAICompatibleProvider(
        service=ServiceType.GROQ,
        base_url="https://example.test/v1",
        model="demo-model",
        key_manager=FakeKeys(),
        http_client=client,
        retry_attempts=1,
    )

    response = await provider.complete(CompletionRequest(messages=[{"role": "user", "content": "Selam"}]))

    assert response.content == "Merhaba"
    assert client.calls == 2


@pytest.mark.asyncio
async def test_fast_completion_uses_bounded_fast_timeout():
    class Client:
        def __init__(self):
            self.timeout = None

        async def post(self, url, *, json, headers, timeout):
            self.timeout = timeout
            return FakeResponse()

    client = Client()
    provider = OpenAICompatibleProvider(
        service=ServiceType.GROQ,
        base_url="https://example.test/v1",
        model="demo-model",
        key_manager=FakeKeys(),
        http_client=client,
        timeout_seconds=20,
        fast_timeout_seconds=8,
    )
    await provider.complete(CompletionRequest(
        messages=[{"role": "user", "content": "Selam"}],
        task="fast",
    ))
    assert client.timeout == 8


@pytest.mark.asyncio
async def test_transient_transport_failure_does_not_cool_down_valid_key():
    class Keys:
        keys = {ServiceType.GROQ: ["valid-key"]}

        def __init__(self):
            self.errors = []

        def get_key(self, service):
            return "valid-key"

        def report_success(self, service, key):
            pass

        def report_error(self, service, key, message):
            self.errors.append((service, key, message))

    class TimeoutClient:
        async def post(self, *args, **kwargs):
            raise TimeoutError("upstream read timeout")

    keys = Keys()
    provider = OpenAICompatibleProvider(
        service=ServiceType.GROQ,
        base_url="https://example.test/v1",
        model="demo-model",
        key_manager=keys,
        http_client=TimeoutClient(),
        retry_attempts=0,
    )

    with pytest.raises(TimeoutError):
        await provider.complete(CompletionRequest(messages=[{"role": "user", "content": "Selam"}]))

    assert keys.errors == []


@pytest.mark.asyncio
async def test_completion_forwards_structured_response_format():
    class Client:
        def __init__(self):
            self.payload = None

        async def post(self, url, *, json, headers, timeout):
            self.payload = json
            return FakeResponse()

    client = Client()
    provider = OpenAICompatibleProvider(
        service=ServiceType.GROQ,
        base_url="https://example.test/v1",
        model="demo-model",
        key_manager=FakeKeys(),
        http_client=client,
    )

    await provider.complete(CompletionRequest(
        messages=[{"role": "user", "content": "plan"}],
        response_format={"type": "json_object"},
    ))

    assert client.payload["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_openai_compatible_provider_supports_vision_transcription_and_embeddings():
    client = MultiModalClient()
    keys = FakeKeys()
    provider = OpenAICompatibleProvider(
        service=ServiceType.GROQ,
        base_url="https://example.test/v1",
        model="demo-model",
        key_manager=keys,
        http_client=client,
    )

    vision = await provider.vision(VisionRequest(prompt="Ne görüyorsun?", image_bytes=b"image"))
    transcript = await provider.transcribe(AudioInput(audio_bytes=b"audio-data"))
    vectors = await provider.embed(["bir", "iki"])

    assert vision.content == "Görselde bir kedi var."
    assert transcript.text == "Türkçe ses metni"
    assert vectors == [[0.1, 0.4], [0.2, 0.3]]
    assert [url.rsplit("/", 1)[-1] for url, _ in client.calls] == [
        "completions", "transcriptions", "embeddings"
    ]


@pytest.mark.asyncio
async def test_vision_uses_the_same_configured_completion_budget():
    client = MultiModalClient()
    provider = OpenAICompatibleProvider(
        service=ServiceType.GROQ,
        base_url="https://example.test/v1",
        model="demo-model",
        key_manager=FakeKeys(),
        http_client=client,
        max_completion_tokens=128,
    )

    await provider.vision(VisionRequest(prompt="Ne görüyorsun?", image_bytes=b"image", max_tokens=999))
    assert client.calls[0][1]["json"]["max_tokens"] == 128


@pytest.mark.asyncio
async def test_evren_vision_uses_mimo_model_and_disables_reasoning():
    class Keys:
        keys = {ServiceType.CLEANAPIS: ["fixture-evren-secret"]}

        def get_key(self, service):
            return "fixture-evren-secret"

        def report_success(self, service, key):
            pass

        def report_error(self, service, key, message):
            raise AssertionError(message)

    class Client:
        async def post(self, url, *, json, headers, timeout):
            assert json["model"] == "mimo-v2.6-pro"
            assert json["reasoning_effort"] == "none"
            assert "thinking" not in json
            return MultiModalResponse({"choices": [{"message": {"content": "G\u00f6rsel analizi"}}]})

    provider = OpenAICompatibleProvider(
        service=ServiceType.CLEANAPIS,
        base_url="https://evren.example/v1",
        model="deepseek-v4.1-flash",
        vision_model="mimo-v2.6-pro",
        key_manager=Keys(),
        http_client=Client(),
    )
    response = await provider.vision(VisionRequest(prompt="Ne var?", image_bytes=b"image"))
    assert response.model == "mimo-v2.6-pro"
    assert response.content == "G\u00f6rsel analizi"


@pytest.mark.asyncio
async def test_cleanapis_uses_exact_model_endpoint_and_never_returns_secret():
    class Keys:
        keys = {ServiceType.CLEANAPIS: ["fixture-cleanapis-secret"]}

        def get_key(self, service):
            return "fixture-cleanapis-secret"

        def report_success(self, service, key):
            self.success = (service, key)

        def report_error(self, service, key, message):
            raise AssertionError(message)

    class Client:
        async def post(self, url, *, json, headers, timeout):
            assert url == "https://cleanapis.com/v1/chat/completions"
            assert json["model"] == "deepseek-v4-flash-0731"
            assert json["reasoning_effort"] == "none"
            assert json["thinking"] == {"type": "disabled"}
            assert headers["Authorization"] == "Bearer fixture-cleanapis-secret"
            return MultiModalResponse({
                "choices": [{"message": {"role": "assistant", "content": "Haz\u0131r\u0131m."}}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2},
            })

    provider = OpenAICompatibleProvider(
        service=ServiceType.CLEANAPIS,
        base_url="https://cleanapis.com/v1",
        model="deepseek-v4-flash-0731",
        key_manager=Keys(),
        http_client=Client(),
    )
    response = await provider.complete(CompletionRequest(
        messages=[{"role": "user", "content": "Arelbot haz\u0131r m\u0131?"}],
        reasoning_effort="none",
        thinking_enabled=False,
    ))

    assert response.content == "Haz\u0131r\u0131m."
    assert response.provider == "cleanapis"
    assert response.model == "deepseek-v4-flash-0731"
    assert "fixture-cleanapis-secret" not in repr(response.raw)


@pytest.mark.asyncio
async def test_completion_forwards_bounded_max_tokens_and_rejects_oversized_messages():
    class Client:
        def __init__(self):
            self.payload = None

        async def post(self, url, *, json, headers, timeout):
            self.payload = json
            return FakeResponse()

    client = Client()
    provider = OpenAICompatibleProvider(
        service=ServiceType.GROQ,
        base_url="https://example.test/v1",
        model="demo-model",
        key_manager=FakeKeys(),
        http_client=client,
        max_completion_tokens=512,
        max_message_chars=100,
    )

    response = await provider.complete(
        CompletionRequest(
            messages=[{"role": "user", "content": "Selam"}],
            max_tokens=9999,
        )
    )
    assert response.content == "Merhaba"
    assert client.payload["max_tokens"] == 512

    with pytest.raises(ValueError, match="AI_MESSAGE_TOO_LARGE"):
        await provider.complete(CompletionRequest(messages=[{"role": "user", "content": "x" * 2_000}]))


def test_openai_compatible_provider_rejects_unsafe_endpoint_urls():
    for url in (
        "http://example.test/v1",
        "https://localhost/v1",
        "https://user:password@example.test/v1",
        "https://example.test/v1?token=secret",
    ):
        with pytest.raises(AIProviderConfigurationError, match="AI_PROVIDER_URL_INVALID"):
            OpenAICompatibleProvider(
                service=ServiceType.GROQ,
                base_url=url,
                model="demo-model",
                key_manager=FakeKeys(),
                http_client=FakeClient(),
            )


@pytest.mark.asyncio
async def test_completion_rejects_model_override_outside_provider_allowlist():
    provider = OpenAICompatibleProvider(
        service=ServiceType.GROQ,
        base_url="https://example.test/v1",
        model="demo-model",
        key_manager=FakeKeys(),
        http_client=FakeClient(),
    )
    with pytest.raises(ValueError, match="AI_MODEL_NOT_ALLOWED"):
        await provider.complete(
            CompletionRequest(
                messages=[{"role": "user", "content": "Selam"}],
                model="untrusted-user-model",
            )
        )


@pytest.mark.asyncio
async def test_completion_rejects_invalid_typed_response():
    class InvalidClient:
        async def post(self, url, *, json, headers, timeout):
            return MultiModalResponse({"choices": []})

    class Keys(FakeKeys):
        def report_error(self, service, key, message):
            self.error = (service, key, message)

    provider = OpenAICompatibleProvider(
        service=ServiceType.GROQ,
        base_url="https://example.test/v1",
        model="demo-model",
        key_manager=Keys(),
        http_client=InvalidClient(),
    )
    with pytest.raises(AIProviderProtocolError, match="AI_RESPONSE_CHOICES_INVALID"):
        await provider.complete(CompletionRequest(messages=[{"role": "user", "content": "Selam"}]))


@pytest.mark.asyncio
async def test_transient_rate_limit_does_not_report_quota_exhaustion():
    class Keys:
        keys = {ServiceType.GROQ: ["secret"]}

        def __init__(self):
            self.error = None

        def get_key(self, service):
            return "secret"

        def report_success(self, service, key):
            pass

        def report_error(self, service, key, message):
            self.error = message

    class Client:
        async def post(self, url, *, json, headers, timeout):
            return RetryableResponse(429, {"error": {"message": "temporarily rate limited"}})

    keys = Keys()
    provider = OpenAICompatibleProvider(
        service=ServiceType.GROQ,
        base_url="https://example.test/v1",
        model="demo-model",
        key_manager=keys,
        http_client=Client(),
    )
    with pytest.raises(AIProviderHTTPError) as caught:
        await provider.complete(CompletionRequest(messages=[{"role": "user", "content": "Selam"}]))
    assert caught.value.status_code == 429
    assert caught.value.quota_exhausted is False
    assert keys.error == "upstream_rate_limited"


@pytest.mark.asyncio
async def test_transport_timeout_is_retried_for_single_route():
    class FlakyClient:
        def __init__(self):
            self.calls = 0

        async def post(self, url, *, json, headers, timeout):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("upstream timeout")
            return FakeResponse()

    client = FlakyClient()
    provider = OpenAICompatibleProvider(
        service=ServiceType.GROQ,
        base_url="https://example.test/v1",
        model="demo-model",
        key_manager=FakeKeys(),
        http_client=client,
        retry_attempts=1,
    )
    response = await provider.complete(CompletionRequest(messages=[{"role": "user", "content": "Selam"}]))
    assert response.content == "Merhaba"
    assert client.calls == 2
