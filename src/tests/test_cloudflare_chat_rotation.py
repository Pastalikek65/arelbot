import asyncio
from unittest.mock import Mock

import pytest

from src.ai.providers.cloudflare_chat import CloudflareChatProvider
from src.ai.openai_compatible import AIProviderProtocolError
from src.ai.provider_base import CompletionRequest, CompletionResponse
from src.ai.router import AIProviderRouter, AIProviderUnavailable
from src.key_manager import ServiceType


class Client:
    def __init__(self, message=None, status=200):
        self.message = message if message is not None else {"content": "Merhaba"}
        self.status = status
        self.calls = []

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return Mock(status_code=self.status, json=lambda: {"choices": [{"message": self.message}]})


def adapter(client, pair=None):
    pair = pair or "a"*32 + ":fixture-value"
    keys = Mock(keys={ServiceType.CLOUDFLARE: [pair]})
    keys.get_key.return_value = pair
    return CloudflareChatProvider(model="@cf/qwen/qwen3-30b-a3b-fp8", key_manager=keys, http_client=client)


@pytest.mark.asyncio
async def test_cloudflare_splits_account_from_bearer_and_keeps_request_immutable():
    client = Client(); provider = adapter(client)
    messages = [{"role": "user", "content": "Merhaba"}]
    result = await provider.complete(CompletionRequest(messages=messages))
    url, call = client.calls[0]
    assert url == "https://api.cloudflare.com/client/v4/accounts/" + "a"*32 + "/ai/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer fixture-value"
    assert call["follow_redirects"] is False
    assert call["json"]["options"] == {"rejectIfBusy": True}
    assert call["json"]["messages"][0]["content"].endswith("/no_think")
    assert messages == [{"role": "user", "content": "Merhaba"}]
    assert result.provider == "cloudflare" and result.content == "Merhaba"


@pytest.mark.asyncio
@pytest.mark.parametrize("pair", ["bad", "bad:fixture", "a"*32+":", "a"*32+":a b"])
async def test_malformed_account_never_makes_network_request(pair):
    client=Client()
    with pytest.raises(ValueError, match="CLOUDFLARE_ACCOUNT_INVALID"):
        await adapter(client,pair).complete(CompletionRequest(messages=[{"role":"user","content":"Hi"}]))
    assert not client.calls


@pytest.mark.asyncio
async def test_empty_answer_and_invalid_json_are_failover_events_but_tools_are_valid():
    request=CompletionRequest(messages=[{"role":"user","content":"Hi"}])
    with pytest.raises(AIProviderProtocolError,match="AI_RESPONSE_EMPTY"):
        await adapter(Client({"content":""})).complete(request)
    with pytest.raises(AIProviderProtocolError,match="JSON_OBJECT_INVALID"):
        await adapter(Client()).complete(CompletionRequest(messages=request.messages,response_format={"type":"json_object"}))
    result=await adapter(Client({"content":None,"tool_calls":[{"id":"call1","type":"function","function":{"name":"weather","arguments":"{}"}}]})).complete(request)
    assert result.raw["message"]["tool_calls"][0]["id"]=="call1"
    result=await adapter(Client({"content":None,"tool_calls":[{"id":"call1","function":{"name":"weather","arguments":"{}"}}]})).complete(CompletionRequest(messages=request.messages,response_format={"type":"json_object"}))
    assert result.raw["message"]["tool_calls"]


@pytest.mark.asyncio
async def test_malformed_tool_response_falls_through_to_valid_provider():
    broken=adapter(Client({"content":None,"tool_calls":[{"id":"call1"}]}))
    good=Provider("evren")
    router=AIProviderRouter([broken,good],routes={"chat":["cloudflare","evren"]})
    result=await router.complete(CompletionRequest(messages=[{"role":"user","content":"Hi"}]))
    assert result.provider=="evren"


@pytest.mark.asyncio
async def test_tool_arguments_that_expand_beyond_consumer_bound_fail_before_success():
    arguments='{"x":['+','.join(['1e1']*4000)+']}'
    assert len(arguments)<16384
    tool={"id":"call1","function":{"name":"fixture","arguments":arguments}}
    with pytest.raises(AIProviderProtocolError,match="AI_TOOL_CALLS_INVALID"):
        await adapter(Client({"content":None,"tool_calls":[tool]})).complete(CompletionRequest(messages=[{"role":"user","content":"Hi"}]))


@pytest.mark.asyncio
async def test_non_thinking_suffix_does_not_reject_an_otherwise_valid_bounded_message():
    import json
    provider=adapter(Client());provider.max_message_chars=1000
    message={"role":"user","content":""}
    message["content"]="x"*(1000-len(json.dumps(message,ensure_ascii=False)))
    assert len(json.dumps(message))==1000
    await provider.complete(CompletionRequest(messages=[message]))
    assert provider.http_client.calls[0][1]["json"]["messages"][0]==message


class Provider:
    def __init__(self,name,fail=False,delay=0):
        self.name=name;self.fail=fail;self.delay=delay;self.calls=0;self.timeout_seconds=.02
    async def complete(self,request):
        self.calls+=1
        if self.delay:await asyncio.sleep(self.delay)
        if self.fail:raise RuntimeError("provider_http_503")
        return CompletionResponse(content="ok",provider=self.name,model="fixture")


@pytest.mark.asyncio
async def test_concurrent_requests_rotate_fairly_and_explicit_probe_stays_pinned():
    providers=[Provider(n) for n in ("cloudflare","evren","nvidia")]
    router=AIProviderRouter(providers, routes={"chat":[p.name for p in providers]},rotation_tasks={"chat"})
    request=CompletionRequest(messages=[{"role":"user","content":"Hi"}])
    results=await asyncio.gather(*(router.complete(request) for _ in range(30)))
    assert [p.calls for p in providers]==[10,10,10]
    assert {r.provider for r in results}=={p.name for p in providers}
    assert (await router.complete(request,provider_names=["evren"])).provider=="evren"


@pytest.mark.asyncio
async def test_unhealthy_provider_is_skipped_then_recovers_without_losing_fallback():
    bad=Provider("cloudflare",fail=True);good=Provider("evren")
    router=AIProviderRouter([bad,good],routes={"chat":["cloudflare","evren"]},rotation_tasks={"chat"},failure_threshold=1,cooldown_seconds=.01)
    request=CompletionRequest(messages=[{"role":"user","content":"Hi"}])
    assert (await router.complete(request)).provider=="evren"
    assert (await router.complete(request)).provider=="evren"
    assert bad.calls==1
    await asyncio.sleep(.02);bad.fail=False
    assert (await router.complete(request)).provider=="cloudflare"


@pytest.mark.asyncio
async def test_per_provider_timeout_preserves_remaining_budget_for_fallback():
    slow=Provider("cloudflare",delay=.2);fast=Provider("evren")
    router=AIProviderRouter([slow,fast],request_timeout_seconds=.1)
    request=CompletionRequest(messages=[{"role":"user","content":"Hi"}])
    result=await asyncio.wait_for(router.complete(request),timeout=.15)
    assert result.provider=="evren"
    slow.fail=True;fast.fail=True
    with pytest.raises(AIProviderUnavailable):await router.complete(request)


def test_d2api_is_retired_even_if_old_environment_enables_it(monkeypatch):
    from config import Config
    from src.chat_engine import ChatEngine
    monkeypatch.setattr(Config,"D2API_ENABLED",True);monkeypatch.setattr(Config,"D2API_KEY","fixture")
    monkeypatch.setattr(Config,"AI_PROVIDER_ROUTES",{k:["cloudflare","evren","nvidia","d2api"] for k in ("chat","fast","analysis","summary","vision","embedding","transcribe","sticker_tagging")})
    engine=ChatEngine.__new__(ChatEngine)
    engine.key_manager=Mock(keys={ServiceType.CLOUDFLARE:["a"*32+":fixture"],ServiceType.EVREN:["fixture"],ServiceType.NVIDIA:["fixture"],ServiceType.D2API:["fixture"]})
    engine.http_client=Client()
    router=engine._build_ai_router()
    assert "d2api" not in router.providers
    assert router.routes["chat"]==["cloudflare","evren","nvidia"]
    for task in ("vision","embedding","transcribe","sticker_tagging"):assert "cloudflare" not in router.routes[task]


def test_empty_route_configuration_includes_cloudflare_in_text_defaults(monkeypatch):
    from config import Config
    from src.chat_engine import ChatEngine
    monkeypatch.setattr(Config,"AI_PROVIDER_ROUTES",{})
    engine=ChatEngine.__new__(ChatEngine);engine.http_client=Client()
    engine.key_manager=Mock(keys={s:["fixture"] for s in (ServiceType.CLOUDFLARE,ServiceType.EVREN,ServiceType.NVIDIA)})
    assert engine._build_ai_router().routes["chat"]==["evren","nvidia","cloudflare"]


def test_nvidia_chat_model_override_does_not_replace_media_models(monkeypatch):
    from config import Config
    from src.chat_engine import ChatEngine
    monkeypatch.setattr(Config,"NVIDIA_MODEL","old-media-fixture")
    monkeypatch.setattr(Config,"NVIDIA_CHAT_MODEL","new-chat-fixture")
    for key in ("NVIDIA_VISION_MODEL","AI_VISION_MODEL","AI_TRANSCRIPTION_MODEL","AI_EMBEDDING_MODEL"):monkeypatch.setattr(Config,key,None)
    config=ChatEngine._service_configs()[ServiceType.NVIDIA]
    assert config["model"]=="new-chat-fixture"
    assert {config[k] for k in ("vision_model","transcription_model","embedding_model")}=={"old-media-fixture"}


@pytest.mark.asyncio
async def test_nvidia_deepseek_explicitly_disables_unrequested_reasoning():
    from src.ai.openai_compatible import OpenAICompatibleProvider
    client=Client();keys=Mock(keys={ServiceType.NVIDIA:["fixture"]});keys.get_key.return_value="fixture"
    provider=OpenAICompatibleProvider(service=ServiceType.NVIDIA,base_url="https://integrate.api.nvidia.com/v1",model="deepseek-ai/deepseek-v4.1-flash",key_manager=keys,http_client=client)
    await provider.complete(CompletionRequest(messages=[{"role":"user","content":"Hi"}]))
    payload=client.calls[0][1]["json"]
    assert payload["reasoning_effort"]=="none"
    assert payload["chat_template_kwargs"]=={"thinking":False}


@pytest.mark.asyncio
async def test_only_cloudflare_never_sends_media_or_unsplit_credentials():
    from src.ai.provider_base import AudioInput,VisionRequest
    client=Client();provider=adapter(client)
    router=AIProviderRouter([provider],routes={"chat":["cloudflare"],"vision":[],"embedding":[],"transcribe":[]})
    for call in (lambda:router.vision(VisionRequest(prompt="Hi")),lambda:router.embed(["Hi"]),lambda:router.transcribe(AudioInput(audio_bytes=b"x"))):
        with pytest.raises(AIProviderUnavailable):await call()
    for call in (lambda:provider.vision(None),lambda:provider.embed([]),lambda:provider.transcribe(None)):
        with pytest.raises(AIProviderProtocolError,match="TEXT_ONLY"):await call()
    assert not client.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(("model", "effort", "thinking", "expected"), [
    ("@cf/openai/gpt-oss-120b", None, None, "low"),
    ("@cf/openai/gpt-oss-120b", "none", False, "low"),
    ("@cf/openai/gpt-oss-120b", "max", True, "high"),
    ("@cf/openai/gpt-oss-120b", "low", True, "medium"),
    ("@cf/openai/gpt-oss-120b", "high", False, "low"),
    ("@cf/mistralai/mistral-small-3.1-24b-instruct", None, None, None),
])
async def test_non_qwen_models_preserve_user_text_and_use_their_own_reasoning_policy(model, effort, thinking, expected):
    client = Client()
    provider = adapter(client)
    provider.model = model
    provider.allowed_models = {model}
    original = [{"role": "user", "content": "Doğal Türkçeyle konuş."}]
    await provider.complete(CompletionRequest(messages=original, reasoning_effort=effort, thinking_enabled=thinking))
    payload = client.calls[0][1]["json"]
    assert payload["messages"] == original
    assert payload.get("reasoning_effort") == expected
    assert original[0]["content"] == "Doğal Türkçeyle konuş."


@pytest.mark.asyncio
async def test_explicit_non_qwen_model_override_does_not_receive_qwen_suffix():
    client = Client()
    provider = adapter(client)
    provider.allowed_models = provider.allowed_models | {"@cf/openai/gpt-oss-120b"}
    await provider.complete(CompletionRequest(messages=[{"role": "user", "content": "Merhaba"}], model="@cf/openai/gpt-oss-120b"))
    assert client.calls[0][1]["json"]["messages"][0]["content"] == "Merhaba"


@pytest.mark.asyncio
async def test_quality_primary_rotation_keeps_nvidia_as_outage_fallback():
    cf, evren, nvidia = [Provider(n) for n in ("cloudflare", "evren", "nvidia")]
    router = AIProviderRouter([cf, evren, nvidia], routes={"chat": ["cloudflare", "evren", "nvidia"]}, rotation_tasks={"chat"}, rotation_primary_count=2)
    request = CompletionRequest(messages=[{"role": "user", "content": "Merhaba"}])
    assert [(await router.complete(request)).provider for _ in range(6)] == ["cloudflare", "evren"] * 3
    assert nvidia.calls == 0
    cf.fail = evren.fail = True
    assert (await router.complete(request)).provider == "nvidia"
    assert (await router.complete(request, provider_names=["nvidia"])).provider == "nvidia"


@pytest.mark.parametrize("count", [1, 2, 20])
def test_rotation_primary_count_is_bounded_by_route_and_empty_route_stays_empty(count):
    router = AIProviderRouter([Provider("cloudflare")], routes={"chat": ["cloudflare"], "vision": []}, rotation_tasks={"chat", "vision"}, rotation_primary_count=count)
    assert router._ordered_names("chat") == ["cloudflare"]
    assert router._ordered_names("vision") == []
