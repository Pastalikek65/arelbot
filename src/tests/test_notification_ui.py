from src.services.notification_ui import (
    category_help,
    crypto_tracking_panel,
    feed_tracking_panel,
    group_center,
    personal_center,
    notification_preferences_panel,
    notification_timezone_panel,
    price_tracking_panel,
    render_rows,
)

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.handlers.callback_handler import CallbackHandler
from src.features.city_directory import CityDirectory
from src.storage.subscription_store import SubscriptionStore
from src.storage.notification_preference_store import NotificationPreferenceStore
from src.services.subscription_service import SubscriptionService
from src.handlers.command_handler import CommandHandler


def test_personal_notification_center_is_category_driven():
    text, markup = personal_center(42)
    assert "Bildirim merkezi" in text
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert "notify_weather:42" in callbacks
    assert "notify_earthquake:42" in callbacks
    assert "notify_crypto:42" in callbacks
    assert "notify_pref:show:42" in callbacks


def test_notification_preferences_expose_bounded_timezone_controls():
    text, markup = notification_preferences_panel(
        42,
        {"timezone": "Europe/Istanbul", "quiet_hours_enabled": False, "quiet_start": "23:00", "quiet_end": "08:00"},
    )
    assert "Europe/Istanbul" in text
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert "notify_pref:timezone:42" in callbacks
    assert all(len(callback.encode("utf-8")) <= 64 for callback in callbacks)


def test_timezone_panel_uses_safe_tokens_and_owner_scope():
    text, markup = notification_timezone_panel(42, "Europe/Istanbul")
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert "Saat dilimi" in text
    assert "notify_pref:tz:42:Europe~Istanbul" in callbacks
    assert all(callback.startswith("notify_pref:tz:42:") or callback == "notify_pref:show:42" for callback in callbacks)


def test_group_center_keeps_mutations_admin_only():
    member_text, member_markup = group_center(7, is_admin=False)
    assert "yalnızca grup yöneticileri" in member_text
    assert all("notify_group_" in (button.callback_data or "") for row in member_markup.inline_keyboard for button in row)

    admin_text, admin_markup = group_center(7, is_admin=True)
    assert "Planlı duyuru" in admin_text
    assert any(button.callback_data == "notify_group_weather:7" for row in admin_markup.inline_keyboard for button in row)
    callbacks = [button.callback_data for row in admin_markup.inline_keyboard for button in row]
    assert "notify_group_air_quality:7" in callbacks
    assert "notify_group_news:7" in callbacks


def test_group_center_does_not_expose_schedule_mutations_to_members():
    _, markup = group_center(7, is_admin=False)
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert callbacks == ["notify_group_list:7"]


def test_render_rows_escapes_payload_and_scopes_stop_callback():
    text, markup = render_rows(
        [{
            "enabled": True,
            "feature_key": "weather",
            "params": {"city": "<İstanbul>"},
            "schedule_kind": "daily",
            "subscription_id": "abcdef123456",
        }],
        owner_id=42,
    )
    assert "&lt;İstanbul&gt;" in text
    assert markup.inline_keyboard[0][0].callback_data == "notify_stop:42:abcdef123456"


def test_category_help_uses_stable_command_examples():
    text, markup = category_help("prices", 5)
    assert "/alarm kur usd 40 üstü" in text
    assert markup.inline_keyboard[0][0].callback_data == "notify_center:5"


@pytest.mark.asyncio
async def test_notification_center_callback_is_owner_scoped():
    query = SimpleNamespace(
        data="notify_center:42",
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
        message=SimpleNamespace(message_id=1),
    )
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=42),
        effective_chat=SimpleNamespace(id=42, type="private"),
    )
    context = SimpleNamespace(chat_data={}, bot=SimpleNamespace())
    handler = CallbackHandler(
        SimpleNamespace(db=SimpleNamespace(reachable=False)),
        SimpleNamespace(owner_id=999),
        None,
    )
    await handler.handle_callback_query(update, context)
    query.edit_message_text.assert_awaited_once()
    assert "Bildirim merkezi" in query.edit_message_text.await_args.args[0]


@pytest.mark.asyncio
async def test_notification_timezone_callback_updates_owner_preference():
    store = NotificationPreferenceStore()
    handler = CallbackHandler(
        SimpleNamespace(db=SimpleNamespace(reachable=False)),
        SimpleNamespace(owner_id=999),
        None,
        notification_preference_store=store,
    )
    query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())
    update = SimpleNamespace(effective_user=SimpleNamespace(id=42))

    await handler._handle_notification_preference_callback(
        update, query, "notify_pref:tz:42:Asia~Tokyo"
    )

    assert (await store.get(42))["timezone"] == "Asia/Tokyo"
    assert "Asia/Tokyo" in query.edit_message_text.await_args.args[0]


@pytest.mark.asyncio
async def test_notification_settings_command_is_private_and_renders_panel():
    replies = []

    class Message:
        async def reply_text(self, text, **kwargs):
            replies.append((text, kwargs))

    handler = CommandHandler(
        chat_engine=SimpleNamespace(),
        sudo_manager=None,
        image_router=None,
        tool_manager=None,
        notification_preference_store=NotificationPreferenceStore(),
    )
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42),
        effective_chat=SimpleNamespace(id=42, type="private"),
        effective_message=Message(),
    )
    await handler.handle_notification_settings_command(update, SimpleNamespace(args=[]))

    assert replies and "Bildirim ayarların" in replies[0][0]
    assert replies[0][1]["reply_markup"]


def test_crypto_category_help_is_discoverable():
    text, markup = category_help("crypto", 5)
    assert "/alarm kur kripto btc" in text
    assert markup.inline_keyboard[0][0].callback_data == "notify_center:5"


def test_personal_tracking_panels_expose_bounded_owner_scoped_callbacks():
    for panel in (price_tracking_panel, crypto_tracking_panel, feed_tracking_panel):
        _, markup = panel(42)
        callbacks = [
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
            if button.callback_data
        ]
        assert callbacks
        assert all(callback.split(":")[1] == "42" for callback in callbacks if ":" in callback)
        assert all(len(callback.encode("utf-8")) <= 64 for callback in callbacks)


@pytest.mark.asyncio
async def test_personal_price_crypto_and_feed_callbacks_create_scoped_subscriptions():
    class Query:
        def __init__(self):
            self.edits = []
            self.answers = []

        async def answer(self, *args, **kwargs):
            self.answers.append((args, kwargs))

        async def edit_message_text(self, text, **kwargs):
            self.edits.append((text, kwargs))

    store = SubscriptionStore()
    service = SubscriptionService(store)
    handler = CallbackHandler(
        SimpleNamespace(db=SimpleNamespace()),
        SimpleNamespace(owner_id=999),
        None,
        subscription_service=service,
    )
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42),
        effective_chat=SimpleNamespace(id=42, type="private"),
    )
    context = SimpleNamespace(bot=SimpleNamespace())
    query = Query()

    await handler._handle_notification_navigation(update, query, context, "notify_prices:42")
    await handler._handle_notification_navigation(update, query, context, "notify_price_add:42:currency:3600")
    await handler._handle_notification_navigation(update, query, context, "notify_crypto_add:42:bitcoin:86400")
    await handler._handle_notification_navigation(update, query, context, "notify_feed_add:42:breaking_news:7200")

    rows = await store.list_for_user(42)
    assert {(row["feature_key"], row["params"].get("symbol")) for row in rows} == {
        ("currency", None),
        ("crypto", "bitcoin"),
        ("breaking_news", None),
    }
    assert all(row["scope"] == "user" and row["chat_id"] == 42 for row in rows)


@pytest.mark.asyncio
async def test_personal_tracking_callbacks_are_rejected_in_groups():
    class Query:
        def __init__(self):
            self.answers = []
            self.edits = []

        async def answer(self, *args, **kwargs):
            self.answers.append((args, kwargs))

        async def edit_message_text(self, text, **kwargs):
            self.edits.append((text, kwargs))

    handler = CallbackHandler(
        SimpleNamespace(db=SimpleNamespace()),
        SimpleNamespace(owner_id=999),
        None,
        subscription_service=SubscriptionService(SubscriptionStore()),
    )
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42),
        effective_chat=SimpleNamespace(id=-100, type="supergroup"),
    )
    query = Query()
    await handler._handle_notification_navigation(
        update, query, SimpleNamespace(bot=SimpleNamespace()), "notify_price_add:42:currency:3600"
    )
    assert not query.edits
    assert query.answers


@pytest.mark.asyncio
async def test_group_air_quality_schedule_is_admin_only_and_persistent():
    class Bot:
        async def get_chat_member(self, chat_id, user_id):
            return SimpleNamespace(status="administrator" if user_id == 9 else "member")

    class Query:
        def __init__(self):
            self.message = SimpleNamespace(message_thread_id=17)
            self.edits = []

        async def answer(self, *args, **kwargs):
            return None

        async def edit_message_text(self, text, **kwargs):
            self.edits.append((text, kwargs))

    store = SubscriptionStore()
    service = SubscriptionService(store)
    engine = SimpleNamespace(city_directory=CityDirectory(), db=SimpleNamespace())
    handler = CallbackHandler(engine, SimpleNamespace(owner_id=999), None, subscription_service=service)
    context = SimpleNamespace(bot=Bot())

    denied_update = SimpleNamespace(
        effective_user=SimpleNamespace(id=8),
        effective_chat=SimpleNamespace(id=-100, type="supergroup"),
    )
    denied_query = Query()
    await handler._handle_notification_navigation(
        denied_update, denied_query, context, "notify_group_air_quality:8"
    )
    assert not denied_query.edits

    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=9),
        effective_chat=SimpleNamespace(id=-100, type="supergroup"),
    )
    query = Query()
    await handler._handle_notification_navigation(update, query, context, "notify_group_air_quality:9")
    pick_callback = query.edits[-1][1]["reply_markup"].inline_keyboard[0][0].callback_data
    await handler._handle_notification_navigation(update, query, context, pick_callback)
    freq_callback = query.edits[-1][1]["reply_markup"].inline_keyboard[0][0].callback_data
    await handler._handle_notification_navigation(update, query, context, freq_callback)
    time_callback = query.edits[-1][1]["reply_markup"].inline_keyboard[0][0].callback_data
    await handler._handle_notification_navigation(update, query, context, time_callback)

    rows = await store.list_for_chat(-100)
    assert len(rows) == 1
    assert rows[0]["feature_key"] == "air_quality"
    assert rows[0]["params"]["city"]
    assert rows[0]["message_thread_id"] == 17


@pytest.mark.asyncio
async def test_group_news_schedule_uses_content_deduplication():
    class Bot:
        async def get_chat_member(self, chat_id, user_id):
            return SimpleNamespace(status="administrator")

    class Query:
        def __init__(self):
            self.message = SimpleNamespace(message_thread_id=None)
            self.edits = []

        async def answer(self, *args, **kwargs):
            return None

        async def edit_message_text(self, text, **kwargs):
            self.edits.append((text, kwargs))

    store = SubscriptionStore()
    service = SubscriptionService(store)
    handler = CallbackHandler(
        SimpleNamespace(db=SimpleNamespace()),
        SimpleNamespace(owner_id=999),
        None,
        subscription_service=service,
    )
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=9),
        effective_chat=SimpleNamespace(id=-100, type="group"),
    )
    context = SimpleNamespace(bot=Bot())
    query = Query()
    await handler._handle_notification_navigation(update, query, context, "notify_group_news:9")
    await handler._handle_notification_navigation(
        update, query, context,
        query.edits[-1][1]["reply_markup"].inline_keyboard[0][0].callback_data,
    )
    await handler._handle_notification_navigation(
        update, query, context,
        query.edits[-1][1]["reply_markup"].inline_keyboard[0][0].callback_data,
    )
    row = (await store.list_for_chat(-100))[0]
    assert row["feature_key"] == "breaking_news"
    assert row["dedupe_policy"] == "content"


@pytest.mark.asyncio
async def test_personal_air_quality_notification_is_private_and_owner_scoped():
    class Query:
        def __init__(self):
            self.edits = []

        async def answer(self, *args, **kwargs):
            return None

        async def edit_message_text(self, text, **kwargs):
            self.edits.append((text, kwargs))

    service = SubscriptionService(SubscriptionStore())
    handler = CallbackHandler(
        SimpleNamespace(city_directory=CityDirectory(), db=SimpleNamespace()),
        SimpleNamespace(owner_id=999),
        None,
        subscription_service=service,
    )
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42),
        effective_chat=SimpleNamespace(id=42, type="private"),
    )
    query = Query()
    context = SimpleNamespace(bot=SimpleNamespace())
    await handler._handle_notification_navigation(update, query, context, "notify_air_quality:42")
    pick = query.edits[-1][1]["reply_markup"].inline_keyboard[0][0].callback_data
    await handler._handle_notification_navigation(update, query, context, pick)
    add = query.edits[-1][1]["reply_markup"].inline_keyboard[0][0].callback_data
    await handler._handle_notification_navigation(update, query, context, add)
    rows = await service.store.list_for_user(42)
    assert len(rows) == 1
    assert rows[0]["feature_key"] == "air_quality"
    assert rows[0]["scope"] == "user"

    group_update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42),
        effective_chat=SimpleNamespace(id=-100, type="supergroup"),
    )
    denied_query = Query()
    await handler._handle_notification_navigation(group_update, denied_query, context, "notify_air_quality:42")
    assert not denied_query.edits


@pytest.mark.asyncio
async def test_group_city_selectors_are_paginated_and_personal_weather_is_group_scoped():
    class Bot:
        async def get_chat_member(self, chat_id, user_id):
            return SimpleNamespace(status="administrator")

    class Query:
        def __init__(self):
            self.edits = []
            self.answers = []

        async def answer(self, *args, **kwargs):
            self.answers.append((args, kwargs))

        async def edit_message_text(self, text, **kwargs):
            self.edits.append((text, kwargs))

    handler = CallbackHandler(
        SimpleNamespace(city_directory=CityDirectory(), db=SimpleNamespace()),
        SimpleNamespace(owner_id=999),
        None,
        subscription_service=SubscriptionService(SubscriptionStore()),
    )
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=9),
        effective_chat=SimpleNamespace(id=-100, type="supergroup"),
    )
    context = SimpleNamespace(bot=Bot())
    query = Query()

    await handler._handle_notification_navigation(update, query, context, "notify_group_weather:9")
    markup = query.edits[-1][1]["reply_markup"]
    page_next = next(
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data.startswith("notify_group_weather_page:")
    )
    assert len(markup.inline_keyboard) >= 10
    assert all(
        len(button.callback_data.encode("utf-8")) <= 64
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data
    )
    await handler._handle_notification_navigation(update, query, context, page_next)
    second_page = query.edits[-1][1]["reply_markup"]
    first_city_callback = second_page.inline_keyboard[0][0].callback_data
    assert first_city_callback == "notify_group_weather_pick:9:9"
    assert "İl sayfası 2/11" in query.edits[-1][0]

    private_weather = Query()
    await handler._handle_notification_navigation(update, private_weather, context, "notify_weather:9")
    assert private_weather.edits == []
    assert private_weather.answers
