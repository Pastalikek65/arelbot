import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.ai.chatgpt_plan import ChatGPTCliProvider
from src.ai.personality import PersonalityPolicy
from src.ai.provider_base import CompletionRequest, CompletionResponse
from src.ai.router import AIProviderRouter
from src.chat_engine import ChatEngine
from src.key_manager import ServiceType


def engine(router):
    e = ChatEngine.__new__(ChatEngine)
    e.ai_router = router
    e.locks = {}
    e.personality_policy = PersonalityPolicy()
    e.get_chat_history = AsyncMock(return_value=[])
    e.record_message = AsyncMock()
    e._should_attach_tools = lambda *args, **kwargs: False
    e.tool_manager = SimpleNamespace()
    return e


def test_roast_prefers_proven_turkish_fallback_without_changing_normal_route():
    router = AIProviderRouter(routes={'chat': ['openai', 'hrmn', 'cloudflare', 'evren', 'nvidia']})
    app = engine(router)
    assert app._roast_provider_names() == ['openai', 'hrmn', 'evren', 'cloudflare', 'nvidia']
    assert router.routes['chat'] == ['openai', 'hrmn', 'cloudflare', 'evren', 'nvidia']


@pytest.mark.asyncio
async def test_roast_response_uses_scoped_fallback_order():
    router = AIProviderRouter(routes={'chat': ['openai', 'hrmn', 'cloudflare', 'evren', 'nvidia']})
    app = engine(router)
    app._request_with_failover = AsyncMock(return_value={
        'provider':'evren', 'service':None, 'key':None, 'url':None,
        'model':'fixture', 'timeout':1, 'message':{'content':'bu plan bok gibi olmuş'},
    })
    app._ensure_reply_language = AsyncMock(side_effect=lambda text,*_args:text)
    await app.get_response(42,'yine son güne bıraktım',persona_mode='roast',
                           persist_conversation=False)
    assert app._request_with_failover.await_args.kwargs['provider_names'] == [
        'openai', 'hrmn', 'evren', 'cloudflare', 'nvidia']


@pytest.mark.asyncio
async def test_roast_capitulation_is_repaired_by_existing_turkish_provider_once():
    router = SimpleNamespace(
        routes={'chat':['openai','hrmn','evren','cloudflare','nvidia']},
        providers={'evren':object(),'cloudflare':object()},
        complete=AsyncMock(return_value=CompletionResponse(
            'Bu sikindirik erteleme planı yine boka battı.', 'evren', 'fixture')),
    )
    app = engine(router)
    app._request_with_failover = AsyncMock(return_value={
        'provider':'cloudflare', 'service':None, 'key':None, 'url':None,
        'model':'fixture', 'timeout':1,
        'message':{'content':'Anladım, daha sakin bir şekilde yanıtlayayım. İşleri parçalara böl.'},
    })
    app._ensure_reply_language = AsyncMock(side_effect=lambda text,*_args:text)
    reply = await app.get_response(42,'Böyle konuşma, yine son güne bıraktım',
                                   persona_mode='roast',persist_conversation=False)
    assert 'sikindirik' in reply
    router.complete.assert_awaited_once()
    assert router.complete.await_args.kwargs['provider_names'] == ['evren']
    repair_request = router.complete.await_args.args[0]
    assert repair_request.max_tokens <= 160
    assert len(repair_request.messages[0]['content']) < 1000
    assert 'daha sakin' not in repair_request.messages[1]['content']


@pytest.mark.asyncio
@pytest.mark.parametrize('replacement', ['Siktiriboktanlık olmuş.', None])
async def test_roast_repair_rejects_bad_rewrite_and_drops_capitulation_prefix(replacement):
    async def complete(_request, provider_names=None):
        assert provider_names == ['evren']
        if replacement is None:
            raise TimeoutError('fixture timeout')
        return CompletionResponse(replacement, 'evren', 'fixture')

    app = engine(SimpleNamespace(providers={'evren':object()},complete=complete))
    answer = await app._repair_roast_reply(
        'Anladım, daha sakin bir şekilde yanıtlayayım. İşleri küçük parçalara böl.',
        'Böyle konuşma, yine son güne bıraktım',
        'Seçili roast modu geçerli.')
    assert answer == 'İşleri küçük parçalara böl.'


@pytest.mark.asyncio
async def test_tool_follow_up_uses_other_providers_when_first_completion_provider_times_out():
    calls = []

    async def openai(request):
        calls.append(('openai',request.model))
        raise RuntimeError('cli_tools_unsupported')

    async def hrmn(request):
        calls.append(('hrmn',request.model))
        if any(item.get('role') == 'tool' for item in request.messages):
            raise TimeoutError('follow-up timed out')
        return CompletionResponse('', 'hrmn', 'fixture-model', raw={
            'service': ServiceType.HRMN,
            'message': {'content': '', 'tool_calls': [
                {'id':'call-1','type':'function','function':{'name':'web_search','arguments':'{}'}}
            ]},
        })

    async def nvidia(request):
        calls.append(('nvidia',request.model))
        return CompletionResponse('yedek cevap geldi', 'nvidia', 'fixture-nvidia')

    router = AIProviderRouter([
        SimpleNamespace(name='openai',complete=openai),
        SimpleNamespace(name='hrmn',complete=hrmn),
        SimpleNamespace(name='nvidia',complete=nvidia),
    ],routes={'chat':['openai','hrmn','nvidia']},request_timeout_seconds=1)
    app = engine(router)
    app._should_attach_tools = lambda *_args, **_kwargs: True
    app.tool_manager = SimpleNamespace(get_openai_tools_def=lambda:[{'type':'function'}],
                                       execute_tool=AsyncMock(return_value={'ok':True}))
    app._ensure_reply_language = AsyncMock(side_effect=lambda text,*_args:text)
    reply = await app.get_response(42,'bana bir bilgi bul',user_id=7,
                                   persona_mode='normal',persist_conversation=False)
    assert reply == 'yedek cevap geldi'
    assert [name for name,_model in calls] == ['openai','hrmn','openai','hrmn','nvidia']
    assert all(model is None for name,model in calls[2:])
    app.tool_manager.execute_tool.assert_awaited_once()


@pytest.mark.asyncio
async def test_ordinary_chat_does_not_starve_the_preferred_provider():
    seen = []

    async def primary(request):
        seen.append(request.task)
        await asyncio.sleep(0.04)
        return CompletionResponse('iyidir ya sen nasılsın', 'openai', 'gpt-5.6-luna')

    fallback = AsyncMock(return_value=CompletionResponse('yedek', 'hrmn', 'fixture'))
    router = AIProviderRouter([
        SimpleNamespace(name='openai', complete=primary, timeout_seconds=1, fast_timeout_seconds=1),
        SimpleNamespace(name='hrmn', complete=fallback, timeout_seconds=1, fast_timeout_seconds=1),
    ], routes={'chat': ['openai', 'hrmn'], 'fast': ['openai', 'hrmn']},
        request_timeout_seconds=1, fast_timeout_seconds=0.01)
    reply = await asyncio.wait_for(engine(router).get_response(
        42, 'selam naber', persona_mode='normal', persist_conversation=False), timeout=2)
    assert reply == 'iyidir ya sen nasılsın'
    assert seen == ['chat']
    fallback.assert_not_called()


@pytest.mark.asyncio
async def test_cli_startup_uses_configured_budget_and_keeps_fallback_time():
    async def primary(_request):
        await asyncio.sleep(0.04)
        return CompletionResponse('primary', 'openai', 'gpt-5.6-luna')

    router = AIProviderRouter([
        SimpleNamespace(name='openai', complete=primary, timeout_seconds=0.5,
                        needs_startup_budget=True),
        SimpleNamespace(name='hrmn', complete=AsyncMock()),
    ], routes={'chat': ['openai', 'hrmn']}, request_timeout_seconds=0.2,
        provider_timeout_seconds=0.01)
    reply = await router.complete(CompletionRequest([{'role':'user','content':'selam'}]))
    assert reply.provider == 'openai'
    router.providers['hrmn'].complete.assert_not_called()


@pytest.mark.asyncio
async def test_primary_failure_immediately_uses_hrmn_before_other_providers():
    order = []

    async def primary(_request):
        order.append('openai')
        raise RuntimeError('fixture failure')

    async def secondary(_request):
        order.append('hrmn')
        return CompletionResponse('tamam buradayım', 'hrmn', 'fixture')

    tail = AsyncMock()
    router = AIProviderRouter([
        SimpleNamespace(name='openai', complete=primary),
        SimpleNamespace(name='hrmn', complete=secondary),
        SimpleNamespace(name='nvidia', complete=tail),
    ], routes={'chat': ['openai', 'hrmn', 'nvidia'], 'fast': ['openai','hrmn','nvidia']})
    assert await engine(router).get_response(42, 'selam', persona_mode='normal', persist_conversation=False) == 'tamam buradayım'
    assert order == ['openai', 'hrmn']
    tail.assert_not_called()


@pytest.mark.asyncio
async def test_cli_startup_timeout_leaves_a_working_http_fallback_slot():
    cancelled = asyncio.Event()

    async def stalled(_request):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    hrmn = AsyncMock(return_value=CompletionResponse('yedek çalıştı', 'hrmn', 'fixture'))
    router = AIProviderRouter([
        SimpleNamespace(name='openai', complete=stalled, timeout_seconds=1, needs_startup_budget=True),
        SimpleNamespace(name='hrmn', complete=hrmn, timeout_seconds=1),
    ], routes={'chat':['openai','hrmn']}, request_timeout_seconds=0.2,
       provider_timeout_seconds=0.05)
    reply = await asyncio.wait_for(router.complete(CompletionRequest(
        [{'role':'user','content':'selam'}])), timeout=1)
    assert reply.provider == 'hrmn'
    assert cancelled.is_set()
    hrmn.assert_awaited_once()


@pytest.mark.asyncio
async def test_two_slow_providers_do_not_use_entire_chat_budget_before_working_tail():
    async def stalled(_request):
        await asyncio.Event().wait()

    tail = AsyncMock(return_value=CompletionResponse('üçüncü sağlayıcı yanıtladı','nvidia','fixture'))
    router = AIProviderRouter([
        SimpleNamespace(name='openai',complete=stalled,timeout_seconds=1,needs_startup_budget=True),
        SimpleNamespace(name='hrmn',complete=stalled,timeout_seconds=1),
        SimpleNamespace(name='nvidia',complete=tail,timeout_seconds=1),
    ],routes={'chat':['openai','hrmn','nvidia']},request_timeout_seconds=0.3,
       provider_timeout_seconds=0.05)
    reply = await asyncio.wait_for(router.complete(CompletionRequest(
        [{'role':'user','content':'selam'}])),timeout=1)
    assert reply.provider == 'nvidia'
    tail.assert_awaited_once()


@pytest.mark.asyncio
async def test_unapproved_request_model_never_launches_cli(monkeypatch):
    from src.ai.provider_base import AIProviderRequestError
    spawn = AsyncMock()
    monkeypatch.setattr('src.ai.chatgpt_plan.shutil.which', lambda _: '/usr/bin/codex')
    monkeypatch.setattr('src.ai.chatgpt_plan.asyncio.create_subprocess_exec', spawn)
    with pytest.raises(AIProviderRequestError):
        await ChatGPTCliProvider().complete(CompletionRequest(
            [{'role':'user','content':'selam'}], model='gpt-unapproved'))
    spawn.assert_not_called()


def test_cli_default_is_an_explicit_approved_model():
    assert ChatGPTCliProvider().model == 'gpt-5.6-luna'
    with pytest.raises(ValueError):
        ChatGPTCliProvider(model='gpt-unapproved')


@pytest.mark.asyncio
async def test_router_cancellation_kills_and_reaps_the_owned_cli(monkeypatch, tmp_path):
    started = asyncio.Event()
    done = asyncio.Event()

    class Process:
        returncode = None
        killed = False
        reaped = False

        async def communicate(self, prompt=None):
            started.set()
            await done.wait()
            self.reaped = True
            return b'', b''

        def kill(self):
            self.killed = True
            self.returncode = -9
            done.set()

    process = Process()
    monkeypatch.setattr('src.ai.chatgpt_plan.shutil.which', lambda _: '/usr/bin/codex')
    monkeypatch.setattr('src.ai.chatgpt_plan.asyncio.create_subprocess_exec', AsyncMock(return_value=process))
    provider = ChatGPTCliProvider(workdir=tmp_path)
    call = asyncio.create_task(provider.complete(CompletionRequest([{'role':'user','content':'selam'}])))
    await asyncio.wait_for(started.wait(), timeout=1)
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call
    assert process.killed and process.reaped
