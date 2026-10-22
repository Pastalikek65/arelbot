from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pytest

from src.moderation.telegram_guardian_service import GuardianTelegramService


class Member:
    status = "left"


class Message:
    def __init__(self, text=""):
        self.deleted = False
        self.replies = []
        self.text = text

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))

    async def delete(self):
        self.deleted = True


class Bot:
    def __init__(self):
        self.calls = []

    async def get_chat_member(self, **kwargs):
        return Member()

    async def promote_chat_member(self, **kwargs):
        self.calls.append(("promote", kwargs))

    async def unpin_all_chat_messages(self, **kwargs):
        self.calls.append(("unpin_all", kwargs))

    async def delete_message(self, **kwargs):
        self.calls.append(("delete", kwargs))

    async def get_chat_administrators(self, **kwargs):
        class User:
            id = 8
            full_name = "Arel Admin"
            username = "areladmin"
        class Admin:
            user = User()
            status = "administrator"
        return [Admin()]

    async def set_chat_permissions(self, **kwargs):
        self.calls.append(("permissions", kwargs))

    async def restrict_chat_member(self, **kwargs):
        self.calls.append(("restrict", kwargs))


class ResolverBot(Bot):
    async def get_chat_member(self, **kwargs):
        class Member:
            status = "member"
        self.calls.append(("get_chat_member", kwargs))
        return Member()


class ResolverPort:
    async def find_user_by_username(self, username):
        if username.casefold() == "hedef":
            return {"_id": 42, "username": "hedef"}
        return None


@pytest.mark.asyncio
async def test_forced_subscription_consumes_unsubscribed_message():
    message = Message()
    consumed = await GuardianTelegramService().enforce_fsub(
        bot=Bot(), chat_id=-100, user_id=7, message=message,
        settings={"fsub_channel": "@arelkanal"}, is_admin=False,
    )
    assert consumed is True
    assert message.deleted is True
    assert message.replies


@pytest.mark.asyncio
async def test_pending_captcha_suppresses_without_extra_group_message():
    message = Message()
    bot = Bot()
    consumed = await GuardianTelegramService().enforce_pending_captcha(
        bot=bot, chat_id=-100, user_id=7, message=message,
        settings={"captcha_enabled": True, "guardian_pending_captcha": {"7": {}}}, is_admin=False,
    )
    assert consumed is True
    assert message.deleted is True
    assert message.replies == []


@pytest.mark.asyncio
async def test_expired_persisted_captcha_is_consumed_after_restart():
    class Port:
        def __init__(self):
            self.values = []

        async def update_chat_setting_key(self, chat_id, key, value):
            self.values.append((chat_id, key, value))

    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    port = Port()
    message = Message("anything")
    consumed = await GuardianTelegramService().enforce_pending_captcha(
        bot=Bot(), chat_id=-100, user_id=7, message=message,
        settings={
            "captcha_enabled": True,
            "captcha_timeout_seconds": 300,
            "guardian_pending_captcha": {"7": {"created_at": old, "type": "text", "answer": "x"}},
        },
        is_admin=False, data_port=port,
    )
    assert consumed is True
    assert port.values[-1][2] == {}
    assert message.replies == []


@pytest.mark.asyncio
async def test_transport_profiles_and_bounded_purge_are_available():
    bot = Bot()
    service = GuardianTelegramService()
    assert await service.apply_admin_permissions(bot=bot, chat_id=-100, user_id=8, profile="moderator")
    assert await service.unpin_all(bot=bot, chat_id=-100)
    assert await service.purge_range(bot=bot, chat_id=-100, end_message_id=20, amount=3) == 3
    assert [item[0] for item in bot.calls] == ["promote", "unpin_all", "delete", "delete", "delete"]


@pytest.mark.asyncio
async def test_math_captcha_accepts_exact_answer_and_persists_consumption():
    class Port:
        def __init__(self):
            self.values = []

        async def update_chat_setting_key(self, chat_id, key, value):
            self.values.append((chat_id, key, value))

    message = Message("15")
    port = Port()
    bot = Bot()
    consumed = await GuardianTelegramService().enforce_pending_captcha(
        bot=bot, chat_id=-100, user_id=7, message=message,
        settings={"captcha_enabled": True, "guardian_pending_captcha": {"7": {"type": "math", "answer": "15", "message_id": 44}}},
        is_admin=False, data_port=port,
    )
    assert consumed is True
    assert message.deleted is False
    assert message.replies
    assert port.values[-1][2] == {}
    assert any(item[0] == "restrict" for item in bot.calls)


@pytest.mark.asyncio
async def test_administrator_listing_is_bounded_and_safe():
    rows = await GuardianTelegramService().list_administrators(bot=Bot(), chat_id=-100)
    assert rows == [{"user_id": 8, "name": "Arel Admin", "username": "areladmin", "status": "administrator"}]


@pytest.mark.asyncio
async def test_night_permissions_are_applied_through_transport_service():
    bot = Bot()
    assert await GuardianTelegramService().set_night_permissions(bot=bot, chat_id=-100, locked=True)
    assert bot.calls[-1][0] == "permissions"
    assert bot.calls[-1][1]["chat_id"] == -100


@pytest.mark.asyncio
async def test_username_target_is_resolved_only_from_persisted_member_and_verified_in_chat():
    service = GuardianTelegramService()
    resolved = await service.resolve_member_username(
        bot=ResolverBot(), chat_id=-100, username="@Hedef", data_port=ResolverPort()
    )
    assert resolved == 42


@pytest.mark.asyncio
async def test_unknown_username_target_fails_closed():
    service = GuardianTelegramService()
    resolved = await service.resolve_member_username(
        bot=ResolverBot(), chat_id=-100, username="@bilinmeyen", data_port=ResolverPort()
    )
    assert resolved is None

