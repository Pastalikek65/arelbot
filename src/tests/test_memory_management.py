from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.orchestrator import Orchestrator


class FakeMemoryDB:
    def __init__(self):
        self.enabled = False
        self.rows = []

    async def set_memory_consent(self, user_id, enabled):
        self.enabled = bool(enabled)

    async def get_memory_consent(self, user_id):
        return self.enabled

    async def delete_user_facts(self, user_id):
        count = len(self.rows)
        self.rows.clear()
        return count

    async def list_user_facts(self, user_id, limit=50):
        return self.rows[:limit]

    async def delete_user_fact(self, user_id, fact_id):
        before = len(self.rows)
        self.rows[:] = [row for row in self.rows if row["fact_id"] != fact_id]
        return len(self.rows) != before


@pytest.mark.asyncio
async def test_memory_management_supports_show_delete_and_clear():
    db = FakeMemoryDB()
    db.enabled = True
    db.rows = [{"fact_id": "abc123", "key": "sehir", "value": "Ankara"}]
    owner = SimpleNamespace(chat_engine=SimpleNamespace(db=db))
    replies = []

    class Message:
        async def reply_text(self, text, **kwargs):
            replies.append((text, kwargs))

    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=7),
        effective_chat=SimpleNamespace(id=7, type="private"),
        effective_message=Message(),
    )

    await Orchestrator.handle_memory_consent_command(owner, update, SimpleNamespace(args=["show"]))
    assert "abc123" in replies[-1][0]
    assert "Ankara" in replies[-1][0]

    await Orchestrator.handle_memory_consent_command(owner, update, SimpleNamespace(args=["delete", "abc123"]))
    assert db.rows == []

    db.rows = [{"fact_id": "def456", "key": "takim", "value": "Arel"}]
    await Orchestrator.handle_memory_consent_command(owner, update, SimpleNamespace(args=["clear"]))
    assert db.rows == []


@pytest.mark.asyncio
async def test_memory_management_refuses_group_surface_before_reading_facts():
    class MustNotReadDB:
        async def get_memory_consent(self, user_id):
            raise AssertionError("group memory command read private state")

    owner = SimpleNamespace(chat_engine=SimpleNamespace(db=MustNotReadDB()))
    reply = AsyncMock()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=7),
        effective_chat=SimpleNamespace(id=-100, type="supergroup"),
        effective_message=SimpleNamespace(reply_text=reply),
    )

    await Orchestrator.handle_memory_consent_command(owner, update, SimpleNamespace(args=["show"]))

    reply.assert_awaited_once()
