from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.services.menu_service import MenuService
from src.storage.notification_preference_store import NotificationPreferenceStore
from src.services.daily_brief_service import DailyBriefService
from src.features.contracts import FeatureResult


def _buttons(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row]


def test_main_menu_exposes_turkiye_catalog_notifications_and_daily_brief():
    text, markup = MenuService().main_menu("tr", user_id=42)
    callbacks = _buttons(markup)
    assert "menu_turkiye" in callbacks
    assert "bim_page:42:0" in callbacks
    assert "notify_center:42" in callbacks
    assert "daily_brief:42" in callbacks


def test_group_main_menu_exposes_group_notification_controls_instead_of_private_digest():
    _text, markup = MenuService().main_menu("tr", user_id=42, chat_type="supergroup")
    callbacks = _buttons(markup)
    assert "notify_center:42" in callbacks
    assert "daily_brief:42" not in callbacks


def test_private_settings_menu_is_actionable():
    _text, markup = MenuService().personal_settings_menu(42)
    callbacks = _buttons(markup)
    assert "settings_persona" in callbacks
    assert "daily_brief:42" in callbacks
    assert "settings_memory" in callbacks


def test_group_news_menu_routes_to_group_center():
    _text, markup = MenuService().news_menu(user_id=42, chat_type="supergroup")
    callbacks = _buttons(markup)
    assert "notify_center:42" in callbacks
    assert "notify_feeds:42" not in callbacks


def test_category_menus_have_actionable_navigation_buttons():
    service = MenuService()
    for renderer in (service.media_menu, service.news_menu, service.ai_menu):
        _text, markup = renderer()
        assert len(_buttons(markup)) >= 2


@pytest.mark.asyncio
async def test_word_game_category_callback_passes_selected_category_to_command(monkeypatch):
    from src.handlers.callback_handler import CallbackHandler

    captured = {}

    class FakeCommandHandler:
        def __init__(self, *args, **kwargs):
            return None

        def __getattr__(self, name):
            async def unused(*args, **kwargs):
                return None
            return unused

        async def handle_wordgame_command(self, update, context):
            captured["args"] = list(context.args)

    monkeypatch.setattr("src.handlers.command_handler.CommandHandler", FakeCommandHandler)

    class Query:
        message = SimpleNamespace(message_id=12)

        async def answer(self, *args, **kwargs):
            captured["answered"] = True

    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42, first_name="Arel"),
        effective_chat=SimpleNamespace(id=-100, type="supergroup"),
    )
    context = SimpleNamespace(args=[])
    handler = CallbackHandler(SimpleNamespace(), SimpleNamespace(owner_id=1), None)

    await handler._handle_game_start_callback(update, Query(), context, "game_word_category:bilim")

    assert captured["args"] == ["bilim"]
    assert captured["answered"] is True


@pytest.mark.asyncio
async def test_word_game_difficulty_callback_passes_selected_profile_to_command(monkeypatch):
    from src.handlers.callback_handler import CallbackHandler

    captured = {}

    class FakeCommandHandler:
        def __init__(self, *args, **kwargs):
            return None

        def __getattr__(self, name):
            async def unused(*args, **kwargs):
                return None
            return unused

        async def handle_wordgame_command(self, update, context):
            captured["args"] = list(context.args)

    monkeypatch.setattr("src.handlers.command_handler.CommandHandler", FakeCommandHandler)

    class Query:
        message = SimpleNamespace(message_id=12)

        async def answer(self, *args, **kwargs):
            captured["answered"] = True

    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42, first_name="Arel"),
        effective_chat=SimpleNamespace(id=-100, type="supergroup"),
    )
    context = SimpleNamespace(args=[])
    handler = CallbackHandler(SimpleNamespace(), SimpleNamespace(owner_id=1), None)

    await handler._handle_game_start_callback(update, Query(), context, "game_word_difficulty:expert")

    assert captured["args"] == ["expert"]
    assert captured["answered"] is True


@pytest.mark.asyncio
async def test_hangman_difficulty_callback_starts_hangman_with_selected_profile(monkeypatch):
    from src.handlers.callback_handler import CallbackHandler

    captured = {}

    class FakeCommandHandler:
        def __init__(self, *args, **kwargs):
            return None

        def __getattr__(self, name):
            async def unused(*args, **kwargs):
                return None
            return unused

        async def handle_hangman_command(self, update, context):
            captured["args"] = list(context.args)

    monkeypatch.setattr("src.handlers.command_handler.CommandHandler", FakeCommandHandler)

    class Query:
        message = SimpleNamespace(message_id=12)

        async def answer(self, *args, **kwargs):
            captured["answered"] = True

    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42, first_name="Arel"),
        effective_chat=SimpleNamespace(id=-100, type="supergroup"),
    )
    context = SimpleNamespace(args=[])
    handler = CallbackHandler(SimpleNamespace(), SimpleNamespace(owner_id=1), None)

    await handler._handle_game_start_callback(update, Query(), context, "game_hangman_difficulty:hard")

    assert captured["args"] == ["hard"]
    assert captured["answered"] is True


@pytest.mark.asyncio
async def test_hangman_letter_callback_refreshes_inline_keyboard():
    from src.handlers.callback_handler import CallbackHandler

    class Ledger:
        async def claim(self, *args, **kwargs):
            return True

    class DataPort:
        reachable = True

        async def save_game_state(self, *args, **kwargs):
            return None

        async def delete_game_state(self, *args, **kwargs):
            return None

        async def get_game_state(self, *args, **kwargs):
            return None

    edits = []

    class Query:
        message = SimpleNamespace(message_id=77)

        async def answer(self, *args, **kwargs):
            return None

        async def edit_message_text(self, text, **kwargs):
            edits.append((text, kwargs))

    state = {
        "word": "KEDI",
        "hidden": ["_", "_", "_", "_"],
        "lives": 6,
        "max_lives": 6,
        "guessed_letters": [],
        "difficulty": "normal",
        "difficulty_label": "Normal",
    }
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42, first_name="Arel", full_name="Arel"),
        effective_chat=SimpleNamespace(id=-100, type="supergroup"),
        effective_message=SimpleNamespace(message_id=80),
    )
    context = SimpleNamespace(chat_data={"hangman": state})
    handler = CallbackHandler(
        SimpleNamespace(),
        SimpleNamespace(owner_id=1),
        None,
        data_port=DataPort(),
        game_action_ledger=Ledger(),
    )

    await handler._handle_hangman_guess_callback(update, Query(), context, "hangman_guess:k")

    assert edits
    text, kwargs = edits[-1]
    assert "K _ _ _" in text
    callback_data = [
        button.callback_data
        for row in kwargs["reply_markup"].inline_keyboard
        for button in row
    ]
    assert "hangman_hint" in callback_data
    assert "hangman_stop" in callback_data


@pytest.mark.asyncio
async def test_word_game_hint_callback_refreshes_inline_keyboard():
    from src.handlers.callback_handler import CallbackHandler

    class Ledger:
        async def claim(self, *args, **kwargs):
            return True

    class DataPort:
        reachable = True

        async def save_game_state(self, *args, **kwargs):
            return None

        async def delete_game_state(self, *args, **kwargs):
            return None

        async def get_game_state(self, *args, **kwargs):
            return None

    edits = []

    class Query:
        message = SimpleNamespace(message_id=88)

        async def answer(self, *args, **kwargs):
            return None

        async def edit_message_text(self, text, **kwargs):
            edits.append((text, kwargs))

    state = {
        "letters": "KELIMELER",
        "category": "genel",
        "difficulty_label": "Normal",
        "target_count": 3,
        "found_words": [],
        "valid_words": ["kelime", "eller", "kiler"],
        "hints_used": 0,
    }
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42, first_name="Arel", full_name="Arel"),
        effective_chat=SimpleNamespace(id=-100, type="supergroup"),
        effective_message=SimpleNamespace(message_id=90),
    )
    context = SimpleNamespace(chat_data={"word_game": state})
    handler = CallbackHandler(
        SimpleNamespace(),
        SimpleNamespace(owner_id=1),
        None,
        data_port=DataPort(),
        game_action_ledger=Ledger(),
    )

    await handler._handle_word_game_control(update, Query(), context, "word_game_hint")

    assert edits
    text, kwargs = edits[-1]
    assert "İpucu" in text
    callbacks = [
        button.callback_data
        for row in kwargs["reply_markup"].inline_keyboard
        for button in row
    ]
    assert {"word_game_shuffle", "word_game_hint", "word_game_stop"}.issubset(callbacks)


@pytest.mark.asyncio
async def test_word_game_stop_callback_clears_owner_game():
    from src.handlers.callback_handler import CallbackHandler

    class Ledger:
        async def claim(self, *args, **kwargs):
            return True

    class DataPort:
        reachable = True

        async def delete_game_state(self, *args, **kwargs):
            return None

        async def get_game_state(self, *args, **kwargs):
            return None

    edits = []

    class Query:
        message = SimpleNamespace(message_id=89)

        async def answer(self, *args, **kwargs):
            return None

        async def edit_message_text(self, text, **kwargs):
            edits.append(text)

    state = {"owner_id": 42, "letters": "KELIMELER"}
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42),
        effective_chat=SimpleNamespace(id=-100, type="supergroup"),
    )
    context = SimpleNamespace(chat_data={"word_game": state})
    handler = CallbackHandler(
        SimpleNamespace(),
        SimpleNamespace(owner_id=1),
        None,
        data_port=DataPort(),
        game_action_ledger=Ledger(),
    )

    await handler._handle_word_game_stop(update, Query(), context)

    assert "word_game" not in context.chat_data
    assert edits == ["🛑 Kelime oyunu bitirildi."]


@pytest.mark.asyncio
async def test_word_guess_hint_callback_updates_persistent_panel():
    from src.handlers.callback_handler import CallbackHandler

    class Ledger:
        async def claim(self, *args, **kwargs):
            return True

    class DataPort:
        reachable = True

        async def save_game_state(self, *args, **kwargs):
            return None

        async def get_game_state(self, *args, **kwargs):
            return None

    edits = []

    class Query:
        message = SimpleNamespace(message_id=91)

        async def answer(self, *args, **kwargs):
            return None

        async def edit_message_text(self, text, **kwargs):
            edits.append((text, kwargs))

    state = {
        "category": "Bilim",
        "description": "Bir şeyi açıklayan, günlük hayatta kullanılan güvenli bir tanım.",
        "hints": ["İlk ipucu"],
        "hint_index": 0,
        "attempts": 1,
        "difficulty": "normal",
    }
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42),
        effective_chat=SimpleNamespace(id=-100, type="supergroup"),
    )
    context = SimpleNamespace(chat_data={"word_guess": state})
    handler = CallbackHandler(
        SimpleNamespace(),
        SimpleNamespace(owner_id=1),
        None,
        data_port=DataPort(),
        game_action_ledger=Ledger(),
    )

    await handler._handle_word_guess_hint(update, Query(), context)

    assert state["hint_index"] == 1
    assert edits
    text, kwargs = edits[-1]
    assert "İlk ipucu" in text
    callbacks = [
        button.callback_data
        for row in kwargs["reply_markup"].inline_keyboard
        for button in row
    ]
    assert {"word_hint", "word_skip", "word_guess_stop"}.issubset(callbacks)


@pytest.mark.asyncio
async def test_word_guess_hint_callback_allows_sequential_hints_on_same_panel():
    from src.handlers.callback_handler import CallbackHandler

    claimed_ids = set()

    class Ledger:
        async def claim(self, _chat_id, _game_type, action_id, **_kwargs):
            if action_id in claimed_ids:
                return False
            claimed_ids.add(action_id)
            return True

    class DataPort:
        reachable = True

        async def save_game_state(self, *args, **kwargs):
            return None

        async def get_game_state(self, *args, **kwargs):
            return None

    class Query:
        message = SimpleNamespace(message_id=91)

        async def answer(self, *args, **kwargs):
            return None

        async def edit_message_text(self, text, **kwargs):
            return None

    state = {
        "category": "Bilim",
        "description": "Kısa ve güvenli bir tanım.",
        "hints": ["İlk ipucu", "İkinci ipucu"],
        "hint_index": 0,
        "attempts": 1,
        "difficulty": "normal",
    }
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42),
        effective_chat=SimpleNamespace(id=-100, type="supergroup"),
    )
    context = SimpleNamespace(chat_data={"word_guess": state})
    handler = CallbackHandler(
        SimpleNamespace(),
        SimpleNamespace(owner_id=1),
        None,
        data_port=DataPort(),
        game_action_ledger=Ledger(),
    )

    await handler._handle_word_guess_hint(update, Query(), context)
    await handler._handle_word_guess_hint(update, Query(), context)

    assert state["hint_index"] == 2
    assert len(claimed_ids) == 2


@pytest.mark.asyncio
async def test_media_menu_button_arms_an_explicit_photo_action():
    from src.handlers.callback_handler import CallbackHandler
    from src.storage.state_scope import get_scoped_state

    handler = CallbackHandler(SimpleNamespace(), SimpleNamespace(), None, menu_service=MenuService())
    handler.user_states = {}
    edits = []

    class Query:
        data = "media_intent:vision"
        message = SimpleNamespace(message_id=5)
        async def answer(self, *args, **kwargs):
            return None
        async def edit_message_text(self, text, **kwargs):
            edits.append(text)

    update = SimpleNamespace(
        callback_query=Query(),
        effective_user=SimpleNamespace(id=42),
        effective_chat=SimpleNamespace(id=42, type="private"),
    )
    await handler.handle_callback_query(update, SimpleNamespace(chat_data={}))
    state = get_scoped_state(handler.user_states, user_id=42, chat_id=42)
    assert state["state"] == "awaiting_intent_photo"
    assert state["action"] == "vision"
    assert edits and "fotoğraf" in edits[0].casefold()


@pytest.mark.asyncio
async def test_armed_photo_action_queues_only_the_selected_operation():
    from src.handlers.media_handler import MediaHandler
    from src.storage.state_scope import set_scoped_state

    submitted = []

    class Attachments:
        async def submit(self, **kwargs):
            submitted.append(kwargs)
            return "job-vision", False

    handler = MediaHandler(
        chat_engine=None,
        sudo_manager=SimpleNamespace(is_blacklisted=lambda *_: False),
        tool_manager=None,
        attachment_service=Attachments(),
    )
    handler.user_states = {}
    set_scoped_state(handler.user_states, user_id=42, chat_id=42, value={"state": "awaiting_intent_photo", "action": "vision"})

    class Message:
        photo = [SimpleNamespace(file_id="photo-1", file_unique_id="unique-1", file_size=10)]
        caption = None
        message_id = 7
        async def reply_text(self, *args, **kwargs):
            async def edit_text(*args, **kwargs):
                return None
            return SimpleNamespace(message_id=8, edit_text=edit_text)

    update = SimpleNamespace(
        message=Message(), effective_message=Message(),
        effective_chat=SimpleNamespace(id=42, type="private"),
        effective_user=SimpleNamespace(id=42, full_name="Ada", username="ada"),
    )
    await handler.handle_photo(update, SimpleNamespace(bot_info=SimpleNamespace(username="arelbot", id=99)))
    assert submitted and submitted[0]["job_type"] == "vision"
    assert handler.user_states == {}


@pytest.mark.asyncio
async def test_daily_brief_preference_claim_is_idempotent_for_same_local_date():
    store = NotificationPreferenceStore()
    await store.update(
        42,
        daily_brief_enabled=True,
        daily_brief_time="08:00",
        daily_brief_city="İstanbul",
    )
    assert await store.claim_daily_brief(42, "2026-10-11") is True
    assert await store.claim_daily_brief(42, "2026-10-11") is False
    assert await store.claim_daily_brief(42, "2026-10-12") is True


@pytest.mark.asyncio
async def test_plain_photo_is_ignored_without_explicit_intent():
    from src.handlers.media_handler import MediaHandler

    states = {}
    handler = MediaHandler(
        chat_engine=None,
        sudo_manager=SimpleNamespace(is_blacklisted=lambda *_: False),
        tool_manager=None,
        attachment_service=SimpleNamespace(submit=None),
    )
    handler.user_states = states
    message = SimpleNamespace(
        message_id=7,
        photo=[SimpleNamespace(file_id="file-1", file_unique_id="unique-1", file_size=100)],
        caption=None,
        reply_text=None,
    )
    replies = []

    async def reply_text(text, **kwargs):
        replies.append((text, kwargs))
        return SimpleNamespace(message_id=9, edit_text=lambda *a, **k: None)

    message.reply_text = reply_text
    update = SimpleNamespace(
        message=message,
        effective_chat=SimpleNamespace(id=10, type="private"),
        effective_user=SimpleNamespace(id=42, full_name="Ada", username="ada"),
        effective_message=message,
    )
    context = SimpleNamespace(bot_info=SimpleNamespace(username="arelbot", id=99))
    await handler.handle_photo(update, context)
    assert states == {}
    assert replies == []


@pytest.mark.asyncio
async def test_photo_with_sticker_caption_opens_explicit_action_menu():
    from src.handlers.media_handler import MediaHandler

    states = {}
    handler = MediaHandler(
        chat_engine=None,
        sudo_manager=SimpleNamespace(is_blacklisted=lambda *_: False),
        tool_manager=None,
        attachment_service=SimpleNamespace(submit=None),
    )
    handler.user_states = states
    replies = []

    async def reply_text(text, **kwargs):
        replies.append((text, kwargs))
        return SimpleNamespace(message_id=9)

    message = SimpleNamespace(
        message_id=7,
        photo=[SimpleNamespace(file_id="file-1", file_unique_id="unique-1", file_size=100)],
        caption="/sticker",
        reply_text=reply_text,
    )
    update = SimpleNamespace(
        message=message,
        effective_chat=SimpleNamespace(id=10, type="private"),
        effective_user=SimpleNamespace(id=42, full_name="Ada", username="ada"),
        effective_message=message,
    )
    context = SimpleNamespace(bot_info=SimpleNamespace(username="arelbot", id=99))
    await handler.handle_photo(update, context)
    assert states[42]["state"] == "awaiting_photo_action"
    assert "photo_action" in str(replies[0][1]["reply_markup"].inline_keyboard)


@pytest.mark.asyncio
async def test_daily_brief_combines_typed_features_and_productivity():
    class Features:
        async def execute(self, key, params=None):
            labels = {"weather": "İstanbul 18°C", "earthquakes": "Son deprem: 2.1", "breaking_news": "Başlık"}
            return FeatureResult.success(labels[key], display_blocks=(labels[key],), metadata={"fresh": True})

    class Productivity:
        async def daily_summary(self, user_id, **kwargs):
            return {"open_task_count": 1, "overdue_count": 0, "upcoming_count": 1, "tasks": [{"text": "Toplantı"}], "overdue_reminders": [], "upcoming_reminders": []}

    service = DailyBriefService(Features(), Productivity())
    text = await service.build(42, city="İstanbul", timezone_name="Europe/Istanbul")
    assert "İstanbul 18°C" in text
    assert "Toplantı" in text
    assert "Kaynak" not in text


@pytest.mark.asyncio
async def test_photo_action_callback_requires_owner_and_enqueues_selected_action():
    from src.handlers.callback_handler import CallbackHandler
    from src.storage.state_scope import set_scoped_state

    submitted = []

    class Media:
        async def enqueue_photo_action(self, **kwargs):
            submitted.append(kwargs)
            return "job-1", False

    handler = CallbackHandler(chat_engine=SimpleNamespace(), sudo_manager=SimpleNamespace(), tool_manager=None)
    handler.media_handler = Media()
    handler.user_states = {}
    set_scoped_state(handler.user_states, user_id=42, chat_id=10, value={"state": "awaiting_photo_action", "source_file_id": "f", "source_key": "k"})

    class Query:
        data = "photo_action:42:ocr"
        message = SimpleNamespace(message_id=9)
        async def answer(self, *args, **kwargs):
            return None
        async def edit_message_text(self, *args, **kwargs):
            self.edited = args[0]

    update = SimpleNamespace(
        callback_query=Query(),
        effective_user=SimpleNamespace(id=42),
        effective_chat=SimpleNamespace(id=10, type="private"),
    )
    await handler.handle_callback_query(update, SimpleNamespace(chat_data={}))
    assert submitted[0]["action"] == "ocr"
    assert handler.user_states == {}


@pytest.mark.asyncio
async def test_daily_brief_callback_updates_personal_preference_panel():
    from src.handlers.callback_handler import CallbackHandler

    store = NotificationPreferenceStore()
    handler = CallbackHandler(
        SimpleNamespace(), SimpleNamespace(), None,
        menu_service=MenuService(), notification_preference_store=store,
    )
    query = SimpleNamespace(
        data="daily_brief:toggle:42",
        message=SimpleNamespace(message_id=1),
        answer=lambda *a, **k: None,
        edit_message_text=None,
    )
    edits = []
    async def answer(*args, **kwargs):
        return None
    async def edit(*args, **kwargs):
        edits.append(args[0])
    query.answer = answer
    query.edit_message_text = edit
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=42),
        effective_chat=SimpleNamespace(id=42, type="private"),
    )
    await handler.handle_callback_query(update, SimpleNamespace(chat_data={}))
    assert (await store.get(42))["daily_brief_enabled"] is True
    assert edits and "Günlük özet" in edits[0]


@pytest.mark.asyncio
async def test_daily_brief_tick_claims_once_before_delivery():
    from src.orchestrator import Orchestrator

    class Brief:
        async def build(self, *args, **kwargs):
            return "<b>Özet</b>"

    class Bot:
        def __init__(self):
            self.sent = []
        async def send_message(self, **kwargs):
            self.sent.append(kwargs)

    store = NotificationPreferenceStore()
    from zoneinfo import ZoneInfo
    current_time = datetime.now(timezone.utc).astimezone(ZoneInfo("Europe/Istanbul")).strftime("%H:%M")
    await store.update(42, daily_brief_enabled=True, daily_brief_time=current_time, daily_brief_city="İstanbul")
    orchestrator = object.__new__(Orchestrator)
    orchestrator.notification_preference_store = store
    orchestrator.daily_brief_service = Brief()
    bot = Bot()
    context = SimpleNamespace(bot=bot)
    await orchestrator._daily_brief_tick(context)
    await orchestrator._daily_brief_tick(context)
    assert len(bot.sent) == 1

