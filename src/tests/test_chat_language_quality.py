from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.ai.conversation_style import chat_style_instruction
from src.ai.personality import PersonalityPolicy
from src.ai.response_language import has_unexpected_script
from src.chat_engine import ChatEngine


def _chat_engine(reply='Merhaba.'):
    engine = ChatEngine.__new__(ChatEngine)
    engine.locks = {}
    engine.personality_policy = PersonalityPolicy()
    engine.get_chat_history = AsyncMock(return_value=[])
    engine.record_message = AsyncMock()
    engine._should_attach_tools = lambda *args, **kwargs: False
    engine._request_with_failover = AsyncMock(return_value={
        'provider': 'openai', 'service': None, 'key': None, 'url': None,
        'model': 'gpt-5.6-luna', 'timeout': 8, 'message': {'content': reply},
    })
    engine.ai_router = SimpleNamespace(complete=AsyncMock())
    engine.tool_manager = SimpleNamespace()
    return engine


@pytest.mark.parametrize('reply', [
    'uykun bölünmeden dinlenmişsindir，希望',
    'o portu kontrol et. એટલે ayarları değiştirmeden servise bak.',
    'çok güzel bir gün Привет',
])
def test_observed_foreign_prose_is_rejected(reply):
    assert has_unexpected_script(reply, 'Arel bir şey söyle')


@pytest.mark.parametrize(('reply', 'prompt'), [
    ('iyi akşamlar 😊', 'naber'),
    ('Öğün, güç, ışık, çığ, şüphe; güzel Türkçe.', 'yaz'),
    ('`print("你好")` kullan.', 'Python kodu yaz'),
    ('```python\nprint("Привет")\n```', 'kod yaz'),
    ('https://example.org/你好 adresini aç.', 'bağlantı ver'),
    ('希望 umut demek.', '希望 ne demek?'),
    ('你好', 'Merhaba kelimesini Çinceye çevir.'),
    ('你好', 'Merhaba kelimesini Çinceye çevir, açıklama ekleme.'),
    ('Привет', 'Rusça kısa bir selam yaz.'),
    ('Привет', 'Merhaba ifadesinin Rusçası nedir?'),
    ('こんばんは', 'İyi akşamlar ifadesinin Japoncası nedir?'),
])
def test_deliberate_language_and_code_are_preserved(reply, prompt):
    assert not has_unexpected_script(reply, prompt)


def test_foreign_language_prohibition_does_not_disable_check():
    assert has_unexpected_script('iyi akşamlar 希望', 'Çince kelime ekleme, Türkçe yaz.')
    assert has_unexpected_script('希望', 'Çince yanıt yazma.')
    assert has_unexpected_script('希望', 'Çince ne demek?')
    assert has_unexpected_script('希望', 'Çince metni Türkçeye çevir.')


def test_retired_modes_all_receive_the_single_normal_contract():
    p = PersonalityPolicy()
    prompts = [chat_style_instruction(p.for_mode(mode), mode, 'işi erteledim', 0)
               for mode in ('normal', 'serious', 'balanced', 'needle', None)]
    assert all(text.count('AREL NORMAL SOHBET STİLİ') == 1 for text in prompts)
    assert len(set(prompts)) == 1
    assert 'yakın bir arkadaş' in prompts[0]
    assert 'Şakacı, esprili ve kinayeli' in prompts[0]


def test_roast_card_is_kept_verbatim():
    p = PersonalityPolicy().for_mode('roast')
    assert p.system_instruction() in chat_style_instruction(p, 'roast', 'işi erteledim', 0)


@pytest.mark.asyncio
async def test_clean_reply_never_spends_an_extra_model_call():
    engine = ChatEngine.__new__(ChatEngine)
    engine.ai_router = SimpleNamespace(complete=AsyncMock())
    assert await engine._ensure_reply_language('Merhaba.', 'selam', 'sys', 'gpt-5.6-luna', 'openai') == 'Merhaba.'
    engine.ai_router.complete.assert_not_called()


@pytest.mark.asyncio
async def test_language_repair_pins_original_provider_and_model():
    engine = ChatEngine.__new__(ChatEngine)
    engine.ai_router = SimpleNamespace(complete=AsyncMock(return_value=SimpleNamespace(content='İyi dinlenmişsindir.')))
    result = await engine._ensure_reply_language('iyi dinlenmişsindir，希望', 'uyuyakaldım', 'sys', 'gpt-5.6-luna', 'openai')
    assert result == 'İyi dinlenmişsindir.'
    request = engine.ai_router.complete.call_args.args[0]
    assert request.model == 'gpt-5.6-luna'
    assert request.tools == []
    assert engine.ai_router.complete.call_args.kwargs['provider_names'] == ['openai']


@pytest.mark.asyncio
async def test_invalid_repair_is_not_sent_and_never_loops():
    engine = ChatEngine.__new__(ChatEngine)
    engine.ai_router = SimpleNamespace(complete=AsyncMock(return_value=SimpleNamespace(content='希望')))
    result = await engine._ensure_reply_language('iyi günler 希望', 'naber', 'sys', 'gpt-5.6-luna', 'openai')
    assert not has_unexpected_script(result, 'naber')
    assert 'yeniden' in result
    assert engine.ai_router.complete.await_count == 1


@pytest.mark.asyncio
async def test_failed_language_repair_is_bounded():
    engine = ChatEngine.__new__(ChatEngine)
    engine.ai_router = SimpleNamespace(complete=AsyncMock(side_effect=TimeoutError))
    result = await engine._ensure_reply_language('iyi günler 希望', 'naber', 'sys', 'gpt-5.6-luna', 'openai')
    assert not has_unexpected_script(result, 'naber')


@pytest.mark.asyncio
async def test_runtime_repairs_before_persisting_or_returning():
    engine = _chat_engine('iyi dinlenmişsindir，希望')
    engine.ai_router.complete.return_value = SimpleNamespace(content='İyi dinlenmişsindir.')
    result = await engine.get_response(42, 'uyuyakaldım', persona_mode='normal')
    assert result == 'İyi dinlenmişsindir.'
    assert engine.record_message.call_args.args[3] == result
    assert engine._request_with_failover.await_count == 1
    assert engine.ai_router.complete.await_count == 1


@pytest.mark.asyncio
async def test_history_and_search_start_together(monkeypatch):
    import asyncio
    monkeypatch.setattr('src.chat_engine._requires_live_web_search', lambda text: True)
    engine = _chat_engine()
    history_started, search_started = asyncio.Event(), asyncio.Event()

    async def history(session):
        history_started.set()
        await search_started.wait()
        return []

    async def search(prompt):
        search_started.set()
        await history_started.wait()

    engine.get_chat_history = history
    engine._prefetch_live_search = search
    assert await asyncio.wait_for(engine.get_response(42, 'güncel veri', persist_conversation=False), timeout=1) == 'Merhaba.'


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['normal', 'serious', 'balanced', 'needle'])
async def test_real_chat_request_has_only_selected_style(mode):
    engine = _chat_engine()
    await engine.get_response(42, 'işi erteledim', persona_mode=mode, persist_conversation=False)
    system = engine._request_with_failover.call_args.args[0][0]['content']
    assert system.count('AREL NORMAL SOHBET STİLİ') == 1


def test_explicit_openai_only_route_never_falls_back_to_other_providers(monkeypatch):
    from config import Config
    from src.key_manager import ServiceType
    monkeypatch.setattr(Config, 'OPENAI_CHATGPT_ENABLED', False)
    monkeypatch.setattr(Config, 'AI_PROVIDER_ROUTES', {t: ['openai'] for t in ('chat','fast','summary','analysis')})
    engine = ChatEngine.__new__(ChatEngine)
    engine.key_manager = SimpleNamespace(keys={ServiceType.CLOUDFLARE: ['fixture']})
    engine.http_client = SimpleNamespace()
    router = engine._build_ai_router()
    assert all(router.routes[t] == [] for t in ('chat','fast','summary','analysis'))


def test_codex_cli_mode_registers_as_primary_and_can_fail_over(monkeypatch):
    from config import Config
    monkeypatch.setattr(Config, 'OPENAI_CHATGPT_ENABLED', True)
    monkeypatch.setattr(Config, 'OPENAI_CHATGPT_MODE', 'cli')
    engine = ChatEngine.__new__(ChatEngine)
    engine.key_manager = SimpleNamespace(keys={})
    engine.http_client = SimpleNamespace()
    router = engine._build_ai_router()
    assert router.providers['openai'].__class__.__name__ == 'ChatGPTCliProvider'
