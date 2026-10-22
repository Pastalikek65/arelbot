from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.handlers.callback_handler import CallbackHandler
from src.handlers.command_handler import CommandHandler
from src.services.sticker_pack_service import StickerPackService
from src.storage.state_scope import set_scoped_state


class _Database:
    def __init__(self):
        self.pack = {
            "_id": "pack-id",
            "pack_name": "arel_fun_by_bot",
            "pack_title": "Eski Paket",
            "user_id": 42,
        }
        self.deleted = []
        self.deleted_stickers = []

    async def get_pack_by_name(self, pack_name, user_id=None):
        if pack_name != self.pack["pack_name"]:
            return None
        if user_id is not None and int(user_id) != self.pack["user_id"]:
            return None
        return dict(self.pack)

    async def update_sticker_pack(self, pack_name, user_id, updates):
        self.pack.update(updates)
        return dict(self.pack)

    async def update_sticker_emojis(self, *args):
        return None

    async def delete_sticker_pack(self, pack_name, user_id):
        self.deleted.append((pack_name, user_id))
        return True

    async def delete_sticker_from_db(self, pack_id, file_id):
        self.deleted_stickers.append((pack_id, file_id))


class _Bot:
    def __init__(self):
        self.calls = []

    async def set_sticker_set_title(self, name, title):
        self.calls.append(("rename", name, title))
        return True

    async def set_sticker_emoji_list(self, sticker, emoji_list):
        self.calls.append(("emoji", sticker, tuple(emoji_list)))
        return True

    async def set_sticker_position_in_set(self, sticker, position):
        self.calls.append(("position", sticker, position))
        return True

    async def delete_sticker_set(self, name):
        self.calls.append(("delete", name))
        return True

    async def delete_sticker_from_set(self, sticker):
        self.calls.append(("delete_sticker", sticker))
        return True


class _Message:
    def __init__(self, reply_to_message=None):
        self.reply_to_message = reply_to_message
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))
        return SimpleNamespace()


class _PacksPort:
    async def get_user_packs(self, user_id):
        return []


class _BotInfo:
    username = "arel_test_bot"


class _Context:
    bot = SimpleNamespace(get_me=AsyncMock(return_value=_BotInfo()))


@pytest.mark.asyncio
async def test_stpack_rename_is_owner_scoped_and_uses_typed_service():
    db = _Database()
    bot = _Bot()
    message = _Message()
    update = SimpleNamespace(
        message=message,
        effective_message=message,
        effective_user=SimpleNamespace(id=42),
        effective_chat=SimpleNamespace(id=42, type="private"),
    )
    context = SimpleNamespace(args=["yenile", "arel_fun_by_bot", "Yeni", "Başlık"], bot=bot)
    handler = CommandHandler(
        SimpleNamespace(db=db), None, None, None,
        sticker_pack_service=StickerPackService(db),
    )

    await handler.handle_stpack_command(update, context)

    assert bot.calls == [("rename", "arel_fun_by_bot", "Yeni Başlık")]
    assert "güncellendi" in message.replies[-1][0]


@pytest.mark.asyncio
async def test_packs_command_explains_pending_private_wizard_step():
    message = _Message()
    update = SimpleNamespace(
        message=message,
        effective_message=message,
        effective_user=SimpleNamespace(id=42),
        effective_chat=SimpleNamespace(id=42, type="private"),
    )
    handler = CommandHandler(
        SimpleNamespace(db=object()), None, None, None,
        data_port=_PacksPort(),
    )
    handler.user_states = {}
    set_scoped_state(
        handler.user_states,
        user_id=42,
        chat_id=42,
        value={"state": "awaiting_pack_emoji", "data": {}},
    )

    await handler.handle_packs_command(update, _Context())

    assert "1-3 emoji" in message.replies[-1][0]
    assert "özel sohbette" in message.replies[-1][0]
    markup = message.replies[-1][1]["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data == "stk_cancel_pack:42"


@pytest.mark.asyncio
async def test_packs_command_in_group_offers_private_chat_button():
    message = _Message()
    update = SimpleNamespace(
        message=message,
        effective_message=message,
        effective_user=SimpleNamespace(id=42),
        effective_chat=SimpleNamespace(id=-10042, type="supergroup"),
    )
    handler = CommandHandler(
        SimpleNamespace(db=object()), None, None, None,
        data_port=_PacksPort(),
    )

    await handler.handle_packs_command(update, _Context())

    markup = message.replies[-1][1]["reply_markup"]
    assert markup.inline_keyboard[0][0].url == "https://t.me/arel_test_bot"
    assert "özel sohbette" in message.replies[-1][0]


@pytest.mark.asyncio
async def test_stpack_delete_callback_requires_owner_before_remote_delete():
    db = _Database()
    bot = _Bot()
    service = StickerPackService(db)
    handler = CallbackHandler(SimpleNamespace(db=db), SimpleNamespace(owner_id=1), None, sticker_pack_service=service)

    class Query:
        def __init__(self):
            self.data = "stpack:delete:42:arel_fun_by_bot"
            self.answers = []
            self.edited = []

        async def answer(self, *args, **kwargs):
            self.answers.append((args, kwargs))

        async def edit_message_text(self, text, **kwargs):
            self.edited.append((text, kwargs))

    query = Query()
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=7),
        effective_chat=SimpleNamespace(id=7, type="private"),
    )
    context = SimpleNamespace(bot=bot, chat_data={})

    await handler.handle_callback_query(update, context)

    assert bot.calls == []
    assert db.deleted == []
    assert any(args and "başka" in str(args[0]).lower() for args, _ in query.answers)


@pytest.mark.asyncio
async def test_stpack_delete_callback_deletes_after_owner_confirmation():
    db = _Database()
    bot = _Bot()
    service = StickerPackService(db)
    handler = CallbackHandler(SimpleNamespace(db=db), SimpleNamespace(owner_id=1), None, sticker_pack_service=service)

    class Query:
        data = "stpack:delete:42:arel_fun_by_bot"

        async def answer(self, *args, **kwargs):
            return None

        async def edit_message_text(self, text, **kwargs):
            self.text = text

    query = Query()
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=42),
        effective_chat=SimpleNamespace(id=42, type="private"),
    )
    context = SimpleNamespace(bot=bot, chat_data={})

    await handler.handle_callback_query(update, context)

    assert bot.calls == [("delete", "arel_fun_by_bot")]
    assert db.deleted == [("arel_fun_by_bot", 42)]
    assert "silindi" in query.text


@pytest.mark.asyncio
async def test_sticker_delete_confirmation_uses_persistent_short_token_and_owner_scope():
    db = _Database()
    bot = _Bot()
    service = StickerPackService(db)
    handler = CallbackHandler(SimpleNamespace(db=db), SimpleNamespace(owner_id=1), None, sticker_pack_service=service)
    handler.user_states = {
        42: {
            "state": "awaiting_sticker_delete_confirmation",
            "data": {"token": "abc123", "pack_name": "arel_fun_by_bot", "file_id": "sticker-file"},
        }
    }

    class Query:
        data = "stpack:sticker_delete:42:abc123"

        async def answer(self, *args, **kwargs):
            return None

        async def edit_message_text(self, text, **kwargs):
            self.text = text

    query = Query()
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=42),
        effective_chat=SimpleNamespace(id=42, type="private"),
    )
    context = SimpleNamespace(bot=bot, chat_data={})

    await handler.handle_callback_query(update, context)

    assert bot.calls == [("delete_sticker", "sticker-file")]
    assert db.deleted_stickers == [("pack-id", "sticker-file")]
    assert 42 not in handler.user_states
    assert "silindi" in query.text


@pytest.mark.asyncio
async def test_sticker_command_delegates_to_injected_media_handler():
    media_handler = SimpleNamespace(handle_sticker_command=AsyncMock())
    handler = CommandHandler(None, None, None, None, media_handler=media_handler)
    update = SimpleNamespace(effective_message=SimpleNamespace())
    class Bot:
        async def get_me(self):
            return SimpleNamespace(username="arelbot")

    context = SimpleNamespace(bot=Bot())

    await handler.handle_sticker_command(update, context)

    media_handler.handle_sticker_command.assert_awaited_once_with(update, context)


@pytest.mark.asyncio
async def test_sticker_creation_fsm_persists_title_emoji_and_waits_for_name():
    from src.handlers.message_handler import MessageHandler

    sent = []

    class Message:
        text = "Arel Koleksiyonu"

        async def reply_text(self, text, **kwargs):
            sent.append((text, kwargs))

    handler = MessageHandler(None, None, None, None)
    handler.user_states = {42: {"state": "awaiting_pack_title", "data": {"file_id": "f1", "is_video": False}}}
    update = SimpleNamespace(
        message=Message(),
        effective_message=Message(),
        effective_user=SimpleNamespace(id=42),
        effective_chat=SimpleNamespace(id=42, type="private"),
    )
    class Bot:
        async def get_me(self):
            return SimpleNamespace(username="arelbot")

    context = SimpleNamespace(bot=Bot())

    await handler._handle_pack_title_input(update, context)
    assert handler.user_states[42]["state"] == "awaiting_pack_emoji"
    assert "1-3 emoji" in sent[-1][0]
    assert "**" not in sent[-1][0]

    update.message.text = "😀 😂"
    await handler._handle_pack_emoji_input(update, context)
    assert handler.user_states[42]["state"] == "awaiting_pack_name"
    assert handler.user_states[42]["data"]["emojis"] == ["😀", "😂"]
    update.message.text = "arel_komik"
    await handler._handle_pack_name_input(update, context)
    assert handler.user_states[42]["state"] == "awaiting_pack_confirmation"
    keyboard = sent[-1][1]["reply_markup"]
    assert keyboard.inline_keyboard[0][0].callback_data == "stk_confirm_pack:42"
    assert sent


@pytest.mark.asyncio
async def test_sticker_creation_confirmation_calls_telegram_once_and_clears_state():
    from src.handlers.message_handler import MessageHandler

    class DB:
        available = False

        def __init__(self):
            self.created = []
            self.registered = []

        async def create_sticker_pack(self, value):
            self.created.append(value)
            return value.get("_id")

        async def get_user_packs(self, user_id, pack_type=None):
            return [pack for pack in self.created if pack["user_id"] == int(user_id)]

        async def register_sticker(self, *args):
            self.registered.append(args)

        async def get_game_state(self, *args):
            return None

    class Bot(_Bot):
        async def create_new_sticker_set(self, **kwargs):
            self.calls.append(("create", kwargs))
            return True

    db = DB()
    bot = Bot()
    message_handler = MessageHandler(SimpleNamespace(db=db), None, None, None)
    message_handler.user_states = {
        42: {
            "state": "awaiting_pack_confirmation",
            "data": {
                "file_id": "file-1",
                "full_pack_name": "arel_fun_by_bot",
                "title": "Arel Fun",
                "emojis": ["😀", "😂"],
                "is_video": False,
            },
        }
    }
    handler = CallbackHandler(SimpleNamespace(db=db), SimpleNamespace(owner_id=1), None)
    handler.message_handler = message_handler

    class Query:
        data = "stk_confirm_pack:42"
        text = None

        async def answer(self, *args, **kwargs):
            return None

        async def edit_message_text(self, text, **kwargs):
            self.text = text

    query = Query()
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=42),
        effective_chat=SimpleNamespace(id=42, type="private"),
    )
    context = SimpleNamespace(bot=bot, chat_data={})

    await handler.handle_callback_query(update, context)

    assert bot.calls[0][0] == "create"
    assert list(bot.calls[0][1]["stickers"][0].emoji_list) == ["😀", "😂"]
    assert db.created[0]["pack_name"] == "arel_fun_by_bot"
    assert 42 not in message_handler.user_states
    assert "Paket oluşturuldu" in query.text

    # Verify the user-facing recovery path: /packs exposes the pack and a direct link button.
    from src.handlers.command_handler import CommandHandler

    packs_message = _Message()
    packs_update = SimpleNamespace(
        message=packs_message,
        effective_message=packs_message,
        effective_user=SimpleNamespace(id=42),
        effective_chat=SimpleNamespace(id=42, type="private"),
    )
    packs_handler = CommandHandler(SimpleNamespace(db=db), None, None, None)
    await packs_handler.handle_packs_command(packs_update, SimpleNamespace(args=[]))
    rendered, options = packs_message.replies[-1]
    assert "Arel Fun" in rendered
    assert "https://t.me/addstickers/arel_fun_by_bot" in rendered
    assert options["reply_markup"].inline_keyboard[0][0].url == "https://t.me/addstickers/arel_fun_by_bot"


@pytest.mark.asyncio
async def test_sticker_creation_does_not_claim_success_when_pack_persistence_is_unavailable():
    """A remote Telegram set must have a durable owner record before success."""
    from src.handlers.message_handler import MessageHandler

    class DB:
        async def create_sticker_pack(self, value):
            # This mirrors the old degraded Database implementation, which
            # silently returned when MongoDB was unavailable.
            return None

        async def register_sticker(self, *args):
            return None

    class Bot:
        async def create_new_sticker_set(self, **kwargs):
            return True

    handler = MessageHandler(SimpleNamespace(db=DB()), None, None, None)
    handler.user_states = {
        42: {
            "state": "awaiting_pack_confirmation",
            "data": {
                "file_id": "file-1",
                "full_pack_name": "arel_fun_by_bot",
                "title": "Arel Fun",
                "emojis": ["😀"],
                "is_video": False,
            },
        }
    }

    with pytest.raises(RuntimeError, match="STICKER_PACK_PERSISTENCE_UNAVAILABLE"):
        await handler.confirm_pack_creation(42, 42, SimpleNamespace(bot=Bot()))


@pytest.mark.asyncio
async def test_pack_confirmation_reports_persistence_failure_and_removes_orphan_remote_set():
    from src.handlers.message_handler import MessageHandler

    class DB:
        async def create_sticker_pack(self, value):
            return None

        async def register_sticker(self, *args):
            return None

    class Bot:
        def __init__(self):
            self.calls = []

        async def create_new_sticker_set(self, **kwargs):
            self.calls.append(("create", kwargs["name"]))
            return True

        async def delete_sticker_set(self, name):
            self.calls.append(("delete", name))
            return True

    class Query:
        data = "stk_confirm_pack:42"

        async def answer(self, *args, **kwargs):
            return None

        async def edit_message_text(self, text, **kwargs):
            self.text = text

    db = DB()
    bot = Bot()
    message_handler = MessageHandler(SimpleNamespace(db=db), None, None, None)
    message_handler.user_states = {
        42: {
            "state": "awaiting_pack_confirmation",
            "data": {
                "file_id": "file-1",
                "full_pack_name": "arel_fun_by_bot",
                "title": "Arel Fun",
                "emojis": ["\U0001f600"],
                "is_video": False,
            },
        }
    }
    callback_handler = CallbackHandler(SimpleNamespace(db=db), SimpleNamespace(owner_id=1), None)
    callback_handler.message_handler = message_handler
    query = Query()
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=42),
        effective_chat=SimpleNamespace(id=42, type="private"),
    )

    await callback_handler.handle_callback_query(update, SimpleNamespace(bot=bot, chat_data={}))

    assert bot.calls == [("create", "arel_fun_by_bot"), ("delete", "arel_fun_by_bot")]
    assert "Paket olu\u015fturuldu" not in query.text
    assert "kal\u0131c\u0131 kayd\u0131 tamamlanamad\u0131" in query.text
