from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.orchestrator import Orchestrator
from src.services.announcement_service import AnnouncementService
from src.storage.subscription_store import SubscriptionStore


def test_announcement_parser_supports_daily_and_turkish_weekday():
    daily = AnnouncementService.parse(["her", "gÃ¼n", "10:00", "ToplantÄ±", "baÅŸlÄ±yor"])
    assert daily.schedule_kind == "daily"
    assert daily.weekday is None
    assert daily.local_time == "10:00"
    assert daily.text == "ToplantÄ± baÅŸlÄ±yor"

    weekly = AnnouncementService.parse(["her", "pazartesi", "09:05", "HaftalÄ±k", "Ã¶zet"])
    assert weekly.schedule_kind == "weekly"
    assert weekly.weekday == 0


def test_announcement_parser_rejects_invalid_payloads():
    with pytest.raises(ValueError, match="HH:MM"):
        AnnouncementService.parse(["her", "gÃ¼n", "25:00", "Mesaj"])
    with pytest.raises(ValueError, match="en az"):
        AnnouncementService.parse(["her", "gÃ¼n", "10:00", "x"])


@pytest.mark.asyncio
async def test_group_announcement_is_deduplicated_and_scoped():
    service = AnnouncementService(SubscriptionStore())
    request = AnnouncementService.parse(["her", "gÃ¼n", "10:00", "NÃ¶bet"])
    first = await service.create(admin_id=10, chat_id=-100, request=request)
    second = await service.create(admin_id=11, chat_id=-100, request=request)
    assert first["subscription_id"] == second["subscription_id"]
    assert len(await service.list_for_chat(-100)) == 1
    other_chat = await service.create(admin_id=11, chat_id=-200, request=request)
    assert other_chat["subscription_id"] != first["subscription_id"]
    assert await service.disable(-100, first["subscription_id"]) is True
    assert await service.list_for_chat(-100) == []


@pytest.mark.asyncio
async def test_subscription_tick_delivers_escaped_group_announcement():
    store = SubscriptionStore()
    service = AnnouncementService(store)
    request = AnnouncementService.parse(["her", "gÃ¼n", "10:00", "<b>Merhaba</b>"])
    item = await service.create(admin_id=10, chat_id=-100, request=request)
    store._items[item["subscription_id"]]["next_run_at"] = datetime.now(timezone.utc)

    sent = AsyncMock()
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.subscription_service = service
    orchestrator.notification_preference_store = SimpleNamespace(quiet_until=AsyncMock(return_value=None))
    orchestrator.chat_engine = SimpleNamespace(
        feature_executor=SimpleNamespace(execute=AsyncMock(side_effect=AssertionError("provider Ã§aÄŸrÄ±lmamalÄ±")))
    )
    context = SimpleNamespace(bot=SimpleNamespace(send_message=sent))

    await orchestrator._subscription_tick(context)

    sent.assert_awaited_once()
    assert "&lt;b&gt;Merhaba&lt;/b&gt;" in sent.await_args.kwargs["text"]
    assert sent.await_args.kwargs["chat_id"] == -100
    assert await store.due() == []


@pytest.mark.asyncio
async def test_subscription_tick_keeps_forum_topic_for_group_announcement():
    store = SubscriptionStore()
    service = AnnouncementService(store)
    request = AnnouncementService.parse(["her", "gÃ¼n", "10:00", "Topic mesajÄ±"])
    item = await service.create(
        admin_id=10,
        chat_id=-100,
        request=request,
        message_thread_id=77,
    )
    store._items[item["subscription_id"]]["next_run_at"] = datetime.now(timezone.utc)

    sent = AsyncMock()
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.subscription_service = service
    orchestrator.notification_preference_store = SimpleNamespace(quiet_until=AsyncMock(return_value=None))
    orchestrator.chat_engine = SimpleNamespace(
        feature_executor=SimpleNamespace(execute=AsyncMock(side_effect=AssertionError("provider Ã§aÄŸrÄ±lmamalÄ±")))
    )
    await orchestrator._subscription_tick(SimpleNamespace(bot=SimpleNamespace(send_message=sent)))

    assert sent.await_args.kwargs["message_thread_id"] == 77
