from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.moderation.action_executor import (
    ModerationActionExecutor,
    TelegramModerationActionExecutor,
)
from src.moderation.action_ledger import ModerationActionLedger
from src.moderation.contracts import ModerationAction


@pytest.mark.asyncio
async def test_moderation_ledger_does_not_collide_set_and_set_on_insert():
    """The production Mongo upsert must not fail on overlapping update paths."""

    class Collection:
        async def find_one(self, *_args, **_kwargs):
            return None

        async def update_one(self, _query, update, **_kwargs):
            assert not set(update.get("$set", {})).intersection(update.get("$setOnInsert", {}))
            return SimpleNamespace(modified_count=0, upserted_id="new-action")

    class Database:
        moderation_actions = Collection()

    action = ModerationAction(
        action="ban",
        chat_id=-100,
        actor_id=7,
        target_user_id=42,
    )
    ledger = ModerationActionLedger(database=Database(), required=True)

    assert await ledger.claim(action) is True


class FakeBot:
    def __init__(self):
        self.calls = []

    async def delete_message(self, **kwargs):
        self.calls.append(("delete_message", kwargs))
        return SimpleNamespace(message_id=501)

    async def ban_chat_member(self, **kwargs):
        self.calls.append(("ban_chat_member", kwargs))
        return True

    async def restrict_chat_member(self, **kwargs):
        self.calls.append(("restrict_chat_member", kwargs))
        return True


def test_executor_classifies_telegram_rejections_without_exposing_details():
    from telegram.error import BadRequest, Forbidden, NetworkError

    code, retryable = TelegramModerationActionExecutor._classify_transport_error(
        BadRequest("Bad Request: not enough rights to restrict")
    )
    assert (code, retryable) == ("telegram_not_enough_rights", False)

    code, retryable = TelegramModerationActionExecutor._classify_transport_error(
        BadRequest("Bad Request: user is an administrator")
    )
    assert (code, retryable) == ("target_is_admin", False)

    code, retryable = TelegramModerationActionExecutor._classify_transport_error(
        Forbidden("Forbidden: bot was kicked")
    )
    assert (code, retryable) == ("telegram_forbidden", False)

    code, retryable = TelegramModerationActionExecutor._classify_transport_error(
        NetworkError("secret token must not escape")
    )
    assert (code, retryable) == ("telegram_action_uncertain", True)


def test_generic_executor_alias_keeps_typed_telegram_boundary():
    assert ModerationActionExecutor is TelegramModerationActionExecutor


@pytest.mark.asyncio
async def test_executor_rejects_action_when_permission_checker_denies():
    bot = FakeBot()
    executor = TelegramModerationActionExecutor(
        bot,
        ledger=ModerationActionLedger(),
        permission_checker=lambda _action: False,
    )
    action = ModerationAction(
        action="delete_message",
        chat_id=-100,
        actor_id=7,
        target_message_id=42,
    )

    result = await executor.execute(action)

    assert result.status == "rejected"
    assert result.error_code == "permission_denied"
    assert bot.calls == []


@pytest.mark.asyncio
async def test_executor_sends_delete_once_for_duplicate_action():
    bot = FakeBot()
    executor = TelegramModerationActionExecutor(
        bot,
        ledger=ModerationActionLedger(),
        permission_checker=lambda _action: True,
    )
    action = ModerationAction(
        action="delete_message",
        chat_id=-100,
        actor_id=7,
        target_message_id=42,
    )

    first = await executor.execute(action)
    second = await executor.execute(action)

    assert first.status == "sent"
    assert second.status == "skipped"
    assert second.error_code == "duplicate_action"
    assert [name for name, _ in bot.calls] == ["delete_message"]


@pytest.mark.asyncio
async def test_executor_dispatches_ban_with_explicit_target_and_reason():
    bot = FakeBot()
    executor = TelegramModerationActionExecutor(
        bot,
        ledger=ModerationActionLedger(),
        permission_checker=lambda _action: True,
    )
    action = ModerationAction(
        action="ban",
        chat_id=-100,
        actor_id=7,
        target_user_id=42,
        reason="raid",
    )

    result = await executor.execute(action)

    assert result.status == "sent"
    assert bot.calls[0][0] == "ban_chat_member"
    assert bot.calls[0][1]["chat_id"] == -100
    assert bot.calls[0][1]["user_id"] == 42
    assert bot.calls[0][1]["revoke_messages"] is True


@pytest.mark.asyncio
async def test_executor_dispatches_temporary_mute_with_bounded_duration():
    bot = FakeBot()
    executor = TelegramModerationActionExecutor(
        bot,
        ledger=ModerationActionLedger(),
        permission_checker=lambda _action: True,
    )
    action = ModerationAction(
        action="temp_mute",
        chat_id=-100,
        actor_id=7,
        target_user_id=42,
        duration_seconds=60,
    )

    result = await executor.execute(action)

    assert result.status == "sent"
    assert bot.calls[0][0] == "restrict_chat_member"
    assert bot.calls[0][1]["chat_id"] == -100
    assert bot.calls[0][1]["user_id"] == 42
    assert bot.calls[0][1]["until_date"] is not None


@pytest.mark.asyncio
async def test_executor_marks_transport_failure_uncertain_without_leaking_exception():
    class BrokenBot(FakeBot):
        async def delete_message(self, **kwargs):
            raise RuntimeError("telegram token must not escape")

    bot = BrokenBot()
    executor = TelegramModerationActionExecutor(
        bot,
        ledger=ModerationActionLedger(),
        permission_checker=lambda _action: True,
    )
    action = ModerationAction(
        action="delete_message",
        chat_id=-100,
        actor_id=7,
        target_message_id=42,
    )

    result = await executor.execute(action)

    assert result.status == "uncertain"
    assert result.error_code == "telegram_action_uncertain"
    assert "token" not in (result.error_detail or "")


@pytest.mark.asyncio
async def test_executor_marks_finalize_failure_uncertain():
    class FinalizeBrokenLedger(ModerationActionLedger):
        async def mark_sent(self, action_or_key, telegram_message_id=None):
            return False

    bot = FakeBot()
    executor = TelegramModerationActionExecutor(
        bot,
        ledger=FinalizeBrokenLedger(),
        permission_checker=lambda _action: True,
    )
    action = ModerationAction(action="delete_message", chat_id=-100, actor_id=7, target_message_id=44)

    result = await executor.execute(action)

    assert result.status == "uncertain"
    assert result.error_code == "moderation_action_finalize_failed"


@pytest.mark.asyncio
async def test_executor_fails_closed_without_permission_checker():
    bot = FakeBot()
    executor = TelegramModerationActionExecutor(bot, ledger=ModerationActionLedger())
    action = ModerationAction(
        action="delete_message",
        chat_id=-100,
        actor_id=7,
        target_message_id=42,
    )

    result = await executor.execute(action)

    assert result.status == "rejected"
    assert result.error_code == "permission_denied"
    assert bot.calls == []
