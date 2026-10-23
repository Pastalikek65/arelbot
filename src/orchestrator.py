"""
Orchestrator - Merkezi Event Dispatcher

Tüm handler'lara dağıtım yapan basit dispatcher.
Modüler yapıya geçiş için eski devasa dosya parçalandı.
"""
import logging
import asyncio
import html
import os
import tempfile
from typing import Any
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import ContextTypes

from config import Config
from src.key_manager import KeyManager
from src.image_router import ImageRouter
from src.chat_engine import ChatEngine
from src.sudo_manager import SudoManager
from src.mcp_manager import McpManager
from src.utils import (
    CLEANUP_INTERVAL_SECONDS,
    CLEANUP_MAX_AGE_SECONDS,
    cleanup_directories,
    cleanup_old_files,
)
from src.media import SHUTDOWN_CLEANUP_TIMEOUT_SECONDS, MediaDownloadService
from src.documents import DocumentIngestionService
from src.attachments import AttachmentProcessingService
from src.media.cookie_vault import CookieVaultError
from src.media.error_messages import cookie_error_message, media_url_error_message
from src.storage.state_store import PersistentStateMap, create_state_store
from src.storage.state_scope import set_scoped_state
from src.storage.handler_data_port import HandlerDataPort
from src.storage.group_reply_store import RecentMessageIndex, ReplyDeliveryLedger
from src.storage.subscription_store import SubscriptionStore
from src.storage.notification_preference_store import NotificationPreferenceStore
from src.services.feature_presentation import format_feature_result
from src.services.daily_brief_service import DailyBriefService
from src.services.alert_service import AlertService
from src.services.announcement_service import AnnouncementService
from src.moderation import ModerationEngine, ModerationService, TelegramModerationService
from src.moderation.action_ledger import ModerationActionLedger
from src.moderation.contracts import ModerationAction
from src.moderation.action_executor import TelegramModerationActionExecutor
from src.moderation.guardian_adapter import GuardianAdapter
from src.moderation.authorization import TelegramModerationAuthorization
from src.moderation.telegram_guardian_service import GuardianTelegramService
from src.handlers.handler_utils import is_admin
from src.stickers.render_service import StickerRenderService
from src.services.sticker_pack_service import StickerPackService
from src.quotes.render_service import QuoteRenderService
from src.quotes.service import QuoteProcessingService
from src.speech import SpeechSynthesisService
from src.stickers.tag_service import StickerTagService
from src.services.market_chart_service import MarketChartService
from src.market_chart import MarketChartProcessingService
from src.image_generation import ImageGenerationService
from src.games.action_ledger import GameActionLedger
from src.services.inline_query_service import InlineQueryService
from src.observability.provider_report import (
    format_ai_provider_health,
    format_provider_governance,
    format_provider_health,
)
from src.observability import ObservabilityServer
from src.observability import health_status, summarize_provider_rows
from src.security.log_redaction import summarize_exception, summarize_private_text
from src.services.arel_card_renderer import ArelCardRenderer
from src.services.group_reply_delivery import GroupReplyDeliveryService
from src.group_replies import GroupReplyRouter
from src.observability import MetricsRegistry
from src.services.akinator_service import AkinatorService
from src.services.guess_age_service import GuessAgeService
from src.services.uno_service import UnoService
from src.services.uno_inline_service import UnoInlineService
from src.services.uno_media_registry import load_sticker_ids

logger = logging.getLogger(__name__)


class Orchestrator:
    SUBSCRIPTION_RETRY_DELAYS = (60, 300)

    def _get_data_port(self) -> HandlerDataPort:
        """Resolve the handler repository port, including lightweight test hosts."""

        port = getattr(self, "data_port", None)
        if port is None:
            port = HandlerDataPort.from_engine(self.chat_engine)
            self.data_port = port
        return port

    def __init__(self, encryptor=None):
        self.key_manager = KeyManager(encryptor=encryptor)
        self.mcp_manager = McpManager(Config.MCP_SERVERS)
        self.chat_engine = ChatEngine(self.key_manager, self.mcp_manager)
        self.card_renderer = ArelCardRenderer()
        self.data_port = HandlerDataPort(self.chat_engine.db)
        self.media_service = MediaDownloadService()
        self.group_reply_metrics = MetricsRegistry()
        self.group_reply_index = RecentMessageIndex(
            database=self.chat_engine.db,
            required=str(getattr(Config, "APP_ENV", "development")).casefold() == "production",
        )
        self.group_reply_ledger = ReplyDeliveryLedger(
            database=self.chat_engine.db,
            required=str(getattr(Config, "APP_ENV", "development")).casefold() == "production",
        )
        self.sudo_manager = SudoManager(Config.OWNER_ID)
        self.image_router = ImageRouter(self.key_manager)
        self.image_generation_service = ImageGenerationService(
            image_router=self.image_router,
            database=self.chat_engine.db,
        )
        self.bot_info = None

        self.user_states = PersistentStateMap()
        self.state_store = None
        self.creation_cache = {}
        self.document_service = DocumentIngestionService(self.chat_engine.rag_engine, self.chat_engine.db)
        self.sticker_render_service = StickerRenderService(
            temp_dir=Config.STICKER_TEMP_DIR,
            max_concurrency=Config.STICKER_MAX_CONCURRENCY,
            max_input_bytes=Config.STICKER_MAX_INPUT_BYTES,
            timeout_seconds=Config.STICKER_RENDER_TIMEOUT_SECONDS,
        )
        self.sticker_pack_service = None
        self.quote_render_service = QuoteRenderService(
            output_dir=Config.QUOTE_OUTPUT_DIR,
            max_concurrency=Config.QUOTE_MAX_CONCURRENCY,
            timeout_seconds=Config.QUOTE_RENDER_TIMEOUT_SECONDS,
            max_messages=Config.QUOTE_MAX_MESSAGES,
            max_output_bytes=Config.QUOTE_MAX_OUTPUT_BYTES,
        )
        self.market_chart_service = MarketChartService()
        self.moderation = ModerationEngine()
        self.guardian_adapter = GuardianAdapter.from_environment()
        self.moderation_service = ModerationService(
            self.chat_engine.db,
            self.moderation,
            guardian_adapter=self.guardian_adapter,
        )
        self.moderation_action_ledger = ModerationActionLedger(
            self.chat_engine.db,
            required=str(getattr(Config, "APP_ENV", "development")).casefold() == "production",
        )
        self.telegram_moderation_service = TelegramModerationService()
        self.guardian_telegram_service = GuardianTelegramService()
        self.game_action_ledger = GameActionLedger(self.chat_engine.db)
        self.akinator_service = AkinatorService(
            max_concurrency=getattr(Config, "AKINATOR_MAX_CONCURRENCY", 4),
            timeout_seconds=getattr(Config, "AKINATOR_TIMEOUT_SECONDS", 45),
            solver_url=getattr(Config, "AKINATOR_SOLVER_URL", None),
            solver_timeout_ms=getattr(Config, "AKINATOR_SOLVER_TIMEOUT_MS", 120000),
        )
        self.guess_age_service = GuessAgeService(
            self.data_port,
            asset_dir="assets/guess_age",
            rounds=getattr(Config, "GUESS_AGE_ROUNDS", 5),
            round_seconds=getattr(Config, "GUESS_AGE_ROUND_SECONDS", 30),
        )
        from src.services.inline_signing import signing_key
        inline_secret = signing_key(Config.TELEGRAM_TOKEN)
        self._card_inline_secret = inline_secret
        self.uno_service = UnoService(
            self.data_port,
            turn_seconds=getattr(Config, "UNO_TURN_SECONDS", 60),
            inline_secret=inline_secret,
        )
        self.uno_inline_service = UnoInlineService(
            self.uno_service, secret=inline_secret,
            file_ids=load_sticker_ids(),
        )
        self.inline_query_service = InlineQueryService(
            self.chat_engine.city_directory,
            self.chat_engine.db,
            self.chat_engine.feature_executor,
        )
        self._background_tasks = []
        self.observability_server: ObservabilityServer | None = None
        self._health_snapshot_cache: tuple[float, dict] | None = None

        self._init_handlers()

    def configure_telegram_dependencies(self, bot: Any) -> None:
        """Bind transport-backed moderation role checks after Application init."""
        authorization = TelegramModerationAuthorization(bot, bot_id=getattr(bot, "id", None))
        self.telegram_authorization = authorization
        self.moderation_service.member_admin_checker = authorization.is_admin
        # Command/callback handlers use the same injected boundary for every
        # group permission lookup.  Keeping one instance avoids ad-hoc
        # ``get_chat_member`` calls and makes failures fail closed uniformly.
        for handler in (self.command_handler, self.callback_handler, self.message_handler, self.media_handler):
            handler.authorization = authorization

    def _init_handlers(self):
        from src.handlers.command_handler import CommandHandler
        from src.handlers.callback_handler import CallbackHandler
        from src.handlers.message_handler import MessageHandler
        from src.handlers.media_handler import MediaHandler
        from src.handlers.quote_handler import QuoteHandler
        from src.services import (
            LeaderboardService,
            EconomyService,
            AnnouncementService,
            MenuService,
            NewsDigestService,
            GroupAnalyticsService,
            ReminderService,
            ProductivityService,
            SubscriptionService,
            STTService,
            TTSService,
            VisionService,
            StickerPackService,
            BlackjackService,
            BatakService,
            RouletteService,
        )

        self.menu_service = MenuService()
        self.leaderboard_service = LeaderboardService(self.chat_engine.db, card_renderer=self.card_renderer)
        self.guess_age_service.leaderboard_service = self.leaderboard_service
        self.uno_service.leaderboard = self.leaderboard_service
        self.economy_service = EconomyService(self.chat_engine.db, intent_signing_key=Config.TELEGRAM_TOKEN)
        self.reminder_service = ReminderService()
        self.productivity_service = ProductivityService(self.chat_engine.db)
        self.subscription_service = SubscriptionService(SubscriptionStore(self.chat_engine.db))
        self.announcement_service = AnnouncementService(self.subscription_service.store)
        self.notification_preference_store = NotificationPreferenceStore(self.chat_engine.db)
        self.daily_brief_service = DailyBriefService(
            self.chat_engine.feature_executor,
            self.productivity_service,
        )
        self.tts_service = TTSService(
            output_dir=Config.TTS_OUTPUT_DIR,
            max_concurrency=Config.TTS_MAX_CONCURRENCY,
            timeout_seconds=Config.TTS_TIMEOUT_SECONDS,
            max_chars=Config.TTS_MAX_CHARS,
            max_output_bytes=Config.TTS_MAX_OUTPUT_BYTES,
        )
        self.stt_service = STTService(self.chat_engine)
        self.vision_service = VisionService(self.chat_engine)
        self.attachment_service = AttachmentProcessingService(
            vision_service=self.vision_service,
            stt_service=self.stt_service,
            tts_service=self.tts_service,
            sticker_render_service=self.sticker_render_service,
            database=self.chat_engine.db,
        )
        self.quote_service = QuoteProcessingService(
            render_service=self.quote_render_service,
            database=self.chat_engine.db,
        )
        self.speech_service = SpeechSynthesisService(
            tts_service=self.tts_service,
            database=self.chat_engine.db,
        )
        self.sticker_tag_service = StickerTagService(
            vision_service=self.vision_service,
            database=self.chat_engine.db,
        )
        self.market_chart_job_service = MarketChartProcessingService(
            renderer=self.market_chart_service,
            database=self.chat_engine.db,
        )
        self.news_digest_service = NewsDigestService(
            self.chat_engine,
            feature_executor=self.chat_engine.feature_executor,
            provider=getattr(self.chat_engine, "rss_news_provider", None),
        )
        self.group_analytics_service = GroupAnalyticsService(self.chat_engine.db)
        self.sticker_pack_service = StickerPackService(self.chat_engine.db)
        self.blackjack_service = BlackjackService(
            self.chat_engine.db,
            economy_service=self.economy_service,
            leaderboard_service=self.leaderboard_service,
            action_ledger=self.game_action_ledger,
        )
        self.batak_service = BatakService(
            self.chat_engine.db,
            leaderboard_service=self.leaderboard_service,
            economy_service=self.economy_service,
            action_ledger=self.game_action_ledger,
        )
        self.roulette_service = RouletteService(
            self.chat_engine.db,
            action_ledger=self.game_action_ledger,
            leaderboard_service=self.leaderboard_service,
            economy_service=self.economy_service,
        )

        self.command_handler = CommandHandler(
            chat_engine=self.chat_engine,
            sudo_manager=self.sudo_manager,
            image_router=self.image_router,
            image_generation_service=self.image_generation_service,
            tool_manager=self.chat_engine.tool_manager,
            menu_service=self.menu_service,
            leaderboard_service=self.leaderboard_service,
            economy_service=self.economy_service,
            announcement_service=self.announcement_service,
            reminder_service=self.reminder_service,
            productivity_service=self.productivity_service,
            subscription_service=self.subscription_service,
            notification_preference_store=self.notification_preference_store,
            tts_service=self.tts_service,
            speech_service=self.speech_service,
            news_digest_service=self.news_digest_service,
            group_analytics_service=self.group_analytics_service,
            moderation_service=self.moderation_service,
            sticker_pack_service=self.sticker_pack_service,
            blackjack_service=self.blackjack_service,
            batak_service=self.batak_service,
            roulette_service=self.roulette_service,
            market_chart_service=self.market_chart_service,
            market_chart_job_service=self.market_chart_job_service,
            card_renderer=self.card_renderer,
            data_port=self.data_port,
            akinator_service=self.akinator_service,
            guess_age_service=self.guess_age_service,
            uno_service=self.uno_service,
        )
        # Guardian actions use the same durable ledger and Telegram role
        # boundary as automatic moderation.  There is no second bot or
        # duplicate action store in the embedded Guardian surface.
        self.command_handler.moderation_action_ledger = self.moderation_action_ledger
        self.callback_handler = CallbackHandler(
            chat_engine=self.chat_engine,
            sudo_manager=self.sudo_manager,
            tool_manager=self.chat_engine.tool_manager,
            menu_service=self.menu_service,
            leaderboard_service=self.leaderboard_service,
            economy_service=self.economy_service,
            announcement_service=self.announcement_service,
            subscription_service=self.subscription_service,
            notification_preference_store=self.notification_preference_store,
            productivity_service=self.productivity_service,
            sticker_pack_service=self.sticker_pack_service,
            game_action_ledger=self.game_action_ledger,
            blackjack_service=self.blackjack_service,
            batak_service=self.batak_service,
            roulette_service=self.roulette_service,
            data_port=self.data_port,
            akinator_service=self.akinator_service,
            guess_age_service=self.guess_age_service,
            uno_service=self.uno_service,
        )
        self.message_handler = MessageHandler(
            chat_engine=self.chat_engine,
            sudo_manager=self.sudo_manager,
            image_router=self.image_router,
            image_generation_service=self.image_generation_service,
            tool_manager=self.chat_engine.tool_manager,
            leaderboard_service=self.leaderboard_service,
            economy_service=self.economy_service,
            sticker_tag_service=self.sticker_tag_service,
            data_port=self.data_port,
            recent_message_index=self.group_reply_index,
            reply_delivery_ledger=self.group_reply_ledger,
            group_reply_router=GroupReplyRouter(metrics=self.group_reply_metrics),
            group_reply_delivery=GroupReplyDeliveryService(
                ledger=self.group_reply_ledger,
                metrics=self.group_reply_metrics,
            ),
            group_reply_metrics=self.group_reply_metrics,
            authorization=getattr(self, "telegram_authorization", None),
            guess_age_service=self.guess_age_service,
        )
        self.message_handler.word_chain_service = self.command_handler.word_chain_service
        self.media_handler = MediaHandler(
            chat_engine=self.chat_engine,
            sudo_manager=self.sudo_manager,
            tool_manager=self.chat_engine.tool_manager,
            vision_service=self.vision_service,
            stt_service=self.stt_service,
            tts_service=self.tts_service,
            media_service=self.media_service,
            sticker_render_service=self.sticker_render_service,
            document_service=self.document_service,
            attachment_service=self.attachment_service,
            authorization=getattr(self, "telegram_authorization", None),
        )
        self.command_handler.media_handler = self.media_handler
        self.callback_handler.message_handler = self.message_handler
        self.callback_handler.media_handler = self.media_handler
        from src.services.tabu_service import TabuService
        from src.handlers.tabu_handler import TabuHandler
        self.tabu_handler = TabuHandler(
            TabuService(self.data_port, leaderboard=self.leaderboard_service, economy=self.economy_service),
            sudo_manager=self.sudo_manager, authorization=getattr(self, "telegram_authorization", None),
        )
        self.command_handler.tabu_handler = self.tabu_handler
        self.callback_handler.tabu_handler = self.tabu_handler
        self.message_handler.tabu_handler = self.tabu_handler
        self.command_handler.game_runtime.tabu_handler = self.tabu_handler
        from src.services.spy_service import SpyService
        from src.handlers.spy_handler import SpyHandler
        from src.services.vampire_service import VampireService
        from src.handlers.vampire_handler import VampireHandler
        for name, service_class, handler_class in (("spy_handler", SpyService, SpyHandler),
                                                    ("vampire_handler", VampireService, VampireHandler)):
            adapter = handler_class(
                service_class(self.data_port, leaderboard=self.leaderboard_service, economy=self.economy_service,
                              **({'ruleset': 'greywolf'} if service_class is VampireService else {})),
                sudo_manager=self.sudo_manager, authorization=getattr(self, "telegram_authorization", None),
            )
            setattr(self, name, adapter)
            for surface in (self.command_handler, self.callback_handler, self.message_handler, self.command_handler.game_runtime):
                setattr(surface, name, adapter)
        from src.games.puzzle_catalog import PUZZLES
        from src.handlers.puzzle_suite import PuzzleSuite
        from src.handlers.economy_handler import EconomyHandler
        self.economy_handler = EconomyHandler(self.economy_service)
        self.command_handler.economy_handler = self.economy_handler
        self.callback_handler.economy_handler = self.economy_handler
        from src.handlers.word_puzzles_handler import WordPuzzlesHandler
        from src.handlers.grid_puzzles_handler import GridPuzzlesHandler
        from src.handlers.logic_puzzles_handler import LogicPuzzlesHandler
        from src.services.word_puzzles_service import WordPuzzlesService
        from src.services.grid_puzzles_service import GridPuzzlesService
        from src.services.logic_puzzles_service import LogicPuzzlesService
        from src.handlers.word_arcade_handler import WordArcadeHandler
        from src.services.word_arcade_service import WordArcadeService
        from src.handlers.reasoning_arcade_handler import ReasoningArcadeHandler
        from src.services.reasoning_arcade_service import ReasoningArcadeService
        from src.handlers.social_arcade_handler import SocialArcadeHandler
        from src.services.social_arcade_service import SocialArcadeService
        from src.handlers.card_table import CardTableHandler
        from src.services.card_table_service import CardTableService
        from src.handlers.tile2048 import Tile2048Handler
        from src.services.tile2048_service import Tile2048Service
        from src.handlers.clue_arcade import ClueArcadeHandler
        from src.services.clue_arcade_service import ClueArcadeService
        puzzle_adapters = {
            "word": WordPuzzlesHandler(WordPuzzlesService(self.data_port, leaderboard=self.leaderboard_service, economy=self.economy_service), sudo_manager=self.sudo_manager, authorization=getattr(self, "telegram_authorization", None)),
            "grid": GridPuzzlesHandler(GridPuzzlesService(self.data_port, leaderboard=self.leaderboard_service, economy=self.economy_service), sudo_manager=self.sudo_manager, authorization=getattr(self, "telegram_authorization", None)),
            "logic": LogicPuzzlesHandler(LogicPuzzlesService(self.data_port, leaderboard=self.leaderboard_service, economy=self.economy_service), sudo_manager=self.sudo_manager, authorization=getattr(self, "telegram_authorization", None)),
        }
        for family, adapter_class, service_class in (
            ("word_arcade", WordArcadeHandler, WordArcadeService),
            ("reasoning_arcade", ReasoningArcadeHandler, ReasoningArcadeService),
            ("social_arcade", SocialArcadeHandler, SocialArcadeService),
            ("card_table", CardTableHandler, CardTableService),
            ("tile2048", Tile2048Handler, Tile2048Service),
            ("clue_arcade", ClueArcadeHandler, ClueArcadeService),
        ):
            adapter = adapter_class(service_class(self.data_port, leaderboard=self.leaderboard_service, economy=self.economy_service),
                                    sudo_manager=self.sudo_manager, authorization=getattr(self, "telegram_authorization", None))
            puzzle_adapters[family] = adapter
            setattr(self, family + "_handler", adapter)
            setattr(self.message_handler, family + "_handler", adapter)
        from src.services.playing_card_ui import load_sticker_ids as load_playing_card_ids
        from src.services.card_table_inline_service import CardTableInlineService
        from src.services.batak_inline_service import BatakInlineService
        card_ids = load_playing_card_ids()

        async def card_table_applied(context, state):
            await self.card_table_handler.process(context.bot, {"state": state})

        async def batak_delivery_failed(chat_id, state):
            await self.batak_service.defer_panel(
                chat_id=chat_id, expected_game_id=state["game_id"])

        self.card_table_inline_service = CardTableInlineService(
            self.card_table_handler.service, self._card_inline_secret, card_ids,
            on_applied=card_table_applied)
        self.card_table_handler.inline_service = self.card_table_inline_service
        self.batak_inline_service = BatakInlineService(
            self.batak_service, secret=self._card_inline_secret, file_ids=card_ids,
            render_public=self.callback_handler._batak_public_text,
            keyboard=self.callback_handler._batak_keyboard,
            defer_delivery=batak_delivery_failed)
        self.command_handler.batak_inline_service = self.batak_inline_service
        self.callback_handler.batak_inline_service = self.batak_inline_service
        self.puzzle_suite = PuzzleSuite({p.key: puzzle_adapters[p.family] for p in PUZZLES}, repository=self.data_port)
        for surface in (self.command_handler, self.callback_handler, self.message_handler, self.command_handler.game_runtime):
            surface.puzzle_suite = self.puzzle_suite
        from src.services.board_panel_service import BoardPanelService
        self.board_panel_service = BoardPanelService(self.data_port,
            leaderboard=self.leaderboard_service, economy=self.economy_service)
        for surface in (self.command_handler, self.callback_handler, self.command_handler.game_runtime):
            surface.board_panels = self.board_panel_service
        self.quote_handler = QuoteHandler(
            chat_engine=self.chat_engine,
            sudo_manager=self.sudo_manager,
            render_service=self.quote_render_service,
            quote_service=self.quote_service,
            data_port=self.data_port,
        )

        self.message_handler.user_states = self.user_states
        self.media_handler.user_states = self.user_states
        self.callback_handler.user_states = self.user_states
        self.command_handler.user_states = self.user_states
        self.message_handler.media_service = self.media_service
        self.message_handler.moderation = self.moderation
        self.message_handler.moderation_service = self.moderation_service
        self.message_handler.moderation_action_ledger = self.moderation_action_ledger
        self.media_handler.data_port = self.data_port
        self.media_handler.moderation_service = self.moderation_service
        self.media_handler.moderation_action_ledger = self.moderation_action_ledger

    async def handle_guardian_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Dispatch the embedded Kek Guardian command surface."""
        return await self.command_handler.handle_guardian_command(update, context)

    async def handle_service_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Apply Guardian settings to Telegram service events."""
        return await self.message_handler.handle_service_message(update, context)

    async def _background_cleanup_task(self):
        while True:
            for target in cleanup_directories():
                try:
                    # The sweep removes whole job directories, so it runs
                    # outside the event loop instead of blocking the bot.
                    removed = await asyncio.to_thread(
                        cleanup_old_files, target, max_age_seconds=CLEANUP_MAX_AGE_SECONDS
                    )
                    if removed:
                        self.media_service.metrics_registry.inc(
                            "media_cleanup_entries_removed", removed
                        )
                except Exception as e:
                    logger.error("Background cleanup error: %s", summarize_exception(e, label="background_cleanup"))
            await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)

    async def start_background_tasks(self, bot=None, job_queue=None):
        logger.info("🧹 Starting background cleanup task...")
        self._background_tasks.append(asyncio.create_task(self._background_cleanup_task(), name="arel-file-cleanup"))
        try:
            if bot is not None:
                await self.media_service.start(bot)
                await self.document_service.start(bot)
                await self.attachment_service.start(bot)
                await self.quote_service.start(bot)
                await self.speech_service.start(bot)
                await self.sticker_tag_service.start(bot)
                await self.market_chart_job_service.start(bot)
                await self.image_generation_service.start(bot)
                redis_client = getattr(self.media_service.queue, "redis", None)
                if self.group_reply_index.required and redis_client is None:
                    raise RuntimeError("GROUP_REPLY_REDIS_REQUIRED=true but Redis is unavailable")
                self.group_reply_index.attach_redis(redis_client)
                self.group_reply_ledger.attach_redis(redis_client)
                self.chat_engine.feature_executor.attach_shared_cache(self.media_service.queue.redis)
        except Exception:
            for task in self._background_tasks:
                task.cancel()
            self._background_tasks.clear()
            # If the second queue fails during bootstrap, release the first
            # queue as well so a partially started process cannot keep Redis
            # leases or workers alive.
            try:
                await self.market_chart_job_service.close()
            finally:
                try:
                    await self.sticker_tag_service.close()
                finally:
                    try:
                        await self.speech_service.close()
                    finally:
                        try:
                            await self.quote_service.close()
                        finally:
                            try:
                                await self.attachment_service.close()
                            finally:
                                try:
                                    await self.document_service.close()
                                finally:
                                    await self.media_service.close()
                                    await self.image_generation_service.close()
            raise
        self.state_store = await create_state_store(self.media_service.queue.redis)
        self.user_states.bind(self.state_store)
        await self.user_states.hydrate()
        try:
            mongo_ready = self.media_service.mongo_available if bot is not None else await self._get_data_port().ping()
            if mongo_ready:
                await self._get_data_port().ensure_indexes()
                if job_queue is not None:
                    if Config.APP_ROLE in {"combined", "bot"}:
                        job_queue.run_repeating(self.command_handler.game_runtime.tick, interval=5, first=1,
                                                name="arel-game-runtime", job_kwargs={"max_instances": 1, "coalesce": True})
                    await self._recover_persisted_reminders(job_queue)
                    job_queue.run_repeating(
                        self._subscription_tick,
                        interval=30,
                        first=5,
                        name="arel-subscription-tick",
                    )
                    job_queue.run_repeating(
                        self._guardian_night_tick,
                        interval=60,
                        first=10,
                        name="arel-guardian-night-mode",
                    )
                    job_queue.run_repeating(
                        self._daily_brief_tick,
                        interval=30,
                        first=15,
                        name="arel-daily-brief-tick",
                    )
            elif Config.MONGODB_REQUIRED or Config.APP_ROLE in {"bot", "worker"}:
                raise RuntimeError("MongoDB is required for the selected production role")
            else:
                logger.warning("MongoDB unavailable; persistence features are running in degraded mode")
        except Exception:
            if Config.MONGODB_REQUIRED or Config.APP_ROLE in {"bot", "worker"}:
                raise
            logger.warning("MongoDB index initialization skipped in degraded mode")
        if Config.OBSERVABILITY_ENABLED and Config.APP_ROLE in {"combined", "bot"}:
            self.observability_server = ObservabilityServer(
                host=Config.OBSERVABILITY_LISTEN,
                port=Config.OBSERVABILITY_PORT,
                token=Config.OBSERVABILITY_TOKEN,
                snapshot_factory=self._observability_snapshot,
                health_factory=self._observability_health,
            )
            await self.observability_server.start()

    async def _observability_snapshot(self) -> dict:
        """Collect bounded service metrics for the optional local exporter."""
        services = {
            "media": self.media_service,
            "documents": self.document_service,
            "attachments": self.attachment_service,
            "quotes": self.quote_service,
            "speech": self.speech_service,
            "sticker_tags": self.sticker_tag_service,
            "market_charts": self.market_chart_job_service,
            "images": self.image_generation_service,
        }
        snapshot: dict[str, object] = {}
        for name, service in services.items():
            try:
                snapshot[name] = await service.get_metrics()
            except Exception:
                snapshot[name] = {"metrics_errors": 1}
        snapshot["feature_persistence"] = self.chat_engine.feature_executor.persistence_metrics()
        snapshot["features"] = self.chat_engine.feature_executor.metrics_snapshot()
        snapshot["group_replies"] = self.group_reply_metrics.snapshot()
        try:
            snapshot["ai_router"] = self.chat_engine.ai_router.metrics_snapshot()
        except Exception:
            snapshot["ai_router"] = {"metrics_errors": 1}
        try:
            snapshot["ai"] = await self.chat_engine.usage_tracker.snapshot()
        except Exception:
            snapshot["ai"] = {"metrics_errors": 1}
        try:
            snapshot["guardian"] = await self.guardian_adapter.health_check()
        except Exception:
            snapshot["guardian"] = {"health": "unavailable"}
        return snapshot

    async def _observability_health(self) -> dict:
        """Return dependency state without exposing URLs, credentials or payloads."""
        now = asyncio.get_running_loop().time()
        if self._health_snapshot_cache and now - self._health_snapshot_cache[0] < 5.0:
            return dict(self._health_snapshot_cache[1])
        try:
            mongo_ok = bool(await asyncio.wait_for(self._get_data_port().ping(), timeout=1.5))
        except Exception:
            mongo_ok = False
        redis_client = getattr(self.media_service.queue, "redis", None)
        redis_ok = bool(redis_client)
        if redis_ok and callable(getattr(redis_client, "ping", None)):
            try:
                ping_result = redis_client.ping()
                if hasattr(ping_result, "__await__"):
                    ping_result = await asyncio.wait_for(ping_result, timeout=1.0)
                redis_ok = bool(ping_result)
            except Exception:
                redis_ok = False
        dependencies = {
            "mongodb": "healthy" if mongo_ok else "unavailable",
            "redis": "healthy" if redis_ok else "unavailable",
        }
        async def probe(factory):
            try:
                return await asyncio.wait_for(factory(), timeout=3.0), False
            except Exception:
                return [], True

        async def ai_health_probe():
            router = getattr(self.chat_engine, "ai_router", None)
            health = getattr(router, "health", None)
            if not callable(health):
                return []
            return await health()

        (feature_rows, feature_probe_error), (media_rows, media_probe_error), (ai_rows, ai_probe_error) = await asyncio.gather(
            probe(self.chat_engine.feature_executor.health_snapshot),
            probe(self.media_service.providers.health),
            probe(ai_health_probe),
        )
        provider_groups = {
            "features": summarize_provider_rows(feature_rows, probe_error=feature_probe_error),
            "media": summarize_provider_rows(media_rows, probe_error=media_probe_error),
            "ai": summarize_provider_rows(ai_rows, probe_error=ai_probe_error),
        }
        status = health_status(dependencies, provider_groups)
        payload = {
            "ok": bool(mongo_ok or not Config.MONGODB_REQUIRED)
            and bool(redis_ok or not Config.MEDIA_REDIS_REQUIRED),
            "status": status,
            "app_role": Config.APP_ROLE,
            "app_env": Config.APP_ENV,
            "dependencies": dependencies,
            "providers": provider_groups,
        }
        self._health_snapshot_cache = (asyncio.get_running_loop().time(), payload)
        return dict(payload)

    async def _subscription_tick(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Deliver due provider subscriptions with an atomic claim."""
        try:
            due = await self.subscription_service.store.due(limit=100)
            for item in due:
                # Personal quiet hours apply only to user subscriptions. Group
                # schedules are explicitly controlled by group administrators.
                if item.get("scope", "user") == "user":
                    quiet_until = await self.notification_preference_store.quiet_until(item["user_id"])
                    if quiet_until is not None:
                        await self.subscription_service.store.defer(item["subscription_id"], quiet_until)
                        continue
                if not await self.subscription_service.store.claim(item["subscription_id"]):
                    continue
                delivery_key = self.subscription_service.delivery_key(item)
                delivery_claimed = await self.subscription_service.store.claim_delivery(
                    delivery_key,
                    subscription_id=item["subscription_id"],
                    chat_id=item["chat_id"],
                )
                if not delivery_claimed:
                    # A previous worker may have sent the Telegram message
                    # and died before advancing the subscription row. Finish
                    # that row without sending a duplicate; an in-flight
                    # worker simply keeps ownership of the delivery key.
                    if await self.subscription_service.store.delivery_was_sent(delivery_key):
                        await self.subscription_service.store.complete(
                            item["subscription_id"],
                            next_run_at=self.subscription_service.next_run_for(item),
                            success=True,
                        )
                    continue
                success = False
                error = None
                fingerprint = None
                delivery_attempt = int(item.get("delivery_attempt", 0))
                delivery_finalized = False
                delivery_send_started = False
                try:
                    if item.get("feature_key") in {"group_announcement", "guardian_recurring"}:
                        # Announcement text is user-authored content. Escape it
                        # before using Telegram HTML parse mode.
                        params = item.get("params") or {}
                        body = html.escape(str(params.get("text", "Duyuru"))[:3500])
                        if not await self.subscription_service.store.begin_delivery_send(delivery_key):
                            raise RuntimeError("DELIVERY_LEASE_LOST")
                        delivery_send_started = True
                        sent_message = await context.bot.send_message(
                            chat_id=item["chat_id"],
                            text=f"📣 <b>GRUP DUYURUSU</b>\n\n{body}",
                            parse_mode="HTML",
                            message_thread_id=item.get("message_thread_id"),
                        )
                        await self.subscription_service.store.mark_delivery_sent(
                            delivery_key,
                            telegram_message_id=getattr(sent_message, "message_id", None),
                        )
                        delivery_finalized = True
                    else:
                        result = await self.chat_engine.feature_executor.execute(
                            item["feature_key"], item.get("params") or {}
                        )
                        if not result.ok:
                            error = result.error_code or "provider_error"
                            raise RuntimeError(error)
                        if item.get("condition") and not AlertService.matches(result.data, item["condition"]):
                            await self.subscription_service.store.mark_delivery_sent(
                                delivery_key, status="skipped"
                            )
                            delivery_finalized = True
                            success = True
                            continue
                        should_deliver, fingerprint = self.subscription_service.should_deliver(item, result.data)
                        if not should_deliver:
                            # Advance unchanged feeds without sending duplicate
                            # Telegram messages; a changed payload delivers.
                            await self.subscription_service.store.mark_delivery_sent(
                                delivery_key, status="skipped"
                            )
                            delivery_finalized = True
                            success = True
                            continue
                        body = format_feature_result(result, "Veri bulunamadı.")
                        if not await self.subscription_service.store.begin_delivery_send(delivery_key):
                            raise RuntimeError("DELIVERY_LEASE_LOST")
                        delivery_send_started = True
                        sent_message = await context.bot.send_message(
                            chat_id=item["chat_id"],
                            text=body,
                            parse_mode="HTML",
                            message_thread_id=item.get("message_thread_id"),
                        )
                        await self.subscription_service.store.mark_delivery_sent(
                            delivery_key,
                            telegram_message_id=getattr(sent_message, "message_id", None),
                        )
                        delivery_finalized = True
                    success = True
                except Exception as exc:
                    error = error or ("DELIVERY_OUTCOME_UNCERTAIN" if delivery_send_started else type(exc).__name__)
                    logger.warning("Subscription delivery failed: %s", error)
                    if delivery_send_started and not delivery_finalized:
                        await self.subscription_service.store.mark_delivery_uncertain(delivery_key, error)
                        delivery_finalized = True
                    elif not delivery_finalized:
                        await self.subscription_service.store.release_delivery(delivery_key, error)
                finally:
                    if success:
                        delivery_attempt = 0
                        next_run = self.subscription_service.next_run_for(item)
                    elif self._is_transient_subscription_error(error) and delivery_attempt < len(self.SUBSCRIPTION_RETRY_DELAYS):
                        next_run = datetime.now(timezone.utc) + timedelta(
                            seconds=self.SUBSCRIPTION_RETRY_DELAYS[delivery_attempt]
                        )
                        delivery_attempt += 1
                    else:
                        delivery_attempt = 0
                        next_run = self.subscription_service.next_run_for(item)
                    await self.subscription_service.store.complete(
                        item["subscription_id"],
                        next_run_at=next_run,
                        success=success,
                        error=error,
                        delivery_attempt=delivery_attempt,
                        fingerprint=fingerprint,
                    )
        except Exception as exc:
            logger.error("Subscription tick failed: %s", summarize_exception(exc, label="subscription_tick"))

    async def _guardian_night_tick(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Apply persisted Guardian night windows after restarts."""
        try:
            now = datetime.now(ZoneInfo("Europe/Istanbul"))
            current = now.strftime("%H:%M")
            for chat_id in await self.chat_engine.get_active_chat_ids():
                if int(chat_id) >= 0:
                    continue
                settings = await self._get_data_port().get_chat_settings(int(chat_id)) or {}
                if not settings.get("night_mode_enabled"):
                    continue
                start = str(settings.get("night_start") or "22:00")
                end = str(settings.get("night_end") or "08:00")
                in_window = (start <= current < end) if start < end else (current >= start or current < end)
                locked = bool(settings.get("guardian_night_locked"))
                if in_window == locked:
                    continue
                if await self.guardian_telegram_service.set_night_permissions(
                    bot=getattr(context, "bot", None), chat_id=int(chat_id), locked=in_window
                ):
                    await self._get_data_port().update_chat_setting_key(int(chat_id), "guardian_night_locked", in_window)
                    try:
                        await context.bot.send_message(
                            chat_id=int(chat_id),
                            text=("🌙 gece modu devrede, grup kilitlendi" if in_window else "☀️ gece modu bitti, grup yeniden açıldı"),
                        )
                    except Exception:
                        pass
        except Exception as exc:
            logger.warning("Guardian night tick failed: %s", summarize_exception(exc, label="guardian_night_tick"))

    async def _daily_brief_tick(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Deliver personal daily digests with an atomic local-date claim."""
        try:
            due = await self.notification_preference_store.due_daily_brief(limit=100)
            for item in due:
                user_id = int(item["user_id"])
                quiet_until = await self.notification_preference_store.quiet_until(user_id)
                if quiet_until is not None:
                    continue
                timezone_name = str(item.get("timezone") or "Europe/Istanbul")
                from zoneinfo import ZoneInfo

                local_date = datetime.now(timezone.utc).astimezone(ZoneInfo(timezone_name)).date().isoformat()
                if not await self.notification_preference_store.claim_daily_brief(user_id, local_date):
                    continue
                try:
                    text = await self.daily_brief_service.build(
                        user_id,
                        city=str(item.get("daily_brief_city") or "İstanbul"),
                        timezone_name=timezone_name,
                    )
                    await context.bot.send_message(chat_id=user_id, text=text, parse_mode="HTML")
                except Exception as exc:
                    await self.notification_preference_store.release_daily_brief(user_id, local_date)
                    logger.warning("Daily brief delivery failed: %s", summarize_exception(exc, label="daily_brief_delivery"))
        except Exception as exc:
            logger.error("Daily brief tick failed: %s", summarize_exception(exc, label="daily_brief_tick"))

    @staticmethod
    def _is_transient_subscription_error(error: str | None) -> bool:
        if not error:
            return False
        normalized = error.casefold()
        return normalized in {
            "provider_unavailable",
            "timeouterror",
            "timedout",
            "networkerror",
            "connectionerror",
            "retryafter",
        } or any(token in normalized for token in ("timeout", "network", "connection"))

    async def _recover_persisted_reminders(self, job_queue) -> None:
        """Requeue reminders that survived a bot restart."""
        try:
            reminders = await self.productivity_service.pending_reminders()
            now = datetime.now(timezone.utc)
            for reminder in reminders:
                due_at = reminder.get("due_at")
                if due_at is None:
                    continue
                if due_at.tzinfo is None:
                    due_at = due_at.replace(tzinfo=timezone.utc)
                delay = max(1, int((due_at - now).total_seconds()))
                job_queue.run_once(
                    self.command_handler._send_reminder,
                    delay,
                    chat_id=reminder["chat_id"],
                    name=f"reminder_{reminder['reminder_id']}",
                    data={
                        "text": reminder["text"],
                        "user_id": reminder.get("user_id", 0),
                        "due_at": due_at.isoformat(),
                        "reminder_id": reminder["reminder_id"],
                        **({"recurrence": reminder.get("recurrence"), "timezone": reminder.get("timezone", "Europe/Istanbul")}
                           if reminder.get("recurrence") else {}),
                    },
                )
        except Exception as exc:
            logger.warning("Persisted reminder recovery failed: %s", exc)

    async def connect_mcp(self):
        await self.mcp_manager.connect_all()

    async def shutdown(self):
        """Stop background workers and release external resources."""
        if self.observability_server is not None:
            await self.observability_server.close()
            self.observability_server = None
        for task in self._background_tasks:
            task.cancel()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
        self._background_tasks.clear()
        # Drain the media reapers before the service stops: a timed-out
        # download must not leave its job directory behind.
        await self.media_service.wait_for_background_cleanup(timeout=SHUTDOWN_CLEANUP_TIMEOUT_SECONDS)
        await self.media_service.close()
        await self.document_service.close()
        await self.attachment_service.close()
        await self.quote_service.close()
        await self.speech_service.close()
        await self.sticker_tag_service.close()
        await self.market_chart_job_service.close()
        await self.image_generation_service.close()
        await self.image_router.close()
        if hasattr(self.quote_handler, "render_service"):
            await self.quote_handler.render_service.close()
        if self.state_store and hasattr(self.state_store, "close"):
            await self.state_store.close()
        if hasattr(self.game_action_ledger, "close"):
            await self.game_action_ledger.close()
        await self.user_states.close()
        await self.chat_engine.close()
        await self.mcp_manager.disconnect_all()

    async def handle_clear_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        chat = update.effective_chat
        scope_user = None if chat.id > 0 else user.id
        deleted = await self.chat_engine.clear_memory(chat.id, scope_user)
        if hasattr(self.user_states, "clear_user"):
            self.user_states.clear_user(user.id)
        else:
            self.user_states.pop(user.id, None)
        await update.effective_message.reply_text(
            f"Sohbet hafizasi temizlendi. Silinen kayit: <code>{deleted}</code>", parse_mode="HTML"
        )

    async def handle_memory_consent_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Manage explicit AI memory consent and user-scoped fact records."""
        args = [str(item).strip() for item in (context.args or [])]
        action = (args[0].casefold() if args else "status")
        if update.effective_chat.type != "private":
            await update.effective_message.reply_text(
                "Ki\u015fisel AI haf\u0131zas\u0131 yaln\u0131zca ozel sohbette yonetilebilir."
            )
            return
        user_id = int(update.effective_user.id)
        db = getattr(self, "data_port", None) or HandlerDataPort.from_engine(self.chat_engine)
        aliases = {
            "a\u00e7\u0131k": "on", "acik": "on", "1": "on", "true": "on",
            "kapal\u0131": "off", "kapali": "off", "0": "off", "false": "off",
            "liste": "show", "g\u00f6ster": "show", "goster": "show",
            "sil": "delete", "temizle": "clear",
        }
        action = aliases.get(action, action)
        if action in {"on", "off"}:
            enabled = action == "on"
            await db.set_memory_consent(user_id, enabled)
            if not enabled:
                deleted = await db.delete_user_facts(user_id)
                audit = getattr(db, "add_audit_log", None)
                if audit:
                    await audit(user_id, "memory_consent_changed", enabled=False, deleted=deleted)
                await update.effective_message.reply_text(
                    f"Ki\u015fisel AI haf\u0131zas\u0131 kapat\u0131ld\u0131; {deleted} kay\u0131t silindi."
                )
                return
            audit = getattr(db, "add_audit_log", None)
            if audit:
                await audit(user_id, "memory_consent_changed", enabled=True)
            await update.effective_message.reply_text(
                "Ki\u015fisel AI haf\u0131zas\u0131 a\u00e7\u0131ld\u0131. Kay\u0131tlar\u0131 g\u00f6rmek i\u00e7in /hafiza show yazabilirsin."
            )
            return
        current = bool(await db.get_memory_consent(user_id))
        if action in {"status", "durum"}:
            status_label = "a\u00e7\u0131k" if current else "kapal\u0131"
            await update.effective_message.reply_text(
                f"Ki\u015fisel AI haf\u0131zas\u0131: <b>{status_label}</b>\n"
                "A\u00e7mak/kapatmak: /hafiza on veya /hafiza off\n"
                "Kay\u0131tlar: /hafiza show | /hafiza delete ID | /hafiza clear",
                parse_mode="HTML",
            )
            return
        if action in {"clear", "delete_all"}:
            if not current:
                await update.effective_message.reply_text("Ki\u015fisel haf\u0131za zaten kapal\u0131; silinecek kay\u0131t yok.")
                return
            deleted = await db.delete_user_facts(user_id)
            audit = getattr(db, "add_audit_log", None)
            if audit:
                await audit(user_id, "memory_clear", deleted=deleted)
            await update.effective_message.reply_text(f"Ki\u015fisel haf\u0131zadaki {deleted} kay\u0131t silindi.")
            return
        if action == "delete":
            if not current:
                await update.effective_message.reply_text("Ki\u015fisel haf\u0131za kapal\u0131.")
                return
            if len(args) < 2:
                await update.effective_message.reply_text("Kullan\u0131m: /hafiza delete <kay\u0131t_id>")
                return
            deleted = await db.delete_user_fact(user_id, args[1])
            audit = getattr(db, "add_audit_log", None)
            if audit:
                await audit(user_id, "memory_fact_deleted", deleted=bool(deleted))
            await update.effective_message.reply_text(
                "Haf\u0131za kayd\u0131 silindi." if deleted else "Bu ID ile sana ait bir haf\u0131za kayd\u0131 bulunamad\u0131."
            )
            return
        if action == "show":
            if not current:
                await update.effective_message.reply_text(
                    "Ki\u015fisel haf\u0131za kapal\u0131. A\u00e7mak i\u00e7in /hafiza on yazabilirsin."
                )
                return
            rows = await db.list_user_facts(user_id, limit=50)
            if not rows:
                await update.effective_message.reply_text("Kay\u0131tl\u0131 bir haf\u0131za notu yok.")
                return
            from html import escape
            lines = ["<b>Ki\u015fisel AI haf\u0131zan</b>", ""]
            for row in rows:
                lines.append(
                    f"- <code>{escape(row['fact_id'])}</code> "
                    f"<b>{escape(row['key'])}</b>: {escape(row['value'])}"
                )
            lines.append("\nTek bir kayd\u0131 silmek i\u00e7in: <code>/hafiza delete ID</code>")
            await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")
            return
        await update.effective_message.reply_text(
            "Haf\u0131za kullan\u0131m\u0131: /hafiza on | off | show | delete ID | clear"
        )

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.message_handler.handle_message(update, context)

    async def handle_chat_member_update(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.message_handler.handle_chat_member_update(update, context)

    async def handle_caption(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        from src.media.url_normalizer import extract_media_url

        message = update.effective_message
        moderate = getattr(self.media_handler, "moderate_update", None)
        if callable(moderate) and await moderate(update, context):
            return
        if await self._route_private_draw_photo(update, context):
            return
        if await self._route_social_caption(update, context):
            return
        url = extract_media_url(message.caption or "")
        if url:
            await self._queue_media_download(update, url)
            return
        if message.photo:
            await self.media_handler.handle_photo(update, context, skip_moderation=True)
        elif message.document:
            await self.media_handler.handle_document(update, context, skip_moderation=True)

    async def handle_animation(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Keep passive GIFs silent; captions with links still enter media."""

        message = update.effective_message
        if message is None or not getattr(message, "animation", None):
            return
        moderate = getattr(self.media_handler, "moderate_update", None)
        if callable(moderate) and await moderate(update, context):
            return
        if await self._route_social_caption(update, context):
            return
        from src.media.url_normalizer import extract_media_url

        url = extract_media_url(getattr(message, "caption", None) or "")
        if url:
            await self._queue_media_download(update, url)
        # A GIF never starts vision/sticker work implicitly. A /sticker
        # command replying to this message remains an explicit command path.

    async def _route_social_caption(self, update, context):
        text = str(getattr(update.effective_message, "caption", None) or "").strip()
        if not text:
            return False
        word_arcade = getattr(self, "word_arcade_handler", None)
        if word_arcade and await word_arcade.on_clue_text(update, context, text):
            return True
        for name in ("tabu_handler", "spy_handler"):
            adapter = getattr(self, name, None)
            if adapter and await adapter.on_text(update, context, text):
                return True
        return False

    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        message = update.effective_message
        if (getattr(update.effective_chat, "type", None) == "private" and
                message is not None and getattr(message, "photo", None)):
            moderate = getattr(self.media_handler, "moderate_update", None)
            if callable(moderate) and await moderate(update, context):
                return
            if await self._route_private_draw_photo(update, context):
                return
            await self.media_handler.handle_photo(update, context, skip_moderation=True)
            return
        await self.media_handler.handle_photo(update, context)

    async def _route_private_draw_photo(self, update, context):
        if (getattr(update.effective_chat, "type", None) != "private" or
                not getattr(update.effective_message, "photo", None)):
            return False
        suite = getattr(self, "puzzle_suite", None)
        return bool(suite and await suite.on_private_photo(update, context))

    async def handle_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.media_handler.handle_document(update, context)

    async def handle_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.media_handler.handle_voice(update, context)

    async def handle_passive_attachment(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Moderate non-text attachments without starting an AI workflow.

        GIFs, videos, audio, video notes and stickers are passive by default.
        Their captions are handled by ``handle_caption``; this path exists for
        uncaptioned media so moderation cannot be bypassed and no spurious
        group-facing error is emitted.
        """
        moderate = getattr(self.media_handler, "moderate_update", None)
        if callable(moderate):
            await moderate(update, context)

    async def handle_download_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        from src.media.url_normalizer import extract_media_url

        url = extract_media_url(" ".join(context.args or []))
        if not url:
            await update.effective_message.reply_text(
                "Kullanım: /indir <Instagram, TikTok, YouTube Shorts, X, Facebook veya Reddit bağlantısı>"
            )
            return
        await self._queue_media_download(update, url)

    async def handle_media_link(self, update: Update, context: ContextTypes.DEFAULT_TYPE, url: str = None):
        from src.media.url_normalizer import extract_media_url

        message = update.effective_message
        detected = url or extract_media_url((message.text or message.caption or ""))
        if detected:
            await self._queue_media_download(update, detected)

    async def _queue_media_download(self, update: Update, url: str):
        from src.media.url_normalizer import InvalidMediaURL
        from src.features.chat_feature_policy import is_chat_feature_enabled

        if self.sudo_manager.is_blacklisted(update.effective_user.id, update.effective_chat.id):
            return
        # Lightweight hosts (replay tests and shutdown recovery) may not have
        # the full ChatEngine wired yet.  The policy helper deliberately keeps
        # the backwards-compatible default of enabled when the repository
        # port is unavailable; production instances always provide it.
        try:
            media_policy_port = self._get_data_port()
        except (AttributeError, RuntimeError):
            media_policy_port = None
        if not await is_chat_feature_enabled(
            media_policy_port,
            update.effective_chat.id,
            "media_enabled",
            True,
        ):
            return
        status = None
        try:
            status = await update.effective_message.reply_text("🔎 Bağlantı analiz ediliyor…")
            job_id, duplicate = await self.media_service.submit(
                url,
                update.effective_chat.id,
                update.effective_user.id,
                status.message_id,
                chat_type=getattr(update.effective_chat, "type", "private"),
            )
            if duplicate:
                await status.edit_text(f"⏳ Bu bağlantı için aktif bir iş var.\nİş: <code>{job_id}</code>", parse_mode="HTML")
            else:
                await status.edit_text(f"⏳ İndirme kuyruğa alındı.\nİş: <code>{job_id}</code>", parse_mode="HTML")
        except InvalidMediaURL as exc:
            await self._media_enqueue_failure(update, status, media_url_error_message(exc))
        except ValueError as exc:
            if str(exc) == "rate_limited":
                message = "⏱️ Çok sık medya isteği gönderdin. Lütfen bir dakika bekle."
            elif str(exc) == "maintenance":
                message = "Medya servisi bakımda. Lütfen daha sonra tekrar deneyin."
            else:
                message = "❌ Medya isteği geçersiz."
            await self._media_enqueue_failure(update, status, message)
        except Exception as exc:
            logger.error("Could not enqueue media URL: %s", summarize_exception(exc, label="media_enqueue"))
            await self._media_enqueue_failure(
                update,
                status,
                "❌ Medya işi başlatılamadı. Lütfen daha sonra tekrar deneyin.",
            )

    async def _media_enqueue_failure(self, update: Update, status, private_text: str) -> None:
        """Keep queue and validation errors out of group timelines."""

        if getattr(getattr(update, "effective_chat", None), "type", None) in {"group", "supergroup"}:
            if status is not None:
                try:
                    await status.delete()
                except Exception:
                    pass
            return
        if status is not None:
            await status.edit_text(private_text)
        else:
            await update.effective_message.reply_text(private_text)

    async def handle_media_job_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.sudo_manager.is_owner(update.effective_user.id):
            return
        job_id = (context.args or [None])[0]
        if not job_id:
            await update.effective_message.reply_text("Kullanım: /jobs <iş_id>")
            return
        job = await self.media_service.get_status(job_id)
        job_kind = "Medya"
        if not job:
            job = await self.document_service.get_status(job_id)
            job_kind = "Belge"
        if not job:
            job = await self.attachment_service.get_status(job_id)
            job_kind = "Ek medya"
        if not job:
            job = await self.quote_service.get_status(job_id)
            job_kind = "Quote"
        if not job:
            job = await self.speech_service.queue.get_job(job_id)
            job_kind = "Speech"
        if not job:
            job = await self.sticker_tag_service.queue.get_job(job_id)
            job_kind = "Sticker tag"
        if not job:
            job = await self.market_chart_job_service.queue.get_job(job_id)
            job_kind = "Market chart"
        if not job:
            job = await self.image_generation_service.get_status(job_id)
            job_kind = "Görsel"
        if not job:
            await update.effective_message.reply_text("\u0130\u015f bulunamad\u0131.")
            return
        await update.effective_message.reply_text(
            f"<b>{job_kind} \u015fi</b>\nID: <code>{job_id}</code>\nDurum: <b>{job.get('status')}</b>\nKaynak: {job.get('source') or job.get('filename', 'belge')}",
            parse_mode="HTML",
        )

    async def handle_media_metrics_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.sudo_manager.is_owner(update.effective_user.id):
            return
        metrics = await self.media_service.get_metrics()
        document_metrics = await self.document_service.get_metrics()
        attachment_metrics = await self.attachment_service.get_metrics()
        quote_metrics = await self.quote_service.get_metrics()
        speech_metrics = await self.speech_service.get_metrics()
        sticker_tag_metrics = await self.sticker_tag_service.get_metrics()
        chart_metrics = await self.market_chart_job_service.get_metrics()
        image_metrics = await self.image_generation_service.get_metrics()
        ai_metrics = await self.chat_engine.usage_tracker.snapshot()
        feature_persistence = self.chat_engine.feature_executor.persistence_metrics()
        await update.effective_message.reply_text(
            "📊 <b>Medya kuyruğu</b>\n" + "\n".join(f"{key}: <code>{value}</code>" for key, value in metrics.items())
            + "\n\n<b>Belge kuyruğu</b>\n" + "\n".join(f"{key}: <code>{value}</code>" for key, value in document_metrics.items())
            + "\n\n<b>Ek medya kuyruğu</b>\n" + "\n".join(f"{key}: <code>{value}</code>" for key, value in attachment_metrics.items())
            + "\n\n<b>Quote kuyruğu</b>\n" + "\n".join(f"{key}: <code>{value}</code>" for key, value in quote_metrics.items())
            + "\n\n<b>Speech kuyruğu</b>\n" + "\n".join(f"{key}: <code>{value}</code>" for key, value in speech_metrics.items())
            + "\n\n<b>Sticker etiket kuyruğu</b>\n" + "\n".join(f"{key}: <code>{value}</code>" for key, value in sticker_tag_metrics.items())
            + "\n\n<b>Grafik kuyruğu</b>\n" + "\n".join(f"{key}: <code>{value}</code>" for key, value in chart_metrics.items())
            + "\n\n<b>Görsel kuyruğu</b>\n" + "\n".join(f"{key}: <code>{value}</code>" for key, value in image_metrics.items())
            + "\n\n<b>Feature telemetry</b>\n"
            + "\n".join(f"{key}: <code>{value}</code>" for key, value in feature_persistence.items())
            + "\n\n<b>AI sağlayıcıları</b>\n" + "\n".join(
                f"{name}: <code>{data['successes']}/{data['requests']} · {data['avg_latency_ms']:.0f}ms · "
                f"{data['input_tokens'] + data['output_tokens']} token · ${data.get('cost_usd', 0):.6f}</code>"
                for name, data in ai_metrics.items()
            ),
            parse_mode="HTML",
        )

    async def handle_health_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.sudo_manager.is_owner(update.effective_user.id):
            return
        import shutil

        metrics = await self.media_service.get_metrics()
        document_metrics = await self.document_service.get_metrics()
        attachment_metrics = await self.attachment_service.get_metrics()
        quote_metrics = await self.quote_service.get_metrics()
        speech_metrics = await self.speech_service.get_metrics()
        sticker_tag_metrics = await self.sticker_tag_service.get_metrics()
        chart_metrics = await self.market_chart_job_service.get_metrics()
        image_metrics = await self.image_generation_service.get_metrics()
        feature_persistence = self.chat_engine.feature_executor.persistence_metrics()
        redis_state = "bağlı" if self.media_service.queue.redis else "bellek fallback"
        ffmpeg_state = "var" if shutil.which("ffmpeg") else "yok"
        ffprobe_state = "var" if shutil.which("ffprobe") else "yok"
        mongo_state = "bagli" if await self._get_data_port().ping() else "yok"
        provider_state = ", ".join(
            f"{item.name}:{'ok' if item.healthy else 'kapali'}"
            for item in await self.media_service.providers.health()
        )
        feature_provider_state = ", ".join(
            f"{item.name}:{'ok' if item.healthy else 'kapali'}"
            for item in await self.chat_engine.feature_executor.health_snapshot()
        )
        ai_health = await self.chat_engine.ai_router.health()
        ai_provider_state = ", ".join(
            f"{str(item.get('name', 'unknown'))[:40]}:{'ok' if item.get('healthy') else 'kapali'}"
            for item in ai_health
        ) or "yok"
        try:
            tts_health = await self.tts_service.health_check()
            tts_state = "ok" if tts_health.get("healthy") else "kapalı"
        except Exception:
            tts_state = "kapalı"
        try:
            sticker_health = await self.sticker_render_service.health_check()
            sticker_state = "ok" if sticker_health.get("healthy") else "ffmpeg yok"
        except Exception:
            sticker_state = "kapalı"
        try:
            quote_health = await self.quote_render_service.health_check()
            quote_state = "ok" if quote_health.get("healthy") else "kapalı"
        except Exception:
            quote_state = "kapalı"
        try:
            market_chart_health = await self.market_chart_service.health_check()
            market_chart_state = "ok" if market_chart_health.get("healthy") else "renderer yok"
        except Exception:
            market_chart_state = "kapal\u0131"
        try:
            image_provider_health = await self.image_router.health_check()
            image_provider_state = "ok" if image_provider_health.get("healthy") else "kapal\u0131"
        except Exception:
            image_provider_state = "kapal\u0131"
        try:
            guardian_health = await self.guardian_adapter.health_check()
            guardian_state = f"{guardian_health.get('mode', 'disabled')}/{guardian_health.get('health', 'unknown')}"
        except Exception:
            guardian_state = "unavailable"
        await update.effective_message.reply_text(
            "🩺 <b>Arel sağlık durumu</b>\n"
            f"Media queue: <code>{redis_state}</code>\n"
            f"MongoDB: <code>{mongo_state}</code>\n"
            f"FFmpeg: <code>{ffmpeg_state}</code>\n"
            f"FFprobe: <code>{ffprobe_state}</code>\n"
            f"TTS: <code>{tts_state}</code>\n"
            f"Sticker render: <code>{sticker_state}</code>\n"
            f"Quote render: <code>{quote_state}</code>\n"
            f"Market chart: <code>{market_chart_state}</code>\n"
            f"Image provider: <code>{image_provider_state}</code>\n"
            f"Guardian bridge: <code>{html.escape(guardian_state[:80])}</code>\n"
            f"City data: <code>{len(self.city_directory.all())} il / {sum(len(city.districts) for city in self.city_directory.all())} ilçe</code>\n"
            f"Providers: <code>{provider_state}</code>\n"
            f"Feature providers: <code>{feature_provider_state[:900]}</code>\n"
            f"AI providers: <code>{html.escape(ai_provider_state[:500])}</code>\n"
            f"Feature telemetry: <code>pending={feature_persistence.get('pending_health_writes')} dropped={feature_persistence.get('dropped_health_writes')}</code>\n"
            f"Worker: <code>{metrics.get('workers')}</code>\n"
            f"Queue depth: <code>{metrics.get('queue_depth')}</code>\n"
            f"Document queue: <code>{document_metrics.get('backend')} / {document_metrics.get('queue_depth')} / {document_metrics.get('workers')}</code>\n"
            f"Attachment queue: <code>{attachment_metrics.get('backend')} / {attachment_metrics.get('queue_depth')} / {attachment_metrics.get('workers')}</code>\n"
            f"Quote queue: <code>{quote_metrics.get('backend')} / {quote_metrics.get('queue_depth')} / {quote_metrics.get('workers')}</code>\n"
            f"Speech queue: <code>{speech_metrics.get('backend')} / {speech_metrics.get('queue_depth')} / {speech_metrics.get('workers')}</code>\n"
            f"Sticker tag queue: <code>{sticker_tag_metrics.get('backend')} / {sticker_tag_metrics.get('queue_depth')} / {sticker_tag_metrics.get('workers')}</code>\n"
            f"Market chart queue: <code>{chart_metrics.get('backend')} / {chart_metrics.get('queue_depth')} / {chart_metrics.get('workers')}</code>\n"
            f"Image queue: <code>{image_metrics.get('backend')} / {image_metrics.get('queue_depth')} / {image_metrics.get('workers')}</code>",
            parse_mode="HTML",
        )

    async def handle_providers_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.sudo_manager.is_owner(update.effective_user.id):
            return
        if context.args and context.args[0].casefold() in {"governance", "kaynak", "source"}:
            report = self.chat_engine.feature_registry.governance_report()
            await self._get_data_port().add_audit_log(
                update.effective_user.id,
                "provider_governance_view",
                pending_count=report.get("pending_count", 0),
                feature_count=report.get("feature_count", 0),
            )
            await update.effective_message.reply_text(
                format_provider_governance(report),
                parse_mode="HTML",
            )
            return
        if context.args and context.args[0].lower() == "reset":
            await self.media_service.providers.reset()
            self.chat_engine.feature_executor.reset_circuits()
            await self._get_data_port().add_audit_log(update.effective_user.id, "providers_reset")
            await update.effective_message.reply_text("\u2705 Sa\u011flay\u0131c\u0131 circuit durumlar\u0131 s\u0131f\u0131rland\u0131.")
            return
        media_rows = await self.media_service.providers.health()
        feature_rows = await self.chat_engine.feature_executor.health_snapshot()
        text = format_provider_health(media_rows, title="Medya sa\u011flay\u0131c\u0131lar\u0131")
        text += "\n\n" + format_provider_health(feature_rows, title="Feature sa\u011flay\u0131c\u0131lar\u0131")
        text += "\n\n" + format_ai_provider_health(await self.chat_engine.ai_router.health())
        image_health = await self.image_router.health_check()
        text += "\n\n<b>Image provider</b>\n" + (
            "\u2705 configured" if image_health.get("healthy") else "\u26a0\ufe0f key_not_configured"
        )
        guardian_health = await self.guardian_adapter.health_check()
        text += "\n\n<b>Guardian bridge</b>\n<code>" + html.escape(
            f"{guardian_health.get('mode', 'disabled')}/{guardian_health.get('health', 'unknown')}"
        ) + "</code>"
        await update.effective_message.reply_text(text, parse_mode="HTML")

    async def handle_features_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show the registered feature contract for administrators."""
        user_id = update.effective_user.id if update.effective_user else 0
        if not self.sudo_manager.is_owner(user_id):
            return
        args = [str(item).strip() for item in (context.args or []) if str(item).strip()]
        if args and args[0].casefold() in {"on", "off", "acik", "kapali"}:
            if len(args) != 2:
                await update.effective_message.reply_text(
                    "Kullanım: <code>/features on|off feature_key</code>", parse_mode="HTML"
                )
                return
            action = args[0].casefold()
            enabled = action in {"on", "acik"}
            feature_key = args[1].casefold()
            if self.chat_engine.feature_registry.get_feature(feature_key) is None:
                await update.effective_message.reply_text("Feature bulunamadı.", parse_mode="HTML")
                return
            if not self.chat_engine.feature_registry.runtime_toggle_supported(feature_key):
                await update.effective_message.reply_text(
                    "Bu özellik provider kill-switch kapsamı dışında; kendi queue/servis bakım kontrolü kullanılır.",
                    parse_mode="HTML",
                )
                return
            if not self.chat_engine.feature_registry.set_feature_enabled(feature_key, enabled):
                await update.effective_message.reply_text("Feature durumu değiştirilemedi.", parse_mode="HTML")
                return
            await self._get_data_port().add_audit_log(
                user_id,
                "feature_kill_switch",
                feature_key=feature_key,
                enabled=enabled,
                scope="process",
            )
            state = "açıldı" if enabled else "kapatıldı"
            await update.effective_message.reply_text(
                f"✅ <b>{html.escape(feature_key)}</b> kaynağı {state}. "
                "Bu acil durum ayarı restart sonrası config değerine döner.",
                parse_mode="HTML",
            )
            return
        definitions = self.chat_engine.feature_registry.definitions()
        lines = ["<b>Arelbot feature manifest</b>", ""]
        for item in definitions:
            commands = ", ".join(f"/{value}" for value in item.commands)
            status = self.chat_engine.feature_registry.feature_status(item.key)
            status_label = "acik" if status["enabled"] else "KAPALI"
            lines.append(
                f"• <code>{item.key}</code> [{item.category}] — {commands or '-'} "
                f"({item.timeout_ms}ms, cache={item.cache_ttl_seconds}s, {status_label})"
            )
        await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")

    async def handle_cache_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.sudo_manager.is_owner(update.effective_user.id):
            return
        if context.args and context.args[0].lower() in {"clear", "temizle"}:
            removed = await self.media_service.clear_cache()
            await self._get_data_port().add_audit_log(update.effective_user.id, "media_cache_clear", removed=removed)
            await update.effective_message.reply_text(f"✅ Medya cache temizlendi: <code>{removed}</code>", parse_mode="HTML")
            return
        await update.effective_message.reply_text("Kullanım: /cache clear")

    async def handle_maintenance_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.sudo_manager.is_owner(update.effective_user.id):
            return
        action = (context.args or ["status"])[0].lower()
        if action in {"on", "açık", "acik"}:
            self.media_service.set_maintenance(True)
            await self._get_data_port().add_audit_log(update.effective_user.id, "maintenance_toggle", enabled=True)
        elif action in {"off", "kapalı", "kapali"}:
            self.media_service.set_maintenance(False)
            await self._get_data_port().add_audit_log(update.effective_user.id, "maintenance_toggle", enabled=False)
        await update.effective_message.reply_text(
            f"Medya bakım modu: <b>{'açık' if self.media_service.maintenance else 'kapalı'}</b>", parse_mode="HTML"
        )

    async def handle_audit_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.sudo_manager.is_owner(update.effective_user.id):
            return
        try:
            rows = await self._get_data_port().get_audit_logs(limit=10)
        except Exception:
            rows = []
        if not rows:
            await update.effective_message.reply_text("Henüz denetim kaydı yok veya MongoDB bağlı değil.")
            return
        text = "<b>Son denetim kayıtları</b>\n" + "\n".join(
            f"• <code>{row.get('action', 'unknown')}</code> · actor <code>{row.get('actor_id')}</code>"
            for row in rows
        )
        await update.effective_message.reply_text(text, parse_mode="HTML")

    async def handle_media_cookie_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.sudo_manager.is_owner(update.effective_user.id):
            return
        args = [arg.strip().lower() for arg in (context.args or [])]
        action = args[0] if args else "durum"
        if action in {"on", "off"}:
            self.media_service.set_cookie_enabled(action == "on")
            await self._get_data_port().add_audit_log(update.effective_user.id, "media_cookie_toggle", enabled=action == "on")
            await update.effective_message.reply_text(f"✅ Yönetici cookie fallback {'açıldı' if action == 'on' else 'kapatıldı'}.")
            return
        if action == "durum":
            state = "açık" if self.media_service.cookie_enabled else "kapalı"
            rows = "\n".join(f"{platform}: {'✅' if enabled else '—'}" for platform, enabled in self.media_service.cookie_status().items())
            await update.effective_message.reply_text(f"Cookie fallback: <b>{state}</b>\n{rows}", parse_mode="HTML")
            return
        if action in {"sil", "delete"}:
            platform = args[1] if len(args) > 1 else ""
            try:
                deleted = self.media_service.cookie_vault.delete(platform)
                await self._get_data_port().add_audit_log(update.effective_user.id, "media_cookie_delete", platform=platform)
                await update.effective_message.reply_text("✅ Cookie silindi." if deleted else "Bu platform için kayıt yok.")
            except CookieVaultError as exc:
                await update.effective_message.reply_text(f"❌ {cookie_error_message(exc)}")
            return
        if action in {"yükle", "yukle", "upload"}:
            platform = args[1] if len(args) > 1 else ""
            try:
                self.media_service.cookie_vault._platform(platform)
            except CookieVaultError as exc:
                await update.effective_message.reply_text(f"❌ Kullanım: /medya_cookie yükle <platform>\n{cookie_error_message(exc)}")
                return
            set_scoped_state(
                self.user_states,
                user_id=update.effective_user.id,
                chat_id=update.effective_chat.id,
                value={"state": "awaiting_media_cookie", "platform": platform},
            )
            await self._get_data_port().add_audit_log(update.effective_user.id, "media_cookie_upload_requested", platform=platform)
            await update.effective_message.reply_text(
                f"📄 Şimdi <b>{platform}</b> için Netscape veya tarayıcı JSON cookie dışa aktarımını belge olarak gönderin.\n"
                "JSON içeriği seçilen platform alan adıyla sınırlandırılır; cookie yalnızca şifreli kasaya yazılır.", parse_mode="HTML"
            )
            return
        await update.effective_message.reply_text("Kullanım: /medya_cookie durum | on | off | yükle <platform> | sil <platform>")

    async def handle_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.callback_handler.handle_callback_query(update, context)

    async def handle_deep_link(self, update: Update, context: ContextTypes.DEFAULT_TYPE, payload: str) -> bool:
        return await self._handle_deep_link(update, context, payload)

    async def _handle_deep_link(self, update: Update, context: ContextTypes.DEFAULT_TYPE, payload: str) -> bool:
        if payload == "uno_source":
            from src.handlers.uno_source import deliver_source
            await deliver_source(update, context)
            return True
        for prefix, name in (("sp_", "spy_handler"), ("vp_", "vampire_handler"),
                             ("wa_", "word_arcade_handler"), ("sa_", "social_arcade_handler"),
                             ("ct_", "card_table_handler")):
            adapter = getattr(self, name, None)
            if payload.startswith(prefix) and adapter:
                return await adapter.deep_link(update, context, payload)
        if payload.startswith("tb_") and getattr(self, "tabu_handler", None):
            return await self.tabu_handler.deep_link(update, context, payload)
        import json
        import uuid
        logger.info("Processing deep link payload: %s", summarize_private_text(payload, label="deep_link"))

        file_id = None
        suffix = "static"

        if payload.startswith("stk_create_"):
            unique_id = payload.replace("stk_create_", "")
            logger.info("Checking sticker creation cache")

            try:
                cached_data = await self._get_data_port().get_creation_cache(unique_id)
                if cached_data:
                    if isinstance(cached_data, str):
                        cached_data = json.loads(cached_data)
                    file_id = cached_data.get('file_id')
                    suffix = cached_data.get('suffix', 'static')
                    logger.info("Sticker creation cache hit suffix=%s", suffix)
                else:
                    logger.warning("Sticker creation cache miss")
                    await update.message.reply_text(
                        "⚠️ <b>Bağlantı süresi dolmuş veya geçersiz.</b> Lütfen gruptan tekrar 'Yeni Paket Oluştur' butonuna basın.",
                        parse_mode='HTML'
                    )
                    return True
            except Exception as e:
                logger.error("Error retrieving/parsing sticker cache: %s", summarize_exception(e, label="sticker_cache"))
                await update.message.reply_text("⚠️ <b>Teknik bir hata oluştu.</b>", parse_mode='HTML')
                return True

        elif payload.startswith("stk_new_"):
            try:
                parts = payload.split("_")
                if len(parts) >= 4:
                    suffix = parts[-1]
                    file_id = "_".join(parts[2:-1])
            except (ValueError, IndexError):
                pass

        if file_id:
            try:
                user_id = update.effective_user.id
                user_name = update.effective_user.full_name

                bot = await context.bot.get_me()
                username = f"@{bot.username}"

                auto_title = f"{user_name}'s Pack | {username} ({'Video' if suffix == 'video' else 'Statik'})"

                set_scoped_state(
                    self.user_states,
                    user_id=user_id,
                    chat_id=update.effective_chat.id,
                    value={
                        'state': 'awaiting_pack_emoji',
                        'data': {
                            'file_id': file_id,
                            'is_video': suffix == "video",
                            'title': auto_title
                        }
                    },
                )
                logger.info("Sticker creation state advanced: awaiting_pack_emoji")

                await update.message.reply_text(
                    f"📦 <b>Yeni Sticker Paketi Oluşturma</b>\n\n"
                    f"Paket başlığı otomatik olarak <b>{auto_title}</b> şeklinde belirlendi.\n\n"
                    f"Bu akış özel sohbette ilerler; gruba dönersen adımlar tüketilmez.\n"
                    f"Şimdi burada paket sticker'ı için 1-3 emoji yazın (örn: 😀 😂), "
                    f"ardından bağlantı adını belirleyin.",
                    parse_mode='HTML'
                )
                return True
            except Exception as e:
                logger.error("Deep link processing failed: %s", summarize_exception(e, label="deep_link"))
                return False

        return False

    async def toggle_observation(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.toggle_observation(update, context)

    async def handle_sohbet_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.toggle_observation(update, context)

    async def handle_image_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_image_command(update, context)

    async def handle_ayar_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_ayar_command(update, context)

    async def handle_personality_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_personality_command(update, context)

    async def handle_culture_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_culture_command(update, context)

    async def handle_story_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_story_command(update, context)

    async def handle_lyrics_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_lyrics_command(update, context)

    async def handle_search_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_search_command(update, context)

    async def handle_cevir_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_cevir_command(update, context)

    async def handle_finance_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_finance_command(update, context)

    async def handle_sticker_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_sticker_command(update, context)

    async def handle_sticker_search(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_sticker_search(update, context)

    async def handle_catalog(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_catalog(update, context)

    async def handle_packs_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_packs_command(update, context)

    async def handle_stdelete_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_stdelete_command(update, context)

    async def handle_stpack_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_stpack_command(update, context)

    async def handle_inline_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        for controller in (self.uno_inline_service, self.card_table_inline_service,
                           self.batak_inline_service):
            if await controller.answer_query(update, context):
                return
        await self._handle_inline_query(update, context)

    async def handle_chosen_inline_result(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        for controller in (self.uno_inline_service, self.card_table_inline_service,
                           self.batak_inline_service):
            if await controller.chosen_result(update, context):
                return

    async def _handle_inline_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._handle_inline_query_typed(update, context)
        return

    async def _handle_inline_query_typed(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        from telegram import InlineQueryResultArticle, InlineQueryResultCachedSticker, InputTextMessageContent

        query = update.inline_query
        if not query:
            return
        try:
            inline_text = (query.query or "").strip()
            if inline_text.casefold().startswith("top:"):
                try:
                    requested_chat = int(inline_text.split(":", 1)[1].strip())
                except ValueError:
                    requested_chat = 0
                if requested_chat > 0:
                    authorized = requested_chat == int(query.from_user.id)
                else:
                    authorized = False
                    try:
                        member = await context.bot.get_chat_member(requested_chat, query.from_user.id)
                        authorized = member.status in {"member", "administrator", "creator"}
                    except Exception:
                        authorized = False
                if not authorized:
                    await query.answer([], cache_time=5, is_personal=True)
                    return
            page = await self.inline_query_service.search(query.query, query.offset)
            results = [
                InlineQueryResultArticle(
                    id=item.result_id,
                    title=item.title,
                    description=item.description,
                    input_message_content=InputTextMessageContent(item.text),
                )
                for item in page.articles
            ]
            results.extend(
                InlineQueryResultCachedSticker(id=item.result_id, sticker_file_id=item.file_id)
                for item in page.stickers
            )
            if not results:
                results = [
                    InlineQueryResultArticle(
                        id="inline:empty",
                        title="Sonuç bulunamadı",
                        description="Farklı bir şehir, haber, kripto, borsa, sticker veya quote araması deneyin.",
                        input_message_content=InputTextMessageContent(
                            "Arama sonucu bulunamadı. Örnek: sehir ist, haber istanbul, kripto btc, borsa thy veya top:-100123"
                        ),
                    )
                ]
            await query.answer(
                results,
                cache_time=page.cache_time,
                is_personal=False,
                next_offset=page.next_offset,
            )
        except Exception:
            logger.warning("Inline query failed", exc_info=True)
            await query.answer([], cache_time=5, is_personal=True)

    async def handle_remind_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_remind_command(update, context)

    async def handle_note_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_note_command(update, context)

    async def handle_notes_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_notes_command(update, context)

    async def handle_documents_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_documents_command(update, context)

    async def handle_task_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_task_command(update, context)

    async def handle_tasks_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_tasks_command(update, context)

    async def handle_plan_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_plan_command(update, context)

    async def handle_reminders_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_reminders_command(update, context)

    async def handle_notification_settings_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_notification_settings_command(update, context)

    async def handle_subscription_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_subscription_command(update, context)

    async def handle_alert_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_alert_command(update, context)

    async def handle_announcement_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_announcement_command(update, context)

    async def handle_menu_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_menu_command(update, context)

    async def handle_daily_brief_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_daily_brief_command(update, context)

    async def handle_leaderboard_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_leaderboard_command(update, context)

    async def handle_profile_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_profile_command(update, context)

    async def handle_achievements_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_achievements_command(update, context)

    async def handle_season_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_season_command(update, context)

    async def handle_economy_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_economy_command(update, context)

    async def handle_daily_reward_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_daily_reward_command(update, context)

    async def handle_lang_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_lang_command(update, context)

    async def handle_digest_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_digest_command(update, context)

    async def handle_group_analytics_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_group_analytics_command(update, context)

    async def handle_moderation_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_moderation_command(update, context)

    async def handle_quiz_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_quiz_command(update, context)

    async def handle_q_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.quote_handler.handle_q_command(update, context)

    async def handle_qtop_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.quote_handler.handle_qtop_command(update, context)

    async def handle_qrand_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.quote_handler.handle_qrand_command(update, context)

    async def handle_qd_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.quote_handler.handle_qd_command(update, context)

    async def handle_qtemplate_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.quote_handler.handle_qtemplate_command(update, context)

    async def handle_test_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_test_command(update, context)

    async def handle_ping_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_ping_command(update, context)

    async def handle_haber_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_haber_command(update, context)

    async def handle_deprem_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_deprem_command(update, context)

    async def handle_ucak_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_ucak_command(update, context)

    async def handle_hava_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_hava_command(update, context)

    async def handle_air_quality_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_air_quality_command(update, context)

    async def handle_city_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_city_command(update, context)

    async def handle_eczane_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_eczane_command(update, context)

    async def handle_akaryakit_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_akaryakit_command(update, context)

    async def handle_doviz_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_doviz_command(update, context)

    async def handle_bim_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_bim_command(update, context)

    async def handle_ezan_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_ezan_command(update, context)

    async def handle_hangman_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_hangman_command(update, context)

    async def handle_wordgame_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_wordgame_command(update, context)

    async def handle_anlat_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_anlat_command(update, context)

    async def handle_connect4_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_connect4_command(update, context)

    async def handle_tictactoe_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_tictactoe_command(update, context)

    async def handle_gomoku_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_gomoku_command(update, context)

    async def handle_chess_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_chess_command(update, context)

    async def handle_akinator_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_akinator_command(update, context)

    async def handle_guess_age_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_guess_age_command(update, context)

    async def handle_guess_age_guess_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_guess_age_guess_command(update, context)

    async def handle_uno_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_uno_command(update, context)

    async def handle_word_chain_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_word_chain_command(update, context)

    async def handle_tabu_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_tabu_command(update, context)

    async def handle_puzzle_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.puzzle_suite.command(update, context)

    async def handle_spy_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.spy_handler.command(update, context)

    async def handle_vampire_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.vampire_handler.command(update, context)

    async def handle_batak_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_batak_command(update, context)

    async def handle_blackjack_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_blackjack_command(update, context)

    async def handle_oyun_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_oyun_command(update, context)

    async def handle_oyunbitir_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_oyunbitir_command(update, context)

    async def handle_sisecevir_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_sisecevir_command(update, context)

    async def handle_truth_dare_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_truth_dare_command(update, context)

    async def handle_rusruleti_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_rusruleti_command(update, context)

    async def handle_zar_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_zar_command(update, context)

    async def handle_yazitura_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_yazitura_command(update, context)

    async def handle_slot_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_slot_command(update, context)

    async def handle_duello_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_duello_command(update, context)

    async def handle_tts_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_tts_command(update, context)

    async def handle_qcolor_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_qcolor_command(update, context)

    async def handle_qemoji_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_qemoji_command(update, context)

    async def handle_qrate_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_qrate_command(update, context)

    async def handle_qtheme_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.command_handler.handle_qtheme_command(update, context)

    async def handle_start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Render the welcome surface and resolve optional deep links."""
        user = update.effective_user
        if context.args:
            if await self.handle_deep_link(update, context, context.args[0]):
                return

        first_name = html.escape(user.first_name or "arkadaş")
        welcome_text = (
            f"🌌 <b>Hoş Geldiniz, {first_name}!</b>\n\n"
            "Ben <b>Arel</b>; doğal dille sohbet, medya, üretkenlik, veri araçları ve oyunları "
            "tek bir Telegram arayüzünde birleştiren yapay zeka asistanınım. 🤖✨\n\n"
            "Başlamak için bir mesaj yazın veya aşağıdaki menülerden birini seçin.\n\n"
            "<i>Gruplarda yönetici kontrollü ayarları ve planlı bildirimleri de kullanabilirsiniz.</i>"
        )
        try:
            bot_user = await context.bot.get_me()
        except Exception:
            bot_user = None
        keyboard = [
            [
                InlineKeyboardButton("🏠 Ana Menü", callback_data="menu_main"),
                InlineKeyboardButton("🎮 Oyun Salonu", callback_data="menu_games"),
            ],
            [
                InlineKeyboardButton("📖 Komut Rehberi", callback_data="help_cat_ai"),
                InlineKeyboardButton("⚙️ Grup Ayarları", callback_data="open_ayar_menu"),
            ],
            [
                InlineKeyboardButton("👨‍💻 Geliştirici", url="https://t.me/pastalikek"),
                InlineKeyboardButton("📢 Kanal", url="https://t.me/botsofspart"),
            ],
        ]
        if getattr(bot_user, "username", None):
            keyboard.insert(2, [InlineKeyboardButton(
                "➕ Arel'i Grubuna Ekle",
                url=f"https://t.me/{bot_user.username}?startgroup=true",
            )])
        markup = InlineKeyboardMarkup(keyboard)
        destination = None
        try:
            destination = tempfile.NamedTemporaryFile(prefix="arel_welcome_", suffix=".png", delete=False).name
            renderer = getattr(self, "card_renderer", None) or ArelCardRenderer()
            await asyncio.to_thread(renderer.render_welcome, destination, user.first_name or "arkadaş")
            await update.effective_message.reply_photo(
                photo=InputFile(destination),
                caption=welcome_text,
                reply_markup=markup,
                parse_mode="HTML",
            )
        except Exception as exc:
            logger.warning("Welcome card delivery failed: %s", summarize_exception(exc, label="welcome_card"))
            await update.effective_message.reply_text(
                welcome_text,
                reply_markup=markup,
                parse_mode="HTML",
            )
        finally:
            if destination:
                try:
                    os.unlink(destination)
                except OSError:
                    pass

    async def handle_help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Open the categorized help menu; detailed pages stay in callbacks."""
        help_text = (
            "<b>Arel yardım</b>\n\n"
            "Bir konu seç. İlgili komutları ve kısa kullanım örneklerini görebilirsin."
        )
        keyboard = [
            [
                InlineKeyboardButton("🤖 Yapay zekâ", callback_data="help_cat_ai"),
                InlineKeyboardButton("🎮 Oyunlar", callback_data="help_cat_games"),
            ],
            [
                InlineKeyboardButton("🎨 Sticker", callback_data="help_cat_sticker"),
                InlineKeyboardButton("📊 Finans ve öneriler", callback_data="help_cat_finance"),
            ],
            [
                InlineKeyboardButton("⚙️ Diğer araçlar", callback_data="help_cat_tools"),
                InlineKeyboardButton("📹 Medya indirme", callback_data="help_cat_download"),
            ],
            [InlineKeyboardButton("🪙 Ekonomi ve mağaza", callback_data="help_feat_economy"),
             InlineKeyboardButton("🎮 Tüm oyun rehberi", callback_data="help_feat_catalog_0")],
            [InlineKeyboardButton("🛡️ Grup yönetimi", callback_data="help_cat_guardian")],
        ]
        await update.effective_message.reply_text(
            help_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML",
        )

    async def handle_admin_center_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Owner-only operational center for the embedded Guardian runtime."""
        actor_id = int(getattr(update.effective_user, "id", 0) or 0)
        if not self.sudo_manager.is_owner(actor_id):
            return
        text = (
            "🛡️ <b>AREL MERKEZ</b>\n\n"
            "Kek Guardian artık Arel'in içinde çalışıyor; ayrı bot veya ikinci polling süreci gerekmez.\n\n"
            "<b>Operasyon:</b> /health · /metrics · /providers · /jobs\n"
            "<b>Yayın:</b> /broadcast · /broadcastpin\n"
            "<b>Yetki:</b> /sudo · /unsudo · /sudolist\n"
            "<b>Grup:</b> /groups · /audit\n\n"
            "Grup moderasyon komutları yalnızca Telegram grup yöneticilerine görünür ve ayrıca işlem anında doğrulanır."
        )
        await update.effective_message.reply_text(text, parse_mode="HTML")

    async def handle_sudohelp_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.sudo_manager.is_sudo(update.effective_user.id):
            return
        text = (
            "🚨 <b>SUDO ADMIN KOMUTLARI</b> 🚨\n\n"
            "• <code>/sudo id</code> — Yetkili ekle\n"
            "• <code>/unsudo id</code> — Yetkiyi kaldır\n"
            "• <code>/ban id</code> — Kullanıcı veya grubu engelle\n"
            "• <code>/unban id</code> — Engeli kaldır\n"
            "• <code>/broadcast mesaj</code> — Aktif sohbetlere duyuru\n"
            "• <code>/groups</code> — Kayıtlı grupları listele\n"
            "• <code>/sudolist</code> — Yetkili listesini göster\n"
            "• <code>/obs on|off</code> — Gözlem modunu yönet"
        )
        await update.effective_message.reply_text(text, parse_mode="HTML")

    async def handle_sudo_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.sudo_manager.is_owner(update.effective_user.id):
            return
        try:
            target_id = int((context.args or [])[0])
        except (IndexError, ValueError):
            await update.effective_message.reply_text("❌ Kullanım: /sudo <user_id>", parse_mode="HTML")
            return
        self.sudo_manager.add_sudo(target_id)
        await self._get_data_port().add_audit_log(update.effective_user.id, "sudo_add", target_id=target_id)
        await update.effective_message.reply_text(f"✅ {target_id} sudo listesine eklendi.", parse_mode="HTML")

    async def handle_unsudo_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.sudo_manager.is_owner(update.effective_user.id):
            return
        try:
            target_id = int((context.args or [])[0])
        except (IndexError, ValueError):
            await update.effective_message.reply_text("❌ Kullanım: /unsudo <user_id>", parse_mode="HTML")
            return
        self.sudo_manager.remove_sudo(target_id)
        await self._get_data_port().add_audit_log(update.effective_user.id, "sudo_remove", target_id=target_id)
        await update.effective_message.reply_text(f"✅ {target_id} sudo listesinden çıkarıldı.", parse_mode="HTML")

    @staticmethod
    def _moderation_target_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int | None:
        """Resolve a group moderation target from a reply first, then an id argument."""
        message = getattr(update, "effective_message", None)
        replied = getattr(message, "reply_to_message", None)
        replied_user = getattr(replied, "from_user", None)
        replied_id = getattr(replied_user, "id", None)
        if replied_id is not None:
            try:
                return int(replied_id)
            except (TypeError, ValueError):
                return None
        args = getattr(context, "args", None) or []
        try:
            return int(str(args[0]).strip())
        except (IndexError, TypeError, ValueError):
            return None

    async def handle_ban_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        actor_id = update.effective_user.id
        chat = getattr(update, "effective_chat", None)
        if chat is not None and getattr(chat, "type", "private") in {"group", "supergroup"}:
            if not await is_admin(
                update,
                context,
                self.sudo_manager,
                authorization=getattr(self, "telegram_authorization", None),
            ):
                await update.effective_message.reply_text(
                    "Bu işlemi yalnızca grup yöneticileri yapabilir. Yetki rozeti olmadan sihirbazlık yapmıyorum."
                )
                return
            target_id = self._moderation_target_id(update, context)
            if target_id is None:
                await update.effective_message.reply_text(
                    "Kullanım: /ban <kullanıcı_id> veya banlamak istediğin mesaja yanıt ver."
                )
                return
            if target_id == actor_id or target_id <= 0:
                await update.effective_message.reply_text("Kendini ya da geçersiz bir hedefi banlayamam.")
                return
            action = ModerationAction(
                action="ban",
                chat_id=int(chat.id),
                actor_id=int(actor_id),
                target_user_id=int(target_id),
                reason="manual_group_ban",
                metadata={
                    "source": "manual_group_ban",
                    "generation_id": f"update:{getattr(update, 'update_id', None) or getattr(getattr(update, 'effective_message', None), 'message_id', 0)}",
                },
            )
            result = await TelegramModerationActionExecutor(
                getattr(context, "bot", None),
                ledger=getattr(self, "moderation_action_ledger", None) or ModerationActionLedger(),
                permission_checker=TelegramModerationAuthorization(
                    getattr(context, "bot", None),
                    bot_id=getattr(getattr(context, "bot", None), "id", None),
                ),
            ).execute(action)
            if not result.ok:
                await update.effective_message.reply_text(
                    "Ban uygulanamadı. Botun bu grupta kullanıcı engelleme yetkisi var mı kontrol et."
                )
                return
            await self._get_data_port().add_audit_log(
                actor_id, "group_ban", target_id=target_id, chat_id=chat.id
            )
            await update.effective_message.reply_text(f"🚫 {target_id} gruptan uzaklaştırıldı.")
            return
        if not self.sudo_manager.is_sudo(actor_id):
            return
        try:
            target_id = int((context.args or [])[0])
        except (IndexError, ValueError):
            await update.effective_message.reply_text("❌ Kullanım: /ban <user_id|chat_id>", parse_mode="HTML")
            return
        if str(target_id).startswith("-"):
            self.sudo_manager.ban_group(target_id)
            target_type = "group"
        else:
            self.sudo_manager.ban_user(target_id)
            target_type = "user"
        await self._get_data_port().add_audit_log(actor_id, "ban", target_id=target_id, target_type=target_type)
        await update.effective_message.reply_text(f"🚫 {target_id} kara listeye alındı.", parse_mode="HTML")

    async def handle_unban_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        actor_id = update.effective_user.id
        chat = getattr(update, "effective_chat", None)
        if chat is not None and getattr(chat, "type", "private") in {"group", "supergroup"}:
            if not await is_admin(
                update,
                context,
                self.sudo_manager,
                authorization=getattr(self, "telegram_authorization", None),
            ):
                await update.effective_message.reply_text(
                    "Bu işlemi yalnızca grup yöneticileri yapabilir."
                )
                return
            target_id = self._moderation_target_id(update, context)
            if target_id is None:
                await update.effective_message.reply_text(
                    "Kullanım: /unban <kullanıcı_id> veya kullanıcının mesajına yanıt ver."
                )
                return
            if target_id <= 0:
                await update.effective_message.reply_text("Geçerli bir kullanıcı hedefi ver.")
                return
            action = ModerationAction(
                action="unban",
                chat_id=int(chat.id),
                actor_id=int(actor_id),
                target_user_id=int(target_id),
                reason="manual_group_unban",
                metadata={
                    "source": "manual_group_unban",
                    "generation_id": f"update:{getattr(update, 'update_id', None) or getattr(getattr(update, 'effective_message', None), 'message_id', 0)}",
                },
            )
            result = await TelegramModerationActionExecutor(
                getattr(context, "bot", None),
                ledger=getattr(self, "moderation_action_ledger", None) or ModerationActionLedger(),
                permission_checker=TelegramModerationAuthorization(
                    getattr(context, "bot", None),
                    bot_id=getattr(getattr(context, "bot", None), "id", None),
                ),
            ).execute(action)
            if not result.ok:
                await update.effective_message.reply_text(
                    "Yasak kaldırılamadı. Botun bu grupta kullanıcı yönetme yetkisini kontrol et."
                )
                return
            await self._get_data_port().add_audit_log(
                actor_id, "group_unban", target_id=target_id, chat_id=chat.id
            )
            await update.effective_message.reply_text(f"✅ {target_id} grubun yasak listesinden çıkarıldı.")
            return
        if not self.sudo_manager.is_sudo(actor_id):
            return
        try:
            target_id = int((context.args or [])[0])
        except (IndexError, ValueError):
            await update.effective_message.reply_text("❌ Kullanım: /unban <user_id|chat_id>", parse_mode="HTML")
            return
        if str(target_id).startswith("-"):
            self.sudo_manager.unban_group(target_id)
            target_type = "group"
        else:
            self.sudo_manager.unban_user(target_id)
            target_type = "user"
        await self._get_data_port().add_audit_log(actor_id, "unban", target_id=target_id, target_type=target_type)
        await update.effective_message.reply_text(f"✅ {target_id} kara listeden çıkarıldı.", parse_mode="HTML")

    async def handle_broadcast_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        actor_id = update.effective_user.id
        if not self.sudo_manager.is_sudo(actor_id):
            return
        if not context.args:
            await update.effective_message.reply_text("❌ Kullanım: /broadcast <mesaj>", parse_mode="HTML")
            return
        broadcast_text = html.escape(" ".join(context.args))
        delivered = 0
        for chat_id in await self.chat_engine.get_active_chat_ids():
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"📢 <b>DUYURU</b>\n\n{broadcast_text}",
                    parse_mode="HTML",
                )
                delivered += 1
                await asyncio.sleep(0.05)
            except Exception:
                logger.warning("Broadcast delivery failed for chat", exc_info=True)
        await self._get_data_port().add_audit_log(actor_id, "broadcast", delivered=delivered)
        await update.effective_message.reply_text(f"✅ Duyuru {delivered} sohbete ulaştırıldı.", parse_mode="HTML")

    async def handle_broadcastpin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Guardian-compatible owner broadcast that also pins the message."""
        actor_id = int(update.effective_user.id)
        if not self.sudo_manager.is_sudo(actor_id):
            return
        if not context.args:
            await update.effective_message.reply_text("Kullan\u0131m: /broadcastpin mesaj", parse_mode="HTML")
            return
        text = html.escape(" ".join(context.args)[:3500])
        delivered = 0
        failed = 0
        for chat_id in await self.chat_engine.get_active_chat_ids():
            try:
                sent = await context.bot.send_message(
                    chat_id=int(chat_id), text="\U0001f4e2 <b>DUYURU</b>\n\n" + text, parse_mode="HTML"
                )
                try:
                    await context.bot.pin_chat_message(chat_id=int(chat_id), message_id=int(sent.message_id), disable_notification=True)
                except Exception:
                    pass
                delivered += 1
            except Exception:
                failed += 1
        await self._get_data_port().add_audit_log(actor_id, "broadcast_pin", delivered=delivered, failed=failed)
        await update.effective_message.reply_text(
            f"Yay\u0131n + sabitleme tamamland\u0131 ? ba\u015far\u0131l\u0131: {delivered} ? ba\u015far\u0131s\u0131z: {failed}"
        )

    async def handle_groups_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.sudo_manager.is_sudo(update.effective_user.id):
            return
        rows = []
        for chat_id in await self.chat_engine.get_active_chat_ids():
            if not str(chat_id).startswith("-"):
                continue
            try:
                chat = await context.bot.get_chat(chat_id)
                title = html.escape(chat.title or "Bilinmeyen grup")
            except Exception:
                title = "Bilinmeyen grup"
            rows.append(f"🔹 {title} (<code>{int(chat_id)}</code>)")
        text = "📭 Henüz kayıtlı grup yok." if not rows else "👥 <b>Kayıtlı Gruplar</b>\n\n" + "\n".join(rows)
        await update.effective_message.reply_text(text, parse_mode="HTML")

    async def handle_sudolist_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.sudo_manager.is_sudo(update.effective_user.id):
            return
        rows = [f"<code>{int(item)}</code>" for item in self.sudo_manager.sudos if item != self.sudo_manager.owner_id]
        text = (
            f"👑 <b>Owner:</b> <code>{int(self.sudo_manager.owner_id)}</code>\n\n"
            "⚔️ <b>Sudolar:</b>\n" + ("\n".join(rows) if rows else "Henüz sudo yok.")
        )
        await update.effective_message.reply_text(text, parse_mode="HTML")

    async def toggle_observation(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id

        if not self.sudo_manager.is_sudo(user_id):
            try:
                member = await context.bot.get_chat_member(chat_id, user_id)
                if member.status not in ['administrator', 'creator']:
                    await update.message.reply_text("❌ Bu komutu sadece adminler kullanabilir.", parse_mode='HTML')
                    return
            except Exception:
                await update.message.reply_text("❌ Bu komutu sadece adminler kullanabilir.", parse_mode='HTML')
                return

        args = context.args
        if not args:
            current_mode = self.chat_engine.get_observation_mode(chat_id)
            status = "AÇIK" if current_mode else "KAPALI"
            await update.message.reply_text(
                f"👁️ Gözlem Modu şu an: <b>{status}</b>\n\nDeğiştirmek için: `/obs on` veya `/obs off`",
                parse_mode='HTML'
            )
            return

        mode_str = args[0].lower()
        if mode_str == "on":
            self.chat_engine.set_observation_mode(chat_id, True)
            await update.message.reply_text(
                "✅ <b>Gözlem Modu AÇILDI.</b> Arel artık bu gruptaki tüm konuşmaları detaylıca takip edecek. 🧠",
                parse_mode='HTML'
            )
        elif mode_str == "off":
            self.chat_engine.set_observation_mode(chat_id, False)
            await update.message.reply_text(
                "❌ <b>Gözlem Modu KAPATILDI.</b> Arel artık sadece kendisine sorulanlara cevap verecek.",
                parse_mode='HTML'
            )
        else:
            await update.message.reply_text("❌ Kullanım: `/obs on` veya `/obs off`", parse_mode='HTML')
