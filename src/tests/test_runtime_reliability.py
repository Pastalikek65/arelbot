"""Regressions for observed runtime failures, with no external services."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import importlib

import pytest

from scripts import healthcheck
from src.tool_manager import ToolManager
from src.tools.group_analytics_tool import GroupAnalyticsTool


def test_card_actions_import_and_have_independent_immutable_defaults():
    module = importlib.import_module("src.games.card_engine")
    first = module.CardAction(action_id="first", kind="draw", player_id=1)
    second = module.CardAction(action_id="second", kind="draw", player_id=2)
    assert dict(first.payload) == dict(second.payload) == {}
    assert first.payload is not second.payload
    with pytest.raises(TypeError):
        first.payload["count"] = 2


@pytest.mark.parametrize("probe,driver", [(healthcheck.check_mongo, "pymongo"), (healthcheck.check_redis, "redis")])
def test_protocol_failure_cannot_become_healthy_from_open_tcp(monkeypatch, probe, driver):
    module = importlib.import_module(driver)
    tcp = Mock(return_value=True)
    monkeypatch.setattr(healthcheck, "check_url", tcp)
    if driver == "pymongo":
        monkeypatch.setattr(module, "MongoClient", Mock(side_effect=RuntimeError("protocol failed")))
        uri = "mongodb://127.0.0.1:1234/fixture"
    else:
        monkeypatch.setattr(module.Redis, "from_url", Mock(side_effect=RuntimeError("protocol failed")))
        uri = "redis://127.0.0.1:1234/0"
    assert probe(uri) is False
    tcp.assert_not_called()


def analytics_manager():
    db = SimpleNamespace(get_top_users=AsyncMock(return_value=[]), get_total_messages=AsyncMock(return_value=7))
    manager = ToolManager()
    manager.register_tool(GroupAnalyticsTool(db))
    definition = SimpleNamespace(key="group_analytics", input_model="group_analytics_period", permission="group_admin", provider_key=None)
    manager.bind_feature_executor(SimpleNamespace(), SimpleNamespace(definitions=lambda: [definition]))
    return manager, db


@pytest.mark.asyncio
async def test_registered_group_analytics_rejects_non_admin_before_querying():
    manager, db = analytics_manager()
    result = await manager.execute_tool("group_analytics", {"session_id": -222, "analysis_type": "activity_summary"}, {"session_id": -111, "user_id": 1, "chat_type": "group", "is_group_admin": False})
    assert result.get("error_code") == "TOOL_PERMISSION_DENIED"
    db.get_top_users.assert_not_awaited()
    db.get_total_messages.assert_not_awaited()


@pytest.mark.asyncio
async def test_authorized_analytics_always_uses_trusted_current_chat():
    manager, db = analytics_manager()
    result = await manager.execute_tool("group_analytics", {"session_id": -222, "analysis_type": "activity_summary"}, {"session_id": -111, "user_id": 1, "chat_type": "group", "is_group_admin": True})
    assert result["total_messages"] == 7
    db.get_top_users.assert_awaited_once_with(-111, limit=5)
    db.get_total_messages.assert_awaited_once_with(-111)


@pytest.mark.asyncio
async def test_analytics_missing_chat_context_is_denied():
    manager, db = analytics_manager()
    result = await manager.execute_tool("group_analytics", {"session_id": -222, "analysis_type": "activity_summary"}, {"is_group_admin": True})
    assert result.get("error_code") == "TOOL_PERMISSION_DENIED"
    db.get_total_messages.assert_not_awaited()


@pytest.mark.parametrize("alias,expected", [("chatkurallari", "rules"), ("temizle", "purge"), ("dil", "language"), ("fed_olustur", "newfed"), ("fed_katil", "joinfed"), ("unfban", "funban")])
def test_advertised_guardian_aliases_reach_their_canonical_operation(alias, expected):
    from src.moderation.guardian_commands import GuardianCommandService
    assert GuardianCommandService.canonical(alias) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["del", "mesajisil"])
async def test_message_delete_command_reaches_authorization_instead_of_silent_return(command):
    from src.handlers.command_handler import CommandHandler
    handler = CommandHandler.__new__(CommandHandler)
    handler.guardian_surface = SimpleNamespace(
        has_custom_alias=AsyncMock(return_value=False),
        resolve_command=AsyncMock(side_effect=lambda chat, value: value),
        is_command_disabled=AsyncMock(return_value=False),
        execute=AsyncMock(return_value=None),
    )
    handler.announcement_service = None
    handler._is_group_admin = AsyncMock(return_value=False)
    handler._guardian_admin_denial_message = AsyncMock(return_value="Grup yöneticisi yetkisi gerekli.")
    message = SimpleNamespace(text=f"/{command}", reply_text=AsyncMock())
    update = SimpleNamespace(effective_message=message, effective_chat=SimpleNamespace(id=-111, type="supergroup"), effective_user=SimpleNamespace(id=1))
    await handler.handle_guardian_command(update, SimpleNamespace(args=[]))
    message.reply_text.assert_awaited_once_with("Grup yöneticisi yetkisi gerekli.")


@pytest.mark.asyncio
@pytest.mark.parametrize("authorized", [False, True])
async def test_chat_engine_preserves_verified_admin_context_at_real_tool_call(authorized):
    from src.chat_engine import ChatEngine
    from src.ai.personality import PersonalityPolicy
    from src.ai.provider_base import CompletionResponse
    engine = ChatEngine.__new__(ChatEngine)
    engine.locks = {}
    engine.personality_policy = PersonalityPolicy()
    engine.get_chat_history = AsyncMock(return_value=[])
    engine._should_attach_tools = lambda *args, **kwargs: True
    engine._request_with_failover = AsyncMock(return_value={
        "service": SimpleNamespace(value="fixture"), "key": "fixture",
        "url": "https://fixture.invalid", "model": "fixture", "timeout": 1,
        "message": {"role": "assistant", "content": None, "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "group_analytics", "arguments": '{"analysis_type":"total_messages"}'}}]},
    })
    engine.tool_manager = SimpleNamespace(get_openai_tools_def=lambda: [], execute_tool=AsyncMock(return_value={"total_messages": 7}))
    engine.ai_router = SimpleNamespace(complete=AsyncMock(return_value=CompletionResponse(content="İstatistik hazır.", provider="fixture", model="fixture")))
    await engine.get_response(-111, "grup istatistiğini çıkar", user_id=1, persist_conversation=False, is_group_admin=authorized)
    assert engine.tool_manager.execute_tool.await_args.kwargs["context"]["is_group_admin"] is authorized


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["fedapprove", "fedunapprove"])
async def test_federation_trust_commands_require_existing_federation_authority(command):
    from src.moderation.guardian_surface import GuardianSurface
    port = SimpleNamespace(get_chat_settings=AsyncMock(return_value={}), update_chat_setting_key=AsyncMock())
    result = await GuardianSurface(port).execute(command, chat_id=-111, user_id=1, args=["22"], is_admin=True)
    assert result is not None
    assert "federasyon" in result.text.casefold()
    assert result.changed is False
    port.update_chat_setting_key.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["Ankara hava durumu", "son deprem bilgisi"])
async def test_group_feature_request_uses_tool_pipeline_instead_of_text_only_reply_planner(text):
    import asyncio
    from src.chat_engine import ChatEngine
    from src.handlers.message_handler import MessageHandler
    handler = MessageHandler.__new__(MessageHandler)
    handler.chat_engine = SimpleNamespace(_should_attach_tools=ChatEngine._should_attach_tools)
    handler._group_reply_lock = lambda *args: asyncio.Lock()
    handler._process_group_targeted_replies_unlocked = AsyncMock(return_value=True)
    update = SimpleNamespace(effective_message=SimpleNamespace(message_thread_id=7))
    consumed = await handler._process_group_targeted_replies(update, -111, text, SimpleNamespace())
    assert consumed is False
    handler._process_group_targeted_replies_unlocked.assert_not_awaited()
