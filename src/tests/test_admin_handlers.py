"""Orchestrator yönetim komutlarının yetki ve çıktı sözleşmesi testleri."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.orchestrator import Orchestrator


class FakeSudoManager:
    def __init__(self, owner_id=1, sudos=None):
        self.owner_id = owner_id
        self.sudos = set(sudos or {owner_id})
        self.blacklist_users = set()
        self.blacklist_groups = set()

    def is_owner(self, user_id):
        return user_id == self.owner_id

    def is_sudo(self, user_id):
        return user_id in self.sudos

    def add_sudo(self, user_id):
        self.sudos.add(user_id)

    def remove_sudo(self, user_id):
        self.sudos.discard(user_id)

    def ban_user(self, user_id):
        self.blacklist_users.add(user_id)

    def unban_user(self, user_id):
        self.blacklist_users.discard(user_id)

    def ban_group(self, chat_id):
        self.blacklist_groups.add(chat_id)

    def unban_group(self, chat_id):
        self.blacklist_groups.discard(chat_id)


class FakeMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))


def make_handler(*, owner_id=1, sudos=None, chat_ids=None):
    handler = object.__new__(Orchestrator)
    handler.sudo_manager = FakeSudoManager(owner_id, sudos)
    handler.chat_engine = SimpleNamespace(
        get_active_chat_ids=AsyncMock(return_value=list(chat_ids or [])),
        db=SimpleNamespace(add_audit_log=AsyncMock()),
    )
    return handler


def make_update(user_id, message=None):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        effective_message=message or FakeMessage(),
    )


class GroupMessage(FakeMessage):
    def __init__(self, *, reply_user_id=None):
        super().__init__()
        self.reply_to_message = (
            SimpleNamespace(from_user=SimpleNamespace(id=reply_user_id))
            if reply_user_id is not None else None
        )


class GroupBot:
    def __init__(self, *, actor_status="administrator"):
        self.actor_status = actor_status
        self.banned = []
        self.unbanned = []

    async def get_chat_member(self, chat_id, user_id):
        if user_id == 42:
            return SimpleNamespace(status=self.actor_status)
        return SimpleNamespace(status="member")

    async def ban_chat_member(self, chat_id, user_id, **kwargs):
        self.banned.append((chat_id, user_id, kwargs))

    async def unban_chat_member(self, chat_id, user_id, **kwargs):
        self.unbanned.append((chat_id, user_id, kwargs))


@pytest.mark.asyncio
async def test_sudo_commands_require_owner_and_write_audit():
    handler = make_handler(owner_id=1)
    denied_message = FakeMessage()
    await handler.handle_sudo_command(
        make_update(2, denied_message), SimpleNamespace(args=["42"])
    )
    assert denied_message.replies == []
    assert 42 not in handler.sudo_manager.sudos

    message = FakeMessage()
    await handler.handle_sudo_command(
        make_update(1, message), SimpleNamespace(args=["42"])
    )
    assert 42 in handler.sudo_manager.sudos
    assert "42" in message.replies[-1][0]
    handler.chat_engine.db.add_audit_log.assert_awaited_once_with(
        1, "sudo_add", target_id=42
    )


@pytest.mark.asyncio
async def test_ban_and_unban_support_users_and_groups():
    handler = make_handler(owner_id=1)

    await handler.handle_ban_command(
        make_update(1, FakeMessage()), SimpleNamespace(args=["123"])
    )
    await handler.handle_ban_command(
        make_update(1, FakeMessage()), SimpleNamespace(args=["-100123"])
    )
    assert 123 in handler.sudo_manager.blacklist_users
    assert -100123 in handler.sudo_manager.blacklist_groups

    await handler.handle_unban_command(
        make_update(1, FakeMessage()), SimpleNamespace(args=["123"])
    )
    await handler.handle_unban_command(
        make_update(1, FakeMessage()), SimpleNamespace(args=["-100123"])
    )
    assert not handler.sudo_manager.blacklist_users
    assert not handler.sudo_manager.blacklist_groups
    assert handler.chat_engine.db.add_audit_log.await_count == 4


@pytest.mark.asyncio
async def test_group_ban_and_unban_use_telegram_membership_actions_for_admins():
    handler = make_handler(owner_id=1)
    bot = GroupBot()
    message = GroupMessage(reply_user_id=77)
    update = make_update(42, message)
    update.effective_chat = SimpleNamespace(id=-10042, type="supergroup")
    context = SimpleNamespace(args=[], bot=bot)

    await handler.handle_ban_command(update, context)
    await handler.handle_unban_command(update, context)

    assert bot.banned == [(-10042, 77, {"revoke_messages": True})]
    assert bot.unbanned == [(-10042, 77, {"only_if_banned": True})]
    assert "uzaklaştırıldı" in message.replies[0][0]
    assert "çıkarıldı" in message.replies[1][0]


@pytest.mark.asyncio
async def test_group_ban_requires_group_admin_and_reports_missing_target():
    handler = make_handler(owner_id=1)
    bot = GroupBot(actor_status="member")
    message = GroupMessage()
    update = make_update(42, message)
    update.effective_chat = SimpleNamespace(id=-10042, type="group")
    context = SimpleNamespace(args=[], bot=bot)

    await handler.handle_ban_command(update, context)
    assert message.replies
    assert "yönetici" in message.replies[0][0]
    assert bot.banned == []


@pytest.mark.asyncio
async def test_broadcast_escapes_html_and_audits_delivery_count():
    handler = make_handler(owner_id=1, chat_ids=[7, -100])
    sent = []

    class Bot:
        async def send_message(self, **kwargs):
            sent.append(kwargs)

    message = FakeMessage()
    await handler.handle_broadcast_command(
        make_update(1, message),
        SimpleNamespace(args=["<b>uyarı</b>", "&", "güvenli"], bot=Bot()),
    )

    assert len(sent) == 2
    assert "&lt;b&gt;uyarı&lt;/b&gt; &amp; güvenli" in sent[0]["text"]
    assert all(item["parse_mode"] == "HTML" for item in sent)
    handler.chat_engine.db.add_audit_log.assert_awaited_once_with(
        1, "broadcast", delivered=2
    )
    assert "2" in message.replies[-1][0]


@pytest.mark.asyncio
async def test_groups_escapes_titles_before_html_rendering():
    handler = make_handler(owner_id=1, chat_ids=[-100, -200, 7])

    class Bot:
        async def get_chat(self, chat_id):
            return SimpleNamespace(title="<Admin> & ekip" if chat_id == -100 else None)

    message = FakeMessage()
    await handler.handle_groups_command(
        make_update(1, message), SimpleNamespace(args=[], bot=Bot())
    )

    text = message.replies[-1][0]
    assert "&lt;Admin&gt; &amp; ekip" in text
    assert "-100" in text and "-200" in text
    assert "<Admin>" not in text


@pytest.mark.asyncio
async def test_start_escapes_user_name_and_renders_menu():
    handler = make_handler(owner_id=1)

    class Message(FakeMessage):
        def __init__(self):
            super().__init__()
            self.photo = None

        async def reply_photo(self, photo, caption, **kwargs):
            self.photo = (photo, caption, kwargs)

    class Bot:
        async def get_me(self):
            return SimpleNamespace(username="arelbot")

    message = Message()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1, first_name="<kullanıcı>"),
        effective_message=message,
    )
    await handler.handle_start_command(update, SimpleNamespace(args=[], bot=Bot()))

    assert message.photo is not None
    assert "&lt;kullanıcı&gt;" in message.photo[1]
    assert "<kullanıcı>" not in message.photo[1]
    assert message.photo[2]["reply_markup"].inline_keyboard[0][0].callback_data == "menu_main"


@pytest.mark.asyncio
async def test_start_falls_back_to_text_when_card_delivery_fails():
    handler = make_handler(owner_id=1)

    class Message(FakeMessage):
        async def reply_photo(self, *args, **kwargs):
            raise RuntimeError("telegram photo unavailable")

    class Bot:
        async def get_me(self):
            return SimpleNamespace(username="arelbot")

    message = Message()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1, first_name="Ada"),
        effective_message=message,
    )
    await handler.handle_start_command(update, SimpleNamespace(args=[], bot=Bot()))

    assert message.replies
    assert "Hoş Geldiniz" in message.replies[-1][0]


@pytest.mark.asyncio
async def test_help_renders_categorized_callback_surface():
    handler = make_handler(owner_id=1)
    message = FakeMessage()
    await handler.handle_help_command(
        make_update(1, message), SimpleNamespace(args=[], bot=SimpleNamespace())
    )

    markup = message.replies[-1][1]["reply_markup"]
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert "help_cat_ai" in callbacks
    assert "help_cat_sticker" in callbacks
