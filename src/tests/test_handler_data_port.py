from __future__ import annotations

import pytest

from src.games.action_ledger import GameActionLedger
from src.storage.handler_data_port import HandlerDataPort


class FakeDatabase:
    available = True

    def __init__(self):
        self.claimed = set()
        self.audit_calls = []

    async def get_user(self, user_id):
        return {"_id": user_id, "language": "tr"}

    async def update_user(self, user_id, data):
        return user_id, data

    async def get_chat_settings(self, chat_id):
        return {"chat_id": chat_id}

    async def claim_game_action(self, **kwargs):
        key = kwargs["action_key"]
        if key in self.claimed:
            return False
        self.claimed.add(key)
        return True

    async def release_game_action(self, key):
        self.claimed.discard(key)

    async def add_audit_log(self, actor_id, action, **details):
        self.audit_calls.append((actor_id, action, details))
        return True

    async def get_audit_logs(self, limit=10):
        return list(self.audit_calls)[-limit:]


@pytest.mark.asyncio
async def test_handler_data_port_exposes_only_repository_operations():
    port = HandlerDataPort(FakeDatabase())

    assert await port.get_user(42) == {"_id": 42, "language": "tr"}
    assert await port.update_user(42, {"language": "en"}) == (42, {"language": "en"})
    assert await port.get_chat_settings(-100) == {"chat_id": -100}
    with pytest.raises(AttributeError):
        _ = port.users


def test_handler_data_port_from_engine_prefers_explicit_port():
    explicit = object()
    engine = type("Engine", (), {"data_port": explicit, "db": FakeDatabase()})()

    assert HandlerDataPort.from_engine(engine) is explicit


@pytest.mark.asyncio
async def test_handler_data_port_advertises_durable_game_action_capability():
    port = HandlerDataPort(FakeDatabase())
    ledger = GameActionLedger(port)

    assert port.supports("claim_game_action") is True
    assert await ledger.claim(-100, "quiz", "callback-1") is True
    assert await ledger.claim(-100, "quiz", "callback-1") is False
    await ledger.release(-100, "quiz", "callback-1")
    assert await ledger.claim(-100, "quiz", "callback-1") is True


@pytest.mark.asyncio
async def test_handler_data_port_keeps_admin_audit_calls_behind_the_port():
    database = FakeDatabase()
    port = HandlerDataPort(database)

    await port.add_audit_log(7, "provider_reset", scope="admin")

    assert await port.get_audit_logs(limit=1) == [(7, "provider_reset", {"scope": "admin"})]
