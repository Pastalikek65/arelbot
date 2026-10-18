from types import SimpleNamespace

import pytest


class SettingsPort:
    def __init__(self, settings=None):
        self.settings = dict(settings or {})
        self.writes = []

    async def get_chat_settings(self, chat_id):
        return dict(self.settings)

    async def update_chat_setting_key(self, chat_id, key, value):
        self.settings[key] = value
        self.writes.append((chat_id, key, value))


class SpyMessage:
    def __init__(self, text="/q", chat_id=-100, chat_type="supergroup"):
        self.text = text
        self.message_id = 10
        self.chat = SimpleNamespace(id=chat_id, type=chat_type)
        self.reply_to_message = None
        self.calls = []

    async def reply_text(self, *args, **kwargs):
        self.calls.append(("reply_text", args, kwargs))
        return SimpleNamespace(message_id=99)

    async def reply_sticker(self, *args, **kwargs):
        self.calls.append(("reply_sticker", args, kwargs))


def _update(message, user_id=1):
    user = SimpleNamespace(id=user_id, full_name="Test", username="test")
    return SimpleNamespace(
        effective_user=user,
        effective_chat=message.chat,
        effective_message=message,
        message=message,
    )


@pytest.mark.asyncio
async def test_quote_commands_are_silent_when_quote_is_disabled():
    from src.handlers.quote_handler import QuoteHandler

    port = SettingsPort({"q_enabled": False})
    handler = QuoteHandler(
        SimpleNamespace(),
        SimpleNamespace(owner_id=1, is_sudo=lambda _user_id: True),
        data_port=port,
    )
    for method_name in ("handle_q_command", "handle_qtop_command", "handle_qrand_command", "handle_qd_command", "handle_qtemplate_command"):
        message = SpyMessage()
        update = _update(message)
        context = SimpleNamespace(args=[])
        await getattr(handler, method_name)(update, context)
        assert message.calls == [], method_name


@pytest.mark.asyncio
async def test_quote_option_commands_are_silent_when_quote_is_disabled():
    from src.handlers.command_handler import CommandHandler
    from src.storage.handler_data_port import HandlerDataPort

    port = SettingsPort({"q_enabled": False})
    handler = CommandHandler(
        SimpleNamespace(), SimpleNamespace(owner_id=1), None, None,
        data_port=HandlerDataPort(port),
    )
    for method_name, args in (
        ("handle_qcolor_command", ["#ffffff"]),
        ("handle_qemoji_command", ["✨"]),
        ("handle_qrate_command", []),
        ("handle_qtheme_command", ["dark"]),
    ):
        message = SpyMessage()
        update = _update(message)
        context = SimpleNamespace(args=args, bot=SimpleNamespace())
        await getattr(handler, method_name)(update, context)
        assert message.calls == [], method_name


@pytest.mark.asyncio
async def test_quote_callbacks_do_not_query_or_mutate_when_disabled():
    from src.handlers.callback_handler import CallbackHandler
    from src.storage.handler_data_port import HandlerDataPort

    class Query:
        message = SimpleNamespace(chat=SimpleNamespace(id=-100, type="supergroup"))

        async def answer(self, *args, **kwargs):
            self.answered = True

    class Port(SettingsPort):
        async def get_top_quotes(self, *args, **kwargs):
            raise AssertionError("quote callback queried while disabled")

        async def update_quote_rating(self, *args, **kwargs):
            raise AssertionError("quote callback mutated while disabled")

    handler = CallbackHandler(
        SimpleNamespace(), SimpleNamespace(owner_id=1), None,
        data_port=HandlerDataPort(Port({"q_enabled": False})),
    )
    update = _update(SpyMessage(), user_id=1)
    query = Query()
    await handler._handle_qtop_callback(update, query, SimpleNamespace(), "qtop_next_0")
    await handler._handle_qrate_callback(update, query, SimpleNamespace(), "qrate_up_1")


@pytest.mark.asyncio
async def test_group_settings_expose_media_toggle_and_admin_can_change_it():
    from src.handlers.callback_handler import CallbackHandler
    from src.storage.handler_data_port import HandlerDataPort

    class Bot:
        async def get_chat_member(self, chat_id, user_id):
            return SimpleNamespace(status="administrator")

    class Query:
        def __init__(self):
            self.message = SimpleNamespace(chat=SimpleNamespace(id=-100, type="supergroup"))
            self.markup = None

        async def answer(self, *args, **kwargs):
            pass

        async def edit_message_text(self, text, **kwargs):
            self.markup = kwargs.get("reply_markup")

        async def edit_message_reply_markup(self, reply_markup=None):
            self.markup = reply_markup

    port = SettingsPort({"q_enabled": True, "media_enabled": True})
    handler = CallbackHandler(
        SimpleNamespace(), SimpleNamespace(owner_id=1), None,
        data_port=HandlerDataPort(port),
    )
    update = _update(SpyMessage(), user_id=5)
    context = SimpleNamespace(bot=Bot())
    query = Query()
    await handler._handle_ayar_menu(update, query, context)
    callbacks = [button.callback_data for row in query.markup.inline_keyboard for button in row]
    assert "ayar_media_toggle" in callbacks
    await handler._handle_ayar_callback(update, query, context, "ayar_media_toggle")
    assert port.settings["media_enabled"] is False


@pytest.mark.asyncio
async def test_group_media_toggle_uses_enabled_default_when_setting_is_absent():
    from src.handlers.callback_handler import CallbackHandler
    from src.storage.handler_data_port import HandlerDataPort

    class Bot:
        async def get_chat_member(self, chat_id, user_id):
            return SimpleNamespace(status="administrator")

    class Query:
        def __init__(self):
            self.message = SimpleNamespace(chat=SimpleNamespace(id=-101, type="supergroup"))

        async def answer(self, *args, **kwargs):
            pass

        async def edit_message_reply_markup(self, reply_markup=None):
            pass

    port = SettingsPort()
    handler = CallbackHandler(
        SimpleNamespace(), SimpleNamespace(owner_id=1), None,
        data_port=HandlerDataPort(port),
    )
    update = _update(SpyMessage(chat_id=-101, chat_type="supergroup"), user_id=5)
    await handler._handle_ayar_callback(update, Query(), SimpleNamespace(bot=Bot()), "ayar_media_toggle")
    assert port.settings["media_enabled"] is False


@pytest.mark.asyncio
async def test_private_settings_can_toggle_quote_and_media_for_that_chat():
    from src.handlers.callback_handler import CallbackHandler
    from src.storage.handler_data_port import HandlerDataPort

    class Query:
        def __init__(self):
            self.message = SimpleNamespace(chat=SimpleNamespace(id=77, type="private"))

        async def answer(self, *args, **kwargs):
            self.answer_args = args

    port = SettingsPort({"q_enabled": True, "media_enabled": True})
    handler = CallbackHandler(
        SimpleNamespace(), SimpleNamespace(owner_id=1), None,
        data_port=HandlerDataPort(port),
    )
    update = _update(SpyMessage(chat_id=77, chat_type="private"), user_id=77)
    query = Query()
    await handler._handle_ayar_callback(update, query, SimpleNamespace(), "settings_quote_toggle")
    await handler._handle_ayar_callback(update, query, SimpleNamespace(), "settings_media_toggle")
    assert port.settings == {"q_enabled": False, "media_enabled": False}


@pytest.mark.asyncio
async def test_private_settings_toggle_is_reachable_from_callback_router():
    from src.handlers.callback_handler import CallbackHandler
    from src.storage.handler_data_port import HandlerDataPort

    class Query:
        data = "settings_media_toggle"
        message = SimpleNamespace(chat=SimpleNamespace(id=77, type="private"))

        async def answer(self, *args, **kwargs):
            pass

    port = SettingsPort({"media_enabled": True})
    handler = CallbackHandler(
        SimpleNamespace(), SimpleNamespace(owner_id=1), None,
        data_port=HandlerDataPort(port),
    )
    update = _update(SpyMessage(chat_id=77, chat_type="private"), user_id=77)
    update.callback_query = Query()
    await handler.handle_callback_query(update, SimpleNamespace())
    assert port.settings["media_enabled"] is False


@pytest.mark.asyncio
async def test_media_link_is_silent_when_disabled_and_open_by_default():
    from src.handlers.message_handler import MessageHandler
    from src.storage.handler_data_port import HandlerDataPort

    class Media:
        def __init__(self):
            self.calls = []

        async def submit(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return "job", False

    disabled = Media()
    message = SpyMessage(text="https://example.com/video")
    handler = MessageHandler(
        SimpleNamespace(), SimpleNamespace(is_blacklisted=lambda *_: False), None, None, None,
        data_port=HandlerDataPort(SettingsPort({"media_enabled": False})),
    )
    handler.media_service = disabled
    await handler.orchestrator_media_link(_update(message), SimpleNamespace(), "https://example.com/video")
    assert disabled.calls == []
    assert message.calls == []

    enabled = Media()
    message = SpyMessage(text="https://example.com/video")
    handler.data_port = HandlerDataPort(SettingsPort())
    handler.media_service = enabled
    await handler.orchestrator_media_link(_update(message), SimpleNamespace(), "https://example.com/video")
    assert len(enabled.calls) == 1
    assert message.calls


@pytest.mark.asyncio
async def test_direct_download_command_and_caption_path_honor_media_gate():
    from src.orchestrator import Orchestrator
    from src.storage.handler_data_port import HandlerDataPort

    class Status:
        message_id = 99

        async def edit_text(self, *args, **kwargs):
            pass

    class Message(SpyMessage):
        async def reply_text(self, *args, **kwargs):
            self.calls.append(("reply_text", args, kwargs))
            return Status()

    class Media:
        def __init__(self):
            self.calls = []

        async def submit(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return "job", False

    obj = object.__new__(Orchestrator)
    obj.sudo_manager = SimpleNamespace(is_blacklisted=lambda *_: False)
    obj.media_service = Media()
    obj.data_port = HandlerDataPort(SettingsPort({"media_enabled": False}))
    update = _update(Message(text="/indir https://example.com/video"))
    await obj._queue_media_download(update, "https://example.com/video")
    assert obj.media_service.calls == []

    obj.data_port = HandlerDataPort(SettingsPort())
    await obj._queue_media_download(update, "https://example.com/video")
    assert len(obj.media_service.calls) == 1


@pytest.mark.asyncio
async def test_inline_quote_search_is_empty_when_quote_is_disabled():
    from src.services.inline_query_service import InlineQueryService

    class Database:
        async def get_chat_settings(self, chat_id):
            return {"q_enabled": False}

        async def get_top_quotes(self, *args, **kwargs):
            raise AssertionError("inline quote search queried while disabled")

    service = InlineQueryService(SimpleNamespace(), Database())
    page = await service.search("top:-100")
    assert page.stickers == ()
