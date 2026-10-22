"""Economy read model and cosmetic selection behavior."""
import asyncio
from datetime import UTC, datetime

import pytest

from src.services.economy_service import EconomyService
from src.storage.economy_store import EconomyStore
from src.tests.test_economy_service import _FakeDatabase
from src.tests.test_economy_storage_v2 import BSONCollection

NOW = datetime(2026, 10, 22, 10, tzinfo=UTC)


@pytest.mark.asyncio
async def test_snapshot_reward_remainder_and_shop_eligibility_from_real_account():
    service = EconomyService(intent_signing_key="synthetic-only")
    empty = await service.snapshot(7, -100, now=NOW)
    assert empty["game_reward_remaining_today"] == 150
    assert empty["shop_eligibility"]["merakli"] == {
        "owned": False, "affordable": False, "missing_coins": 75, "eligible": False}
    assert await service.credit(7, -100, 90, reason="quiz_reward", event_id="first")
    current = await service.snapshot(7, -100, now=datetime.now(UTC))
    assert current["game_reward_remaining_today"] == 60
    assert current["shop_eligibility"]["merakli"] == {
        "owned": False, "affordable": True, "missing_coins": 0, "eligible": True}
    assert current["shop_eligibility"]["strateji"]["missing_coins"] == 160

    quote = await service.prepare_purchase(7, -100, "merakli")
    result = await service.purchase(7, -100, "merakli", event_id="buy:1",
                                    confirmation_token=quote["token"])
    assert result["accepted"]
    after = await service.snapshot(7, -100, now=datetime.now(UTC))
    assert after["shop_eligibility"]["merakli"] == {
        "owned": True, "affordable": False, "missing_coins": 60, "eligible": False}


@pytest.mark.asyncio
async def test_unequip_is_atomic_slot_only_and_idempotent_in_persistent_store():
    collection = BSONCollection()
    store = EconomyStore(_FakeDatabase(collection))
    now = datetime.now(UTC)
    assert await store.apply_event(user_id=7, chat_id=-100, event_key="seed", delta=300,
        direction="credit", amount=300, reason="fixture_seed", created_at=now)
    assert await store.purchase(7, -100, "merakli", 75, "buy:merakli", now)
    assert await store.purchase(7, -100, "pusula", 80, "buy:pusula", now)
    assert await store.equip(7, -100, "merakli", "title")
    assert await store.equip(7, -100, "pusula", "badge")
    before = await store.snapshot(7, -100)
    assert await store.unequip(7, -100, "title")
    assert collection.updates[-1][1] == {"$unset": {"equipped.title": ""}}
    after = await EconomyStore(_FakeDatabase(collection)).snapshot(7, -100)
    assert after["balance"] == before["balance"]
    assert after["inventory"] == before["inventory"]
    assert after["equipped"] == {"badge": "pusula"}
    assert not await store.unequip(7, -100, "title")
    assert not await store.unequip(8, -100, "badge")


@pytest.mark.asyncio
async def test_service_unequip_validates_slot_and_preserves_owned_item():
    service = EconomyService(intent_signing_key="synthetic-only")
    await service.credit(7, -100, 100, reason="fixture_seed", event_id="seed")
    quote = await service.prepare_purchase(7, -100, "merakli")
    await service.purchase(7, -100, "merakli", event_id="buy:1",
                           confirmation_token=quote["token"])
    await service.equip(7, -100, "merakli")
    with pytest.raises(ValueError, match="economy_slot_unknown"):
        await service.unequip(7, -100, "balance")
    first = await service.unequip(7, -100, "title")
    assert first["accepted"] and first["status"] == "unequipped"
    assert "merakli" in first["owned"] and first["equipped"] == {}
    assert first["balance"] == 25
    second = await service.unequip(7, -100, "title")
    assert not second["accepted"] and second["status"] == "unchanged"


@pytest.mark.asyncio
async def test_two_store_game_rewards_share_atomic_daily_cap_and_keep_zero_receipts():
    collection = BSONCollection()
    first, second = EconomyStore(_FakeDatabase(collection)), EconomyStore(_FakeDatabase(collection))
    stores = [first, second] * 5
    outcomes = await asyncio.gather(*(store.apply_event(
        user_id=7, chat_id=-100, event_key=f"game:{index}", delta=25,
        direction="credit", amount=25, reason="quiz_reward", created_at=NOW,
        reward_day="2026-10-22", reward_limit=150)
        for index, store in enumerate(stores)))
    assert all(outcomes)
    account = collection.account(7, -100)
    assert account["balance"] == account["reward_earned"] == 150
    assert sum(row["amount"] for row in account["applied_events"].values()) == 150
    assert len(account["receipts"]) == 10
    assert not await second.apply_event(user_id=7, chat_id=-100, event_key="game:9",
        delta=25, direction="credit", amount=25, reason="quiz_reward", created_at=NOW,
        reward_day="2026-10-23", reward_limit=200)
    assert collection.account(7, -100)["balance"] == 150
