"""Crash-recoverable owner leases remain separate from permanent action receipts."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from src.games.action_ledger import GameActionLedger
from src.storage.handler_data_port import HandlerDataPort


class LeaseRepository:
    available = True

    def __init__(self):
        self.leases = {}
        self.actions = set()
        self.renew_calls = 0
        self.renew_allowed = True

    async def claim_game_lease(self, action_key, owner_token, lease_seconds, now):
        current = self.leases.get(action_key)
        if current and current[1] > now:
            return False
        self.leases[action_key] = (owner_token, now + timedelta(seconds=lease_seconds))
        return True

    async def renew_game_lease(self, action_key, owner_token, lease_seconds, now):
        self.renew_calls += 1
        current = self.leases.get(action_key)
        if not self.renew_allowed or not current or current[0] != owner_token or current[1] <= now:
            return False
        self.leases[action_key] = (owner_token, now + timedelta(seconds=lease_seconds))
        return True

    async def release_game_lease(self, action_key, owner_token):
        if self.leases.get(action_key, (None,))[0] == owner_token:
            self.leases.pop(action_key, None)

    async def claim_game_action(self, *, action_key, **_):
        if action_key in self.actions:
            return False
        self.actions.add(action_key)
        return True

    async def release_game_action(self, action_key):
        self.actions.discard(action_key)


@pytest.mark.asyncio
async def test_durable_lease_contends_and_releases_without_erasing_action_receipt():
    repository = LeaseRepository()
    ledger = GameActionLedger(HandlerDataPort(repository), allow_memory_fallback=False)
    assert await ledger.claim(-100, "blackjack", "callback-1", user_id=7)
    async with ledger.lease(-100, "blackjack_owner_v2", "owner:7", user_id=7):
        with pytest.raises(ValueError, match="game_busy"):
            async with GameActionLedger(HandlerDataPort(repository), allow_memory_fallback=False).lease(
                -100, "blackjack_owner_v2", "owner:7", user_id=7
            ):
                pass
    async with GameActionLedger(HandlerDataPort(repository), allow_memory_fallback=False).lease(
        -100, "blackjack_owner_v2", "owner:7", user_id=7
    ):
        pass
    assert not await ledger.claim(-100, "blackjack", "callback-1", user_id=7)


@pytest.mark.asyncio
async def test_crashed_owner_can_recover_after_expiry_and_old_owner_cannot_release_new():
    repository = LeaseRepository()
    key = GameActionLedger._key(-100, "blackjack_owner_v2", "owner:7")
    now = datetime.now(timezone.utc)
    assert await repository.claim_game_lease(key, "crashed-process", 60, now)
    ledger = GameActionLedger(HandlerDataPort(repository), allow_memory_fallback=False)
    with pytest.raises(ValueError, match="game_busy"):
        async with ledger.lease(-100, "blackjack_owner_v2", "owner:7", user_id=7):
            pass
    repository.leases[key] = ("crashed-process", now - timedelta(seconds=1))
    async with ledger.lease(-100, "blackjack_owner_v2", "owner:7", user_id=7):
        new_token = repository.leases[key][0]
        await repository.release_game_lease(key, "crashed-process")
        assert repository.leases[key][0] == new_token
    assert key not in repository.leases


@pytest.mark.asyncio
async def test_heartbeat_renews_and_lost_lease_cancels_operation(monkeypatch):
    repository = LeaseRepository()
    monkeypatch.setattr(GameActionLedger, "LEASE_HEARTBEAT_SECONDS", 0.01)
    monkeypatch.setattr(GameActionLedger, "LEASE_OPERATION_SECONDS", 0.3)
    ledger = GameActionLedger(HandlerDataPort(repository), allow_memory_fallback=False)
    entered = asyncio.Event()
    with pytest.raises(RuntimeError, match="game_lease_lost"):
        async with ledger.lease(-100, "blackjack_owner_v2", "owner:7", user_id=7):
            entered.set()
            await asyncio.sleep(0.04)
            assert repository.renew_calls >= 1
            repository.renew_allowed = False
            await asyncio.sleep(0.2)
    assert entered.is_set()
    assert not repository.leases


@pytest.mark.asyncio
async def test_operation_timeout_releases_lease(monkeypatch):
    repository = LeaseRepository()
    monkeypatch.setattr(GameActionLedger, "LEASE_OPERATION_SECONDS", 0.02)
    ledger = GameActionLedger(HandlerDataPort(repository), allow_memory_fallback=False)
    with pytest.raises(TimeoutError, match="game_lease_timeout"):
        async with ledger.lease(-100, "blackjack_owner_v2", "owner:7", user_id=7):
            await asyncio.sleep(1)
    assert repository.leases == {}


@pytest.mark.asyncio
async def test_partial_repository_capability_fails_closed():
    class Partial:
        available = True

        async def claim_game_lease(self, *_):
            return True

    ledger = GameActionLedger(HandlerDataPort(Partial()), allow_memory_fallback=True)
    with pytest.raises(RuntimeError, match="game_persistence_unavailable"):
        async with ledger.lease(-100, "blackjack_owner_v2", "owner:7", user_id=7):
            pass


@pytest.mark.asyncio
async def test_memory_fallback_lease_is_separate_from_callback_claims():
    ledger = GameActionLedger()
    async with ledger.lease(-100, "blackjack_owner_v2", "owner:7", user_id=7):
        with pytest.raises(ValueError, match="game_busy"):
            async with ledger.lease(-100, "blackjack_owner_v2", "owner:7", user_id=7):
                pass
    assert await ledger.claim(-100, "blackjack", "callback-1", user_id=7)
    assert not await ledger.claim(-100, "blackjack", "callback-1", user_id=7)
