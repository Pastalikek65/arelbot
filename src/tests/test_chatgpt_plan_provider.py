import json
import os
import time

import pytest

from src.ai.chatgpt_plan import (
    PLAN_SCOPE,
    RESOURCE_SCOPE,
    ChatGPTPlanCredentials,
    ChatGPTPlanError,
    ChatGPTPlanProvider,
    _atomic_write_record,
    _message_from_response,
    _response_input,
)
from src.ai.provider_base import CompletionRequest
from src.ai.router import AIProviderRouter
from src.key_manager import ServiceType


def _credentials(path, *, expires_at=None):
    record = {
        "version": 1,
        "client_id": "oaiapp_test",
        "ext_agent_host_id": "urn:uuid:test-host",
        "subject": "opaque-subject",
        "access_token": "access-secret",
        "refresh_token": "refresh-secret",
        "id_token": "id-secret",
        "scopes": [PLAN_SCOPE, RESOURCE_SCOPE],
        "expires_at": expires_at or time.time() + 3600,
    }
    _atomic_write_record(path, record)
    return record


class FakeStreamResponse:
    def __init__(self, lines, status_code=200, error_body=b""):
        self.status_code = status_code
        self.lines = lines
        self.error_body = error_body

    async def aiter_lines(self):
        for line in self.lines:
            yield line

    async def aread(self):
        return self.error_body


class FakeStreamContext:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *_):
        return False


class FakeClient:
    def __init__(self, response=None, *, refresh_response=None):
        self.response = response
        self.refresh_response = refresh_response
        self.calls = []
        self.refresh_calls = 0

    def stream(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return FakeStreamContext(self.response)

    async def post(self, url, **kwargs):
        self.refresh_calls += 1
        return self.refresh_response


def _sse(event):
    return "data: " + json.dumps(event, ensure_ascii=False)


@pytest.mark.asyncio
async def test_chatgpt_plan_streams_store_false_and_normalizes_completed_text(tmp_path):
    path = tmp_path / "credentials.json"
    _credentials(path)
    response_data = {
        "status": "completed",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "Merhaba!"}]}],
        "usage": {"input_tokens": 6, "output_tokens": 2},
    }
    client = FakeClient(FakeStreamResponse([_sse({"type": "response.completed", "response": response_data})]))
    provider = ChatGPTPlanProvider(
        model="gpt-5.6-luna", credentials_path=path, http_client=client
    )

    result = await provider.complete(CompletionRequest(messages=[
        {"role": "system", "content": "Türkçe yanıt ver."},
        {"role": "user", "content": "Selam"},
    ], max_tokens=99))

    payload = client.calls[0][2]["json"]
    assert payload["store"] is False
    assert payload["stream"] is True
    assert payload["instructions"] == "Türkçe yanıt ver."
    assert payload["input"] == [{"role": "user", "content": "Selam"}]
    assert "temperature" not in payload
    assert "max_output_tokens" not in payload
    assert result.content == "Merhaba!"
    assert result.usage == {"prompt_tokens": 6, "completion_tokens": 2}
    assert result.raw["service"] is ServiceType.OPENAI
    assert "access-secret" not in str(result.raw)


@pytest.mark.asyncio
async def test_chatgpt_plan_uses_namespace_tools_and_roundtrips_tool_items(tmp_path):
    path = tmp_path / "credentials.json"
    _credentials(path)
    response_data = {
        "output": [{
            "type": "function_call",
            "call_id": "call-1",
            "name": "get_weather",
            "namespace": "arel",
            "arguments": '{"city":"İstanbul"}',
        }],
        "usage": {"input_tokens": 20, "output_tokens": 3},
    }
    client = FakeClient(FakeStreamResponse([_sse({"type": "response.completed", "response": response_data})]))
    provider = ChatGPTPlanProvider(model="gpt-5.6-luna", credentials_path=path, http_client=client)
    tools = [{"type": "function", "function": {
        "name": "get_weather",
        "description": "Hava durumunu getir.",
        "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
    }}]

    result = await provider.complete(CompletionRequest(
        messages=[{"role": "user", "content": "İstanbul hava durumu"}], tools=tools
    ))

    payload = client.calls[0][2]["json"]
    assert payload["tools"][0]["type"] == "namespace"
    assert payload["tools"][0]["name"] == "arel"
    assert payload["tools"][0]["tools"][0]["name"] == "get_weather"
    assert result.raw["message"]["tool_calls"][0]["namespace"] == "arel"
    history = [
        {"role": "assistant", "content": "", "tool_calls": result.raw["message"]["tool_calls"]},
        {"role": "tool", "tool_call_id": "call-1", "name": "get_weather", "content": '{"ok":true}'},
    ]
    _, input_items = _response_input(history)
    assert input_items == [
        {"type": "function_call", "call_id": "call-1", "name": "get_weather", "arguments": '{"city":"İstanbul"}', "namespace": "arel"},
        {"type": "function_call_output", "call_id": "call-1", "output": '{"ok":true}', "namespace": "arel"},
    ]


@pytest.mark.asyncio
async def test_quota_failure_after_stream_start_is_not_treated_as_success(tmp_path):
    path = tmp_path / "credentials.json"
    _credentials(path)
    lines = [
        _sse({"type": "response.output_text.delta", "delta": "partial answer"}),
        _sse({"type": "response.failed", "response": {"error": {"code": "subscription_sharing_usage_limit_exceeded"}}}),
    ]
    client = FakeClient(FakeStreamResponse(lines))
    provider = ChatGPTPlanProvider(model="gpt-5.6-luna", credentials_path=path, http_client=client)

    with pytest.raises(ChatGPTPlanError) as error:
        await provider.complete(CompletionRequest(messages=[{"role": "user", "content": "test"}]))

    assert error.value.safe_code == "subscription_sharing_usage_limit_exceeded"
    assert error.value.circuit_cooldown_seconds == 3600


@pytest.mark.asyncio
async def test_token_refresh_serializes_and_persists_rotated_refresh_token(tmp_path):
    path = tmp_path / "credentials.json"
    _credentials(path, expires_at=time.time() - 1)

    class TokenResponse:
        status_code = 200

        def json(self):
            return {
                "access_token": "access-new",
                "refresh_token": "refresh-new",
                "expires_in": 3600,
                "scope": f"{PLAN_SCOPE} {RESOURCE_SCOPE}",
            }

    client = FakeClient(refresh_response=TokenResponse())
    credentials = ChatGPTPlanCredentials(path, client)
    token = await credentials.access_token()
    saved = json.loads(path.read_text(encoding="utf-8"))

    assert token == "access-new"
    assert client.refresh_calls == 1
    assert saved["refresh_token"] == "refresh-new"
    assert saved["access_token"] == "access-new"
    if os.name != "nt":
        assert path.stat().st_mode & 0o077 == 0


@pytest.mark.asyncio
async def test_router_opens_quota_circuit_immediately_and_falls_back():
    class QuotaProvider:
        name = "openai"

        async def complete(self, request):
            raise ChatGPTPlanError(
                "subscription_sharing_usage_limit_exceeded",
                circuit_cooldown_seconds=3600,
            )

    class HealthyProvider:
        name = "evren"

        async def complete(self, request):
            from src.ai.provider_base import CompletionResponse
            return CompletionResponse("fallback", "evren", "test")

    router = AIProviderRouter(
        [QuotaProvider(), HealthyProvider()],
        routes={"chat": ["openai", "evren"]},
        failure_threshold=10,
    )

    result = await router.complete(CompletionRequest(messages=[{"role": "user", "content": "test"}]))

    assert result.content == "fallback"
    assert router._opened_until["openai"] > time.monotonic() + 3500


def test_response_output_parser_rejects_invalid_function_json():
    with pytest.raises(ChatGPTPlanError, match="response_tool_call_invalid"):
        _message_from_response({"output": [{
            "type": "function_call", "call_id": "call", "name": "tool", "arguments": "not json"
        }]})


@pytest.mark.asyncio
async def test_only_model_unavailability_selects_luna_fallback(tmp_path):
    path = tmp_path / "credentials.json"
    _credentials(path)
    provider = ChatGPTPlanProvider(model="gpt-5.6-luna", credentials_path=path, http_client=FakeClient())
    from unittest.mock import AsyncMock
    provider._stream_response = AsyncMock(side_effect=[
        ChatGPTPlanError("model_not_found", status_code=404),
        ({"content": "Merhaba"}, {"prompt_tokens": 5, "completion_tokens": 1}),
    ])
    result = await provider.complete(CompletionRequest(messages=[{"role": "user", "content": "selam"}]))
    assert result.model == "gpt-6-luna"
    assert [c.args[0] for c in provider._stream_response.call_args_list] == ["gpt-5.6-luna", "gpt-6-luna"]
    assert provider._stream_response.call_args_list[0].args[2]["model"] == "gpt-5.6-luna"
    assert provider._stream_response.call_args_list[1].args[2]["reasoning"] == {"effort": "none"}


@pytest.mark.asyncio
async def test_permission_denial_never_switches_client_or_model(tmp_path):
    from unittest.mock import AsyncMock
    path = tmp_path / "credentials.json"
    _credentials(path)
    provider = ChatGPTPlanProvider(model="gpt-5.6-luna", credentials_path=path, http_client=FakeClient())
    provider._stream_response = AsyncMock(side_effect=ChatGPTPlanError("provider_http_403", status_code=403))
    with pytest.raises(ChatGPTPlanError):
        await provider.complete(CompletionRequest(messages=[{"role": "user", "content": "selam"}]))
    assert provider._stream_response.await_count == 1


def test_unapproved_model_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="MODEL_NOT_ALLOWED"):
        ChatGPTPlanProvider(model="gpt-6.1-sol", credentials_path=tmp_path / 'credentials.json', http_client=FakeClient())
