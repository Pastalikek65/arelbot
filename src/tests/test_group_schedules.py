from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from src.services.subscription_service import SubscriptionRequest, SubscriptionService
from src.services.time_format import format_turkey_datetime
from src.storage.subscription_store import SubscriptionStore


def test_local_schedule_calculation_respects_turkey_time_and_rollover():
    before = datetime(2026, 10, 5, 6, 0, tzinfo=timezone.utc)  # 09:00 Europe/Istanbul
    same_day = SubscriptionService._next_local_run("10:00", now=before)
    assert same_day == datetime(2026, 10, 5, 7, 0, tzinfo=timezone.utc)

    after = datetime(2026, 10, 5, 8, 0, tzinfo=timezone.utc)  # 11:00 local
    next_day = SubscriptionService._next_local_run("10:00", now=after)
    assert next_day == datetime(2026, 10, 6, 7, 0, tzinfo=timezone.utc)


def test_public_data_timestamp_is_formatted_in_turkey_timezone():
    value = datetime(2026, 10, 5, 7, 0, tzinfo=timezone.utc)
    assert format_turkey_datetime(value) == "05.10.2026 10:00"


@pytest.mark.asyncio
async def test_group_schedule_is_shared_by_admins_and_can_be_stopped():
    store = SubscriptionStore()
    service = SubscriptionService(store)
    first = await service.create_group_schedule(
        admin_id=10,
        chat_id=-100,
        feature_key="weather",
        params={"city": "Ä°stanbul"},
        schedule_kind="daily",
        local_time="10:00",
    )
    second = await service.create_group_schedule(
        admin_id=11,
        chat_id=-100,
        feature_key="weather",
        params={"city": "Ä°stanbul"},
        schedule_kind="daily",
        local_time="10:00",
    )
    assert first["subscription_id"] == second["subscription_id"]
    assert second["scope"] == "group"
    assert second["timezone"] == "Europe/Istanbul"
    assert len(await store.list_for_chat(-100)) == 1
    assert await store.disable_for_chat(-100, first["subscription_id"]) is True
    assert not (await store.list_for_chat(-100))[0]["enabled"]


@pytest.mark.asyncio
async def test_group_scope_does_not_leak_into_personal_tracking():
    store = SubscriptionStore()
    service = SubscriptionService(store)
    group = await service.create_group_schedule(
        admin_id=10,
        chat_id=-100,
        feature_key="earthquakes",
        params={},
        schedule_kind="daily",
        local_time="10:00",
    )
    personal = await service.create(
        user_id=10,
        chat_id=10,
        request=SubscriptionRequest("earthquakes", {}, 900, "15 dakika"),
    )
    personal_rows = await store.list_for_user(10)
    assert [row["subscription_id"] for row in personal_rows] == [personal["subscription_id"]]
    assert await store.disable(10, group["subscription_id"]) is False


@pytest.mark.asyncio
async def test_multiple_groups_have_isolated_schedules_and_controls():
    store = SubscriptionStore()
    service = SubscriptionService(store)
    first_group = await service.create_group_schedule(
        admin_id=10,
        chat_id=-100,
        feature_key="weather",
        params={"city": "Ä°stanbul"},
        schedule_kind="daily",
        local_time="10:00",
    )
    second_group = await service.create_group_schedule(
        admin_id=20,
        chat_id=-200,
        feature_key="weather",
        params={"city": "Ankara"},
        schedule_kind="weekly",
        local_time="18:00",
        weekday=0,
    )
    assert first_group["subscription_id"] != second_group["subscription_id"]
    assert [row["params"]["city"] for row in await store.list_for_chat(-100)] == ["Ä°stanbul"]
    assert [row["params"]["city"] for row in await store.list_for_chat(-200)] == ["Ankara"]
    assert await store.disable_for_chat(-100, second_group["subscription_id"]) is False
    assert (await store.list_for_chat(-200))[0]["enabled"] is True


@pytest.mark.asyncio
async def test_forum_topics_have_independent_group_schedules():
    store = SubscriptionStore()
    service = SubscriptionService(store)
    general = await service.create_group_schedule(
        admin_id=10,
        chat_id=-100,
        feature_key="weather",
        params={"city": "Istanbul"},
        schedule_kind="daily",
        local_time="10:00",
        message_thread_id=None,
    )
    topic = await service.create_group_schedule(
        admin_id=11,
        chat_id=-100,
        feature_key="weather",
        params={"city": "Istanbul"},
        schedule_kind="daily",
        local_time="10:00",
        message_thread_id=42,
    )
    same_topic = await service.create_group_schedule(
        admin_id=12,
        chat_id=-100,
        feature_key="weather",
        params={"city": "Istanbul"},
        schedule_kind="daily",
        local_time="10:00",
        message_thread_id=42,
    )

    assert general["subscription_id"] != topic["subscription_id"]
    assert same_topic["subscription_id"] == topic["subscription_id"]
    assert topic["message_thread_id"] == 42


@pytest.mark.asyncio
async def test_group_information_schedules_support_air_quality_and_news():
    store = SubscriptionStore()
    service = SubscriptionService(store)
    air = await service.create_group_schedule(
        admin_id=10,
        chat_id=-100,
        feature_key="air_quality",
        params={"city": "Ä°stanbul"},
        schedule_kind="daily",
        local_time="10:00",
    )
    news = await service.create_group_schedule(
        admin_id=10,
        chat_id=-100,
        feature_key="breaking_news",
        params={},
        schedule_kind="weekly",
        local_time="18:00",
        weekday=0,
    )
    assert air["feature_key"] == "air_quality"
    assert air["params"] == {"city": "Ä°stanbul"}
    assert news["feature_key"] == "breaking_news"
    assert news["dedupe_policy"] == "content"


@pytest.mark.asyncio
async def test_group_schedule_rejects_unknown_feature():
    service = SubscriptionService(SubscriptionStore())
    with pytest.raises(ValueError, match="desteklenmiyor"):
        await service.create_group_schedule(
            admin_id=10,
            chat_id=-100,
            feature_key="arbitrary_tool",
            params={},
            schedule_kind="daily",
            local_time="10:00",
        )


@pytest.mark.asyncio
async def test_group_schedule_writes_bounded_admin_audit_event():
    database = type("AuditDatabase", (), {"subscriptions": None, "add_audit_log": AsyncMock()})()
    service = SubscriptionService(SubscriptionStore(database))
    item = await service.create_group_schedule(
        admin_id=10,
        chat_id=-100,
        feature_key="air_quality",
        params={"city": "Ä°stanbul"},
        schedule_kind="daily",
        local_time="10:00",
        message_thread_id=42,
    )
    database.add_audit_log.assert_awaited_once()
    args, kwargs = database.add_audit_log.await_args
    assert args[:2] == (10, "group_notification_schedule_created")
    assert kwargs["chat_id"] == -100
    assert kwargs["subscription_id"] == item["subscription_id"]
    assert kwargs["feature_key"] == "air_quality"
    assert kwargs["message_thread_id"] == 42
    assert "city" not in kwargs
