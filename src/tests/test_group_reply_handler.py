from datetime import datetime, timezone
from types import SimpleNamespace
import asyncio

import pytest
from unittest.mock import AsyncMock

from src.handlers.message_handler import MessageHandler
from src.storage.group_reply_store import RecentMessageIndex, ReplyDeliveryLedger


class DB:
    reachable = True

    async def get_game_state(self, *_args):
        return None

    async def get_chat_settings(self, _chat_id):
        return {}


class Sudo:
    def is_blacklisted(self, *_args):
        return False

    def is_sudo(self, *_args):
        return False


class Bot:
    def __init__(self):
        self.calls = []

    async def get_me(self):
        return SimpleNamespace(id=999, username="arelbot")

    async def send_message(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(message_id=700 + len(self.calls))


class Message:
    def __init__(self, message_id, text, user):
        self.message_id = message_id
        self.text = text
        self.from_user = user
        self.reply_to_message = None
        self.message_thread_id = 7

    async def reply_text(self, *_args, **_kwargs):
        raise AssertionError("targeted group delivery should use the bot with each source message")


def update(message_id, text, user_id, name):
    user = SimpleNamespace(id=user_id, full_name=name, username=name.casefold())
    message = Message(message_id, text, user)
    return SimpleNamespace(
        message=message,
        effective_message=message,
        effective_user=user,
        effective_chat=SimpleNamespace(id=-100, type="supergroup"),
    )


class Engine:
    def __init__(self):
        self.plans = []
        self.records = []
        self.persona_modes = []

    async def record_message(self, *args, **kwargs):
        self.records.append((args, kwargs))

    async def get_group_reply_plan(self, context, *, persona_mode=None):
        self.persona_modes.append(persona_mode)
        self.plans.append(context)
        ids = [item.message_id for item in context.candidates]
        return '{"replies": [' + ",".join(
            '{"target_message_id": %d, "text": "ayrı yanıt"}' % item_id for item_id in ids
        ) + ']}'


class EmptyPlanEngine(Engine):
    async def get_group_reply_plan(self, context, *, persona_mode=None):
        return '{"replies": []}'

    async def get_response(self, *args, **kwargs):
        return "tamam buraday?m"


@pytest.mark.asyncio
async def test_triggered_group_turn_replies_to_each_recent_user_message_separately():
    engine = Engine()
    bot = Bot()
    handler = MessageHandler(
        engine,
        Sudo(),
        None,
        None,
        data_port=SimpleNamespace(
            reachable=True,
            get_chat_settings=DB().get_chat_settings,
            get_game_state=DB().get_game_state,
        ),
        recent_message_index=RecentMessageIndex(),
        reply_delivery_ledger=ReplyDeliveryLedger(),
    )
    handler._resolve_persona_mode = AsyncMock(return_value="roast")
    handler._log_event = AsyncMock()
    context = SimpleNamespace(bot=bot, chat_data={}, job_queue=None, application=None)

    await handler.handle_message(update(101, "sınavı geçtim", 1, "X"), context)
    await handler.handle_message(update(202, "Arel naber", 2, "Y"), context)

    assert [call["reply_to_message_id"] for call in bot.calls] == [101, 202]
    assert all(call["chat_id"] == -100 for call in bot.calls)
    assert len(engine.plans) == 1
    assert engine.persona_modes == ["roast"]


@pytest.mark.asyncio
async def test_explicit_group_trigger_never_silently_drops_an_empty_plan():
    engine = EmptyPlanEngine()
    bot = Bot()
    handler = MessageHandler(
        engine,
        Sudo(),
        None,
        None,
        data_port=SimpleNamespace(
            reachable=True,
            get_chat_settings=DB().get_chat_settings,
            get_game_state=DB().get_game_state,
        ),
        recent_message_index=RecentMessageIndex(),
        reply_delivery_ledger=ReplyDeliveryLedger(),
    )
    context = SimpleNamespace(bot=bot, chat_data={}, job_queue=None, application=None)
    handler._log_event = AsyncMock()

    await handler.handle_message(update(303, "Arel ses var m?", 3, "Z"), context)

    assert len(bot.calls) == 1
    assert bot.calls[0]["reply_to_message_id"] == 303


@pytest.mark.asyncio
async def test_same_telegram_message_retry_does_not_send_duplicate_replies():
    engine = Engine()
    bot = Bot()
    index = RecentMessageIndex()
    handler = MessageHandler(
        engine,
        Sudo(),
        None,
        None,
        data_port=SimpleNamespace(
            reachable=True,
            get_chat_settings=DB().get_chat_settings,
            get_game_state=DB().get_game_state,
        ),
        recent_message_index=index,
        reply_delivery_ledger=ReplyDeliveryLedger(),
    )
    context = SimpleNamespace(bot=bot, chat_data={}, job_queue=None, application=None)
    first = update(101, "Arel naber", 1, "X")
    first.update_id = 42
    first_context = context

    # Seed the observed message exactly as MessageHandler.handle_message does.
    await index.add_message({
        "chat_id": -100,
        "thread_id": 7,
        "telegram_message_id": 101,
        "author_user_id": 1,
        "display_name": "X",
        "text": "Arel naber",
        "created_at": datetime.now(timezone.utc),
    })
    await handler._process_group_targeted_replies(first, -100, first.message.text, first_context, turn_id="telegram:-100:101:42")
    await handler._process_group_targeted_replies(first, -100, first.message.text, first_context, turn_id="telegram:-100:101:42")

    assert len(bot.calls) == 1


@pytest.mark.asyncio
async def test_concurrent_group_triggers_do_not_cross_or_duplicate_targets():
    engine = Engine()
    bot = Bot()
    index = RecentMessageIndex()
    ledger = ReplyDeliveryLedger()
    handler = MessageHandler(
        engine,
        Sudo(),
        None,
        None,
        data_port=SimpleNamespace(
            reachable=True,
            get_chat_settings=DB().get_chat_settings,
            get_game_state=DB().get_game_state,
        ),
        recent_message_index=index,
        reply_delivery_ledger=ledger,
    )
    context = SimpleNamespace(bot=bot, chat_data={}, job_queue=None, application=None)
    now = datetime.now(timezone.utc)
    for message_id, user_id, text in ((101, 1, "Arel X"), (202, 2, "Arel Y")):
        await index.add_message({
            "chat_id": -100,
            "thread_id": 7,
            "telegram_message_id": message_id,
            "author_user_id": user_id,
            "display_name": str(user_id),
            "text": text,
            "created_at": now,
        })

    await asyncio.gather(
        handler._process_group_targeted_replies(update(101, "Arel X", 1, "X"), -100, "Arel X", context, turn_id="telegram:-100:101:1"),
        handler._process_group_targeted_replies(update(202, "Arel Y", 2, "Y"), -100, "Arel Y", context, turn_id="telegram:-100:202:2"),
    )

    assert sorted(call["reply_to_message_id"] for call in bot.calls) == [101, 202]

