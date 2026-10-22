import base64
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from PIL import Image

from config import Config
from src.ai.provider_base import CompletionRequest, CompletionResponse
from src.ai.providers.hrmn import HrmnChatProvider
from src.ai.providers.hrmn_image import HrmnImageProvider
from src.ai.providers.cloudflare_image import ImageGenerationRequest, ImageProviderError
from src.ai.router import AIProviderRouter
from src.chat_engine import ChatEngine
from src.image_router import ImageRouter
from src.key_manager import ServiceType


def keys():
    return SimpleNamespace(keys={ServiceType.HRMN: ['fixture-key']},
        get_key=lambda service: 'fixture-key', report_success=lambda *a: None,
        report_error=lambda *a: None)


def png():
    stream = io.BytesIO()
    Image.new('RGB', (64, 64), 'blue').save(stream, format='PNG')
    return stream.getvalue()


def test_hrmn_inserts_after_openai_preserving_existing_tail(monkeypatch):
    monkeypatch.setattr(Config, 'OPENAI_CHATGPT_ENABLED', True)
    monkeypatch.setattr(Config, 'OPENAI_CHATGPT_MODE', 'cli')
    monkeypatch.setattr(Config, 'AI_ROTATION_ENABLED', True)
    monkeypatch.setattr(Config, 'AI_PROVIDER_ROUTES', {
        'chat': ['openai', 'evren', 'nvidia', 'cloudflare'], 'vision': ['nvidia']})
    engine = ChatEngine.__new__(ChatEngine)
    engine.key_manager = keys()
    engine.key_manager.keys.update({s: ['fixture'] for s in
        (ServiceType.EVREN, ServiceType.NVIDIA, ServiceType.CLOUDFLARE)})
    engine.http_client = SimpleNamespace()
    router = engine._build_ai_router()
    for _ in range(3):
        assert router._ordered_names('chat') == ['openai', 'hrmn', 'evren', 'nvidia', 'cloudflare']
    assert router.routes['vision'] == ['nvidia']
    for task in ('vision', 'embedding', 'transcribe', 'sticker_tagging'):
        assert 'hrmn' not in router.routes[task]
    assert router.providers['hrmn'].retry_attempts == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [500, 429, 401, 'empty', 'timeout'])
async def test_hrmn_failure_moves_to_next_and_cools_only_hrmn(failure):
    async def handle(request):
        if failure == 'timeout':
            raise httpx.ReadTimeout('fixture')
        if failure == 'empty':
            return httpx.Response(200, json={'choices': [{'message': {'content': ''}}]})
        return httpx.Response(failure, json={'error': 'fixture'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = HrmnChatProvider(service=ServiceType.HRMN, key_manager=keys(),
            http_client=client, base_url=Config.HRMN_URL, model=Config.HRMN_CHAT_MODEL)
        fallback = SimpleNamespace(name='evren', complete=AsyncMock(
            return_value=CompletionResponse('yanıt', 'evren', 'existing')))
        router = AIProviderRouter([provider, fallback], routes={'chat': ['hrmn', 'evren']})
        result = await router.complete(CompletionRequest([{'role': 'user', 'content': 'Merhaba'}]))
        assert result.provider == 'evren'
        assert router._ordered_names('chat') == ['evren']
        fallback.complete.assert_awaited_once()


@pytest.mark.asyncio
async def test_hrmn_image_base64_and_model_are_validated():
    payload = png()
    def handle(request):
        assert request.headers['Authorization'] == 'Bearer fixture-key'
        assert request.url.path == '/v1/chat/completions'
        return httpx.Response(200, json={'data': [{'b64_json': base64.b64encode(payload).decode()}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = HrmnImageProvider(keys(), client=client)
        output = await provider.generate(ImageGenerationRequest('Mavi kare', model=Config.HRMN_IMAGE_MODELS[1]))
        try:
            assert Path(output).read_bytes() == payload
        finally:
            Path(output).unlink()


@pytest.mark.asyncio
async def test_hrmn_image_download_never_sends_api_key(monkeypatch, caplog):
    import logging
    caplog.set_level(logging.INFO, logger='httpx')
    payload = png()
    async def validate(url):
        return url
    monkeypatch.setattr('src.ai.providers.hrmn_image.validate_public_url', validate)
    def handle(request):
        if request.method == 'POST':
            return httpx.Response(200, json={'choices': [{'message': {
                'content': '![generated](https://images.example/result.png)'}}]})
        assert 'Authorization' not in request.headers
        return httpx.Response(200, content=payload)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = HrmnImageProvider(keys(), client=client)
        output = await provider.generate(ImageGenerationRequest('Kare'))
        Path(output).unlink()
    assert 'images.example' not in caplog.text
    logging.getLogger('httpx').info('existing_provider_marker')
    assert 'existing_provider_marker' in caplog.text


@pytest.mark.asyncio
async def test_hrmn_image_private_url_rejected():
    def handle(request):
        assert request.method == 'POST'
        return httpx.Response(200, json={'data': [{'url': 'https://127.0.0.1/secret'}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = HrmnImageProvider(keys(), client=client)
        with pytest.raises(ImageProviderError, match='invalid_payload'):
            await provider.generate(ImageGenerationRequest('Kare'))


@pytest.mark.asyncio
@pytest.mark.parametrize('body', [{'data': [[]]}, {'choices': []}, {'data': [{'b64_json': 'invalid!'}]}])
async def test_malformed_image_response_is_a_safe_failover_error(body):
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=body))) as client:
        provider = HrmnImageProvider(keys(), client=client)
        with pytest.raises(ImageProviderError, match='invalid_payload'):
            await provider.generate(ImageGenerationRequest('Kare'))


@pytest.mark.asyncio
async def test_image_models_fail_through_to_unchanged_cloudflare():
    router = ImageRouter(None, provider=SimpleNamespace(generate=AsyncMock(return_value='existing.png')))
    router.hrmn = SimpleNamespace(generate=AsyncMock(side_effect=ImageProviderError('image_provider_upstream')))
    assert await router.generate_image('Kare') == 'existing.png'
    assert [call.args[0].model for call in router.hrmn.generate.await_args_list] == list(Config.HRMN_IMAGE_MODELS)
    assert router.provider.generate.await_args.args[0].model == Config.CLOUDFLARE_IMAGE_MODEL


@pytest.mark.asyncio
async def test_explicit_image_model_never_silently_uses_another_model():
    router = ImageRouter(None, provider=SimpleNamespace(generate=AsyncMock(return_value='fallback.png')))
    router.hrmn = SimpleNamespace(generate=AsyncMock(side_effect=ImageProviderError('image_provider_upstream')))
    with pytest.raises(ImageProviderError, match='image_provider_upstream'):
        await router.generate_image('Mavi çaydanlık --model flare')
    assert [call.args[0].model for call in router.hrmn.generate.await_args_list] == [Config.HRMN_IMAGE_MODELS[1]]
    router.provider.generate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('request_id,expected', [('req_fixture123', 'req_fixture123'), ('secret /private/path', None)])
async def test_hrmn_http_error_has_only_safe_diagnostic_metadata(request_id, expected):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500, json={
            'error': {'message': 'no provider responded, server side error', 'request_id': request_id}}))) as client:
        provider = HrmnImageProvider(keys(), client=client)
        with pytest.raises(ImageProviderError) as raised:
            await provider.generate(ImageGenerationRequest('Kare'))
    assert raised.value.http_status == 500
    assert raised.value.request_id == expected
    assert raised.value.upstream_reason == 'no_provider_responded'
    assert str(raised.value) == 'image_provider_upstream'
