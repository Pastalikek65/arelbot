from datetime import datetime, timezone

import pytest

from src.group_replies import GroupMessageRef, GroupReplyPlan, ReplySegment
from src.storage.group_reply_store import ReplyDeliveryLedger
from src.services.group_reply_delivery import GroupReplyDeliveryService


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


class Bot:
    def __init__(self):
        self.calls = []

    async def send_message(self, **kwargs):
        self.calls.append(kwargs)
        return type("Sent", (), {"message_id": 900 + len(self.calls)})()


class MissingTargetBot(Bot):
    async def send_message(self, **kwargs):
        if kwargs.get("reply_to_message_id") == 202:
            raise RuntimeError("Bad Request: message to reply not found")
        return await super().send_message(**kwargs)


@pytest.mark.asyncio
async def test_delivery_sends_each_segment_as_reply_to_its_own_message():
    first = ref(101, 1, "sınavı geçtim")
    second = ref(202, 2, "arel naber")
    plan = GroupReplyPlan(
        segments=(
            ReplySegment(first, "helal, sonuç nasıl geldi?"),
            ReplySegment(second, "iyiyim ya 😄"),
        ),
        correlation_id="turn-1",
    )
    bot = Bot()
    service = GroupReplyDeliveryService(ledger=ReplyDeliveryLedger())

    result = await service.deliver(bot=bot, plan=plan, generation_id="turn-1")

    assert result.sent_count == 2
    assert [call["reply_to_message_id"] for call in bot.calls] == [101, 202]
    assert [call["message_thread_id"] for call in bot.calls] == [7, 7]
    assert [call["chat_id"] for call in bot.calls] == [-100, -100]


@pytest.mark.asyncio
async def test_delivery_is_idempotent_for_the_same_generation():
    first = ref(101, 1, "sınavı geçtim")
    plan = GroupReplyPlan(
        segments=(ReplySegment(first, "helal"),),
        correlation_id="turn-2",
    )
    bot = Bot()
    service = GroupReplyDeliveryService(ledger=ReplyDeliveryLedger())

    first_result = await service.deliver(bot=bot, plan=plan, generation_id="turn-2")
    second_result = await service.deliver(bot=bot, plan=plan, generation_id="turn-2")

    assert first_result.sent_count == 1
    assert second_result.sent_count == 0
    assert len(bot.calls) == 1


@pytest.mark.asyncio
async def test_delivery_reports_the_actual_targets_when_an_earlier_segment_is_duplicate():
    first = ref(101, 1, "ilk")
    second = ref(202, 2, "ikinci")
    plan = GroupReplyPlan(
        segments=(ReplySegment(first, "ilk yanıt"), ReplySegment(second, "ikinci yanıt")),
        correlation_id="turn-3",
    )
    bot = Bot()
    ledger = ReplyDeliveryLedger()
    key = ledger.make_key(-100, 7, 101, feature="group_targeted_reply", generation_id="turn-3")
    assert await ledger.claim(key, chat_id=-100, target_message_id=101, generation_id="turn-3", thread_id=7)
    service = GroupReplyDeliveryService(ledger=ledger)

    result = await service.deliver(bot=bot, plan=plan, generation_id="turn-3")

    assert result.sent_count == 1
    assert result.sent_target_message_ids == (202,)
    assert bot.calls[0]["reply_to_message_id"] == 202


@pytest.mark.asyncio
async def test_missing_telegram_target_is_skipped_without_uncertain_replay():
    target = ref(202, 2, "silinmiş")
    plan = GroupReplyPlan(segments=(ReplySegment(target, "bunu atla"),), correlation_id="turn-4")
    ledger = ReplyDeliveryLedger()
    bot = MissingTargetBot()
    service = GroupReplyDeliveryService(ledger=ledger)

    result = await service.deliver(bot=bot, plan=plan, generation_id="turn-4")

    assert result.sent_count == 0
    assert result.failed_count == 0
    assert result.missing_target_message_ids == (202,)
    key = ledger.make_key(-100, 7, 202, feature="group_targeted_reply", generation_id="turn-4")
    assert (await ledger.get(key))["status"] == "skipped"
