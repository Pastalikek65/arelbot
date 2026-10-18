from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest

from src.chat_engine import ChatEngine
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
async def test_group_reply_plan_requests_structured_ids_only_on_fast_route():
    engine = ChatEngine.__new__(ChatEngine)
    calls = []

    async def request(messages, tools, **kwargs):
        calls.append((messages, tools, kwargs))
        return {"message": {"content": '{"replies": []}'}}

    engine._request_with_failover = request
    current = ref(202, 2, "arel naber")
    previous = ref(101, 1, "sınavı geçtim")
    context = GroupTurnContext(current, (current, previous), "mention")

    result = await engine.get_group_reply_plan(context)

    assert result == '{"replies": []}'
    assert calls[0][1] == []
    assert calls[0][2]["task"] == "fast"
    assert calls[0][2]["max_tokens"] <= 512
    assert calls[0][2]["reasoning_effort"] == "none"
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
