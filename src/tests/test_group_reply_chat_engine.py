from datetime import datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.chat_engine import ChatEngine
from src.ai.router import AIProviderRouter
from src.ai.personality import PersonalityPolicy
from src.ai.conversation_style import chat_style_instruction
from src.group_replies import GroupMessageRef, GroupTurnContext


def ref(message_id: int, user_id: int, text: str) -> GroupMessageRef:
    return GroupMessageRef(
        chat_id=-100,
        thread_id=7,
        message_id=message_id,
        user_id=user_id,
        display_name=f"U{user_id}",
        text=text,
        created_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["normal", "needle", "roast"])
async def test_group_reply_plan_preserves_persona_and_uses_conversation_budget(mode):
    engine = ChatEngine.__new__(ChatEngine)
    calls = []

    async def request(messages, tools, **kwargs):
        calls.append((messages, tools, kwargs))
        return {"message": {"content": '{"replies": []}'}}

    engine._request_with_failover = request
    current = ref(202, 2, "arel naber")
    previous = ref(101, 1, "sınavı geçtim")
    context = GroupTurnContext(current, (current, previous), "mention")

    result = await engine.get_group_reply_plan(context, persona_mode=mode)

    assert result == '{"replies": []}'
    assert calls[0][1] == []
    assert calls[0][2]["task"] == "chat"
    assert calls[0][2]["max_tokens"] <= 512
    assert calls[0][2]["reasoning_effort"] == "none"
    expected_style = chat_style_instruction(PersonalityPolicy().for_mode(mode), mode, current.text, 0)
    assert calls[0][0][0]["content"].startswith(expected_style)
    payload = calls[0][0][-1]["content"]
    assert "message_id" in payload
    rows = json.loads(payload.split("\n", 1)[1])
    assert {row["message_id"] for row in rows} == {101, 202}


@pytest.mark.asyncio
async def test_group_reply_plan_returns_none_when_provider_is_unavailable():
    engine = ChatEngine.__new__(ChatEngine)

    async def request(*_args, **_kwargs):
        return None

    engine._request_with_failover = request
    current = ref(202, 2, "arel naber")

    assert await engine.get_group_reply_plan(GroupTurnContext(current, (current,), "mention")) is None


@pytest.mark.asyncio
async def test_group_roast_plan_uses_scoped_fallback_order():
    engine = ChatEngine.__new__(ChatEngine)
    engine.ai_router = AIProviderRouter(routes={
        'chat': ['openai', 'hrmn', 'cloudflare', 'evren', 'nvidia']})
    engine._request_with_failover = AsyncMock(return_value={'message': {'content': '{"replies": []}'}})
    current = ref(202, 2, 'böyle konuşma ama yine son güne bıraktım')
    await engine.get_group_reply_plan(GroupTurnContext(current, (current,), 'mention'),
                                      persona_mode='roast')
    assert engine._request_with_failover.await_args.kwargs['provider_names'] == [
        'openai', 'hrmn', 'evren', 'cloudflare', 'nvidia']
