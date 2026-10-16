"""
Handler'lar için birim testleri.
"""
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock


class TestCommandHandler:
    @pytest.mark.asyncio
    async def test_imports(self):
        from src.handlers.command_handler import CommandHandler
        assert CommandHandler is not None

    @pytest.mark.asyncio
    async def test_handler_instantiation(self):
        from src.handlers.command_handler import CommandHandler

        class MockEngine:
            async def get_response(self, **kwargs):
                return "ok"
            tool_manager = None

        class MockManager:
            def is_sudo(self, uid): return True
            owner_id = 0

        handler = CommandHandler(MockEngine(), MockManager(), None, None)
        assert handler is not None

    @pytest.mark.asyncio
    async def test_ayar_command_opens_personal_button_panel_in_private_chat(self):
        from src.handlers.command_handler import CommandHandler
        from src.services.menu_service import MenuService

        class DB:
            async def get_chat_settings(self, chat_id):
                return {"q_enabled": True, "media_enabled": False}

        class Message:
            def __init__(self):
                self.sent = None

            async def reply_text(self, text, **kwargs):
                self.sent = (text, kwargs)

        message = Message()
        update = SimpleNamespace(
            effective_message=message,
            message=message,
            effective_user=SimpleNamespace(id=42),
            effective_chat=SimpleNamespace(id=42, type="private"),
        )
        handler = CommandHandler(
            SimpleNamespace(), SimpleNamespace(owner_id=1), None, None,
            menu_service=MenuService(), data_port=DB(),
        )

        await handler.handle_ayar_command(update, SimpleNamespace(args=[]))

        assert message.sent is not None
        assert message.sent[1]["reply_markup"].inline_keyboard
        callbacks = [
            button.callback_data
            for row in message.sent[1]["reply_markup"].inline_keyboard
            for button in row
        ]
        assert "settings_media_toggle" in callbacks
        assert "daily_brief:42" in callbacks

    @pytest.mark.asyncio
    async def test_ayar_command_opens_group_admin_button_panel(self):
        from src.handlers.command_handler import CommandHandler
        from src.services.menu_service import MenuService

        class DB:
            async def get_chat_settings(self, chat_id):
                return {"media_enabled": True, "q_enabled": False}

        class Bot:
            async def get_chat_member(self, chat_id, user_id):
                return SimpleNamespace(status="administrator")

        class Message:
            def __init__(self):
                self.sent = None

            async def reply_text(self, text, **kwargs):
                self.sent = (text, kwargs)

        message = Message()
        update = SimpleNamespace(
            effective_message=message,
            message=message,
            effective_user=SimpleNamespace(id=42),
            effective_chat=SimpleNamespace(id=-100, type="supergroup"),
        )
        context = SimpleNamespace(args=[], bot=Bot())
        handler = CommandHandler(
            SimpleNamespace(), SimpleNamespace(owner_id=1), None, None,
            menu_service=MenuService(), data_port=DB(),
        )

        await handler.handle_ayar_command(update, context)

        assert message.sent is not None
        callbacks = [
            button.callback_data
            for row in message.sent[1]["reply_markup"].inline_keyboard
            for button in row
        ]
        assert "ayar_q_toggle" in callbacks
        assert "ayar_media_toggle" in callbacks

    @pytest.mark.asyncio
    async def test_lyrics_command_uses_typed_feature_and_supports_artist_separator(self):
        from src.handlers.command_handler import CommandHandler
        from src.features.contracts import FeatureResult

        sent = []

        class Message:
            async def reply_text(self, text, **kwargs):
                sent.append((text, kwargs))

        class Engine:
            class Executor:
                async def execute(self, feature_key, params):
                    assert feature_key == "lyrics"
                    assert params == {"song": "Şarkı", "artist": "Sanatçı"}
                    return FeatureResult.success(
                        {"content": "<b>Kaynak</b>"},
                        display_blocks=("<b>Kaynak</b>",),
                    )
            feature_executor = Executor()

        update = SimpleNamespace(
            effective_message=Message(),
            effective_user=SimpleNamespace(id=1),
            effective_chat=SimpleNamespace(id=1, type="private"),
        )
        context = SimpleNamespace(args=["Şarkı", "|", "Sanatçı"])
        handler = CommandHandler(Engine(), SimpleNamespace(owner_id=1), None, None)

        await handler.handle_lyrics_command(update, context)

        assert sent and "Kaynak" in sent[0][0]
        assert sent[0][1]["parse_mode"] == "HTML"

    @pytest.mark.asyncio
    async def test_search_command_uses_typed_feature(self):
        from src.handlers.command_handler import CommandHandler
        from src.features.contracts import FeatureResult

        sent = []

        class Message:
            async def reply_text(self, text, **kwargs):
                sent.append((text, kwargs))

        class Executor:
            async def execute(self, feature_key, params):
                assert feature_key == "web_search"
                assert params["query"] == "güncel haber"
                return FeatureResult.success(
                    {"content": "<b>Sonuç</b>"},
                    display_blocks=("<b>Sonuç</b>",),
                )

        update = SimpleNamespace(effective_message=Message())
        context = SimpleNamespace(args=["güncel", "haber"])
        handler = CommandHandler(
            SimpleNamespace(feature_executor=Executor()),
            SimpleNamespace(owner_id=1),
            None,
            None,
        )

        await handler.handle_search_command(update, context)

        assert sent and "Sonuç" in sent[0][0]
        assert sent[0][1]["parse_mode"] == "HTML"

    @pytest.mark.asyncio
    async def test_news_command_passes_normalized_category_to_typed_feature(self):
        from src.handlers.command_handler import CommandHandler
        from src.features.contracts import FeatureResult

        sent = []

        class Message:
            async def reply_text(self, text, **kwargs):
                sent.append((text, kwargs))

        class Executor:
            async def execute(self, feature_key, params):
                assert feature_key == "breaking_news"
                assert params == {"category": "dunya"}
                return FeatureResult.success({}, display_blocks=("<b>Dünya</b>",))

        update = SimpleNamespace(effective_message=Message())
        handler = CommandHandler(
            SimpleNamespace(feature_executor=Executor()),
            SimpleNamespace(owner_id=1),
            None,
            None,
        )
        await handler.handle_haber_command(update, SimpleNamespace(args=["dünya"]))
        assert sent and "Dünya" in sent[0][0]

    @pytest.mark.asyncio
    async def test_news_command_rejects_unknown_category_without_provider_call(self):
        from src.handlers.command_handler import CommandHandler

        sent = []

        class Message:
            async def reply_text(self, text, **kwargs):
                sent.append(text)

        class Executor:
            async def execute(self, *_args, **_kwargs):
                raise AssertionError("provider must not run for an invalid category")

        handler = CommandHandler(
            SimpleNamespace(feature_executor=Executor()),
            SimpleNamespace(owner_id=1),
            None,
            None,
        )
        await handler.handle_haber_command(
            SimpleNamespace(effective_message=Message()),
            SimpleNamespace(args=["magazin"]),
        )
        assert sent and "Kategori bulunamadı" in sent[0]

    @pytest.mark.asyncio
    async def test_unified_culture_command_shows_category_picker_without_provider_call(self):
        from src.handlers.command_handler import CommandHandler

        sent = []

        class Message:
            text = "/kultur"

            async def reply_text(self, text, **kwargs):
                sent.append((text, kwargs))

        class Engine:
            feature_executor = SimpleNamespace()

        update = SimpleNamespace(
            effective_message=Message(),
            effective_user=SimpleNamespace(id=9, first_name="Arel"),
            effective_chat=SimpleNamespace(id=9, type="private"),
        )
        handler = CommandHandler(Engine(), SimpleNamespace(owner_id=1), None, None)
        await handler.handle_culture_command(update, SimpleNamespace(args=[]))
        assert sent and "Kültür önerileri" in sent[0][0]
        assert sent[0][1]["reply_markup"].inline_keyboard[0][0].callback_data == "culture:9:movie"

    @pytest.mark.asyncio
    async def test_culture_provider_failure_does_not_leak_exception_text(self):
        from src.handlers.command_handler import CommandHandler

        sent = []

        class Message:
            text = "/kultur film gizem"

            async def reply_text(self, text, **kwargs):
                sent.append((text, kwargs))

        class Executor:
            async def execute(self, *_args, **_kwargs):
                raise RuntimeError("private upstream token should not be visible")

        class Engine:
            feature_executor = Executor()

            async def get_response(self, *args, **kwargs):
                return "unused"

        update = SimpleNamespace(
            effective_message=Message(),
            effective_user=SimpleNamespace(id=9, first_name="Arel"),
            effective_chat=SimpleNamespace(id=9, type="private"),
        )
        handler = CommandHandler(Engine(), SimpleNamespace(owner_id=1), None, None)
        await handler.handle_culture_command(update, SimpleNamespace(args=["film", "gizem"]))
        assert sent
        assert "private upstream token" not in sent[-1][0]
        assert "tekrar dene" in sent[-1][0]

    @pytest.mark.asyncio
    async def test_group_subscription_command_is_admin_only(self):
        from src.handlers.command_handler import CommandHandler

        replies = []

        class MockMessage:
            async def reply_text(self, text, **kwargs):
                replies.append(text)

        class MockEngine:
            tool_manager = None

        class MockManager:
            owner_id = 999

        class MockBot:
            async def get_chat_member(self, chat_id, user_id):
                return SimpleNamespace(status="member")

        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=123),
            effective_chat=SimpleNamespace(id=-100, type="supergroup"),
            effective_message=MockMessage(),
        )
        context = SimpleNamespace(args=["hava", "istanbul"], bot=MockBot())
        handler = CommandHandler(
            MockEngine(), MockManager(), None, None, subscription_service=object()
        )
        await handler.handle_subscription_command(update, context)
        assert replies and "yalnızca grup yöneticileri" in replies[0]

    @pytest.mark.asyncio
    async def test_group_admin_can_list_shared_schedules(self):
        from src.handlers.command_handler import CommandHandler

        captured = []

        class MockMessage:
            async def reply_text(self, text, **kwargs):
                captured.append((text, kwargs))

        class MockBot:
            async def get_chat_member(self, chat_id, user_id):
                return SimpleNamespace(status="administrator")

        class MockStore:
            async def list_for_chat(self, chat_id):
                return [{
                    "subscription_id": "weather-job-123",
                    "feature_key": "weather",
                    "params": {"city": "İstanbul"},
                    "schedule_kind": "daily",
                    "local_time": "10:00",
                    "enabled": True,
                }]

        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=123),
            effective_chat=SimpleNamespace(id=-100, type="supergroup"),
            effective_message=MockMessage(),
        )
        context = SimpleNamespace(args=[], bot=MockBot())
        handler = CommandHandler(
            SimpleNamespace(), SimpleNamespace(owner_id=999), None, None,
            subscription_service=SimpleNamespace(store=MockStore()),
        )
        await handler.handle_subscription_command(update, context)
        assert captured and "Grubun aktif bildirimleri" in captured[0][0]
        markup = captured[0][1]["reply_markup"]
        assert markup.inline_keyboard[0][0].callback_data.startswith("weather_group_stop:123:")

    @pytest.mark.asyncio
    async def test_price_alert_command_creates_threshold_alert(self):
        from src.handlers.command_handler import CommandHandler
        from src.services.subscription_service import SubscriptionService
        from src.storage.subscription_store import SubscriptionStore

        sent = []

        class Message:
            async def reply_text(self, text, **kwargs):
                sent.append(text)

        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=7),
            effective_chat=SimpleNamespace(id=7, type="private"),
            effective_message=Message(),
        )
        context = SimpleNamespace(args=["kur", "usd", "40", "üstü"])
        handler = CommandHandler(
            SimpleNamespace(), SimpleNamespace(), None, None,
            subscription_service=SubscriptionService(SubscriptionStore()),
        )
        await handler.handle_alert_command(update, context)
        assert sent and "Alarm kuruldu" in sent[0]

    @pytest.mark.asyncio
    async def test_stop_game_command_cancels_persistent_games_and_allows_group_admin(self):
        from src.handlers.command_handler import CommandHandler

        replies = []
        calls = []

        class Message:
            async def reply_text(self, text, **kwargs):
                replies.append((text, kwargs))

        class Jobs:
            def get_jobs_by_name(self, name):
                return []

        class Bot:
            async def get_chat_member(self, chat_id, user_id):
                return SimpleNamespace(status="administrator")

        class Game:
            def __init__(self, name):
                self.name = name

            async def cancel(self, **kwargs):
                calls.append((self.name, kwargs))
                return {"status": "cancelled"}

        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=7),
            effective_chat=SimpleNamespace(id=-100, type="supergroup"),
            message=Message(),
        )
        context = SimpleNamespace(args=[], bot=Bot(), chat_data={}, job_queue=Jobs())
        handler = CommandHandler(
            SimpleNamespace(),
            SimpleNamespace(owner_id=999),
            None,
            None,
            blackjack_service=Game("blackjack"),
            batak_service=Game("batak"),
        )

        await handler.handle_oyunbitir_command(update, context)

        assert {name for name, _ in calls} == {"blackjack", "batak"}
        batak_call = next(kwargs for name, kwargs in calls if name == "batak")
        assert batak_call["moderator"] is True
        assert replies and "blackjack, batak" in replies[0][0]

    @pytest.mark.asyncio
    async def test_stop_game_command_clears_all_restart_safe_text_game_states(self):
        from src.handlers.command_handler import CommandHandler

        deleted = []

        class Message:
            async def reply_text(self, text, **kwargs):
                return None

        class Jobs:
            def get_jobs_by_name(self, name):
                return []

        class DataPort:
            async def delete_game_state(self, chat_id, game_type):
                deleted.append((chat_id, game_type))

        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=7),
            effective_chat=SimpleNamespace(id=-100, type="private"),
            message=Message(),
        )
        context = SimpleNamespace(args=[], chat_data={}, job_queue=Jobs())
        handler = CommandHandler(
            SimpleNamespace(),
            SimpleNamespace(owner_id=999),
            None,
            None,
            data_port=DataPort(),
        )

        await handler.handle_oyunbitir_command(update, context)

        assert {game_type for _, game_type in deleted} == {
            "word_guess", "hangman", "word_game", "batak", "bottle", "quiz"
        }

    @pytest.mark.asyncio
    async def test_quiz_menu_exposes_the_science_category(self):
        from src.handlers.command_handler import CommandHandler

        sent = []

        class Message:
            async def reply_text(self, text, **kwargs):
                sent.append(kwargs["reply_markup"])

        update = SimpleNamespace(message=Message())
        handler = CommandHandler(SimpleNamespace(), SimpleNamespace(), None, None)

        await handler.handle_quiz_command(update, SimpleNamespace())

        callbacks = [
            button.callback_data
            for row in sent[0].inline_keyboard
            for button in row
        ]
        assert "quiz_cat_bilim" in callbacks


class TestCallbackHandler:
    @pytest.mark.asyncio
    async def test_imports(self):
        from src.handlers.callback_handler import CallbackHandler
        assert CallbackHandler is not None

    @pytest.mark.asyncio
    async def test_group_weather_callback_receives_context_and_checks_admin(self):
        from src.handlers.callback_handler import CallbackHandler

        class MockBot:
            async def get_chat_member(self, chat_id, user_id):
                return SimpleNamespace(status="administrator")

        class MockQuery:
            def __init__(self):
                self.edited = None

            async def answer(self, *args, **kwargs):
                return None

            async def edit_message_text(self, text, **kwargs):
                self.edited = (text, kwargs)

        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=456),
            effective_chat=SimpleNamespace(id=-100, type="supergroup"),
        )
        context = SimpleNamespace(bot=MockBot())
        query = MockQuery()
        handler = CallbackHandler(SimpleNamespace(), SimpleNamespace(owner_id=1), None)
        await handler._handle_weather_callback(update, query, context, "weather_group_opts:123:34")
        assert query.edited is not None
        markup = query.edited[1]["reply_markup"]
        assert markup.inline_keyboard[0][0].callback_data.startswith("weather_group_freq:456:34:")

    @pytest.mark.asyncio
    async def test_malformed_weather_callback_is_answered_without_handler_exception(self):
        from src.handlers.callback_handler import CallbackHandler

        class MockQuery:
            def __init__(self):
                self.answers = []

            async def answer(self, *args, **kwargs):
                self.answers.append((args, kwargs))

        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=123),
            effective_chat=SimpleNamespace(id=123, type="private"),
        )
        query = MockQuery()
        handler = CallbackHandler(SimpleNamespace(), SimpleNamespace(owner_id=1), None)

        await handler._handle_weather_callback(update, query, SimpleNamespace(), "weather_opts:123")

        assert query.answers
        assert query.answers[-1][1]["show_alert"] is True

    @pytest.mark.asyncio
    async def test_group_settings_inline_controls_are_admin_scoped(self):
        from src.handlers.callback_handler import CallbackHandler

        class DB:
            def __init__(self):
                self.settings = {}

            async def get_chat_settings(self, chat_id):
                return dict(self.settings)

            async def update_chat_setting_key(self, chat_id, key, value):
                self.settings[key] = value

        class MockBot:
            async def get_chat_member(self, chat_id, user_id):
                return SimpleNamespace(status="administrator")

        class MockQuery:
            def __init__(self):
                self.message = SimpleNamespace(chat=SimpleNamespace(id=-100, type="supergroup"))
                self.markup = None
                self.answers = []

            async def answer(self, *args, **kwargs):
                self.answers.append((args, kwargs))

            async def edit_message_text(self, text, **kwargs):
                self.text = text
                self.markup = kwargs.get("reply_markup")

            async def edit_message_reply_markup(self, reply_markup=None):
                self.markup = reply_markup

        db = DB()
        handler = CallbackHandler(SimpleNamespace(db=db), SimpleNamespace(owner_id=999), None)
        update = SimpleNamespace(effective_user=SimpleNamespace(id=456), effective_chat=SimpleNamespace(id=-100, type="supergroup"))
        context = SimpleNamespace(bot=MockBot())
        query = MockQuery()
        await handler._handle_ayar_menu(update, query, context)
        callbacks = [button.callback_data for row in query.markup.inline_keyboard for button in row]
        assert "ayar_mod_toggle" in callbacks
        assert "ayar_welcome_toggle" in callbacks
        assert "ayar_goodbye_toggle" in callbacks
        assert "ayar_ban_toggle" in callbacks
        assert "ayar_persona_cycle" in callbacks
        await handler._handle_ayar_callback(update, query, context, "ayar_mod_toggle")
        assert db.settings["moderation_enabled"] is True
        await handler._handle_ayar_callback(update, query, context, "ayar_persona_cycle")
        assert db.settings["persona_mode"] == "balanced"

    @pytest.mark.asyncio
    async def test_group_persona_command_persists_scoped_mode(self):
        from src.handlers.command_handler import CommandHandler
        from src.storage.handler_data_port import HandlerDataPort

        class DB:
            def __init__(self):
                self.settings = {}

            async def get_chat_settings(self, chat_id):
                return dict(self.settings)

            async def update_chat_setting_key(self, chat_id, key, value):
                self.settings[key] = value

        class Message:
            async def reply_text(self, text, **kwargs):
                self.reply = (text, kwargs)

        db = DB()
        message = Message()
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=9),
            effective_chat=SimpleNamespace(id=-100, type="supergroup"),
            effective_message=message,
            message=message,
        )
        handler = CommandHandler(
            SimpleNamespace(),
            SimpleNamespace(owner_id=9),
            None,
            None,
            data_port=HandlerDataPort(db),
        )
        await handler.handle_ayar_command(update, SimpleNamespace(args=["kişilik", "iğneleyici"]))
        assert db.settings["persona_mode"] == "needle"
        assert "İğneleyici" in message.reply[0]
        await handler.handle_ayar_command(update, SimpleNamespace(args=["kişilik", "İĞNELEYİCİ"]))
        assert db.settings["persona_mode"] == "needle"
        await handler.handle_ayar_command(update, SimpleNamespace(args=["KİŞİLİK", "CİDDİ"]))
        assert db.settings["persona_mode"] == "serious"
        await handler.handle_ayar_command(update, SimpleNamespace(args=[]))
        assert "Kişilik" in message.reply[0]
        assert "Ciddi" in message.reply[0]

    @pytest.mark.asyncio
    async def test_private_persona_command_persists_user_only_preference(self):
        from src.handlers.command_handler import CommandHandler
        from src.storage.handler_data_port import HandlerDataPort

        class DB:
            def __init__(self):
                self.user = {}

            async def get_user(self, user_id):
                return dict(self.user)

            async def update_user(self, user_id, data):
                self.user.update(data)

        class Message:
            async def reply_text(self, text, **kwargs):
                self.reply = (text, kwargs)

        db = DB()
        message = Message()
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=42, full_name="Arel Kullanıcı"),
            effective_chat=SimpleNamespace(id=42, type="private"),
            effective_message=message,
            message=message,
        )
        handler = CommandHandler(
            SimpleNamespace(),
            SimpleNamespace(owner_id=999),
            None,
            None,
            data_port=HandlerDataPort(db),
        )
        await handler.handle_personality_command(update, SimpleNamespace(args=["İĞNELEYİCİ"]))
        assert db.user["persona_mode"] == "needle"
        assert "İğneleyici" in message.reply[0]
        await handler.handle_personality_command(update, SimpleNamespace(args=[]))
        buttons = message.reply[1]["reply_markup"].inline_keyboard
        assert any(button.callback_data == "persona_user:needle" for row in buttons for button in row)

    @pytest.mark.asyncio
    async def test_private_persona_callback_is_owner_scoped_and_persistent(self):
        from src.handlers.callback_handler import CallbackHandler

        class DB:
            def __init__(self):
                self.user = {}

            async def update_user(self, user_id, data):
                self.user.update(data)

        class Query:
            def __init__(self):
                self.message = SimpleNamespace(chat=SimpleNamespace(id=42, type="private"))
                self.text = ""
                self.markup = None
                self.answers = []

            async def answer(self, *args, **kwargs):
                self.answers.append((args, kwargs))

            async def edit_message_text(self, text, **kwargs):
                self.text = text
                self.markup = kwargs.get("reply_markup")

        db = DB()
        handler = CallbackHandler(SimpleNamespace(db=db), SimpleNamespace(owner_id=999), None)
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=42),
            effective_chat=SimpleNamespace(id=42, type="private"),
        )
        query = Query()
        await handler._handle_persona_user_callback(update, query, "persona_user:needle")
        assert db.user["persona_mode"] == "needle"
        assert "İğneleyici" in query.text

        update.effective_user.id = 99
        update.effective_chat.id = 99
        await handler._handle_persona_user_callback(update, query, "persona_user:serious")
        assert db.user["persona_mode"] == "needle"
        assert query.answers[-1][1].get("show_alert") is True

    @pytest.mark.asyncio
    async def test_normal_persona_mode_is_selectable_for_private_chat(self):
        from src.handlers.command_handler import CommandHandler
        from src.storage.handler_data_port import HandlerDataPort

        class DB:
            def __init__(self):
                self.user = {}

            async def get_user(self, user_id):
                return dict(self.user)

            async def update_user(self, user_id, data):
                self.user.update(data)

        class Message:
            async def reply_text(self, text, **kwargs):
                self.reply = (text, kwargs)

        db = DB()
        message = Message()
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=42, full_name="Arel"),
            effective_chat=SimpleNamespace(id=42, type="private"),
            effective_message=message,
            message=message,
        )
        handler = CommandHandler(
            SimpleNamespace(),
            SimpleNamespace(owner_id=999),
            None,
            None,
            data_port=HandlerDataPort(db),
        )

        await handler.handle_personality_command(update, SimpleNamespace(args=["normal"]))

        assert db.user["persona_mode"] == "normal"
        assert "Normal" in message.reply[0]

    @pytest.mark.asyncio
    async def test_group_persona_command_does_not_claim_success_when_persistence_fails(self):
        from src.handlers.command_handler import CommandHandler
        from src.storage.handler_data_port import HandlerDataPort

        class DB:
            async def get_chat_settings(self, chat_id):
                return {}

            async def update_chat_setting_key(self, chat_id, key, value):
                raise RuntimeError("db unavailable")

        class Message:
            async def reply_text(self, text, **kwargs):
                self.reply = (text, kwargs)

        message = Message()
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=9),
            effective_chat=SimpleNamespace(id=-100, type="supergroup"),
            effective_message=message,
            message=message,
        )
        handler = CommandHandler(
            SimpleNamespace(),
            SimpleNamespace(owner_id=9),
            None,
            None,
            data_port=HandlerDataPort(DB()),
        )
        await handler.handle_ayar_command(update, SimpleNamespace(args=["kişilik", "iğneleyici"]))
        assert "kaydedilemedi" in message.reply[0]
        assert "Grup kişilik modu" not in message.reply[0]

    @pytest.mark.asyncio
    async def test_weather_forecast_callback_is_owner_scoped_and_passes_days(self):
        from src.handlers.callback_handler import CallbackHandler
        from src.features.contracts import FeatureResult

        calls = []

        class Directory:
            def by_plate(self, plate):
                return SimpleNamespace(plate=plate, name="İstanbul")

        class Executor:
            async def execute(self, feature_key, params):
                calls.append((feature_key, params))
                return FeatureResult.success(
                    {"content": "<b>Tahmin</b>"},
                    display_blocks=("<b>Tahmin</b>",),
                )

        class Query:
            async def answer(self, *args, **kwargs):
                return None

            async def edit_message_text(self, text, **kwargs):
                self.text = text
                self.kwargs = kwargs

        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=456),
            effective_chat=SimpleNamespace(id=456, type="private"),
        )
        handler = CallbackHandler(
            SimpleNamespace(city_directory=Directory(), feature_executor=Executor()),
            SimpleNamespace(owner_id=999),
            None,
        )
        query = Query()

        await handler._handle_weather_callback(
            update,
            query,
            SimpleNamespace(),
            "weather_forecast:456:34:7",
        )

        assert calls == [("weather", {"city": "İstanbul", "days": 7})]
        assert query.text.startswith("<b>Tahmin</b>")

    def test_feature_result_marks_data_outside_freshness_sla(self):
        from src.features.contracts import FeatureResult, ProviderResponse
        from src.handlers.command_handler import CommandHandler

        result = FeatureResult.success(
            {"content": "<b>Kur</b>"},
            display_blocks=("<b>Kur</b>",),
            response=ProviderResponse(
                data={"content": "<b>Kur</b>"},
                source="Fixture provider",
                source_url="https://example.test/rates",
                metadata={"fresh": False},
            ),
        )

        formatted = CommandHandler(None, None, None, None)._format_feature_result(result, "fallback")

        assert "tazelik hedefinin dışında" in formatted
        assert "Kaynak: <a href=\"https://example.test/rates\">Fixture provider</a>" in formatted


class TestMessageHandler:
    @pytest.mark.asyncio
    async def test_imports(self):
        from src.handlers.message_handler import MessageHandler
        assert MessageHandler is not None

    @pytest.mark.asyncio
    async def test_chat_member_update_uses_persistent_group_settings(self):
        from src.handlers.message_handler import MessageHandler

        class DB:
            async def get_chat_settings(self, chat_id):
                return {"welcome_enabled": True}

        bot = SimpleNamespace(send_message=AsyncMock())
        context = SimpleNamespace(bot=bot)
        user = SimpleNamespace(is_bot=False, full_name="Yeni Kullanıcı", username="yeni")
        event = SimpleNamespace(
            chat=SimpleNamespace(id=-100, type="supergroup", title="Arel Grubu"),
            old_chat_member=SimpleNamespace(status="left"),
            new_chat_member=SimpleNamespace(status="member", user=user),
        )
        update = SimpleNamespace(chat_member=event)
        engine = SimpleNamespace(db=DB())
        handler = MessageHandler(engine, SimpleNamespace(), None, None)

        await handler.handle_chat_member_update(update, context)

        bot.send_message.assert_awaited_once()
        assert "Yeni Kullanıcı" in bot.send_message.await_args.kwargs["text"]


    @pytest.mark.asyncio
    async def test_private_chat_uses_persisted_persona_preference(self):
        from src.handlers.message_handler import MessageHandler
        from src.storage.handler_data_port import HandlerDataPort

        class DB:
            async def get_user(self, user_id):
                return {"persona_mode": "needle"}

        class Engine:
            def __init__(self):
                self.calls = []

            async def get_response(self, *args, **kwargs):
                self.calls.append(kwargs)
                return "Tamam."

        class Message:
            async def reply_text(self, text, **kwargs):
                self.reply = (text, kwargs)

        engine = Engine()
        handler = MessageHandler(
            engine,
            SimpleNamespace(is_blacklisted=lambda *_: False),
            None,
            None,
            data_port=HandlerDataPort(DB()),
        )
        handler._log_event = AsyncMock()
        message = Message()
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=42, full_name="Arel Kullanıcı", username="arel"),
            effective_chat=SimpleNamespace(id=42, type="private"),
            effective_message=message,
        )
        await handler.process_chat_request(update, 42, "Kısa bir cevap ver", SimpleNamespace())
        assert engine.calls[0]["persona_mode"] == "needle"
        assert message.reply[0] == "Tamam."

    @pytest.mark.asyncio
    async def test_group_chat_replies_to_only_the_triggering_message(self):
        from src.handlers.message_handler import MessageHandler
        from src.storage.handler_data_port import HandlerDataPort

        sent = []

        class DB:
            async def get_chat_settings(self, chat_id):
                return {}

        class Engine:
            async def get_response(self, *args, **kwargs):
                return "tek hedefe cevap"

        class Message:
            message_id = 314

            async def reply_text(self, text, **kwargs):
                sent.append((text, kwargs))

        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=42, full_name="Aysel", username="aysel"),
            effective_chat=SimpleNamespace(id=-10042, type="supergroup"),
            effective_message=Message(),
        )
        handler = MessageHandler(
            Engine(),
            SimpleNamespace(is_blacklisted=lambda *_: False),
            None,
            None,
            data_port=HandlerDataPort(DB()),
        )
        handler._log_event = AsyncMock()

        await handler.process_chat_request(update, -10042, "Arel bana bak", SimpleNamespace())

        assert len(sent) == 1
        assert sent[0][1]["reply_to_message_id"] == 314


class TestMediaHandler:
    @pytest.mark.asyncio
    async def test_imports(self):
        from src.handlers.media_handler import MediaHandler
        assert MediaHandler is not None

