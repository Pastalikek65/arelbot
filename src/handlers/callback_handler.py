"""
Callback Handler - Tüm inline button callback işlemleri

Oyun menüsü, help menüsü, sticker işlemleri callbackleri.
"""
import logging
import html
import math
import random
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InlineQueryResultCachedSticker, InputMediaPhoto, InputMediaVideo
from telegram.ext import ContextTypes
from .handler_utils import is_admin, is_group_admin, remove_job_by_name
from src.games.state_store import hydrate_games, persist_game, delete_game
from src.games.action_ledger import GameActionLedger
from src.games.engine import calculate_hangman_score
from src.games.hangman import apply_guess, reveal_hint
from src.security.output_sanitizer import sanitize_html
from src.services.feature_presentation import format_feature_result
from src.security.error_codes import normalize_error_code, public_error_message
from src.security.log_redaction import summarize_exception
from src.services.subscription_service import SubscriptionRequest
from src.services.weather_ui import (
    air_quality_actions,
    air_quality_group_frequencies,
    air_quality_group_times,
    air_quality_intervals,
    earthquake_actions,
    earthquake_group_frequencies,
    earthquake_group_times,
    earthquake_intervals,
    format_interval,
    weather_actions,
    weather_group_frequencies,
    weather_group_times,
    weather_intervals,
    news_group_frequencies,
    news_group_times,
)
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
from src.moderation.guardian_enforcement import validate_captcha_challenge
from src.services.culture_ui import culture_category_help, culture_category_panel
from src.services.bim_ui import bim_page_view
from src.ai.personality import PersonalityPolicy
from src.ai.conversation_style import NORMAL_MODE, is_normal_mode, style_mode_label
from src.storage.handler_data_port import HandlerDataPort
from src.features.chat_feature_policy import ChatFeaturePolicy, is_chat_feature_enabled
from src.storage.state_scope import get_scoped_state, pop_scoped_state, set_scoped_state
from src.services.hangman_ui import hangman_keyboard, render_hangman_text
from src.games.word_game import shuffle_rack, reveal_word_hint
from src.services.word_game_ui import (
    connect4_drop,
    connect4_join,
    connect4_keyboard,
    connect4_start,
    render_connect4_text,
    word_game_keyboard,
    render_word_game_text,
)
from src.services.word_guess_ui import word_guess_keyboard, render_word_guess_text
from src.services.tictactoe_ui import (
    render_tictactoe_text,
    tictactoe_join,
    tictactoe_move,
    tictactoe_new_round,
    tictactoe_select_size,
    tictactoe_start,
)
from src.services.gomoku_ui import (
    SUPPORTED_BOARD_SIZES as GOMOKU_BOARD_SIZES,
    gomoku_join,
    gomoku_keyboard,
    gomoku_move,
    gomoku_select_size,
    gomoku_start,
    render_gomoku_text,
)
from src.services.menu_service import XOX_BOARD_SIZES, tictactoe_surface_keyboard

logger = logging.getLogger(__name__)


class CallbackHandler:
    NOTIFICATION_CITY_PAGE_SIZE = 8

    def __init__(self, chat_engine, sudo_manager, tool_manager, menu_service=None, leaderboard_service=None, economy_service=None, subscription_service=None, notification_preference_store=None, productivity_service=None, announcement_service=None, sticker_pack_service=None, game_action_ledger=None, blackjack_service=None, batak_service=None, roulette_service=None, data_port=None, authorization=None):
        self.chat_engine = chat_engine
        self.data_port = data_port or HandlerDataPort.from_engine(chat_engine)
        self.sudo_manager = sudo_manager
        self.tool_manager = tool_manager
        self.menu_service = menu_service
        self.leaderboard_service = leaderboard_service
        self.economy_service = economy_service
        self.announcement_service = announcement_service
        self.subscription_service = subscription_service
        self.notification_preference_store = notification_preference_store
        self.productivity_service = productivity_service
        self.sticker_pack_service = sticker_pack_service
        self.game_action_ledger = game_action_ledger or GameActionLedger(self.data_port)
        self.blackjack_service = blackjack_service
        self.batak_service = batak_service
        self.roulette_service = roulette_service
        # Injected by Orchestrator after Telegram Application creation.
        self.authorization = authorization
        self.message_handler = None
        self.media_handler = None

    async def _get_language(self, user_id: int) -> str:
        try:
            user = await self.data_port.get_user(user_id)
            return (user or {}).get("language", "tr")
        except Exception:
            return "tr"

    @staticmethod
    async def _safe_query_answer(query, *args, **kwargs) -> None:
        """A callback may already have been acknowledged by the router."""

        try:
            await query.answer(*args, **kwargs)
        except Exception:
            # Telegram rejects a second answer for an already acknowledged
            # callback. The state change itself remains valid.
            return

    async def _handle_guardian_captcha_callback(self, update: Update, query, data: str) -> None:
        """Consume a join challenge only for the exact Telegram user/chat."""
        parts = str(data).split(":")
        if len(parts) != 4:
            await self._safe_query_answer(query, "Geçersiz doğrulama", show_alert=True)
            return
        try:
            chat_id, target_user_id, nonce = int(parts[1]), int(parts[2]), str(parts[3])
        except (TypeError, ValueError):
            await self._safe_query_answer(query, "Geçersiz doğrulama", show_alert=True)
            return
        if int(getattr(update.effective_chat, "id", 0) or 0) != chat_id or int(update.effective_user.id) != target_user_id:
            await self._safe_query_answer(query, "Bu düğme sana ait değil", show_alert=True)
            return
        settings = await self.data_port.get_chat_settings(chat_id) or {}
        pending = dict(settings.get("guardian_pending_captcha") or {})
        challenge = pending.get(str(target_user_id))
        if not isinstance(challenge, dict) or str(challenge.get("nonce") or "") != nonce:
            await self._safe_query_answer(query, "Doğrulama zaten tamamlanmış veya süresi dolmuş", show_alert=True)
            return
        validation = validate_captcha_challenge(
            challenge,
            chat_id=chat_id,
            user_id=target_user_id,
            timeout_seconds=int(settings.get("captcha_timeout_seconds", 300) or 300),
            require_created_at=bool(challenge.get("created_at")),
        )
        if not validation.valid:
            pending.pop(str(target_user_id), None)
            await self.data_port.update_chat_setting_key(chat_id, "guardian_pending_captcha", pending)
            await self._safe_query_answer(query, "Doğrulama süresi dolmuş", show_alert=True)
            return
        pending.pop(str(target_user_id), None)
        await self.data_port.update_chat_setting_key(chat_id, "guardian_pending_captcha", pending)
        bot = None
        try:
            bot = update.get_bot()
        except Exception:
            bot = getattr(query, "bot", None)
        if bot is not None:
            from src.moderation.telegram_guardian_service import GuardianTelegramService
            await GuardianTelegramService().clear_captcha_quarantine(
                bot=bot, chat_id=chat_id, user_id=target_user_id
            )
        await self._safe_query_answer(query, "Doğrulama tamamlandı")
        try:
            await query.edit_message_text("✅ Doğrulama tamamlandı; hoş geldin")
        except Exception:
            pass

    @staticmethod
    def _game_action_id(query, user_id: int, data: str) -> str:
        """Build a stable logical action id across Telegram callback retries."""
        message = getattr(query, "message", None)
        message_id = getattr(message, "message_id", 0) or 0
        return f"{int(message_id)}:{int(user_id)}:{str(data)[:120]}"

    async def _claim_game_action(self, update, query, game_type: str, data: str) -> tuple[bool, str]:
        action_id = self._game_action_id(query, update.effective_user.id, data)
        claimed = await self.game_action_ledger.claim(
            update.effective_chat.id,
            game_type,
            action_id,
            user_id=update.effective_user.id,
            payload={"callback": str(data)[:120]},
        )
        if not claimed:
            try:
                await query.answer("Bu oyun işlemi zaten işlendi.", show_alert=True)
            except Exception:
                # The dispatcher may already have acknowledged this callback;
                # duplicate feedback must never break the callback chain.
                logger.debug("Duplicate game callback was already acknowledged", exc_info=True)
        return claimed, action_id

    async def handle_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = update.effective_user.id

        try:
            await query.answer()
        except Exception:
            pass

        data = query.data
        if not data:
            return
        if data.startswith("guardian_captcha:"):
            await self._handle_guardian_captcha_callback(update, query, data)
            return
        if data == "help_cat_guardian" or data.startswith("guardian_help:"):
            await self._show_guardian_help(query, data)
            return
        if data.startswith("photo_action:"):
            await self._handle_photo_action_callback(update, query, context, data)
            return
        if data.startswith("persona_user:"):
            await self._handle_persona_user_callback(update, query, data)
            return
        if data == "settings_persona" and self.menu_service:
            text, markup = self.menu_service.persona_menu()
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
            return
        if data == "settings_memory":
            await query.edit_message_text(
                "🧠 <b>Hafıza tercihleri</b>\n\nHafıza varsayılan olarak kapalıdır. <code>/memory on</code>, <code>/memory off</code>, <code>/memory show</code> ve <code>/memory clear</code> ile yönetebilirsin.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Ayarlara dön", callback_data="open_ayar_menu"), InlineKeyboardButton("🏠 Ana menü", callback_data="menu_main")]]),
                parse_mode="HTML",
            )
            return
        if data in {"settings_quote_toggle", "settings_media_toggle"}:
            await self._handle_ayar_callback(update, query, context, data)
            return
        if data.startswith("media_intent:"):
            action = data.split(":", 1)[1]
            if action == "cancel":
                pop_scoped_state(self.user_states, user_id=user_id, chat_id=update.effective_chat.id)
                await query.edit_message_text("Görsel işlemi iptal edildi.")
                return
            if action not in {"vision", "ocr", "sticker"}:
                await query.answer("Geçersiz görsel işlemi.", show_alert=True)
                return
            set_scoped_state(
                self.user_states,
                user_id=user_id,
                chat_id=update.effective_chat.id,
                value={"state": "awaiting_intent_photo", "action": action},
            )
            await query.edit_message_text(
                "Tamam. Şimdi fotoğrafı gönder; başka bir şeye atlamadan seçtiğin işlemi uygulayacağım.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("İptal", callback_data="media_intent:cancel")]]),
            )
            return
        if data.startswith("stpack:"):
            await self._handle_sticker_pack_callback(update, query, context, data)
            return
        if data.startswith("sticker_packs_refresh:"):
            await self._handle_sticker_packs_refresh(update, query, data)
            return
        if data.startswith("bim_page:"):
            await self._handle_bim_page_callback(update, query, data)
            return
        if data.startswith("culture_menu:") or data.startswith("culture:"):
            await self._handle_culture_callback(update, query, data)
            return
        await hydrate_games(self.data_port, update.effective_chat.id, context.chat_data)

        if data == "menu_main" and self.menu_service:
            text, markup = self.menu_service.main_menu(
                await self._get_language(user_id),
                user_id=user_id,
                chat_type=getattr(update.effective_chat, "type", None),
            )
            await query.edit_message_text(text, reply_markup=markup, parse_mode='HTML')
            return

        if data == "menu_turkiye" and self.menu_service:
            text, markup = self.menu_service.turkiye_menu(await self._get_language(user_id), user_id=user_id)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
            return

        if data == "menu_plan" and self.menu_service:
            text, markup = self.menu_service.plan_menu()
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
            return

        if data == "menu_plan":
            await query.edit_message_text(
                "📋 <b>Planım</b>\n\n"
                "<code>/plan</code> bugününü, <code>/plan hafta</code> haftanı gösterir.\n"
                "Yeni not için <code>/not</code>, görev için <code>/gorev</code>, hatırlatıcı için <code>/hatirlat</code> yaz.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Ana Menü", callback_data="menu_main")]]),
                parse_mode="HTML",
            )
            return

        if data in {"city_help", "turkiye_pharmacy_help", "turkiye_fuel_help", "turkiye_finance_help"}:
            hints = {
                "city_help": "🏙 <b>Şehir rehberi</b>\n\n<code>/sehir İstanbul</code>\n<code>/plaka 34</code>\n<code>/telefon 212</code>\n<code>/ilce Kadıköy</code>",
                "turkiye_pharmacy_help": "💊 <b>Nöbetçi eczane</b>\n\n<code>/eczane İstanbul Beşiktaş</code>",
                "turkiye_fuel_help": "⛽ <b>Akaryakıt</b>\n\n<code>/akaryakit İstanbul</code>",
                "turkiye_finance_help": "💱 <b>Döviz / altın</b>\n\n<code>/doviz</code> · <code>/altin</code>",
            }
            await query.edit_message_text(
                hints[data],
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🌍 Türkiye menüsüne dön", callback_data="menu_turkiye")]]),
                parse_mode="HTML",
            )
            return

        if data.startswith("daily_brief:"):
            await self._handle_daily_brief_callback(update, query, context, data)
            return

        if data == "menu_games" and self.menu_service:
            text, markup = self.menu_service.games_menu(await self._get_language(user_id))
            await query.edit_message_text(text, reply_markup=markup, parse_mode='HTML')
            return

        if data == "game_word_categories" and self.menu_service:
            text, markup = self.menu_service.word_game_categories_menu()
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
            return

        if data == "menu_language" and self.menu_service:
            text, markup = self.menu_service.language_menu()
            await query.edit_message_text(text, reply_markup=markup, parse_mode='HTML')
            return

        if data in {"lang_tr", "lang_en"}:
            language = data.replace("lang_", "")
            await self.data_port.update_user(user_id, {"language": language})
            text, markup = self.menu_service.main_menu(language, user_id=user_id, chat_type=getattr(update.effective_chat, "type", None)) if self.menu_service else ("✅ Dil güncellendi.", None)
            await query.edit_message_text(text, reply_markup=markup, parse_mode='HTML')
            return

        if data == "menu_leaderboard" and self.leaderboard_service:
            text = await self.leaderboard_service.render_leaderboard(update.effective_chat.id)
            keyboard = [[InlineKeyboardButton("🏠 Ana Menü", callback_data="menu_main")]]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
            return

        if data == "menu_media" and self.menu_service:
            text, markup = self.menu_service.media_menu()
            await query.edit_message_text(text, reply_markup=markup, parse_mode='HTML')
            return

        if data == "menu_news" and self.menu_service:
            text, markup = self.menu_service.news_menu(user_id=user_id, chat_type=getattr(update.effective_chat, "type", None))
            await query.edit_message_text(text, reply_markup=markup, parse_mode='HTML')
            return

        if data == "menu_ai" and self.menu_service:
            text, markup = self.menu_service.ai_menu()
            await query.edit_message_text(text, reply_markup=markup, parse_mode='HTML')
            return

        menu_hints = {
            "media_photo_help": ("🔎 <b>Görsel analizi</b>\n\nBir fotoğraf gönder, sonra bu menüden ne istediğini seç. Arel kendi kendine her görsele atlamaz.", "menu_media"),
            "media_ocr_help": ("📝 <b>OCR</b>\n\nFotoğrafı gönderip bu seçeneği kullan; içindeki metni çıkarmaya çalışırım.", "menu_media"),
            "media_image_help": ("🖼 <b>Görsel üret</b>\n\n<code>/image neon ışıklı İstanbul gecesi</code> gibi bir açıklama gönder.", "menu_media"),
            "media_tts_help": ("🔊 <b>Metni seslendir</b>\n\n<code>/oku bugün kısa bir özet</code> yaz; ses dosyası olarak döneyim.", "menu_media"),
            "news_latest_help": ("🗞 <b>Son haberler</b>\n\n<code>/haber teknoloji</code> veya <code>/haber son dakika</code> yaz. Kaynak ve zamanı birlikte gösteririm.", "menu_news"),
            "news_digest_help": ("🧠 <b>Haber özeti</b>\n\n<code>/digest</code> yaz; uygun kaynakları kısa bir özet halinde toparlarım.", "menu_news"),
            "ai_chat_help": ("💬 <b>Sohbet</b>\n\nDirekt yaz. Güncel bilgi gerekiyorsa web aramasını kendim devreye sokarım; modeli sana törenle tanıtmam.", "menu_ai"),
            "ai_web_help": ("🌐 <b>Web araması</b>\n\n<code>/ara KPSS ortaöğretim sınav tarihi</code> yazabilir veya soruyu normal cümleyle sorabilirsin.", "menu_ai"),
            "ai_document_help": ("📄 <b>Belge analizi</b>\n\nPDF, DOCX veya TXT gönder; sonra özet, soru-cevap ya da kaynaklı açıklama iste.", "menu_ai"),
        }
        if data in menu_hints:
            text, back = menu_hints[data]
            await query.edit_message_text(
                text,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Geri dön", callback_data=back), InlineKeyboardButton("🏠 Ana menü", callback_data="menu_main")]]),
                parse_mode="HTML",
            )
            return

        if data in {"plan_today", "plan_week", "plan_notes", "plan_tasks"}:
            instructions = {
                "plan_today": "📅 <b>Bugünün planı</b>\n\n<code>/plan</code> komutuyla günün hatırlatıcı ve görevlerini açabilirsin.",
                "plan_week": "🗓 <b>Haftalık plan</b>\n\n<code>/plan hafta</code> komutuyla önündeki 7 günü görürsün.",
                "plan_notes": "📝 <b>Notlar</b>\n\n<code>/not metin</code> ile not ekle; <code>/notlar</code> ile yönet.",
                "plan_tasks": "✅ <b>Görevler</b>\n\n<code>/gorev metin</code> ile görev ekle; <code>/gorevler</code> ile butonlardan tamamla.",
            }
            await query.edit_message_text(
                instructions[data],
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📋 Planıma dön", callback_data="menu_plan"), InlineKeyboardButton("🏠 Ana menü", callback_data="menu_main")]]),
                parse_mode="HTML",
            )
            return

        if data == "menu_reminders":
            text = "🔔 <b>HATIRLATICI</b>\n\n<code>/remind 10dk Su iç</code>\n<code>/remind yarın 10:00 Toplantı</code>\n<code>/remind her gün 09:00 Günlük plan</code>"
            keyboard = [[InlineKeyboardButton("🏠 Ana Menü", callback_data="menu_main")]]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
            return

        if data == "weather_help":
            await query.edit_message_text(
                "🌤 <b>Hava durumu</b>\n\nBir şehri seç veya <code>/hava ist</code> yaz. Sonuç ekranından günlük, haftalık ya da saatlik bildirim kurabilirsin.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("İstanbul", callback_data=f"weather_show:{user_id}:34"), InlineKeyboardButton("Ankara", callback_data=f"weather_show:{user_id}:6")],
                    [InlineKeyboardButton("İzmir", callback_data=f"weather_show:{user_id}:35"), InlineKeyboardButton("Antalya", callback_data=f"weather_show:{user_id}:7")],
                    [InlineKeyboardButton("🔔 Bildirim merkezi", callback_data=f"notify_center:{user_id}"), InlineKeyboardButton("🏠 Ana menü", callback_data="menu_main")],
                ]),
                parse_mode="HTML",
            )
            return

        if data == "notification_noop":
            await query.answer("Bu sayfa göstergesi.")
            return

        if data.startswith("notify_") and not data.startswith("notify_pref:"):
            await self._handle_notification_navigation(update, query, context, data)
            return

        if data.startswith("notify_pref:"):
            await self._handle_notification_preference_callback(update, query, data)
            return

        if data.startswith("alert_stop:"):
            parts = data.split(":", 2)
            if len(parts) != 3 or str(update.effective_user.id) != parts[1]:
                await query.answer("Bu alarm başka bir kullanıcıya ait.", show_alert=True)
                return
            if not self.subscription_service:
                await query.answer("Alarm servisi hazır değil.", show_alert=True)
                return
            stopped = await self.subscription_service.store.disable(update.effective_user.id, parts[2])
            await query.answer("Alarm kapatıldı." if stopped else "Alarm zaten kapalı.", show_alert=not stopped)
            if stopped:
                await query.edit_message_text("✅ Fiyat alarmı kapatıldı.", parse_mode="HTML")
            return

        if data.startswith("announce_stop:"):
            await self._handle_announcement_stop_callback(update, query, context, data)
            return

        if data.startswith("prod:"):
            await self._handle_productivity_callback(update, query, data)
            return

        if data.startswith("doc:"):
            parts = data.split(":", 3)
            if len(parts) != 4 or parts[1] not in {"confirm", "cancel"}:
                await query.answer("Belge işlemi geçersiz.", show_alert=True)
                return
            if str(update.effective_user.id) != parts[2] or update.effective_chat.type != "private":
                await query.answer("Bu belge işlemi yalnızca belge sahibine aittir.", show_alert=True)
                return
            if parts[1] == "cancel":
                await query.edit_message_text("Belge silme işlemi iptal edildi.")
                return
            rag = getattr(self.chat_engine, "rag_engine", None)
            if rag is None:
                await query.answer("Belge hafızası hazır değil.", show_alert=True)
                return
            deleted = await rag.delete_document(
                update.effective_user.id,
                parts[3],
                actor_id=update.effective_user.id,
            )
            if deleted:
                await query.edit_message_text(f"✅ Belge hafızadan silindi. Silinen parça: {deleted}")
            else:
                await query.edit_message_text("Belge bulunamadı veya zaten silinmiş.")
            return

        if data == "weather_admin_only":
            await query.answer("Bu grupta bildirim ayarlarını yalnızca yöneticiler değiştirebilir.", show_alert=True)
            return

        if data == "earthquake_admin_only":
            await query.answer("Bu grupta deprem bildirimlerini yalnızca yöneticiler yönetebilir.", show_alert=True)
            return

        if data.startswith("air_quality:"):
            await self._handle_air_quality_callback(update, query, context, data)
            return

        if data.startswith("weather_"):
            await self._handle_weather_callback(update, query, context, data)
            return

        if data.startswith("earthquake_"):
            await self._handle_earthquake_callback(update, query, context, data)
            return

        legacy_map = {
            "help_download": "help_cat_download",
            "help_ai": "help_cat_ai",
            "help_games": "help_cat_games",
            "help_quote": "help_cat_quote",
            "help_sticker": "help_cat_sticker",
            "help_finance": "help_cat_finance",
            "help_tools": "help_cat_tools",
            "help_suggestions": "help_cat_suggestions"
        }
        if data in legacy_map:
            data = legacy_map[data]

        if data == "help_main":
            await self._show_help_main(query)
            return

        if data.startswith("help_cat_"):
            await self._show_help_category(query, data)
            return

        if data.startswith("help_feat_"):
            await self._show_help_feature(query, data)
            return

        if data == "help_suggestions":
            await self._show_suggestions(query)
            return

        if data == "word_hint" or data.startswith("word_hint:"):
            await self._handle_word_guess_hint(update, query, context, data)
            return

        if data.startswith("bj:"):
            await self._handle_blackjack_callback(update, query, context, data)
            return

        if data.startswith("batak:"):
            await self._handle_batak_round_callback(update, query, context, data)
            return

        if data.startswith("connect4:"):
            await self._handle_connect4_callback(update, query, context, data)
            return

        if data.startswith("tictactoe:"):
            await self._handle_tictactoe_callback(update, query, context, data)
            return

        if data.startswith("gomoku:"):
            await self._handle_gomoku_callback(update, query, context, data)
            return

        if data.startswith("hangman_guess:"):
            await self._handle_hangman_guess_callback(update, query, context, data)
            return

        if data == "hangman_hint" or data.startswith("hangman_hint:"):
            await self._handle_hangman_hint_callback(update, query, context, data)
            return

        if data == "hangman_stop" or data.startswith("hangman_stop:"):
            await self._handle_hangman_stop_callback(update, query, context, data)
            return

        if data in {"word_game_shuffle", "word_game_hint"} or data.startswith("word_game_shuffle:") or data.startswith("word_game_hint:"):
            await self._handle_word_game_control(update, query, context, data)
            return

        if data == "word_game_stop" or data.startswith("word_game_stop:"):
            await self._handle_word_game_stop(update, query, context, data)
            return

        if data.startswith("game_batak_"):
            await self._handle_batak_callback(update, query, context, data)
            return

        if data.startswith("game_bottle"):
            await self._handle_bottle_callback(update, query, context, data)
            return

        if data.startswith("game_start_"):
            await self._handle_game_start_callback(update, query, context, data)
            return

        if data == "game_word_difficulty" and self.menu_service:
            text, markup = self.menu_service.word_game_difficulty_menu()
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
            await query.answer()
            return

        if data == "game_hangman_difficulty" and self.menu_service:
            text, markup = self.menu_service.hangman_difficulty_menu()
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
            await query.answer()
            return

        if data == "game_stop_all":
            is_group = str(getattr(update.effective_chat, "type", "")) in {
                "group", "supergroup"
            }
            if is_group and not await is_admin(
                update, context, self.sudo_manager, authorization=self.authorization
            ):
                await self._safe_query_answer(
                    query,
                    "Gruptaki tüm oyunları yalnızca grup yöneticisi kapatabilir.",
                    show_alert=True,
                )
                return
            await self._handle_game_stop(update, query, context)
            return

        if data.startswith("game_word_category:"):
            await self._handle_game_start_callback(update, query, context, data)
            return

        if data.startswith("game_word_difficulty:"):
            await self._handle_game_start_callback(update, query, context, data)
            return

        if data.startswith("game_hangman_difficulty:"):
            await self._handle_game_start_callback(update, query, context, data)
            return

        if data in {"game_word_skip", "word_skip"} or data.startswith("game_word_skip:") or data.startswith("word_skip:"):
            await self._handle_word_skip(update, query, context, data)
            return

        if data == "word_guess_stop" or data.startswith("word_guess_stop:"):
            await self._handle_word_guess_stop(update, query, context, data)
            return

        if data == "game_roulette_spin":
            await self._handle_roulette_callback(update, query, context, "roulette:pull")
            return

        if data.startswith("roulette:"):
            await self._handle_roulette_callback(update, query, context, data)
            return

        if data.startswith("quiz_cat_"):
            await self._handle_quiz_category(update, query, context, data)
            return

        if data.startswith("quiz_ans_") or data.startswith("quiz_ans:"):
            await self._handle_quiz_answer(update, query, context, data)
            return

        if data.startswith("stk_"):
            await self._handle_sticker_callback(update, query, context, data)
            return

        if data == "open_ayar_menu":
            await self._handle_ayar_menu(update, query, context)
            return

        if data.startswith("ayar_"):
            await self._handle_ayar_callback(update, query, context, data)
            return

        if data.startswith("qtop_"):
            await self._handle_qtop_callback(update, query, context, data)
            return

        if data.startswith("qrate_"):
            await self._handle_qrate_callback(update, query, context, data)
            return

        if data.startswith("tts_dl_"):
            await self._handle_tts_download(query, context, data)
            return

        if data == "catalog_refresh":
            await self._handle_catalog_refresh(update, query, context)
            return

    async def _handle_photo_action_callback(self, update, query, context, data: str):
        """Consume an owner-scoped photo choice and enqueue the selected job."""
        parts = data.split(":")
        if len(parts) != 3:
            await query.answer("Geçersiz görsel işlemi.", show_alert=True)
            return
        try:
            owner_id = int(parts[1])
        except ValueError:
            await query.answer("Geçersiz görsel sahibi.", show_alert=True)
            return
        if owner_id != update.effective_user.id:
            await query.answer("Bu görsel işlemi başka bir kullanıcıya ait.", show_alert=True)
            return
        state = get_scoped_state(
            getattr(self, "user_states", None),
            user_id=owner_id,
            chat_id=update.effective_chat.id,
        )
        if not state or state.get("state") != "awaiting_photo_action":
            await query.answer("Bu görsel seçiminin süresi dolmuş.", show_alert=True)
            return
        try:
            created_at = datetime.fromisoformat(str(state.get("created_at")))
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - created_at > timedelta(minutes=15):
                pop_scoped_state(self.user_states, user_id=owner_id, chat_id=update.effective_chat.id)
                await query.answer("Bu görsel seçiminin süresi dolmuş.", show_alert=True)
                return
        except (TypeError, ValueError):
            pass
        action = parts[2].casefold()
        if action == "cancel":
            pop_scoped_state(self.user_states, user_id=owner_id, chat_id=update.effective_chat.id)
            await query.edit_message_text("✅ Görsel işlemi iptal edildi.")
            return
        if action not in {"vision", "ocr", "sticker"} or self.media_handler is None:
            await query.answer("Görsel işleme servisi hazır değil.", show_alert=True)
            return
        try:
            job_id, duplicate = await self.media_handler.enqueue_photo_action(
                chat_id=update.effective_chat.id,
                user_id=owner_id,
                status_message_id=getattr(query.message, "message_id", 0),
                state=state,
                action=action,
            )
        except ValueError:
            await query.answer("Geçersiz görsel işlemi.", show_alert=True)
            return
        except Exception as exc:
            logger.warning("Photo action enqueue failed: %s", summarize_exception(exc, label="photo_action_enqueue"))
            await query.edit_message_text("❌ Görsel kuyruğa alınamadı; biraz sonra tekrar dene.")
            return
        pop_scoped_state(self.user_states, user_id=owner_id, chat_id=update.effective_chat.id)
        label = {"vision": "görsel analizi", "ocr": "metin okuma", "sticker": "sticker üretimi"}[action]
        status = "Bu görsel için aktif iş zaten var" if duplicate else f"{label.capitalize()} kuyruğa alındı"
        await query.edit_message_text(f"⏳ {status}.\nİş: <code>{html.escape(str(job_id))}</code>", parse_mode="HTML")

    async def _handle_daily_brief_callback(self, update, query, context, data: str):
        parts = data.split(":")
        if len(parts) < 2:
            await query.answer("Geçersiz günlük özet menüsü.", show_alert=True)
            return
        try:
            owner_id = int(parts[1]) if len(parts) == 2 else int(parts[2])
        except ValueError:
            await query.answer("Geçersiz günlük özet sahibi.", show_alert=True)
            return
        if update.effective_chat.type in {"group", "supergroup"}:
            text, markup = group_center(
                update.effective_user.id,
                is_admin=await self._is_group_admin(update, context),
            )
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
            return
        if owner_id != update.effective_user.id or update.effective_chat.type != "private":
            await query.answer("Günlük özet ayarlarını yalnızca özel sohbetinde değiştirebilirsin.", show_alert=True)
            return
        if self.notification_preference_store is None or self.menu_service is None:
            await query.answer("Günlük özet servisi hazır değil.", show_alert=True)
            return
        action = parts[0] if len(parts) == 2 else parts[1]
        preferences = await self.notification_preference_store.get(owner_id)
        try:
            if action == "daily_brief" and len(parts) == 2:
                pass
            elif action == "toggle":
                preferences = await self.notification_preference_store.update(
                    owner_id, daily_brief_enabled=not bool(preferences.get("daily_brief_enabled"))
                )
            elif action == "time" and len(parts) == 5:
                preferences = await self.notification_preference_store.update(owner_id, daily_brief_time=f"{parts[3]}:{parts[4]}")
            elif action == "city" and len(parts) == 4:
                cities = {"istanbul": "İstanbul", "ankara": "Ankara", "izmir": "İzmir", "antalya": "Antalya"}
                if parts[3] not in cities:
                    raise ValueError("Şehir adı geçersiz.")
                preferences = await self.notification_preference_store.update(owner_id, daily_brief_city=cities[parts[3]])
            else:
                await query.answer("Geçersiz günlük özet seçimi.", show_alert=True)
                return
        except ValueError as exc:
            await query.answer(str(exc), show_alert=True)
            return
        text, markup = self.menu_service.daily_brief_menu(owner_id, preferences)
        await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")

    async def _handle_sticker_pack_callback(self, update, query, context, data: str):
        """Handle owner-scoped pack and individual-sticker confirmations."""
        parts = data.split(":", 3)
        if len(parts) != 4 or parts[1] not in {"delete", "cancel", "sticker_delete", "cancel_sticker"}:
            await query.answer("Geçersiz sticker paket işlemi.", show_alert=True)
            return
        try:
            owner_id = int(parts[2])
        except ValueError:
            await query.answer("Geçersiz sticker paket işlemi.", show_alert=True)
            return
        if owner_id != update.effective_user.id:
            await query.answer("Bu onay başka bir kullanıcıya ait.", show_alert=True)
            return
        state_store = getattr(self, "user_states", None)
        if parts[1] in {"cancel_sticker", "sticker_delete"}:
            state = (
                get_scoped_state(
                    state_store,
                    user_id=owner_id,
                    chat_id=update.effective_chat.id,
                )
                if state_store is not None
                else None
            )
            state_data = (state or {}).get("data") or {}
            if (
                not state
                or state.get("state") != "awaiting_sticker_delete_confirmation"
                or str(state_data.get("token")) != parts[3]
            ):
                await query.answer("Bu sticker silme onayının süresi dolmuş.", show_alert=True)
                return
            if parts[1] == "cancel_sticker":
                pop_scoped_state(
                    state_store,
                    user_id=owner_id,
                    chat_id=update.effective_chat.id,
                )
                await query.edit_message_text("İşlem iptal edildi.")
                return
            if self.sticker_pack_service is None:
                await query.answer("Sticker paket servisi hazır değil.", show_alert=True)
                return
            try:
                deleted = await self.sticker_pack_service.delete_sticker(
                    context.bot,
                    owner_id,
                    str(state_data.get("pack_name") or ""),
                    str(state_data.get("file_id") or ""),
                )
            except PermissionError:
                await query.answer("Bu sticker paketi sana ait değil veya artık mevcut değil.", show_alert=True)
                return
            except ValueError:
                await query.answer("Sticker dosyası geçersiz.", show_alert=True)
                return
            except Exception as exc:
                logger.error("Sticker delete callback failed: %s", summarize_exception(exc, label="sticker_delete"))
                await query.answer("Sticker silinemedi.", show_alert=True)
                return
            pop_scoped_state(
                state_store,
                user_id=owner_id,
                chat_id=update.effective_chat.id,
            )
            if deleted:
                await query.edit_message_text("Sticker paketten silindi.", parse_mode="HTML")
            else:
                await query.answer("Sticker zaten silinmiş olabilir.", show_alert=True)
            return

        if parts[1] == "cancel":
            await query.edit_message_text("İşlem iptal edildi.")
            return
        if self.sticker_pack_service is None:
            await query.answer("Sticker paket servisi hazır değil.", show_alert=True)
            return
        try:
            deleted = await self.sticker_pack_service.delete(context.bot, owner_id, parts[3])
        except PermissionError:
            await query.answer("Bu paket sana ait değil veya artık mevcut değil.", show_alert=True)
            return
        except ValueError:
            await query.answer("Geçersiz paket adı.", show_alert=True)
            return
        except Exception as exc:
            logger.error("Sticker pack delete callback failed: %s", summarize_exception(exc, label="sticker_pack_delete"))
            await query.answer("Paket silinemedi.", show_alert=True)
            return
        if deleted:
            await query.edit_message_text("Sticker paketi silindi.", parse_mode="HTML")
        else:
            await query.answer("Paket zaten silinmiş olabilir.", show_alert=True)

    async def _handle_announcement_stop_callback(self, update, query, context, data: str):
        parts = data.split(":", 2)
        if len(parts) != 3 or update.effective_chat.type not in {"group", "supergroup"}:
            await query.answer("Bu duyuru yalnızca grup içinde yönetilebilir.", show_alert=True)
            return
        if not await self._is_group_admin(update, context):
            await query.answer("Duyuruları yalnızca grup yöneticileri yönetebilir.", show_alert=True)
            return
        if not self.announcement_service:
            await query.answer("Duyuru servisi hazır değil.", show_alert=True)
            return
        stopped = await self.announcement_service.disable(update.effective_chat.id, parts[2])
        await query.answer("Duyuru durduruldu." if stopped else "Duyuru zaten kapalı.", show_alert=not stopped)
        if stopped:
            await query.edit_message_text("✅ Grup duyurusu durduruldu.", parse_mode="HTML")

    async def _show_notification_city_page(
        self,
        update,
        query,
        owner_id: int,
        feature: str,
        page: int,
        *,
        group: bool,
    ) -> None:
        """Render the bounded 81-province selector used by notification flows."""
        if feature not in {"weather", "air_quality"}:
            await query.answer("Geçersiz bildirim kategorisi.", show_alert=True)
            return
        cities = tuple(self.chat_engine.city_directory.all())
        page_size = self.NOTIFICATION_CITY_PAGE_SIZE
        page_count = max(1, math.ceil(len(cities) / page_size))
        if page < 0 or page >= page_count:
            await query.answer("Geçersiz şehir sayfası.", show_alert=True)
            return
        current = cities[page * page_size : (page + 1) * page_size]
        if group:
            pick_prefix = "notify_group_weather_pick" if feature == "weather" else "notify_group_air_quality_pick"
            page_prefix = "notify_group_weather_page" if feature == "weather" else "notify_group_air_quality_page"
            back = f"notify_center:{owner_id}"
            title = (
                "🌤 <b>Grup hava bildirimi</b>\n\nŞehir seç; frekans ve saati bir sonraki ekranda belirle."
                if feature == "weather"
                else "🌿 <b>Grup hava kalitesi</b>\n\nŞehir seç; frekans ve saati sonraki ekranda belirle."
            )
        else:
            pick_prefix = "notify_weather_pick" if feature == "weather" else "notify_air_quality_pick"
            page_prefix = "notify_weather_page" if feature == "weather" else "notify_air_quality_page"
            back = f"notify_center:{owner_id}"
            title = (
                "🌤 <b>Hava bildirimi</b>\n\nŞehir seç; sonraki ekranda günlük, haftalık veya saatlik planı kurarsın."
                if feature == "weather"
                else "🌿 <b>Hava kalitesi bildirimi</b>\n\nŞehir seç; sonraki ekranda bildirim sıklığını ayarla."
            )
        rows = [
            [InlineKeyboardButton(city.name, callback_data=f"{pick_prefix}:{owner_id}:{city.plate}")]
            for city in current
        ]
        navigation = []
        if page > 0:
            navigation.append(InlineKeyboardButton("⬅️ Önceki", callback_data=f"{page_prefix}:{owner_id}:{page - 1}"))
        if page + 1 < page_count:
            navigation.append(InlineKeyboardButton("Sonraki ➡️", callback_data=f"{page_prefix}:{owner_id}:{page + 1}"))
        if navigation:
            rows.append(navigation)
        rows.append([InlineKeyboardButton(f"Sayfa {page + 1}/{page_count}", callback_data="notification_noop")])
        rows.append([InlineKeyboardButton("📣 Merkeze dön" if group else "🔔 Merkeze dön", callback_data=back)])
        await query.edit_message_text(
            f"{title}\n\n<i>İl sayfası {page + 1}/{page_count}</i>",
            reply_markup=InlineKeyboardMarkup(rows),
            parse_mode="HTML",
        )

    async def _handle_notification_navigation(self, update, query, context, data: str):
        """Route the category-based notification center without network calls."""
        parts = data.split(":")
        action = parts[0]
        try:
            owner_id = int(parts[1]) if len(parts) > 1 else update.effective_user.id
        except (TypeError, ValueError):
            await query.answer("Geçersiz bildirim menüsü.", show_alert=True)
            return
        is_group = update.effective_chat.type in {"group", "supergroup"}
        if not is_group and owner_id != update.effective_user.id:
            await query.answer("Bu bildirim menüsü başka bir kullanıcıya ait.", show_alert=True)
            return
        if action == "notify_center":
            if is_group:
                text, markup = group_center(
                    update.effective_user.id,
                    is_admin=await self._is_group_admin(update, context),
                )
            else:
                text, markup = personal_center(owner_id)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
            return
        if action in {"notify_weather", "notify_weather_page"}:
            if is_group:
                await query.answer("Bu grupta ortak bildirimleri yalnızca yöneticiler yönetebilir.", show_alert=True)
                return
            if action == "notify_weather_page":
                if len(parts) != 3 or owner_id != update.effective_user.id:
                    await query.answer("Geçersiz şehir sayfası.", show_alert=True)
                    return
                try:
                    page = int(parts[2])
                except ValueError:
                    await query.answer("Geçersiz şehir sayfası.", show_alert=True)
                    return
            else:
                page = 0
            await self._show_notification_city_page(update, query, owner_id, "weather", page, group=False)
            return
        if action in {"notify_air_quality", "notify_air_quality_page"}:
            if is_group:
                await query.answer("Kişisel hava kalitesi bildirimini özel sohbette kurabilirsin.", show_alert=True)
                return
            if owner_id != update.effective_user.id:
                await query.answer("Bu bildirim menüsü başka bir kullanıcıya ait.", show_alert=True)
                return
            if action == "notify_air_quality_page":
                if len(parts) != 3:
                    await query.answer("Geçersiz şehir sayfası.", show_alert=True)
                    return
                try:
                    page = int(parts[2])
                except ValueError:
                    await query.answer("Geçersiz şehir sayfası.", show_alert=True)
                    return
            else:
                page = 0
            await self._show_notification_city_page(update, query, owner_id, "air_quality", page, group=False)
            return
        if action == "notify_air_quality_pick":
            if is_group or owner_id != update.effective_user.id or len(parts) != 3:
                await query.answer("Geçersiz şehir seçimi.", show_alert=True)
                return
            try:
                plate = int(parts[2])
            except ValueError:
                await query.answer("Geçersiz şehir seçimi.", show_alert=True)
                return
            await query.edit_message_text(
                "🌿 <b>Hava kalitesi bildirimi</b>\n\nBildirim sıklığını seç:",
                reply_markup=air_quality_intervals(owner_id, plate),
                parse_mode="HTML",
            )
            return
        if action == "notify_air_quality_add":
            if is_group or not self.subscription_service or len(parts) != 4 or owner_id != update.effective_user.id:
                await query.answer("Bu bildirimi yönetemezsin.", show_alert=True)
                return
            try:
                plate = int(parts[2])
                interval = int(parts[3])
            except ValueError:
                await query.answer("Geçersiz bildirim seçimi.", show_alert=True)
                return
            if interval not in {10800, 21600, 86400, 604800}:
                await query.answer("Geçersiz bildirim aralığı.", show_alert=True)
                return
            city = self.chat_engine.city_directory.by_plate(plate)
            if city is None:
                await query.answer("Şehir bulunamadı.", show_alert=True)
                return
            item = await self.subscription_service.create(
                update.effective_user.id,
                update.effective_chat.id,
                SubscriptionRequest("air_quality", {"city": city.name}, interval, "inline"),
            )
            await query.edit_message_text(
                f"✅ <b>{html.escape(city.name)}</b> için hava kalitesi bildirimi kuruldu.\n"
                f"⏱ {html.escape(format_interval(interval))}\n"
                f"ID: <code>{item['subscription_id'][:8]}</code>",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Bildirimlerim", callback_data=f"notify_list:{owner_id}")],
                    [InlineKeyboardButton("Merkeze dön", callback_data=f"notify_center:{owner_id}")],
                ]),
                parse_mode="HTML",
            )
            return
        if action == "notify_earthquake":
            text, markup = await self._earthquake_result_text(update, context, owner_id)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
            return
        if action in {"notify_prices", "notify_crypto", "notify_feeds"}:
            if is_group:
                await query.answer("Kişisel fiyat ve haber takiplerini özel sohbette kurabilirsin.", show_alert=True)
                return
            if action == "notify_prices":
                text, markup = price_tracking_panel(owner_id)
            elif action == "notify_crypto":
                text, markup = crypto_tracking_panel(owner_id)
            else:
                text, markup = feed_tracking_panel(owner_id)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
            return
        if action == "notify_threshold_help":
            if is_group or owner_id != update.effective_user.id:
                await query.answer("Bu yardım menüsü kişisel bildirimlerine aittir.", show_alert=True)
                return
            text, markup = category_help("prices", owner_id)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
            return
        if action == "notify_price_add":
            if is_group or owner_id != update.effective_user.id or len(parts) != 4:
                await query.answer("Bu bildirimi yalnızca sahibi kurabilir.", show_alert=True)
                return
            feature_key = parts[2]
            try:
                interval = int(parts[3])
            except ValueError:
                await query.answer("Geçersiz takip aralığı.", show_alert=True)
                return
            allowed = {"currency": {3600, 21600}, "fuel": {21600}}
            if feature_key not in allowed or interval not in allowed[feature_key]:
                await query.answer("Geçersiz fiyat takip seçimi.", show_alert=True)
                return
            if self.subscription_service is None:
                await query.answer("Bildirim servisi hazır değil.", show_alert=True)
                return
            item = await self.subscription_service.create(
                owner_id,
                update.effective_chat.id,
                SubscriptionRequest(feature_key, {}, interval, format_interval(interval)),
            )
            label = {"currency": "Kur", "fuel": "Akaryakıt"}[feature_key]
            await query.edit_message_text(
                f"✅ <b>{label} takibi kuruldu.</b>\n"
                f"⏱ {html.escape(format_interval(interval))}\n"
                f"ID: <code>{html.escape(item['subscription_id'][:8])}</code>",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📋 Bildirimlerim", callback_data=f"notify_list:{owner_id}")],
                    [InlineKeyboardButton("🔔 Merkeze dön", callback_data=f"notify_center:{owner_id}")],
                ]),
                parse_mode="HTML",
            )
            return
        if action == "notify_crypto_add":
            if is_group or owner_id != update.effective_user.id or len(parts) != 4:
                await query.answer("Bu bildirimi yalnızca sahibi kurabilir.", show_alert=True)
                return
            symbol = parts[2].casefold()
            try:
                interval = int(parts[3])
            except ValueError:
                await query.answer("Geçersiz takip aralığı.", show_alert=True)
                return
            if symbol not in {"bitcoin", "ethereum", "solana"} or interval not in {3600, 86400}:
                await query.answer("Geçersiz kripto takip seçimi.", show_alert=True)
                return
            if self.subscription_service is None:
                await query.answer("Bildirim servisi hazır değil.", show_alert=True)
                return
            item = await self.subscription_service.create(
                owner_id,
                update.effective_chat.id,
                SubscriptionRequest("crypto", {"symbol": symbol}, interval, format_interval(interval)),
            )
            await query.edit_message_text(
                f"✅ <b>{html.escape(symbol.title())} takibi kuruldu.</b>\n"
                f"⏱ {html.escape(format_interval(interval))}\n"
                f"ID: <code>{html.escape(item['subscription_id'][:8])}</code>",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📋 Bildirimlerim", callback_data=f"notify_list:{owner_id}")],
                    [InlineKeyboardButton("🔔 Merkeze dön", callback_data=f"notify_center:{owner_id}")],
                ]),
                parse_mode="HTML",
            )
            return
        if action == "notify_feed_add":
            if is_group or owner_id != update.effective_user.id or len(parts) != 4:
                await query.answer("Bu bildirimi yalnızca sahibi kurabilir.", show_alert=True)
                return
            feature_key = parts[2]
            try:
                interval = int(parts[3])
            except ValueError:
                await query.answer("Geçersiz takip aralığı.", show_alert=True)
                return
            allowed = {"breaking_news": {7200}, "bim_deals": {43200}}
            if feature_key not in allowed or interval not in allowed[feature_key]:
                await query.answer("Geçersiz içerik takip seçimi.", show_alert=True)
                return
            if self.subscription_service is None:
                await query.answer("Bildirim servisi hazır değil.", show_alert=True)
                return
            item = await self.subscription_service.create(
                owner_id,
                update.effective_chat.id,
                SubscriptionRequest(feature_key, {}, interval, format_interval(interval)),
            )
            label = {"breaking_news": "Haber özeti", "bim_deals": "BİM aktüel"}[feature_key]
            await query.edit_message_text(
                f"✅ <b>{label} takibi kuruldu.</b>\n"
                f"⏱ {html.escape(format_interval(interval))}\n"
                f"ID: <code>{html.escape(item['subscription_id'][:8])}</code>",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📋 Bildirimlerim", callback_data=f"notify_list:{owner_id}")],
                    [InlineKeyboardButton("🔔 Merkeze dön", callback_data=f"notify_center:{owner_id}")],
                ]),
                parse_mode="HTML",
            )
            return
        if action == "notify_list":
            if not self.subscription_service:
                await query.answer("Bildirim servisi hazır değil.", show_alert=True)
                return
            rows = await self.subscription_service.store.list_for_user(owner_id)
            text, markup = render_rows(rows, owner_id=owner_id)
            back = [InlineKeyboardButton("🔔 Bildirim merkezine dön", callback_data=f"notify_center:{owner_id}")]
            keyboard = list(markup.inline_keyboard) if markup else []
            keyboard.append(back)
            await query.edit_message_text(
                text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
            )
            return
        if action == "notify_stop":
            if not self.subscription_service or len(parts) != 3 or owner_id != update.effective_user.id:
                await query.answer("Bu bildirimi yönetemezsin.", show_alert=True)
                return
            stopped = await self.subscription_service.store.disable(update.effective_user.id, parts[2])
            await query.answer("Bildirim durduruldu." if stopped else "Bildirim zaten kapalı.", show_alert=not stopped)
            rows = await self.subscription_service.store.list_for_user(owner_id)
            text, markup = render_rows(rows, owner_id=owner_id)
            keyboard = list(markup.inline_keyboard) if markup else []
            keyboard.append([InlineKeyboardButton("🔔 Merkeze dön", callback_data=f"notify_center:{owner_id}")])
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
            return
        if action == "notify_group_weather":
            if not is_group or not await self._is_group_admin(update, context):
                await query.answer("Bu ayarı yalnızca grup yöneticileri değiştirebilir.", show_alert=True)
                return
            await self._show_notification_city_page(update, query, owner_id, "weather", 0, group=True)
            return
        if action == "notify_group_weather_page":
            if not is_group or not await self._is_group_admin(update, context) or len(parts) != 3:
                await query.answer("Bu şehir sayfasını yalnızca grup yöneticileri açabilir.", show_alert=True)
                return
            try:
                page = int(parts[2])
            except ValueError:
                await query.answer("Geçersiz şehir sayfası.", show_alert=True)
                return
            await self._show_notification_city_page(update, query, owner_id, "weather", page, group=True)
            return
        if action == "notify_group_weather_pick":
            if not is_group or not await self._is_group_admin(update, context) or len(parts) != 3:
                await query.answer("Bu ayarı yalnızca grup yöneticileri değiştirebilir.", show_alert=True)
                return
            await self._handle_weather_callback(
                update, query, context,
                f"weather_group_opts:{update.effective_user.id}:{parts[2]}",
            )
            return
        if action == "notify_group_air_quality":
            if not is_group or not await self._is_group_admin(update, context):
                await query.answer("Bu ayarı yalnızca grup yöneticileri değiştirebilir.", show_alert=True)
                return
            await self._show_notification_city_page(update, query, update.effective_user.id, "air_quality", 0, group=True)
            return
        if action == "notify_group_air_quality_page":
            if not is_group or not await self._is_group_admin(update, context) or len(parts) != 3:
                await query.answer("Bu şehir sayfasını yalnızca grup yöneticileri açabilir.", show_alert=True)
                return
            try:
                page = int(parts[2])
            except ValueError:
                await query.answer("Geçersiz şehir sayfası.", show_alert=True)
                return
            await self._show_notification_city_page(update, query, update.effective_user.id, "air_quality", page, group=True)
            return
        if action == "notify_group_air_quality_pick":
            if not is_group or not await self._is_group_admin(update, context) or len(parts) != 3:
                await query.answer("Bu ayarı yalnızca grup yöneticileri değiştirebilir.", show_alert=True)
                return
            try:
                plate = int(parts[2])
            except ValueError:
                await query.answer("Şehir seçimi geçersiz.", show_alert=True)
                return
            await query.edit_message_text(
                "🌿 <b>Grup hava kalitesi</b>\n\nBildirim sıklığını seç:",
                reply_markup=air_quality_group_frequencies(update.effective_user.id, plate),
                parse_mode="HTML",
            )
            return
        if action == "notify_group_air_quality_freq":
            if not is_group or not await self._is_group_admin(update, context) or len(parts) != 4:
                await query.answer("Bu ayarı yalnızca grup yöneticileri değiştirebilir.", show_alert=True)
                return
            if parts[3] not in {"daily", "weekly"}:
                await query.answer("Geçersiz bildirim sıklığı.", show_alert=True)
                return
            try:
                plate = int(parts[2])
            except ValueError:
                await query.answer("Şehir seçimi geçersiz.", show_alert=True)
                return
            await query.edit_message_text(
                "Bildirim saatini seç (Türkiye saati):",
                reply_markup=air_quality_group_times(update.effective_user.id, plate, parts[3]),
                parse_mode="HTML",
            )
            return
        if action == "notify_group_air_quality_time":
            if not is_group or not await self._is_group_admin(update, context) or len(parts) != 6:
                await query.answer("Grup bildirim servisi hazır değil veya işlem geçersiz.", show_alert=True)
                return
            schedule_kind = parts[3]
            local_time = f"{parts[4]}:{parts[5]}"
            if schedule_kind not in {"daily", "weekly"} or parts[4] not in {"08", "10", "18", "22"} or parts[5] != "00":
                await query.answer("Geçersiz bildirim saati.", show_alert=True)
                return
            try:
                plate = int(parts[2])
            except ValueError:
                await query.answer("Şehir seçimi geçersiz.", show_alert=True)
                return
            city = self.chat_engine.city_directory.by_plate(plate)
            if city is None or self.subscription_service is None:
                await query.answer("Şehir veya bildirim servisi bulunamadı.", show_alert=True)
                return
            item = await self.subscription_service.create_group_schedule(
                admin_id=update.effective_user.id,
                chat_id=update.effective_chat.id,
                feature_key="air_quality",
                params={"city": city.name},
                schedule_kind=schedule_kind,
                local_time=local_time,
                weekday=0 if schedule_kind == "weekly" else None,
                message_thread_id=getattr(query.message, "message_thread_id", None),
            )
            frequency = "her gün" if schedule_kind == "daily" else "her pazartesi"
            await query.edit_message_text(
                f"✅ <b>{html.escape(city.name)}</b> için grup hava kalitesi bildirimi ayarlandı.\n"
                f"🗓 {frequency} · ⏰ {html.escape(local_time)} (Türkiye saati)\n"
                f"ID: <code>{item['subscription_id'][:8]}</code>",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📋 Grup bildirimleri", callback_data=f"notify_group_list:{update.effective_user.id}")],
                    [InlineKeyboardButton("📣 Merkeze dön", callback_data=f"notify_center:{update.effective_user.id}")],
                ]),
                parse_mode="HTML",
            )
            return
        if action == "notify_group_news":
            if not is_group or not await self._is_group_admin(update, context):
                await query.answer("Bu ayarı yalnızca grup yöneticileri değiştirebilir.", show_alert=True)
                return
            await query.edit_message_text(
                "📰 <b>Grup haber özeti</b>\n\nBildirim sıklığını seç:",
                reply_markup=news_group_frequencies(update.effective_user.id),
                parse_mode="HTML",
            )
            return
        if action == "notify_group_news_freq":
            if not is_group or not await self._is_group_admin(update, context) or len(parts) != 3:
                await query.answer("Bu ayarı yalnızca grup yöneticileri değiştirebilir.", show_alert=True)
                return
            if parts[2] not in {"daily", "weekly"}:
                await query.answer("Geçersiz bildirim sıklığı.", show_alert=True)
                return
            await query.edit_message_text(
                "Haber özetinin gönderileceği saati seç (Türkiye saati):",
                reply_markup=news_group_times(update.effective_user.id, parts[2]),
                parse_mode="HTML",
            )
            return
        if action == "notify_group_news_time":
            if not is_group or not await self._is_group_admin(update, context) or len(parts) != 5:
                await query.answer("Grup bildirim servisi hazır değil veya işlem geçersiz.", show_alert=True)
                return
            schedule_kind = parts[2]
            local_time = f"{parts[3]}:{parts[4]}"
            if schedule_kind not in {"daily", "weekly"} or parts[3] not in {"08", "10", "18", "22"} or parts[4] != "00":
                await query.answer("Geçersiz bildirim saati.", show_alert=True)
                return
            if self.subscription_service is None:
                await query.answer("Grup bildirim servisi hazır değil.", show_alert=True)
                return
            item = await self.subscription_service.create_group_schedule(
                admin_id=update.effective_user.id,
                chat_id=update.effective_chat.id,
                feature_key="breaking_news",
                params={},
                schedule_kind=schedule_kind,
                local_time=local_time,
                weekday=0 if schedule_kind == "weekly" else None,
                message_thread_id=getattr(query.message, "message_thread_id", None),
            )
            frequency = "her gün" if schedule_kind == "daily" else "her pazartesi"
            await query.edit_message_text(
                "✅ <b>Grup haber özeti ayarlandı.</b>\n"
                f"🗓 {frequency} · ⏰ {html.escape(local_time)} (Türkiye saati)\n"
                f"ID: <code>{item['subscription_id'][:8]}</code>",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📋 Grup bildirimleri", callback_data=f"notify_group_list:{update.effective_user.id}")],
                    [InlineKeyboardButton("📣 Merkeze dön", callback_data=f"notify_center:{update.effective_user.id}")],
                ]),
                parse_mode="HTML",
            )
            return
        if action == "notify_group_earthquake":
            if not is_group or not await self._is_group_admin(update, context):
                await query.answer("Bu ayarı yalnızca grup yöneticileri değiştirebilir.", show_alert=True)
                return
            text, markup = await self._earthquake_result_text(update, context, update.effective_user.id)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
            return
        if action == "notify_group_announcement":
            if not is_group or not await self._is_group_admin(update, context):
                await query.answer("Planlı duyuruyu yalnızca grup yöneticileri yönetebilir.", show_alert=True)
                return
            await query.edit_message_text(
                "📣 <b>Planlı grup duyurusu</b>\n\n"
                "Komut biçimi:\n<code>/duyuru her gün 10:00 Toplantı başlıyor</code>\n"
                "veya\n<code>/duyuru her pazartesi 09:00 Haftalık özet</code>",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("📣 Merkeze dön", callback_data=f"notify_center:{owner_id}")]]
                ),
                parse_mode="HTML",
            )
            return
        if action == "notify_group_list":
            if not self.subscription_service:
                await query.answer("Bildirim servisi hazır değil.", show_alert=True)
                return
            rows = await self.subscription_service.store.list_for_chat(update.effective_chat.id)
            text, markup = render_rows(rows, group=True, owner_id=owner_id)
            keyboard = list(markup.inline_keyboard) if markup else []
            keyboard.append([InlineKeyboardButton("📣 Merkeze dön", callback_data=f"notify_center:{owner_id}")])
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
            return
        if action == "notify_group_stop":
            if not self.subscription_service or not is_group or len(parts) != 3:
                await query.answer("Grup bildirimi bulunamadı.", show_alert=True)
                return
            if not await self._is_group_admin(update, context):
                await query.answer("Bu işlemi yalnızca grup yöneticileri yapabilir.", show_alert=True)
                return
            stopped = await self.subscription_service.store.disable_for_chat(
                update.effective_chat.id, parts[2]
            )
            await query.answer("Grup bildirimi durduruldu." if stopped else "Bildirim zaten kapalı.", show_alert=not stopped)
            rows = await self.subscription_service.store.list_for_chat(update.effective_chat.id)
            text, markup = render_rows(rows, group=True, owner_id=update.effective_user.id)
            keyboard = list(markup.inline_keyboard) if markup else []
            keyboard.append([InlineKeyboardButton("📣 Merkeze dön", callback_data=f"notify_center:{update.effective_user.id}")])
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
            return
        await query.answer("Bilinmeyen bildirim işlemi.", show_alert=True)

    async def _handle_notification_preference_callback(self, update, query, data: str):
        if not self.notification_preference_store:
            await query.answer("Bildirim ayar servisi hazır değil.", show_alert=True)
            return
        parts = data.split(":")
        if len(parts) < 3:
            await query.answer("Geçersiz bildirim ayarı.", show_alert=True)
            return
        try:
            owner_id = int(parts[2])
        except ValueError:
            await query.answer("Geçersiz bildirim ayarı.", show_alert=True)
            return
        if owner_id != update.effective_user.id:
            await query.answer("Bu menü başka bir kullanıcıya ait.", show_alert=True)
            return
        if parts[1] == "show":
            pass
        elif parts[1] == "timezone":
            prefs = await self.notification_preference_store.get(owner_id)
            text, markup = notification_timezone_panel(owner_id, prefs.get("timezone", "Europe/Istanbul"))
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
            return
        elif parts[1] == "tz" and len(parts) == 4:
            allowed_zones = {
                "Europe/Istanbul",
                "Europe/Berlin",
                "Europe/London",
                "Asia/Dubai",
                "America/New_York",
                "Asia/Tokyo",
            }
            timezone_name = parts[3].replace("~", "/")
            if timezone_name not in allowed_zones:
                await query.answer("Geçersiz saat dilimi seçimi.", show_alert=True)
                return
            await self.notification_preference_store.update(owner_id, timezone_name=timezone_name)
        elif parts[1] == "toggle":
            prefs = await self.notification_preference_store.get(owner_id)
            await self.notification_preference_store.update(
                owner_id, quiet_hours_enabled=not prefs["quiet_hours_enabled"]
            )
        elif parts[1] == "preset" and len(parts) == 4:
            presets = {"night": ("23:00", "08:00"), "late": ("22:00", "07:00")}
            if parts[3] not in presets:
                await query.answer("Geçersiz sessiz saat seçimi.", show_alert=True)
                return
            start, end = presets[parts[3]]
            await self.notification_preference_store.update(
                owner_id, quiet_hours_enabled=True, quiet_start=start, quiet_end=end
            )
        else:
            await query.answer("Geçersiz bildirim ayarı.", show_alert=True)
            return
        prefs = await self.notification_preference_store.get(owner_id)
        text, markup = notification_preferences_panel(owner_id, prefs)
        await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")

    async def _handle_culture_callback(self, update, query, data: str):
        """Handle the owner-scoped category picker without doing provider work."""

        parts = data.split(":")
        user_id = int(update.effective_user.id)
        if len(parts) < 2 or parts[1] != str(user_id):
            await query.answer("Bu öneri menüsü başka bir kullanıcıya ait.", show_alert=True)
            return
        if parts[0] == "culture_menu":
            if len(parts) != 2:
                await query.answer("Geçersiz öneri menüsü.", show_alert=True)
                return
            text, markup = culture_category_panel(user_id)
        elif parts[0] == "culture":
            # A category callback has one additional segment and is validated
            # before it reaches the pure presentation helper.
            if len(parts) != 3:
                await query.answer("Geçersiz öneri menüsü.", show_alert=True)
                return
            text, markup = culture_category_help(user_id, parts[2])
        else:
            await query.answer("Geçersiz öneri menüsü.", show_alert=True)
            return
        await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")

    async def _handle_productivity_callback(self, update, query, data: str):
        if not self.productivity_service:
            await query.answer("Üretkenlik servisi hazır değil.", show_alert=True)
            return
        parts = data.split(":", 3)
        if len(parts) != 4:
            await query.answer("Geçersiz işlem.", show_alert=True)
            return
        action, owner_raw, item_id = parts[1], parts[2], parts[3]
        try:
            owner_id = int(owner_raw)
        except ValueError:
            await query.answer("Geçersiz işlem.", show_alert=True)
            return
        if owner_id != update.effective_user.id:
            await query.answer("Bu liste başka bir kullanıcıya ait.", show_alert=True)
            return
        if action == "confirmclear":
            labels = {"notes": "notlar", "tasks": "görevler", "reminders": "hatırlatıcılar"}
            label = labels.get(item_id)
            if not label:
                await query.answer("Geçersiz toplu işlem.", show_alert=True)
                return
            await query.edit_message_text(
                f"⚠️ <b>{label.title()} tamamen silinsin mi?</b>\n\nBu işlem geri alınamaz.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Evet, sil", callback_data=f"prod:clear:{owner_id}:{item_id}"),
                    InlineKeyboardButton("İptal", callback_data=f"prod:cancelclear:{owner_id}:{item_id}"),
                ]]),
                parse_mode="HTML",
            )
            return
        if action == "cancelclear":
            await query.edit_message_text("İşlem iptal edildi.", parse_mode="HTML")
            return
        if action == "clear":
            if item_id not in {"notes", "tasks", "reminders"}:
                await query.answer("Geçersiz toplu işlem.", show_alert=True)
                return
            deleted = await self.productivity_service.clear_user_data(owner_id, sections={item_id})
            await query.answer("Veriler silindi.")
            await query.edit_message_text(
                f"✅ {deleted[item_id]} kayıt silindi.", parse_mode="HTML"
            )
            return
        actions = {
            "td": (self.productivity_service.complete_task, "Görev tamamlandı.", "Görev zaten tamamlanmış veya bulunamadı."),
            "tx": (self.productivity_service.delete_task, "Görev silindi.", "Görev bulunamadı."),
            "nd": (self.productivity_service.delete_note, "Not silindi.", "Not bulunamadı."),
            "rd": (self.productivity_service.delete_reminder, "Hatırlatıcı iptal edildi.", "Hatırlatıcı bulunamadı."),
        }
        operation = actions.get(action)
        if operation is None:
            await query.answer("Geçersiz işlem.", show_alert=True)
            return
        ok = await operation[0](owner_id, item_id)
        await query.answer(operation[1] if ok else operation[2], show_alert=not ok)
        if ok:
            await query.edit_message_text(f"\u2705 {operation[1]}", parse_mode="HTML")

    async def _weather_result_text(self, update, context, user_id: int, plate: int, days: int = 3):
        city = self.chat_engine.city_directory.by_plate(plate)
        if city is None:
            return "⚠️ Şehir bulunamadı.", None
        if days not in {1, 3, 7}:
            days = 3
        result = await self.chat_engine.feature_executor.execute("weather", {"city": city.name, "days": days})
        if not result.ok:
            return "⚠️ Hava durumu şu an alınamıyor.", None
        content = format_feature_result(result, "⚠️ Hava durumu şu an alınamıyor.")
        is_group = update.effective_chat.type in {"group", "supergroup"}
        can_manage = bool(is_group and await self._is_group_admin(update, context))
        return content, weather_actions(
            user_id, city.plate, is_group=is_group, can_manage=can_manage
        )

    async def _handle_weather_callback(self, update: Update, query, context, data: str) -> None:
        """Handle weather callbacks with a bounded malformed-input guard."""
        try:
            await self._handle_weather_callback_unchecked(update, query, context, data)
        except (IndexError, TypeError, ValueError):
            await query.answer("Geçersiz hava durumu işlemi.", show_alert=True)

    async def _handle_weather_callback_unchecked(self, update: Update, query, context, data: str) -> None:
        parts = data.split(":")
        action = parts[0]
        try:
            owner_id = int(parts[1])
        except (IndexError, ValueError):
            await query.answer("Geçersiz hava durumu işlemi.", show_alert=True)
            return
        is_group = update.effective_chat.type in {"group", "supergroup"}
        if action.startswith("weather_group"):
            if not is_group or not await self._is_group_admin(update, context):
                await query.answer("Bu ayarı yalnızca grup yöneticileri değiştirebilir.", show_alert=True)
                return
            # Group controls belong to the chat, so any current administrator
            # may operate a menu originally opened by another administrator.
            owner_id = update.effective_user.id
        elif is_group:
            if action not in {"weather_show", "weather_pick", "weather_forecast"}:
                await query.answer("Grup bildirimlerini yöneticiler yönetir.", show_alert=True)
                return
            owner_id = update.effective_user.id
        elif owner_id != update.effective_user.id:
            await query.answer("Bu hava durumu menüsü başka bir kullanıcıya ait.", show_alert=True)
            return
        if action == "weather_group_opts":
            plate = int(parts[2])
            await query.edit_message_text(
                "📣 <b>Grup hava durumu bildirimi</b>\n\nBildirim sıklığını seç:",
                reply_markup=weather_group_frequencies(owner_id, plate),
                parse_mode="HTML",
            )
            return
        if action == "weather_group_freq":
            plate = int(parts[2])
            schedule_kind = parts[3]
            await query.edit_message_text(
                "Bildirim saatini seç (Türkiye saati):",
                reply_markup=weather_group_times(owner_id, plate, schedule_kind),
                parse_mode="HTML",
            )
            return
        if action == "weather_group_time":
            if self.subscription_service is None or len(parts) != 6:
                await query.answer("Grup bildirim servisi hazır değil.", show_alert=True)
                return
            plate = int(parts[2])
            schedule_kind = parts[3]
            local_time = f"{parts[4]}:{parts[5]}"
            city = self.chat_engine.city_directory.by_plate(plate)
            if city is None:
                await query.answer("Şehir bulunamadı.", show_alert=True)
                return
            item = await self.subscription_service.create_group_schedule(
                admin_id=owner_id,
                chat_id=update.effective_chat.id,
                feature_key="weather",
                params={"city": city.name},
                schedule_kind=schedule_kind,
                local_time=local_time,
                weekday=0 if schedule_kind == "weekly" else None,
                message_thread_id=getattr(query.message, "message_thread_id", None),
            )
            frequency = "her gün" if schedule_kind == "daily" else "her pazartesi"
            await query.edit_message_text(
                f"✅ <b>{html.escape(city.name)}</b> için grup bildirimi ayarlandı.\n"
                f"🗓 {frequency} · ⏰ {html.escape(local_time)} (Türkiye saati)\n"
                f"ID: <code>{item['subscription_id'][:8]}</code>",
                reply_markup=weather_actions(owner_id, plate, is_group=True, can_manage=True),
                parse_mode="HTML",
            )
            return
        if action == "weather_group_list":
            rows = await self.subscription_service.store.list_for_chat(update.effective_chat.id) if self.subscription_service else []
            lines = ["<b>Grup hava durumu bildirimleri</b>"]
            keyboard = []
            active_rows = [row for row in rows if row.get("feature_key") == "weather" and row.get("enabled")]
            for row in active_rows:
                city = html.escape(str((row.get("params") or {}).get("city", "Şehir")))
                schedule = "her gün" if row.get("schedule_kind") == "daily" else "her pazartesi"
                lines.append(f"• {city} · {schedule} · {html.escape(str(row.get('local_time', '10:00')))}")
                keyboard.append([
                    InlineKeyboardButton(
                        f"🛑 {city} bildirimini durdur",
                        callback_data=f"weather_group_stop:{owner_id}:{row['subscription_id']}",
                    )
                ])
            if not active_rows:
                lines.append("\nBu grup için henüz bildirim kurulmamış.")
            plate = parts[2] if len(parts) > 2 else "34"
            keyboard.append([InlineKeyboardButton("🏠 Hava durumuna dön", callback_data=f"weather_show:{owner_id}:{plate}")])
            await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
            return
        if action == "weather_group_stop":
            if self.subscription_service is None or len(parts) != 3:
                await query.answer("Bildirim bulunamadı.", show_alert=True)
                return
            await self.subscription_service.store.disable_for_chat(update.effective_chat.id, parts[2])
            await query.answer("Grup bildirimi durduruldu.")
            await query.edit_message_text("✅ Grup hava durumu bildirimi durduruldu.", parse_mode="HTML")
            return
        if action == "weather_list":
            rows = await self.subscription_service.store.list_for_user(owner_id) if self.subscription_service else []
            lines = ["<b>Hava durumu takiplerin</b>"]
            keyboard = []
            weather_rows = [row for row in rows if row.get("feature_key") == "weather" and row.get("enabled")]
            for row in weather_rows:
                city = html.escape(str((row.get("params") or {}).get("city", "Şehir")))
                interval_label = format_interval(int(row.get("interval_seconds", 3600)))
                lines.append(f"• {city} · {interval_label}")
                keyboard.append([InlineKeyboardButton(f"🛑 {city} takibini durdur", callback_data=f"weather_stop:{owner_id}:{row['subscription_id']}")])
            if not weather_rows:
                lines.append("\nHenüz hava durumu takibin yok.")
            fallback_plate = parts[2] if len(parts) > 2 else "34"
            keyboard.append([InlineKeyboardButton("🏠 Hava durumuna dön", callback_data=f"weather_show:{owner_id}:{fallback_plate}")])
            await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
            return
        if action == "weather_opts":
            plate = int(parts[2])
            city = self.chat_engine.city_directory.by_plate(plate)
            label = html.escape(city.name if city else "şehir")
            await query.edit_message_text(
                f"<b>{label}</b> için bildirim sıklığını seç:",
                reply_markup=weather_intervals(owner_id, plate),
                parse_mode="HTML",
            )
            return
        if action == "weather_add":
            plate = int(parts[2])
            interval = int(parts[3])
            city = self.chat_engine.city_directory.by_plate(plate)
            if city is None or self.subscription_service is None:
                await query.answer("Şehir bulunamadı.", show_alert=True)
                return
            request = SubscriptionRequest("weather", {"city": city.name}, interval, format_interval(interval))
            item = await self.subscription_service.create(owner_id, update.effective_chat.id, request)
            await query.edit_message_text(
                f"✅ <b>{html.escape(city.name)}</b> hava durumu takibi kuruldu.\n"
                f"Bildirim sıklığı: {request.human_interval}\nID: <code>{item['subscription_id'][:8]}</code>",
                reply_markup=weather_actions(owner_id, city.plate),
                parse_mode="HTML",
            )
            return
        if action == "weather_stop":
            if self.subscription_service is None or len(parts) != 3:
                await query.answer("Takip bulunamadı.", show_alert=True)
                return
            subscription_id = parts[2]
            rows = await self.subscription_service.store.list_for_user(owner_id)
            row = next((item for item in rows if item["subscription_id"] == subscription_id), None)
            if row:
                await self.subscription_service.store.disable(owner_id, subscription_id)
            await query.answer("Hava durumu takibi durduruldu.")
            await query.edit_message_text("✅ Hava durumu bildirimi durduruldu.", parse_mode="HTML")
            return
        if action == "weather_forecast":
            try:
                plate = int(parts[2])
                days = int(parts[3])
            except (IndexError, ValueError):
                await query.answer("Geçersiz tahmin seçimi.", show_alert=True)
                return
            text, markup = await self._weather_result_text(update, context, owner_id, plate, days)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
            return
        if action in {"weather_show", "weather_pick"}:
            plate = int(parts[2])
            text, markup = await self._weather_result_text(update, context, owner_id, plate)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
            return
        await query.answer("Bilinmeyen hava durumu işlemi.", show_alert=True)

    async def _handle_air_quality_callback(self, update, query, context, data: str) -> None:
        """Render AQI from the same typed provider used by the command surface."""
        parts = data.split(":")
        if len(parts) != 3:
            await query.answer("Geçersiz hava kalitesi işlemi.", show_alert=True)
            return
        try:
            owner_id = int(parts[1])
            plate = int(parts[2])
        except ValueError:
            await query.answer("Geçersiz şehir seçimi.", show_alert=True)
            return
        is_group = update.effective_chat.type in {"group", "supergroup"}
        if not is_group and owner_id != update.effective_user.id:
            await query.answer("Bu hava kalitesi menüsü başka bir kullanıcıya ait.", show_alert=True)
            return
        city = self.chat_engine.city_directory.by_plate(plate)
        if city is None:
            await query.answer("Şehir bulunamadı.", show_alert=True)
            return
        result = await self.chat_engine.feature_executor.execute("air_quality", {"city": city.name})
        if not result.ok:
            await query.edit_message_text(
                "⚠️ Hava kalitesi verisi şu an alınamıyor.",
                reply_markup=air_quality_actions(update.effective_user.id, plate),
                parse_mode="HTML",
            )
            return
        content = format_feature_result(result, "⚠️ Hava kalitesi verisi şu an alınamıyor.")
        await query.edit_message_text(
            content,
            reply_markup=air_quality_actions(update.effective_user.id, plate),
            parse_mode="HTML",
        )

    async def _earthquake_result_text(self, update, context, user_id: int):
        is_group = update.effective_chat.type in {"group", "supergroup"}
        can_manage = bool(is_group and await self._is_group_admin(update, context))
        result = await self.chat_engine.feature_executor.execute("earthquakes", {})
        if not result.ok:
            return "⚠️ Deprem verileri şu an alınamıyor.", earthquake_actions(
                user_id, is_group=is_group, can_manage=can_manage
            )
        content = format_feature_result(result, "⚠️ Deprem verileri şu an alınamıyor.")
        return content, earthquake_actions(
            user_id, is_group=is_group, can_manage=can_manage
        )

    async def _handle_earthquake_callback(self, update: Update, query, context, data: str) -> None:
        """Handle earthquake callbacks with a bounded malformed-input guard."""
        try:
            await self._handle_earthquake_callback_unchecked(update, query, context, data)
        except (IndexError, TypeError, ValueError):
            await query.answer("Geçersiz deprem işlemi.", show_alert=True)

    async def _handle_earthquake_callback_unchecked(self, update: Update, query, context, data: str) -> None:
        parts = data.split(":")
        action = parts[0]
        try:
            owner_id = int(parts[1])
        except (IndexError, ValueError):
            await query.answer("Geçersiz deprem işlemi.", show_alert=True)
            return
        is_group = update.effective_chat.type in {"group", "supergroup"}
        if action.startswith("earthquake_group"):
            if not is_group or not await self._is_group_admin(update, context):
                await query.answer("Bu ayarı yalnızca grup yöneticileri değiştirebilir.", show_alert=True)
                return
            # The group owns the schedule; any current admin can manage it.
            owner_id = update.effective_user.id
        elif is_group:
            if action != "earthquake_show":
                await query.answer("Grup bildirimlerini yöneticiler yönetir.", show_alert=True)
                return
            owner_id = update.effective_user.id
        elif owner_id != update.effective_user.id:
            await query.answer("Bu deprem menüsü başka bir kullanıcıya ait.", show_alert=True)
            return

        if action == "earthquake_group_opts":
            await query.edit_message_text(
                "📣 <b>Grup deprem bildirimi</b>\n\nBildirim sıklığını seç:",
                reply_markup=earthquake_group_frequencies(owner_id), parse_mode="HTML"
            )
            return
        if action == "earthquake_group_freq":
            schedule_kind = parts[2] if len(parts) > 2 else ""
            if schedule_kind not in {"daily", "weekly"}:
                await query.answer("Geçersiz bildirim sıklığı.", show_alert=True)
                return
            await query.edit_message_text(
                "Bildirim saatini seç (Türkiye saati):",
                reply_markup=earthquake_group_times(owner_id, schedule_kind), parse_mode="HTML"
            )
            return
        if action == "earthquake_group_time":
            if self.subscription_service is None or len(parts) != 5:
                await query.answer("Grup bildirim servisi hazır değil.", show_alert=True)
                return
            schedule_kind = parts[2]
            local_time = f"{parts[3]}:{parts[4]}"
            if schedule_kind not in {"daily", "weekly"}:
                await query.answer("Geçersiz bildirim sıklığı.", show_alert=True)
                return
            item = await self.subscription_service.create_group_schedule(
                admin_id=owner_id,
                chat_id=update.effective_chat.id,
                feature_key="earthquakes",
                params={},
                schedule_kind=schedule_kind,
                local_time=local_time,
                weekday=0 if schedule_kind == "weekly" else None,
                message_thread_id=getattr(query.message, "message_thread_id", None),
            )
            frequency = "her gün" if schedule_kind == "daily" else "her pazartesi"
            await query.edit_message_text(
                "✅ <b>Grup deprem bildirimi ayarlandı.</b>\n"
                f"🗓 {frequency} · ⏰ {html.escape(local_time)} (Türkiye saati)\n"
                f"ID: <code>{item['subscription_id'][:8]}</code>",
                reply_markup=earthquake_actions(owner_id, is_group=True, can_manage=True),
                parse_mode="HTML",
            )
            return
        if action == "earthquake_group_list":
            rows = await self.subscription_service.store.list_for_chat(update.effective_chat.id) if self.subscription_service else []
            active = [row for row in rows if row.get("feature_key") == "earthquakes" and row.get("enabled")]
            lines = ["<b>Grup deprem bildirimleri</b>"]
            keyboard = []
            for row in active:
                schedule = "her gün" if row.get("schedule_kind") == "daily" else "her pazartesi"
                local_time = html.escape(str(row.get("local_time", "10:00")))
                lines.append(f"• {schedule} · {local_time} (Türkiye saati)")
                keyboard.append([InlineKeyboardButton(
                    "🛑 Deprem bildirimini durdur",
                    callback_data=f"earthquake_group_stop:{owner_id}:{row['subscription_id']}",
                )])
            if not active:
                lines.append("\nBu grup için henüz deprem bildirimi kurulmamış.")
            keyboard.append([InlineKeyboardButton("🏠 Deprem akışına dön", callback_data=f"earthquake_show:{owner_id}")])
            await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
            return
        if action == "earthquake_group_stop":
            if self.subscription_service is None or len(parts) != 3:
                await query.answer("Bildirim bulunamadı.", show_alert=True)
                return
            stopped = await self.subscription_service.store.disable_for_chat(update.effective_chat.id, parts[2])
            await query.answer("Grup deprem bildirimi durduruldu." if stopped else "Bildirim zaten kapalı.")
            await query.edit_message_text("✅ Grup deprem bildirimi durduruldu.", parse_mode="HTML")
            return
        if action == "earthquake_opts":
            await query.edit_message_text(
                "<b>Deprem bildirimi sıklığını seç:</b>",
                reply_markup=earthquake_intervals(owner_id), parse_mode="HTML"
            )
            return
        if action == "earthquake_add":
            if self.subscription_service is None or len(parts) != 3:
                await query.answer("Bildirim servisi hazır değil.", show_alert=True)
                return
            interval = int(parts[2])
            request = SubscriptionRequest("earthquakes", {}, interval, format_interval(interval))
            item = await self.subscription_service.create(owner_id, update.effective_chat.id, request)
            await query.edit_message_text(
                f"✅ Deprem bildirimi kuruldu.\nSıklık: {html.escape(request.human_interval)}\n"
                f"ID: <code>{item['subscription_id'][:8]}</code>",
                reply_markup=earthquake_actions(owner_id), parse_mode="HTML"
            )
            return
        if action == "earthquake_list":
            rows = await self.subscription_service.store.list_for_user(owner_id) if self.subscription_service else []
            active = [row for row in rows if row.get("feature_key") == "earthquakes" and row.get("enabled")]
            lines = ["<b>Deprem takiplerin</b>"]
            keyboard = []
            for row in active:
                label = format_interval(int(row.get("interval_seconds", 900)))
                lines.append(f"• {html.escape(label)}")
                keyboard.append([InlineKeyboardButton(
                    "🛑 Bu takibi durdur",
                    callback_data=f"earthquake_stop:{owner_id}:{row['subscription_id']}",
                )])
            if not active:
                lines.append("\nHenüz deprem takibin yok.")
            keyboard.append([InlineKeyboardButton("🏠 Deprem akışına dön", callback_data=f"earthquake_show:{owner_id}")])
            await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
            return
        if action == "earthquake_stop":
            if self.subscription_service is None or len(parts) != 3:
                await query.answer("Takip bulunamadı.", show_alert=True)
                return
            await self.subscription_service.store.disable(owner_id, parts[2])
            await query.answer("Deprem takibi durduruldu.")
            await query.edit_message_text("✅ Deprem bildirimi durduruldu.", parse_mode="HTML")
            return
        if action == "earthquake_show":
            text, markup = await self._earthquake_result_text(update, context, owner_id)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
            return
        await query.answer("Bilinmeyen deprem işlemi.", show_alert=True)

    async def _show_help_main(self, query):
        help_text = (
            "🌟 <b>AREL ASİSTAN - KOMUT REHBERİ</b> 🌟\n\n"
            "Aşağıdaki kategorilere tıklayarak tüm yeteneklerimi keşfedebilirsin. "
            "Her kategori altında daha spesifik özellik butonları bulunmaktadır. 👇"
        )
        keyboard = [
            [InlineKeyboardButton("📹 Medya İndirici", callback_data="help_cat_download"), InlineKeyboardButton("🤖 Yapay Zeka", callback_data="help_cat_ai")],
            [InlineKeyboardButton("🎮 Oyunlar", callback_data="help_cat_games"), InlineKeyboardButton("💬 Quotly (Alıntı)", callback_data="help_cat_quote")],
            [InlineKeyboardButton("🎨 Sticker Sistemi", callback_data="help_cat_sticker"), InlineKeyboardButton("📊 Finans & Kültür", callback_data="help_cat_finance")],
            [InlineKeyboardButton("⚙️ Araçlar & Diğer", callback_data="help_cat_tools"), InlineKeyboardButton("🚀 Geliştirme Fikirleri", callback_data="help_cat_suggestions")]
            , [InlineKeyboardButton("🛡️ Kek Guardian Komut Merkezi", callback_data="help_cat_guardian")]
        ]
        await query.edit_message_text(help_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

    async def _show_guardian_help(self, query, data: str) -> None:
        """Render the embedded Guardian command center without slash-menu noise."""
        category = str(data).split(":", 1)[1] if ":" in str(data) else "main"
        categories = [
            ("🛡️ Yönetici", "admin"), ("🔇 Sessiz İşlem", "silent"),
            ("🎭 Roller", "roles"), ("👋 Karşılama", "greet"),
            ("📝 Notlar", "notes"), ("🖇️ Filtreler", "filters"),
            ("🔒 Kilitler", "locks"), ("🛡️ Koruma", "protect"),
            ("🌍 Federasyon", "fed"), ("📜 Kurallar", "rules"),
            ("🔄 Görevler & Kısayollar", "recurring"),
            ("🤖 Captcha", "captcha"), ("💤 AFK", "afk"),
            ("🌙 Gece", "night"), ("🚀 Medya Yasağı", "mban"),
            ("🔎 Üye İstihbarat", "member"), ("⚙️ Diğer", "misc"),
        ]
        if category == "main":
            text = (
                "✨ <b>KEK GUARDIAN — KOMUT MERKEZİ</b>\n\n"
                "Aşağıdaki kategorilerden birini seçerek Guardian komutlarını, "
                "açıklamalarını ve kullanım örneklerini görüntüleyebilirsin."
            )
            keyboard = []
            row = []
            for label, key in categories:
                row.append(InlineKeyboardButton(label, callback_data=f"guardian_help:{key}"))
                if len(row) == 2:
                    keyboard.append(row)
                    row = []
            if row:
                keyboard.append(row)
            # Sudo buttons are visible only to owner/sudo, matching Guardian.
            if self.sudo_manager.is_sudo(query.from_user.id):
                keyboard.append([InlineKeyboardButton("👑 Sudo & Yayın", callback_data="guardian_help:sudo")])
            keyboard.append([InlineKeyboardButton("🔙 Arel yardımına dön", callback_data="help_main")])
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
            return

        pages = {
            "admin": ("🛡️ YÖNETİCİ KOMUTLARI", "Yanıt vererek, @kullanıcı adıyla veya ID ile kullanılır.\n\n<b>/ban</b> · yasakla\n<b>/unban</b> · yasağı kaldır\n<b>/kick</b> · gruptan çıkar\n<b>/mute</b> · sustur\n<b>/unmute</b> · susturmayı kaldır\n<b>/tban</b> <code>2h</code> · süreli yasak\n<b>/tmute</b> <code>30m</code> · süreli susturma\n<b>/promote</b> · yönetici yap\n<b>/demote</b> · yetkiyi al\n<b>/purge</b> · toplu mesaj temizle\n<b>/pin</b> · mesaj sabitle"),
            "silent": ("🔇 SESSİZ İŞLEMLER", "İşlem yapılır; komut ve bot yanıtı gruba bırakılmaz, kayıt politikası açıksa loglanır.\n\n<b>/sban</b> · sessiz yasak\n<b>/skick</b> · sessiz çıkarma\n<b>/smute</b> · sessiz susturma\n<b>/swarn</b> · sessiz uyarı"),
            "roles": ("🎭 KULLANICI ROLLERİ", "Grup içi muafiyet ve ekip yönetimi.\n\n<b>/mod</b> · moderatör\n<b>/unmod</b> · moderatörlüğü kaldır\n<b>/free</b> · koruma muafiyeti\n<b>/unfree</b> · muafiyeti kaldır\n<b>/staff</b> · ekip listesi"),
            "greet": ("👋 KARŞILAMA & UĞURLAMA", "Yeni üye akışını ve ayrılma mesajlarını yönet.\n\n<b>/welcome on|off</b>\n<b>/setwelcome</b> <code>Hoş geldin {{mention}}</code>\n<b>/goodbye on|off</b>\n<b>/setgoodbye</b>\n<b>/cleanwelcome</b>\n<b>/welcomemute</b>"),
            "notes": ("📝 NOTLAR", "Gruba özel hızlı yanıt ve kayıt sistemi.\n\n<b>/save</b> <code>isim içerik</code>\n<b>/get</b> <code>isim</code> veya <code>#isim</code>\n<b>/notes</b> · listele\n<b>/clear</b> <code>isim</code> · sil"),
            "filters": ("🖇️ FİLTRELER", "Belirli ifadeleri otomatik yakala ve yanıtla.\n\n<b>/filter</b> <code>kelime yanıt</code>\n<b>/stop</b> <code>kelime</code>\n<b>/filters</b> · aktif filtreler"),
            "locks": ("🔒 KİLİTLER", "Mesaj türlerini grup bazında kilitle.\n\n<b>/lock</b> <code>photo|video|sticker|gif|document</code>\n<b>/unlock</b> <code>tür</code>\n<b>/locks</b> · aktif kilitler"),
            "protect": ("🛡️ KORUMA", "Spam, raid ve flood korumaları.\n\n<b>/setflood</b> <code>5</code>\n<b>/setfloodmode</b> <code>mute|kick|ban</code>\n<b>/addblock</b> <code>kelime</code>\n<b>/antiraid</b> <code>on|off</code>\n<b>/warn</b> · uyarı\n<b>/warns</b> · uyarı geçmişi"),
            "fed": ("🌍 FEDERASYON", "Birden çok grubu ortak güvenlik ağına bağlar.\n\n<b>/newfed</b> <code>isim</code>\n<b>/joinfed</b> <code>fed_id</code>\n<b>/leavefed</b>\n<b>/fban</b> · federasyon yasağı\n<b>/funban</b>\n<b>/fedstat</b>\n<b>/fedbanlist</b>"),
            "rules": ("📜 KURALLAR", "Grup kurallarını yaz, göster ve özelden gönder.\n\n<b>/setrules</b> <code>kurallar</code>\n<b>/rules</b>\n<b>/privaterules</b> <code>on|off</code>"),
            "recurring": ("🔄 GÖREVLER & KISAYOLLAR", "Tekrarlı duyuru ve grup içi komut kısayolları.\n\n<b>/recurring</b> <code>30m mesaj</code>\n<b>/stoprecurring</b>\n<b>/alias</b> <code>kısayol komut</code>\n<b>/delalias</b> <code>kısayol</code>\n<b>/aliases</b>"),
            "captcha": ("🤖 CAPTCHA", "Yeni üyeleri bot hesaplarına karşı doğrula.\n\n<b>/captcha</b> <code>on|off</code>\n<b>/captchatype</b> <code>button|math|text</code>\n<b>/approve</b>"),
            "afk": ("💤 AFK", "Etiketlendiğinde AFK durumunu bildirir.\n\n<b>/afk</b> <code>sebep</code>\nMesaj yazınca AFK otomatik kapanır."),
            "night": ("🌙 GECE MODU", "Belirlenen saatlerde grup akışını kısıtlar.\n\n<b>/nightmode</b> <code>on|off</code>\n<b>/nighttime</b> <code>23:00 07:00</code>"),
            "mban": ("🚀 MEDYA YASAĞI", "Fotoğraf, video, GIF, sticker, belge ve ses türlerini hedef kullanıcıya kapatır.\n\n<b>/mban</b> <code>@kullanıcı 2h</code>\n<b>/munban</b>"),
            "member": ("🔎 ÜYE İSTİHBARAT", "Grup yöneticileri için sınırlı üye bilgi araçları.\n\n<b>/firstmsg</b> · ilk mesaj\n<b>/memberlog</b> · üyelik geçmişi\n<b>/userinfo</b> · profil\n<b>/zombies</b> · pasif üyeler"),
            "misc": ("⚙️ DİĞER GUARDIAN ARAÇLARI", "Grup ayarları ve bakım araçları.\n\n<b>/id</b> · chat/kullanıcı kimliği\n<b>/info</b> · profil özeti\n<b>/disable</b> <code>komut</code>\n<b>/enable</b> <code>komut</code>\n<b>/disabled</b> · kapalı komutlar\n<b>/reload</b> · ayarları yenile"),
            "sudo": ("👑 SUDO & YAYIN", "Sadece owner/sudo görür.\n\n<b>/addsudo</b> · sudo ekle\n<b>/delsudo</b> · sudo kaldır\n<b>/stats</b> · sistem durumu\n<b>/gban</b> · genel yasak\n<b>/broadcast</b> · gruplara duyuru\n<b>/broadcastpin</b> · duyuru ve sabitle"),
        }
        title, body = pages.get(category, ("Guardian", "Bu kategori bulunamadı."))
        await query.edit_message_text(
            f"<b>{title}</b>\n\n{body}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Guardian merkezine dön", callback_data="guardian_help:main")],
                [InlineKeyboardButton("🏠 Arel yardımına dön", callback_data="help_main")],
            ]),
            parse_mode="HTML",
        )

    async def _show_help_category(self, query, data):
        btn_back_main = InlineKeyboardButton("🏠 Ana Menüye Dön", callback_data="help_main")
        category_map = {
            "help_cat_download": ("📹", "MEDYA İNDİRİCİ SİSTEMİ", [
                [InlineKeyboardButton("📸 Instagram", callback_data="help_feat_ig"), InlineKeyboardButton("🎵 TikTok", callback_data="help_feat_tt")],
                [InlineKeyboardButton("▶️ YouTube", callback_data="help_feat_yt"), InlineKeyboardButton("🐦 X (Twitter)", callback_data="help_feat_x")],
                [InlineKeyboardButton("📘 Facebook", callback_data="help_feat_fb")]
            ]),
            "help_cat_ai": ("🤖", "AREL AI - YAPAY ZEKA MERKEZİ", [
                [InlineKeyboardButton("💬 Sohbet & Bilgi", callback_data="help_feat_chat"), InlineKeyboardButton("🖼️ Görsel Üretimi", callback_data="help_feat_image")],
                [InlineKeyboardButton("📄 Dosya & Belge", callback_data="help_feat_file"), InlineKeyboardButton("🌐 Akıllı Çeviri", callback_data="help_feat_trans")]
            ]),
            "help_cat_games": ("🎮", "OYUN MERKEZİ", [
                [InlineKeyboardButton("🗣️ Anlatmaca", callback_data="help_feat_anlat"), InlineKeyboardButton("🪑 Adam Asmaca", callback_data="help_feat_hangman")],
                [InlineKeyboardButton("🎯 Quiz / Bilgi", callback_data="help_feat_quiz"), InlineKeyboardButton("🍾 Şişe Çevirme", callback_data="help_feat_bottle")],
                [InlineKeyboardButton("🃏 Batak", callback_data="help_feat_batak"), InlineKeyboardButton("⚔️ Düello", callback_data="help_feat_duello")],
                [InlineKeyboardButton("🎰 Kumar / Diğer", callback_data="help_feat_gambling")]
            ]),
            "help_cat_quote": ("💬", "QUOTLY (MÜKEMMEL ALINTILAR)", [
                [InlineKeyboardButton("📌 Temel Kullanım", callback_data="help_feat_q_basic"), InlineKeyboardButton("🎨 Tasarım (Flag)", callback_data="help_feat_q_flags")],
                [InlineKeyboardButton("👑 Admin Ayarları", callback_data="help_feat_q_admin")]
            ]),
            "help_cat_sticker": ("🎨", "AREL STİCKER FABRİKASI", [
                [InlineKeyboardButton("✂️ Sticker Yap", callback_data="help_feat_st_make"), InlineKeyboardButton("🔍 Akıllı Arama", callback_data="help_feat_st_search")],
                [InlineKeyboardButton("📦 Paket & Katalog", callback_data="help_feat_st_manage")]
            ]),
            "help_cat_finance": ("📊", "FİNANS & KÜLTÜR DURAĞI", [
                [InlineKeyboardButton("📈 Borsa / Kripto", callback_data="help_feat_finance"), InlineKeyboardButton("🎬 AI Önerileri", callback_data="help_feat_suggest")]
            ]),
            "help_cat_tools": ("⚙️", "ARAÇLAR & YARDIMCI KOMUTLAR", [
                [InlineKeyboardButton("⏰ Hatırlatıcı", callback_data="help_feat_remind"), InlineKeyboardButton("🔊 TTS (Seslendirme)", callback_data="help_feat_tts")],
                [InlineKeyboardButton("🎲 Mini Araçlar", callback_data="help_feat_mini_tools")]
            ]),
            "help_cat_guardian": ("🛡️", "AREL GUARDIAN MODERASYON", [
                [InlineKeyboardButton("🔨 Yönetim", callback_data="help_feat_guardian_admin"), InlineKeyboardButton("🧹 Koruma", callback_data="help_feat_guardian_safety")],
                [InlineKeyboardButton("🌐 Federasyon", callback_data="help_feat_guardian_federation"), InlineKeyboardButton("📋 Üye araçları", callback_data="help_feat_guardian_members")],
            ])
        }

        if data in category_map:
            icon, title, keyboard_content = category_map[data]
            if data == "help_cat_quote" and not self.sudo_manager.is_owner(query.from_user.id):
                keyboard_content = [
                    row for row in keyboard_content
                    if not any(
                        getattr(button, "callback_data", "") == "help_feat_q_admin"
                        for button in row
                    )
                ]
            text = f"{icon} <b>{title}</b>\n\nLinkleri ışık hızında videoya dönüştürürüm. Platform detayları için butonlara dokun:"
            keyboard = keyboard_content + [[btn_back_main]]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

    async def _show_help_feature(self, query, data):
        btn_back_main = InlineKeyboardButton("🏠 Ana Menüye Dön", callback_data="help_main")
        feat = data.replace("help_feat_", "")
        if feat == "q_admin" and not self.sudo_manager.is_owner(query.from_user.id):
            await query.answer("Bu bölüm yalnızca bot sahibine açıktır.", show_alert=True)
            return
        detail_text = ""

        feat_map = {
            "ig": "download", "tt": "download", "yt": "download", "x": "download", "fb": "download",
            "chat": "ai", "image": "ai", "file": "ai", "trans": "ai",
            "anlat": "games", "hangman": "games", "quiz": "games", "bottle": "games", "batak": "games", "duello": "games", "gambling": "games",
            "q_basic": "quote", "q_flags": "quote", "q_admin": "quote",
            "st_make": "sticker", "st_search": "sticker", "st_manage": "sticker",
            "finance": "finance", "suggest": "finance",
            "remind": "tools", "tts": "tools", "mini_tools": "tools",
            "guardian_admin": "guardian", "guardian_safety": "guardian",
            "guardian_federation": "guardian", "guardian_members": "guardian"
        }
        back_cat = f"help_cat_{feat_map.get(feat, 'main')}"

        feat_details = {
            "ig": "📸 <b>INSTAGRAM İNDİRİCİ</b>\n\nInstagram'dan dilediğin medyayı saniyeler içinde kalitesini bozmadan indiririm.\n\n🌐 <b>Desteklenen İçerikler:</b>\n• <b>Reels:</b> Tüm makaralar.\n• <b>Posts:</b> Fotoğraflar ve çoklu kaydırmalı gönderiler.\n• <b>Stories:</b> Hikaye linkleri.\n\n💡 <b>Kullanım:</b> Girdiğin linki otomatik tanırım.",
            "tt": "🎵 <b>TIKTOK İNDİRİCİ</b>\n\nTikTok videolarını filigransız (yazısız) ve HD kalitede indiririm.\n\n✨ <b>Özellikler:</b>\n• Yazısız (No Watermark) indirme.\n• MP3 olarak ses ayıklama.\n• Fotoğraf slaytlarını video olarak birleştirme.",
            "yt": "▶️ <b>YOUTUBE / SHORTS İNDİRİCİ</b>\n\nYouTube videolarını ve Shorts videolarını indirir, istersen sese dönüştürürüm.\n\n📊 <b>Formatlar:</b>\n• <b>MP4:</b> 720p/1080p\n• <b>MP3:</b> Müzik olarak indirme.",
            "chat": "💬 <b>AREL AI SOHBET</b>\n\nGemini 2.0 Flash mimarisi ile her konuda uzman seviyesinde sohbet.\n\n🧠 <b>Neler Sorabilirsin?</b>\n• <b>Yazılım:</b> <i>\"Python'da asenkron fonksiyon nasıl yazılır?\"</i>\n• <b>Eğitim:</b> <i>\"Kuantum fiziğini açıkla\"</i>",
            "image": "🎨 <b>AI GÖRSEL ÜRETİMİ</b>\n\nKelimelerini sanata dönüştüren güçlü bir motor.\n\n🛠️ <b>Komut:</b> <code>/image [prompt]</code>\n\n💡 <b>Gelişmiş Prompt:</b> <i>...cinematic lighting, digital art, 8k</i>",
            "anlat": "🗣️ <b>KELİME ANLATMACA</b>\n\nArkadaşlarınla grupta oynayabileceğin en eğlenceli oyun.\n\n🎮 <b>Akış:</b>\n1. <code>/anlat</code> yazarak lobi aç.\n2. Bot sana özelden <b>Hedef Kelime</b> verir.\n3. Grubun diğerlerine anlatmaya çalış.",
            "hangman": "🪑 <b>ADAM ASMACA</b>\n\nKlasik harf tahmin etme oyunu.\n\n🎮 <b>Komut:</b> <code>/adamasmaca</code>\n\n💡 <b>Nasıl Oynanır?</b>\n• Bot gizli bir kelime tutar.\n• Harf gönderirsin.\n• 6 yanlış hakkın var!",
            "quiz": "🎯 <b>BİLGİ YARIŞMASI</b>\n\nGenel kültürünü test et.\n\n🎮 <b>Komut:</b> <code>/quiz</code>\n\n📚 <b>Kategoriler:</b> Tarih, Coğrafya, Bilim, Sanat, Spor.",
            "bottle": "🍾 <b>ŞİŞE ÇEVİRME</b>\n\nGrup içinde şişe döner, şanslı kişi belirlenir.\n\n🎮 <b>Komutlar:</b>\n• <code>/sisecevir</code> — Lobi oluşturur.\n• <code>/d</code> — Doğruluk sorusu.\n• <code>/c</code> — Cesaret görevi.",
            "batak": "🃏 <b>KOZ MAÇA (BATAK)</b>\n\nTelegram üzerindeki en gelişmiş Batak sistemi.\n\n🎮 <b>Komut:</b> <code>/batak</code>",
            "duello": "⚔️ <b>DÜELLO</b>\n\nGruptan birine meydan oku!\n\n🎮 <b>Kullanım:</b> Birinin mesajına yanıt vererek <code>/duello</code> yaz.\n\n🔫 Bot geri sayım yapar. İlk basan kazanır!",
            "gambling": "🎰 <b>KUMAR VE ŞANS OYUNLARI</b>\n\n🎲 <b>Zar:</b> <code>/zar</code>\n🪙 <b>Yazı-Tura:</b> <code>/yazitura</code>\n🎰 <b>Slot:</b> <code>/slot</code>\n🔫 <b>Rus Ruleti:</b> <code>/rusruleti</code>",
            "q_basic": "📌 <b>QUOTLY - TEMEL KULLANIM</b>\n\nMesajlarını stickerlara dönüştür.\n\n🔧 <b>Komutlar:</b>\n• <code>/q</code> — Yanıtlanan tek mesajı alıntılar.\n• <code>/q [sayı]</code> — X adet mesajı alıntılar.\n• <code>/q * [metin]</code> — Geçmişte arama yap.",
            "q_flags": "🎨 <b>QUOTLY - TASARIM</b>\n\nAlıntını kişiselleştir.\n\n🛠️ <b>Mevcut Bayraklar:</b>\n• <code>p</code> — PNG (Şeffaf).\n• <code>rate</code> — Beğen butonları.\n• <code>#ffff00</code> — Arka plan rengi.\n• <code>s1.2</code> — Ölçekleme.",
            "finance": "📈 <b>FİNANSAL VERİ TAKİBİ</b>\n\n💰 <b>Kripto:</b> <code>/kripto [coin]</code>\n📊 <b>Borsa:</b> <code>/borsa [hisse]</code>\n\n7 günlük grafik desteği!",
            "suggest": "🎬 <b>AREL AI ÖNERİ SİSTEMİ</b>\n\n💡 <b>Komutlar:</b>\n• <code>/filmoneri [tür]</code>\n• <code>/dizioneri [tür]</code>\n• <code>/kitaponeri [tür]</code>\n• <code>/sarkioneri [mood]</code>",
            "remind": "⏰ <b>AKILLI HATIRLATICI</b>\n\n🛠️ <b>Komut:</b> <code>/remind [zaman] [not]</code>\n\n🚀 <b>Formatlar:</b>\n• <code>10dk</code>, <code>2sa</code>, <code>1gün</code>\n• <code>yarın 10:00 Toplantı</code>\n• <code>her gün 09:00 Günlük plan</code>\n• <code>her pazartesi 09:00 Haftalık plan</code>",
            "tts": "🔊 <b>SESLENDİRME (TTS)</b>\n\nYazıları doğal insan sesine dönüştürürüm.\n\n🛠️ <b>Kullanım:</b>\n• <code>/oku [metin]</code>\n• Bir mesaja yanıt verip <code>/oku</code>",
            "st_make": "✂️ <b>STİCKER OLUŞTURMA</b>\n\nKendi kişisel paketlerini kur.\n\n🛠️ <b>Adımlar:</b>\n1. Fotoğraf veya video gönder.\n2. Gönderiye cevap olarak <code>/sticker</code>.\n3. Paket oluştur veya mevcuta ekle!",
            "st_search": "🔍 <b>AKILLI STİCKER ARAMA</b>\n\nMilyarlarca sticker arasından AI ile bul.\n\n🛠️ <b>Komut:</b> <code>/st [kelime]</code>\n\n🚀 <b>Örnek:</b> <code>/st komik kedi</code>",
            "st_manage": "📦 <b>PAKET VE KATALOG</b>\n\n📂 <b>Komutlar:</b>\n• <code>/catalog</code> — Genel katalog.\n• <code>/packs</code> — Kendi paketlerin.\n• <code>/stdelete</code> — Sticker sil."
            , "guardian_admin": "🔨 <b>GUARDIAN YÖNETİM</b>\n\n<code>/ban</code>, <code>/mute</code>, <code>/kick</code>, <code>/promote</code>, <code>/demote</code>, <code>/purge</code>, <code>/pin</code> ve sessiz karşılıkları yalnızca grup yöneticilerine açıktır. Hedefi seçmek için mesaja yanıt ver.",
            "guardian_safety": "🧹 <b>GUARDIAN KORUMA</b>\n\n<code>/antilink</code>, <code>/antibotlink</code>, <code>/antiraid</code>, <code>/captcha</code>, <code>/lock</code>, <code>/filter</code>, <code>/setflood</code>, <code>/warn</code> ve gece modu grup bazında kalıcıdır.",
            "guardian_federation": "🌐 <b>GUARDIAN FEDERASYON</b>\n\n<code>/newfed</code>, <code>/joinfed</code>, <code>/fban</code>, <code>/funban</code>, <code>/fedstat</code>, <code>/fedbanlist</code> ve risk araçları Arel'in Mongo/Redis durum katmanını kullanır.",
            "guardian_members": "📋 <b>GUARDIAN ÜYE ARAÇLARI</b>\n\n<code>/userinfo</code>, <code>/firstmsg</code>, <code>/memberlog</code>, <code>/zombies</code>, <code>/notes</code>, <code>/aliases</code>, <code>/rules</code> ve <code>/report</code> grup kapsamına göre çalışır."
        }

        detail_text = feat_details.get(feat, f"💡 <b>{feat.capitalize()}</b> özelliği hakkında detaylı rehber hazırlanıyor...\n\nKomutları kullanmaya hemen başlayabilirsin!")

        keyboard = [[InlineKeyboardButton("🔙 Geri", callback_data=back_cat), btn_back_main]]
        await query.edit_message_text(detail_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

    async def _show_suggestions(self, query):
        text = (
            "🚀 <b>GELECEK PLANLARI & GELİŞTİRME ÖNERİLERİ</b>\n\n"
            "1️⃣ <b>İleri Seviye AI Vizyonu:</b>\n"
            "• <b>Kişilikler:</b> Botun konuşma tarzını değiştiren modlar.\n"
            "• <b>Uzun Süreli Hafıza:</b> Günler sonra bile hatırlayan sistem.\n"
            "• <b>Sesli Cevaplar:</b> Metinleri sesli mesaja dönüştüren.\n\n"
            "2️⃣ <b>Eğlence & Sosyal:</b>\n"
            "• <b>Müzik Botu:</b> VC üzerinden müzik yayını.\n"
            "• <b>Liderlik Tablosu:</b> En aktif üyeler.\n\n"
            "📩 <b>Fikirlerin mi var?</b> @pastalikek"
        )
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Geri", callback_data="help_main")]]), parse_mode='HTML')

    async def _handle_blackjack_callback(self, update, query, context, data: str):
        """Apply one owner-scoped Blackjack action and refresh the message."""
        parts = data.split(":", 2)
        if len(parts) != 3 or self.blackjack_service is None:
            await query.answer("Geçersiz Blackjack işlemi.", show_alert=True)
            return
        try:
            owner_id = int(parts[1])
        except (TypeError, ValueError):
            await query.answer("Geçersiz Blackjack oyuncusu.", show_alert=True)
            return
        if owner_id != int(update.effective_user.id):
            await query.answer("Bu el başka bir oyuncuya ait.", show_alert=True)
            return
        action = parts[2].casefold()
        if action not in {"hit", "stand", "double"}:
            await query.answer("Geçersiz Blackjack hamlesi.", show_alert=True)
            return
        action_id = self._game_action_id(query, owner_id, data)
        try:
            result = await self.blackjack_service.action(
                chat_id=update.effective_chat.id,
                user_id=owner_id,
                action=action,
                action_id=action_id,
            )
        except ValueError as exc:
            messages = {
                "duplicate_action": "Bu hamle zaten işlendi.",
                "game_expired": "Bu Blackjack eli artık aktif değil.",
                "game_finished": "Bu el sona ermiş.",
                "double_only_initial_hand": "Double yalnızca ilk iki kartta kullanılabilir.",
                "insufficient_balance": "Double için yeterli jetonun yok.",
                "not_owner": "Bu el başka bir oyuncuya ait.",
            }
            code = normalize_error_code(exc, "GAME_ERROR").lower()
            await query.answer(messages.get(code, "Bu hamle uygulanamadı."), show_alert=True)
            return
        except Exception as exc:
            logger.error("Blackjack callback failed: %s", summarize_exception(exc, label="blackjack_callback"))
            await query.answer("Hamle uygulanamadı. Lütfen tekrar dene.", show_alert=True)
            return

        markup = None
        if not result.get("terminal"):
            markup = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("➕ Kart çek", callback_data=f"bj:{owner_id}:hit"),
                    InlineKeyboardButton("✅ Dur", callback_data=f"bj:{owner_id}:stand"),
                ],
                [InlineKeyboardButton("2️⃣ Double", callback_data=f"bj:{owner_id}:double")],
            ])
        try:
            await query.edit_message_text(
                sanitize_html(str(result.get("text", "Blackjack"))),
                reply_markup=markup,
                parse_mode="HTML",
            )
            await query.answer("Hamle uygulandı.")
        except Exception as exc:
            logger.error("Could not refresh Blackjack message: %s", summarize_exception(exc, label="blackjack_refresh"))

    async def _handle_batak_round_callback(self, update, query, context, data: str):
        if self.batak_service is None:
            await query.answer("Batak servisi hazır değil.", show_alert=True)
            return
        parts = data.split(":")
        if len(parts) < 2 or parts[1] not in {"join", "start", "play"}:
            await query.answer("Geçersiz Batak işlemi.", show_alert=True)
            return
        action = parts[1]
        user_id = int(update.effective_user.id)
        action_id = self._game_action_id(query, user_id, data)
        try:
            if action == "join":
                result = await self.batak_service.join(
                    chat_id=update.effective_chat.id,
                    user_id=user_id,
                    user_name=update.effective_user.first_name or "Oyuncu",
                    action_id=action_id,
                )
            elif action == "start":
                result = await self.batak_service.start(
                    chat_id=update.effective_chat.id, user_id=user_id, action_id=action_id
                )
            else:
                if len(parts) != 4 or int(parts[2]) != user_id:
                    await query.answer("Bu kart seçimi başka bir oyuncuya ait.", show_alert=True)
                    return
                result = await self.batak_service.play(
                    chat_id=update.effective_chat.id,
                    user_id=user_id,
                    card_index=int(parts[3]) - 1,
                    action_id=action_id,
                )
        except (TypeError, ValueError) as exc:
            messages = {
                "duplicate_action": "Bu hamle zaten işlendi.",
                "game_expired": "Bu Batak masası artık aktif değil.",
                "active_game": "Bu grupta zaten aktif bir Batak masası var.",
                "game_started": "Batak eli zaten başladı.",
                "table_full": "Masa dolu (en fazla 4 oyuncu).",
                "minimum_players": "Başlamak için en az 2 oyuncu gerekir.",
                "not_owner": "Masayı yalnızca kuran oyuncu başlatabilir.",
                "not_your_turn": "Sıra sende değil.",
                "invalid_card": "Geçerli bir kart seç.",
                "must_follow_suit": "Elinde aynı renkten kart varken o renkten oynamalısın.",
                "game_not_playing": "Bu el artık oynanmıyor.",
            }
            code = normalize_error_code(exc, "GAME_ERROR").lower()
            await query.answer(messages.get(code, "Batak işlemi uygulanamadı."), show_alert=True)
            return
        except Exception as exc:
            logger.error("Batak callback failed: %s", summarize_exception(exc, label="batak_callback"))
            await query.answer("Batak işlemi şu anda uygulanamadı.", show_alert=True)
            return

        state = result.get("state", {})
        markup = None
        if result.get("status") == "created" or state.get("phase") == "lobby":
            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("🃏 Masaya katıl", callback_data="batak:join")],
                [InlineKeyboardButton("🏁 Oyunu başlat", callback_data="batak:start")],
            ])
        elif state.get("phase") == "playing":
            current = int(state.get("current_turn", 0))
            if current == user_id:
                markup = InlineKeyboardMarkup([
                    [InlineKeyboardButton(f"{index + 1}. {card[0]}{card[1]}", callback_data=f"batak:play:{user_id}:{index + 1}")]
                    for index, card in enumerate(state.get("hands", {}).get(str(user_id), []))
                ])
        try:
            await query.edit_message_text(
                sanitize_html(str(result.get("text", "Batak"))),
                reply_markup=markup,
                parse_mode="HTML",
            )
            await query.answer("İşlem uygulandı.")
        except Exception as exc:
            logger.error("Could not refresh Batak message: %s", summarize_exception(exc, label="batak_refresh"))

    async def _handle_batak_callback(self, update, query, context, data):
        user = update.effective_user
        claimed, _ = await self._claim_game_action(update, query, "batak", data)
        if not claimed:
            return
        players = context.chat_data.get('batak_players', [])

        if data == "game_batak_join":
            if user.id in players:
                return
            if len(players) >= 4:
                await query.edit_message_text(f"{query.message.text}\n\n⚠️ Masa dolu!", parse_mode='HTML')
                return

            players.append(user.id)
            context.chat_data['batak_players'] = players
            await persist_game(self.data_port, update.effective_chat.id, "batak", {
                "players": players,
                "names": context.chat_data.get("batak_names", []),
            })

            player_names = []
            for pid in players:
                try:
                    p_member = await context.bot.get_chat_member(update.effective_chat.id, pid)
                    player_names.append(p_member.user.first_name)
                except Exception:
                    player_names.append(f"Oyuncu {len(player_names)+1}")

            names_text = "\n".join([f"{i+1}. {name}" for i, name in enumerate(player_names)])

            if len(players) < 4:
                keyboard = [[InlineKeyboardButton("🃏 Masaya Katıl", callback_data="game_batak_join")]]
                await query.edit_message_text(
                    f"🃏 <b>Batak Masası Kuruldu!</b>\n\nKatılmak için butona basın. ({4 - len(players)} kişi lazım)\n\n👥 <b>Oyuncular:</b>\n{names_text}",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode='HTML'
                )
            else:
                suits = ["Maça", "Kupa", "Karo", "Sinek"]
                koz = random.choice(suits)
                await query.edit_message_text(
                    f"🃏 <b>Batak Masası Başladı!</b> 🔥\n\n👥 <b>Kadro:</b>\n{names_text}\n\n🎯 Koz: <b>{koz}</b>\n\nArel kağıtları dağıtıyor... 🃏✨",
                    parse_mode='HTML'
                )
                context.chat_data['batak_players'] = []
                await delete_game(self.data_port, update.effective_chat.id, "batak")

    async def _handle_bottle_callback(self, update, query, context, data):
        claimed, _ = await self._claim_game_action(update, query, "bottle", data)
        if not claimed:
            return
        players = context.chat_data.get('bottle_players', [])

        if data == "game_bottle_join":
            user = update.effective_user
            if user.id in players:
                return
            players.append(user.id)
            context.chat_data['bottle_players'] = players
            await persist_game(self.data_port, update.effective_chat.id, "bottle", {"players": players})

            player_names = []
            for pid in players:
                try:
                    p_member = await context.bot.get_chat_member(update.effective_chat.id, pid)
                    player_names.append(p_member.user.first_name)
                except Exception:
                    player_names.append(f"Oyuncu {len(player_names)+1}")

            names_text = ", ".join(player_names)
            keyboard = [
                [InlineKeyboardButton("🍾 Masaya Katıl", callback_data="game_bottle_join")],
                [InlineKeyboardButton("🏁 Oyunu Başlat", callback_data="game_bottle_spin")]
            ]
            await query.edit_message_text(
                f"🍾 <b>Şişe Çevirmece Lobisi</b>\n\nKatılmak için butona basın.\n\n👥 <b>Oyuncular:</b> {names_text}",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='HTML'
            )

        elif data == "game_bottle_spin":
            if not players:
                users_list = await self.data_port.get_recent_active_user_names(update.effective_chat.id, 20)
                if not users_list:
                    users_list = [update.effective_user.first_name]
            else:
                player_names = []
                for pid in players:
                    try:
                        p_member = await context.bot.get_chat_member(update.effective_chat.id, pid)
                        player_names.append(p_member.user.first_name)
                    except Exception:
                        player_names.append(f"Oyuncu {len(player_names)+1}")
                users_list = player_names

            result = await self.tool_manager.get_tool("games_tool").execute(game_type="bottle_spin", users_list=users_list)
            await query.edit_message_text(f"🍾 <b>Şişe Çevirmece</b>\n\n{result}", parse_mode='HTML')
            context.chat_data['bottle_players'] = []
            await delete_game(self.data_port, update.effective_chat.id, "bottle")

    @staticmethod
    def _roulette_keyboard(state: dict) -> InlineKeyboardMarkup:
        if state.get("phase") == "lobby":
            return InlineKeyboardMarkup([
                [InlineKeyboardButton("👤 Katıl", callback_data="roulette:join")],
                [InlineKeyboardButton("🏁 Oyunu başlat", callback_data="roulette:start")],
                [InlineKeyboardButton("🛑 Lobiyi kapat", callback_data="roulette:cancel")],
            ])
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔫 Tetiği çek", callback_data="roulette:pull")],
            [InlineKeyboardButton("🛑 Oyunu bitir", callback_data="roulette:cancel")],
        ])

    async def _handle_roulette_callback(self, update, query, context, data: str) -> None:
        service = self.roulette_service
        if service is None:
            await query.answer("Rulet servisi kullanılamıyor.", show_alert=True)
            return
        action = data.split(":", 1)[1] if ":" in data else ""
        user = update.effective_user
        try:
            if action == "join":
                result = await service.join(
                    chat_id=update.effective_chat.id,
                    user_id=user.id,
                    user_name=user.first_name or "Oyuncu",
                    action_id=self._game_action_id(query, user.id, data),
                )
            elif action == "start":
                result = await service.start(
                    chat_id=update.effective_chat.id,
                    user_id=user.id,
                    action_id=self._game_action_id(query, user.id, data),
                )
            elif action == "pull":
                result = await service.pull(
                    chat_id=update.effective_chat.id,
                    user_id=user.id,
                    action_id=self._game_action_id(query, user.id, data),
                )
            elif action == "cancel":
                moderator = await is_admin(update, context, self.sudo_manager, authorization=self.authorization)
                result = await service.cancel(
                    chat_id=update.effective_chat.id,
                    user_id=user.id,
                    moderator=moderator,
                )
            else:
                await query.answer("Geçersiz rulet işlemi.", show_alert=True)
                return
        except ValueError as exc:
            messages = {
                "duplicate_action": "Bu işlem zaten işlendi.",
                "game_not_lobby": "Rulet lobisi artık açık değil.",
                "game_not_playing": "Rulet eli artık oynanmıyor.",
                "lobby_full": "Rulet masası dolu.",
                "need_two_players": "Başlamak için en az iki oyuncu gerekir.",
                "not_owner": "Bu işlemi yalnızca masa sahibi yapabilir.",
                "not_turn": "Sıra sende değil; biraz sabır, namlu bile bekliyor.",
                "game_expired": "Bu rulet masası artık aktif değil.",
            }
            code = normalize_error_code(exc, "GAME_ERROR").lower()
            await query.answer(messages.get(code, "Rulet işlemi uygulanamadı."), show_alert=True)
            return
        except Exception as exc:
            logger.error("Roulette callback failed: %s", summarize_exception(exc, label="roulette_callback"))
            await query.answer("Rulet işlemi şu anda uygulanamadı.", show_alert=True)
            return

        if result.get("status") == "duplicate_action":
            await query.answer("Bu işlem zaten işlendi.", show_alert=True)
            return
        state = result.get("state") or {}
        markup = None if result.get("terminal") else self._roulette_keyboard(state)
        await query.edit_message_text(
            sanitize_html(str(result.get("text", "Rulet"))),
            reply_markup=markup,
            parse_mode="HTML",
        )
        await query.answer("Rulet güncellendi.")

    async def _handle_game_start_callback(self, update, query, context, data):
        # A shared game menu must not silently replace another user's active
        # round. Check the in-memory and durable state before starting one.
        active_key = {
            "game_start_wordgame": "word_game",
            "game_start_hangman": "hangman",
        }.get(data)
        if active_key:
            current = context.chat_data.get(active_key)
            if not current and self.data_port is not None:
                try:
                    current = await self.data_port.get_game_state(
                        int(update.effective_chat.id), active_key
                    )
                except Exception:
                    current = None
            status = str((current or {}).get("status") or "active").casefold()
            if current and status not in {"finished", "cancelled", "expired", "done"}:
                owner_id = int((current or {}).get("owner_id", 0) or 0)
                user_id = int(update.effective_user.id)
                moderator = await is_admin(
                    update, context, self.sudo_manager, authorization=self.authorization
                )
                if user_id != owner_id and not moderator:
                    await self._safe_query_answer(
                        query,
                        "Bu sohbette aktif bir tur var; onu yalnızca kuran kişi veya grup yöneticisi değiştirebilir.",
                        show_alert=True,
                    )
                    return

        from src.handlers.command_handler import CommandHandler
        cmd_handler = CommandHandler(
            self.chat_engine,
            self.sudo_manager,
            None,
            self.tool_manager,
            menu_service=self.menu_service,
            leaderboard_service=self.leaderboard_service,
            economy_service=self.economy_service,
            blackjack_service=self.blackjack_service,
            batak_service=self.batak_service,
            roulette_service=self.roulette_service,
            data_port=self.data_port,
            authorization=self.authorization,
        )

        selected_category = None
        selected_difficulty = None
        if data.startswith("game_word_category:"):
            selected_category = data.split(":", 1)[1]
            data = "game_start_wordgame"
        elif data.startswith("game_word_difficulty:"):
            selected_difficulty = data.split(":", 1)[1]
            data = "game_start_wordgame"
        elif data.startswith("game_hangman_difficulty:"):
            selected_difficulty = data.split(":", 1)[1]
            data = "game_start_hangman"

        game_map = {
            "game_start_anlat": cmd_handler.handle_anlat_command,
            "game_start_hangman": cmd_handler.handle_hangman_command,
            "game_start_wordgame": cmd_handler.handle_wordgame_command,
            "game_start_quiz": cmd_handler.handle_quiz_command,
            "game_start_sise": cmd_handler.handle_sisecevir_command,
            "game_start_batak": cmd_handler.handle_batak_command,
            "game_start_blackjack": cmd_handler.handle_blackjack_command,
            "game_start_rusruleti": cmd_handler.handle_rusruleti_command,
            "game_start_duello": cmd_handler.handle_duello_command,
            "game_start_connect4": cmd_handler.handle_connect4_command,
            "game_start_tictactoe": cmd_handler.handle_tictactoe_command,
            "game_start_gomoku": cmd_handler.handle_gomoku_command,
        }

        if data in game_map:
            class CallbackUpdateProxy:
                def __init__(self, source_update, message):
                    self._source_update = source_update
                    self.message = message

                @property
                def effective_user(self):
                    return self._source_update.effective_user

                @property
                def effective_chat(self):
                    return self._source_update.effective_chat

                @property
                def effective_message(self):
                    return self.message

            previous_args = getattr(context, "args", None)
            if selected_category is not None or selected_difficulty is not None:
                context.args = [
                    value
                    for value in (selected_category, selected_difficulty)
                    if value is not None
                ]
            try:
                await game_map[data](CallbackUpdateProxy(update, query.message), context)
            finally:
                if selected_category is not None or selected_difficulty is not None:
                    context.args = previous_args
            await query.answer()

    async def _load_connect4_state(self, context, chat_id: int):
        state = context.chat_data.get("connect4")
        if state:
            return state
        try:
            state = await self.data_port.get_game_state(chat_id, "connect4")
        except Exception:
            state = None
        if state:
            context.chat_data["connect4"] = state
        return state

    async def _handle_connect4_callback(self, update, query, context, data: str) -> None:
        """Apply one round-scoped Connect Four action.

        The round id and prompt message id are checked before mutation. This
        prevents copied/old Telegram buttons from changing a later round, and
        the action ledger makes Telegram callback retries idempotent.
        """
        parts = str(data).split(":")
        if len(parts) != 3 and len(parts) != 4:
            await self._safe_query_answer(query, "Geçersiz Connect Four işlemi.", show_alert=True)
            return
        round_id = parts[1]
        action = parts[2]
        if action == "col" and len(parts) == 4:
            action_key = f"col:{parts[3]}"
        elif len(parts) == 3 and action in {"join", "start", "stop"}:
            action_key = action
        else:
            await self._safe_query_answer(query, "Geçersiz Connect Four işlemi.", show_alert=True)
            return
        state = await self._load_connect4_state(context, int(update.effective_chat.id))
        if not state or str(state.get("round_id")) != round_id:
            await self._safe_query_answer(query, "Bu oyun turu kapanmış veya yenilenmiş.", show_alert=True)
            return
        try:
            if int(state.get("chat_id")) != int(update.effective_chat.id):
                await self._safe_query_answer(query, "Bu oyun başka bir sohbete ait.", show_alert=True)
                return
        except (TypeError, ValueError):
            await self._safe_query_answer(query, "Oyun durumu bozulmuş; yeni bir tur açın.", show_alert=True)
            return
        prompt_id = int(state.get("prompt_message_id", 0) or 0)
        query_message_id = int(getattr(getattr(query, "message", None), "message_id", 0) or 0)
        if prompt_id and query_message_id and prompt_id != query_message_id:
            await self._safe_query_answer(query, "Bu oyun düğmesi eski bir panele ait.", show_alert=True)
            return
        claimed, _ = await self._claim_game_action(
            update, query, "connect4", f"{round_id}:{action_key}:{state.get('moves', 0)}"
        )
        if not claimed:
            return
        user_id = int(update.effective_user.id)
        if action == "join":
            result = connect4_join(
                state,
                user_id=user_id,
                user_name=str(getattr(update.effective_user, "first_name", None) or "Oyuncu"),
            )
        elif action == "start":
            if user_id != int(state.get("owner_id", 0)):
                await self._safe_query_answer(query, "Oyunu yalnızca başlatan kişi başlatabilir.", show_alert=True)
                return
            result = connect4_start(state)
        elif action == "stop":
            owner_id = int(state.get("owner_id", 0) or 0)
            moderator = await is_admin(update, context, self.sudo_manager, authorization=self.authorization)
            if user_id != owner_id and not moderator:
                await self._safe_query_answer(query, "Bu oyunu yalnızca kuran kişi veya grup yöneticisi kapatabilir.", show_alert=True)
                return
            context.chat_data.pop("connect4", None)
            await delete_game(self.data_port, update.effective_chat.id, "connect4")
            await query.edit_message_text("🛑 Connect Four oyunu kapatıldı.", parse_mode="HTML")
            await self._safe_query_answer(query, "Oyun kapatıldı.")
            return
        else:
            try:
                column = int(parts[3])
            except ValueError:
                await self._safe_query_answer(query, "Geçersiz sütun.", show_alert=True)
                return
            result = connect4_drop(
                state,
                user_id=user_id,
                column=column,
                message_id=query_message_id,
            )
        if not result.get("changed"):
            messages = {
                "already_joined": "Zaten masadasın.",
                "full": "Masa dolu; bu tur iki kişilik.",
                "needs_player": "Başlamak için ikinci oyuncu katılsın.",
                "not_your_turn": "Sıra diğer oyuncuda.",
                "column_full": "Bu sütun dolu.",
                "not_playing": "Tur henüz başlamadı.",
                "invalid_state": "Oyun durumu bozulmuş; turu kapatıp yenisini açın.",
                "invalid_user": "Bu kullanıcı oyuna katılamaz.",
                "invalid_move": "Geçersiz hamle.",
            }
            await self._safe_query_answer(query, messages.get(result.get("status"), "Bu hamle yapılamadı."), show_alert=True)
            return
        await persist_game(self.data_port, update.effective_chat.id, "connect4", state)
        terminal = result.get("status") in {"won", "draw", "expired"}
        await query.edit_message_text(
            render_connect4_text(state),
            reply_markup=connect4_keyboard(state),
            parse_mode="HTML",
        )
        if result.get("status") == "won":
            await self._record_callback_game_result(update, "connect4", 50, "win")
            await self._safe_query_answer(query, "Kazandın! 🏆")
        elif result.get("status") == "draw":
            await self._safe_query_answer(query, "Tahta doldu; beraberlik.")
        elif result.get("status") == "expired":
            await self._safe_query_answer(query, "Süre doldu; bu tur kapandı.")
        elif result.get("status") == "joined":
            await self._safe_query_answer(query, "Masaya katıldın.")
        elif result.get("status") == "started":
            await self._safe_query_answer(query, "Tur başladı.")
        else:
            await self._safe_query_answer(query, "Hamle kaydedildi.")

    async def _load_tictactoe_state(self, context, chat_id: int):
        state = context.chat_data.get("tictactoe")
        if state:
            return state
        try:
            state = await self.data_port.get_game_state(chat_id, "tictactoe")
        except Exception:
            state = None
        if state:
            context.chat_data["tictactoe"] = state
        return state

    async def _handle_tictactoe_callback(self, update, query, context, data: str) -> None:
        """Apply one round-scoped XOX action with persistent idempotency."""
        parts = str(data).split(":")
        if len(parts) not in {3, 4}:
            await self._safe_query_answer(query, "Geçersiz XOX işlemi.", show_alert=True)
            return
        round_id, action = parts[1], parts[2]
        if action in {"cell", "size"} and len(parts) == 4:
            action_key = f"{action}:{parts[3]}"
        elif len(parts) == 3 and action in {"join", "start", "stop", "restart"}:
            action_key = action
        else:
            await self._safe_query_answer(query, "Geçersiz XOX işlemi.", show_alert=True)
            return
        chat_id = int(update.effective_chat.id)
        state = await self._load_tictactoe_state(context, chat_id)
        if not state or str(state.get("round_id")) != round_id:
            await self._safe_query_answer(query, "Bu oyun turu kapanmış veya yenilenmiş.", show_alert=True)
            return
        try:
            if int(state.get("chat_id")) != chat_id:
                await self._safe_query_answer(query, "Bu oyun başka bir sohbete ait.", show_alert=True)
                return
        except (TypeError, ValueError):
            await self._safe_query_answer(query, "Oyun durumu bozulmuş; yeni bir tur açın.", show_alert=True)
            return
        prompt_id = int(state.get("prompt_message_id", 0) or 0)
        message_id = int(getattr(getattr(query, "message", None), "message_id", 0) or 0)
        if prompt_id and message_id and prompt_id != message_id:
            await self._safe_query_answer(query, "Bu oyun düğmesi eski bir panele ait.", show_alert=True)
            return
        expected_thread = state.get("thread_id")
        callback_thread = getattr(getattr(query, "message", None), "message_thread_id", None)
        if expected_thread is not None and callback_thread is not None:
            try:
                if int(expected_thread) != int(callback_thread):
                    await self._safe_query_answer(query, "Bu düğme başka bir forum konusuna ait.", show_alert=True)
                    return
            except (TypeError, ValueError):
                await self._safe_query_answer(query, "Forum konusu doğrulanamadı.", show_alert=True)
                return
        if action == "size" and int(update.effective_user.id) != int(state.get("owner_id", 0) or 0):
            await self._safe_query_answer(query, "Tahta boyutunu yalnızca oyunu kuran kişi seçebilir.", show_alert=True)
            return
        claimed, _ = await self._claim_game_action(
            update, query, "tictactoe", f"{round_id}:{action_key}:{state.get('moves', 0)}"
        )
        if not claimed:
            return
        user_id = int(update.effective_user.id)
        if action == "size":
            try:
                board_size = int(parts[3])
            except (TypeError, ValueError):
                await self._safe_query_answer(query, "Geçersiz tahta boyutu.", show_alert=True)
                return
            if board_size not in XOX_BOARD_SIZES:
                await self._safe_query_answer(query, "Tahta boyutu 6, 7 veya 8 olabilir.", show_alert=True)
                return
            if str(state.get("status") or "lobby") != "lobby":
                await self._safe_query_answer(query, "Tahta boyutu yalnızca lobi açıkken seçilebilir.", show_alert=True)
                return
            result = tictactoe_select_size(state, user_id=user_id, board_size=board_size)
            if not result.get("changed"):
                await self._safe_query_answer(query, "Bu tahta boyutu seçilemedi.", show_alert=True)
                return
            state["board_size"] = board_size
            await persist_game(self.data_port, chat_id, "tictactoe", state)
            await query.edit_message_text(
                render_tictactoe_text(state),
                reply_markup=tictactoe_surface_keyboard(state),
                parse_mode="HTML",
            )
            await self._safe_query_answer(query, f"{board_size}×{board_size} tahta seçildi.")
            return
        if action == "restart":
            if user_id != int(state.get("owner_id", 0) or 0):
                await self._safe_query_answer(query, "Yeni turu yal\u0131zca oyunu kuran ki\u015fi ba\u015flatabilir.", show_alert=True)
                return
            result = tictactoe_new_round(state, user_id=user_id)
            if not result.get("changed"):
                messages = {
                    "not_owner": "Yeni turu yal\u0131zca oyunu kuran ki\u015fi ba\u015flatabilir.",
                    "needs_player": "Yeni tur i\u00e7in iki oyuncu gerekli.",
                    "invalid_state": "Oyun durumu bozulmu\u015f; yeni bir XOX lobisi a\u00e7\u0131n.",
                }
                await self._safe_query_answer(query, messages.get(result.get("status"), "Yeni tur ba\u015flat\u0131lamad\u0131."), show_alert=True)
                return
            await persist_game(self.data_port, chat_id, "tictactoe", state)
            await query.edit_message_text(
                render_tictactoe_text(state),
                reply_markup=tictactoe_surface_keyboard(state),
                parse_mode="HTML",
            )
            await self._safe_query_answer(query, "Temiz bir tur ba\u015flad\u0131.")
            return
        if action == "join":
            result = tictactoe_join(
                state,
                user_id=user_id,
                user_name=str(getattr(update.effective_user, "first_name", None) or "Oyuncu"),
            )
        elif action == "start":
            if user_id != int(state.get("owner_id", 0)):
                await self._safe_query_answer(query, "Oyunu yalnızca kuran kişi başlatabilir.", show_alert=True)
                return
            result = tictactoe_start(state)
        elif action == "stop":
            owner_id = int(state.get("owner_id", 0) or 0)
            moderator = await is_admin(update, context, self.sudo_manager, authorization=self.authorization)
            if user_id != owner_id and not moderator:
                await self._safe_query_answer(query, "Bu oyunu yalnızca kuran kişi veya grup yöneticisi kapatabilir.", show_alert=True)
                return
            context.chat_data.pop("tictactoe", None)
            await delete_game(self.data_port, chat_id, "tictactoe")
            await query.edit_message_text("🛑 XOX oyunu kapatıldı.", parse_mode="HTML")
            await self._safe_query_answer(query, "Oyun kapatıldı.")
            return
        else:
            try:
                cell = int(parts[3])
            except (TypeError, ValueError):
                await self._safe_query_answer(query, "Geçersiz kare.", show_alert=True)
                return
            result = tictactoe_move(state, user_id=user_id, cell=cell, message_id=message_id)
        if not result.get("changed"):
            messages = {
                "already_joined": "Zaten masadasın.",
                "full": "Masa dolu; bu tur iki kişilik.",
                "needs_player": "Başlamak için ikinci oyuncu katılsın.",
                "not_your_turn": "Sıra diğer oyuncuda.",
                "occupied": "O kare dolu.",
                "invalid_cell": "Geçersiz kare.",
                "not_playing": "Tur henüz başlamadı.",
                "invalid_state": "Oyun durumu bozulmuş; turu kapatıp yenisini açın.",
            }
            await self._safe_query_answer(query, messages.get(result.get("status"), "Bu hamle yapılamadı."), show_alert=True)
            return
        await persist_game(self.data_port, chat_id, "tictactoe", state)
        await query.edit_message_text(
            render_tictactoe_text(state),
            reply_markup=tictactoe_surface_keyboard(state),
            parse_mode="HTML",
        )
        status = result.get("status")
        if status == "won":
            await self._record_callback_game_result(update, "tictactoe", 30, "win")
            await self._safe_query_answer(query, "Kazandın! 🏆")
        elif status == "draw":
            await self._safe_query_answer(query, "Tahta doldu; beraberlik.")
        elif status == "expired":
            await self._safe_query_answer(query, "Süre doldu; bu tur kapandı.")
        elif status == "joined":
            await self._safe_query_answer(query, "Masaya katıldın.")
        elif status == "started":
            await self._safe_query_answer(query, "Tur başladı.")
        else:
            await self._safe_query_answer(query, "Hamle kaydedildi.")

    async def _load_gomoku_state(self, context, chat_id: int):
        state = context.chat_data.get("gomoku")
        if state:
            return state
        try:
            state = await self.data_port.get_game_state(chat_id, "gomoku")
        except Exception:
            state = None
        if state:
            context.chat_data["gomoku"] = state
        return state

    async def _handle_gomoku_callback(self, update, query, context, data: str) -> None:
        """Apply one round-scoped Gomoku action with restart-safe idempotency."""
        parts = str(data).split(":")
        if len(parts) not in {3, 4}:
            await self._safe_query_answer(query, "Geçersiz Gomoku işlemi.", show_alert=True)
            return
        round_id, action = parts[1], parts[2]
        if action in {"cell", "size"} and len(parts) == 4:
            action_key = f"{action}:{parts[3]}"
        elif len(parts) == 3 and action in {"join", "start", "stop"}:
            action_key = action
        else:
            await self._safe_query_answer(query, "Geçersiz Gomoku işlemi.", show_alert=True)
            return
        chat_id = int(update.effective_chat.id)
        state = await self._load_gomoku_state(context, chat_id)
        if not state or str(state.get("round_id")) != round_id:
            await self._safe_query_answer(query, "Bu oyun turu kapanmış veya yenilenmiş.", show_alert=True)
            return
        try:
            if int(state.get("chat_id")) != chat_id:
                await self._safe_query_answer(query, "Bu oyun başka bir sohbete ait.", show_alert=True)
                return
        except (TypeError, ValueError):
            await self._safe_query_answer(query, "Oyun durumu bozulmuş; yeni bir tur açın.", show_alert=True)
            return
        prompt_id = int(state.get("prompt_message_id", 0) or 0)
        message_id = int(getattr(getattr(query, "message", None), "message_id", 0) or 0)
        if prompt_id and message_id and prompt_id != message_id:
            await self._safe_query_answer(query, "Bu oyun düğmesi eski bir panele ait.", show_alert=True)
            return
        expected_thread = state.get("thread_id")
        callback_thread = getattr(getattr(query, "message", None), "message_thread_id", None)
        if expected_thread is not None and callback_thread is not None:
            try:
                if int(expected_thread) != int(callback_thread):
                    await self._safe_query_answer(query, "Bu düğme başka bir forum konusuna ait.", show_alert=True)
                    return
            except (TypeError, ValueError):
                await self._safe_query_answer(query, "Forum konusu doğrulanamadı.", show_alert=True)
                return
        user_id = int(update.effective_user.id)
        if action == "size" and user_id != int(state.get("owner_id", 0) or 0):
            await self._safe_query_answer(query, "Tahta boyutunu yalnızca oyunu kuran kişi seçebilir.", show_alert=True)
            return
        claimed, _ = await self._claim_game_action(
            update, query, "gomoku", f"{round_id}:{action_key}:{state.get('moves', 0)}"
        )
        if not claimed:
            return
        if action == "size":
            try:
                board_size = int(parts[3])
            except (TypeError, ValueError):
                await self._safe_query_answer(query, "Geçersiz tahta boyutu.", show_alert=True)
                return
            if board_size not in GOMOKU_BOARD_SIZES:
                await self._safe_query_answer(query, "Tahta boyutu 6, 8, 10, 12 veya 15 olabilir.", show_alert=True)
                return
            result = gomoku_select_size(state, user_id=user_id, board_size=board_size)
            if not result.get("changed"):
                await self._safe_query_answer(query, "Tahta boyutu yalnızca lobi açıkken değiştirilebilir.", show_alert=True)
                return
            await persist_game(self.data_port, chat_id, "gomoku", state)
            await query.edit_message_text(
                render_gomoku_text(state), reply_markup=gomoku_keyboard(state), parse_mode="HTML"
            )
            await self._safe_query_answer(query, f"{board_size}×{board_size} tahta seçildi.")
            return
        if action == "join":
            result = gomoku_join(
                state, user_id=user_id,
                user_name=str(getattr(update.effective_user, "first_name", None) or "Oyuncu"),
            )
        elif action == "start":
            if user_id != int(state.get("owner_id", 0) or 0):
                await self._safe_query_answer(query, "Oyunu yalnızca kuran kişi başlatabilir.", show_alert=True)
                return
            result = gomoku_start(state)
        elif action == "stop":
            owner_id = int(state.get("owner_id", 0) or 0)
            moderator = await is_admin(update, context, self.sudo_manager, authorization=self.authorization)
            if user_id != owner_id and not moderator:
                await self._safe_query_answer(query, "Bu oyunu yalnızca kuran kişi veya grup yöneticisi kapatabilir.", show_alert=True)
                return
            context.chat_data.pop("gomoku", None)
            await delete_game(self.data_port, chat_id, "gomoku")
            await query.edit_message_text("🛑 Gomoku oyunu kapatıldı.", parse_mode="HTML")
            await self._safe_query_answer(query, "Oyun kapatıldı.")
            return
        else:
            try:
                cell = int(parts[3])
            except (TypeError, ValueError):
                await self._safe_query_answer(query, "Geçersiz hücre.", show_alert=True)
                return
            result = gomoku_move(
                state, user_id=user_id, cell=cell, message_id=message_id,
                action_id=f"telegram:{round_id}:{message_id}:{user_id}:{cell}",
            )
        if not result.get("changed"):
            messages = {
                "already_joined": "Zaten masadasın.",
                "full": "Masa dolu; bu tur iki kişilik.",
                "needs_player": "Başlamak için ikinci oyuncu katılsın.",
                "not_your_turn": "Sıra diğer oyuncuda.",
                "occupied": "Bu hücre dolu.",
                "invalid_coordinate": "Geçersiz hücre.",
                "invalid_cell": "Geçersiz hücre.",
                "not_playing": "Tur henüz başlamadı.",
                "invalid_state": "Oyun durumu bozulmuş; turu kapatıp yenisini açın.",
                "duplicate_action": "Bu hamle zaten işlendi.",
                "finished": "Bu tur zaten tamamlandı.",
            }
            await self._safe_query_answer(query, messages.get(result.get("status"), "Bu hamle yapılamadı."), show_alert=True)
            return
        await persist_game(self.data_port, chat_id, "gomoku", state)
        await query.edit_message_text(
            render_gomoku_text(state), reply_markup=gomoku_keyboard(state), parse_mode="HTML"
        )
        status = result.get("status")
        if status == "won":
            await self._record_callback_game_result(update, "gomoku", 100, "win")
            await self._safe_query_answer(query, "Beşliyi tamamladın! 🏆")
        elif status == "draw":
            await self._safe_query_answer(query, "Tahta doldu; beraberlik.")
        elif status == "expired":
            await self._safe_query_answer(query, "Süre doldu; bu tur kapandı.")
        elif status == "joined":
            await self._safe_query_answer(query, "Masaya katıldın.")
        elif status == "started":
            await self._safe_query_answer(query, "Tur başladı.")
        elif status == "selected":
            await self._safe_query_answer(query, "Tahta boyutu güncellendi.")
        else:
            await self._safe_query_answer(query, "Hamle kaydedildi.")

    async def _load_hangman_state(self, context, chat_id: int):
        state = context.chat_data.get("hangman")
        if state:
            return state
        try:
            state = await self.data_port.get_game_state(chat_id, "hangman")
        except Exception:
            state = None
        if state:
            context.chat_data["hangman"] = state
        return state

    async def _load_word_game_state(self, context, chat_id: int):
        state = context.chat_data.get("word_game")
        if state:
            return state
        try:
            state = await self.data_port.get_game_state(chat_id, "word_game")
        except Exception:
            state = None
        if state:
            context.chat_data["word_game"] = state
        return state

    async def _handle_word_game_control(self, update, query, context, data: str) -> None:
        action = str(data).split(":", 1)[0]
        state = await self._load_word_game_state(context, update.effective_chat.id)
        if not state:
            await query.answer("Aktif kelime oyunu yok.", show_alert=True)
            return
        if ":" in data and str(state.get("round_id") or "") != str(data).split(":", 1)[1]:
            await self._safe_query_answer(query, "Bu düğme eski bir tura ait.", show_alert=True)
            return

        sequence = (
            int(state.get("shuffle_count", 0) or 0)
            if action == "word_game_shuffle"
            else int(state.get("hints_used", 0) or 0)
        )
        claimed, _ = await self._claim_game_action(
            update, query, "word_game", f"{action}:{sequence}"
        )
        if not claimed:
            return

        if action == "word_game_shuffle":
            shuffle_rack(state)
            await persist_game(self.data_port, update.effective_chat.id, "word_game", state)
            await query.edit_message_text(
                render_word_game_text(state),
                reply_markup=word_game_keyboard(state),
                parse_mode="HTML",
            )
            await query.answer("Harfler karıştırıldı.")
            return

        result = reveal_word_hint(state)
        if result["status"] == "no_hint":
            await query.answer("Açılacak ipucu kalmadı.", show_alert=True)
            return
        await persist_game(self.data_port, update.effective_chat.id, "word_game", state)
        hint = result["hint"]
        note = (
            f"💡 İpucu: {hint['length']} harfli, "
            f"{hint['first']} ile başlıyor ve {hint['last']} ile bitiyor."
        )
        await query.edit_message_text(
            render_word_game_text(state, note=note),
            reply_markup=word_game_keyboard(state),
            parse_mode="HTML",
        )
        await query.answer("İpucu uygulandı.")

    async def _handle_word_game_stop(self, update, query, context, data: str = "word_game_stop") -> None:
        claimed, _ = await self._claim_game_action(update, query, "word_game", "word_game_stop")
        if not claimed:
            return
        state = await self._load_word_game_state(context, update.effective_chat.id)
        if not state:
            await query.answer("Aktif kelime oyunu yok.", show_alert=True)
            return
        if ":" in data and str(state.get("round_id") or "") != str(data).split(":", 1)[1]:
            await self._safe_query_answer(query, "Bu düğme eski bir tura ait.", show_alert=True)
            return
        owner_id = state.get("owner_id")
        if owner_id and int(owner_id) != int(update.effective_user.id):
            if not await is_admin(update, context, self.sudo_manager, authorization=self.authorization):
                await query.answer(
                    "Bu oyunu yalnızca başlatan kişi veya grup yöneticisi kapatabilir.",
                    show_alert=True,
                )
                return
        context.chat_data.pop("word_game", None)
        await delete_game(self.data_port, update.effective_chat.id, "word_game")
        await query.edit_message_text("🛑 Kelime oyunu bitirildi.", parse_mode="HTML")
        await query.answer("Oyun bitirildi.")

    async def _record_callback_game_result(self, update, game_type: str, score: int, result_key: str) -> None:
        """Record an inline game result with the same idempotent reward path."""
        if not self.leaderboard_service or not update.effective_user or not update.effective_chat:
            return
        message = getattr(update, "effective_message", None)
        message_id = getattr(message, "message_id", 0) or 0
        user = update.effective_user
        event_id = f"{game_type}:{update.effective_chat.id}:{message_id}:{user.id}:{result_key}"
        try:
            accepted = await self.leaderboard_service.record_score(
                user_id=int(user.id),
                chat_id=int(update.effective_chat.id),
                game_type=game_type,
                score=int(score),
                user_name=getattr(user, "full_name", None) or getattr(user, "first_name", None),
                event_id=event_id,
            )
            if accepted and self.economy_service:
                await self.economy_service.credit(
                    int(user.id),
                    int(update.effective_chat.id),
                    amount=max(1, int(score) // 2),
                    reason=f"{game_type}_reward",
                    event_id=f"coin:{event_id}",
                )
        except Exception:
            logger.warning("Could not record inline %s game result", game_type, exc_info=True)

    async def _finish_hangman_callback(self, update, query, context, state: dict, status: str) -> None:
        if status == "won":
            elapsed = max(0.0, time.time() - float(state.get("start_time", time.time())))
            score = calculate_hangman_score(
                elapsed_seconds=elapsed,
                difficulty=state.get("difficulty", "normal"),
                lives_remaining=state.get("lives", 0),
                max_lives=state.get("max_lives", 6),
                wrong_guesses=state.get("wrong_guesses", 0),
                hints_used=state.get("hints_used", 0),
            )
            await self._record_callback_game_result(update, "hangman", score, "win")
        elif status == "lost":
            state["hidden"] = list(str(state.get("word", "")).upper())
        context.chat_data.pop("hangman", None)
        await delete_game(self.data_port, update.effective_chat.id, "hangman")
        await query.edit_message_text(render_hangman_text(state, result=status), parse_mode="HTML")
        await query.answer("Tur tamamlandı.")

    async def _handle_hangman_guess_callback(self, update, query, context, data: str) -> None:
        claimed, _ = await self._claim_game_action(update, query, "hangman", data)
        if not claimed:
            return
        state = await self._load_hangman_state(context, update.effective_chat.id)
        if not state:
            await query.answer("Bu adam asmaca turu sona ermiş.", show_alert=True)
            return
        parts = data.split(":")
        if len(parts) == 3:
            if str(state.get("round_id") or "") != parts[1]:
                await self._safe_query_answer(query, "Bu düğme eski bir tura ait.", show_alert=True)
                return
            token = parts[2]
        else:
            token = parts[1]
        result = apply_guess(state, token)
        if result["status"] in {"invalid", "duplicate"}:
            await query.answer(
                "Bu harfi zaten denedin." if result["status"] == "duplicate" else "Geçersiz harf.",
                show_alert=result["status"] == "invalid",
            )
            return
        if result["status"] in {"won", "lost"}:
            await self._finish_hangman_callback(update, query, context, state, result["status"])
            return
        await persist_game(self.data_port, update.effective_chat.id, "hangman", state)
        await query.edit_message_text(
            render_hangman_text(state),
            reply_markup=hangman_keyboard(state),
            parse_mode="HTML",
        )
        await query.answer("Doğru harf." if result["status"] == "hit" else "Bu harf kelimede yok.")

    async def _handle_hangman_hint_callback(self, update, query, context, data: str = "hangman_hint") -> None:
        state = await self._load_hangman_state(context, update.effective_chat.id)
        if not state:
            await query.answer("Aktif adam asmaca turu yok.", show_alert=True)
            return
        if ":" in data and str(state.get("round_id") or "") != str(data).split(":", 1)[1]:
            await self._safe_query_answer(query, "Bu düğme eski bir tura ait.", show_alert=True)
            return
        sequence = int(state.get("hints_used", 0) or 0)
        claimed, _ = await self._claim_game_action(
            update, query, "hangman", f"hangman_hint:{sequence}"
        )
        if not claimed:
            return
        result = reveal_hint(state)
        if result["status"] == "no_hint":
            await query.answer("Açılacak harf kalmadı.", show_alert=True)
            return
        if result["status"] == "won":
            await self._finish_hangman_callback(update, query, context, state, "won")
            return
        await persist_game(self.data_port, update.effective_chat.id, "hangman", state)
        await query.edit_message_text(
            render_hangman_text(state),
            reply_markup=hangman_keyboard(state),
            parse_mode="HTML",
        )
        await query.answer(f"İpucu: {str(result.get('letter', '')).upper()}")

    async def _handle_hangman_stop_callback(self, update, query, context, data: str = "hangman_stop") -> None:
        state = await self._load_hangman_state(context, update.effective_chat.id)
        if not state:
            await query.answer("Aktif adam asmaca turu yok.", show_alert=True)
            return
        if ":" in data and str(state.get("round_id") or "") != str(data).split(":", 1)[1]:
            await self._safe_query_answer(query, "Bu düğme eski bir tura ait.", show_alert=True)
            return
        owner_id = state.get("owner_id")
        if owner_id and int(owner_id) != int(update.effective_user.id):
            if not await is_admin(update, context, self.sudo_manager, authorization=self.authorization):
                await query.answer("Bu turu yalnızca başlatan kişi veya grup yöneticisi kapatabilir.", show_alert=True)
                return
        context.chat_data.pop("hangman", None)
        await delete_game(self.data_port, update.effective_chat.id, "hangman")
        await query.edit_message_text("🛑 Adam asmaca turu kapatıldı.", parse_mode="HTML")
        await query.answer("Tur kapatıldı.")

    async def _handle_game_stop(self, update, query, context):
        if self.roulette_service is not None:
            try:
                moderator = await is_admin(update, context, self.sudo_manager, authorization=self.authorization)
                await self.roulette_service.cancel(
                    chat_id=update.effective_chat.id,
                    user_id=update.effective_user.id,
                    moderator=moderator,
                )
            except ValueError as exc:
                if str(exc) not in {"game_expired", "not_owner"}:
                    logger.warning("Roulette stop rejected: %s", summarize_exception(exc, label="roulette_stop"))

        game_keys = ['word_guess', 'hangman', 'word_game', 'connect4', 'tictactoe', 'gomoku', 'batak_players', 'sise_lobby', 'bottle_players']
        cleared = False
        for key in game_keys:
            if key in context.chat_data:
                del context.chat_data[key]
                cleared = True
        for game_type in ("word_guess", "hangman", "word_game", "connect4", "tictactoe", "gomoku", "batak", "bottle", "quiz"):
            await delete_game(self.data_port, update.effective_chat.id, game_type)

        await query.answer("✅ Oyunlar sonlandırıldı!" if cleared else "🤔 Aktif oyun yok!")

    async def _load_word_guess_state(self, context, chat_id: int):
        state = context.chat_data.get("word_guess")
        if state:
            return state
        try:
            state = await self.data_port.get_game_state(chat_id, "word_guess")
        except Exception:
            state = None
        if state:
            context.chat_data["word_guess"] = state
        return state

    async def _handle_word_guess_hint(self, update, query, context, data: str = "word_hint") -> None:
        state = await self._load_word_guess_state(context, update.effective_chat.id)
        if not state:
            await query.answer("Aktif anlatmaca turu yok.", show_alert=True)
            return
        if ":" in data and str(state.get("round_id") or "") != str(data).split(":", 1)[1]:
            await self._safe_query_answer(query, "Bu düğme eski bir tura ait.", show_alert=True)
            return
        hints = state.get("hints", []) or []
        index = int(state.get("hint_index", 0) or 0)
        # The same inline panel is intentionally reusable. Include the hint
        # index in the idempotency key so later hints are not mistaken for a
        # Telegram retry of the first click.
        claimed, _ = await self._claim_game_action(
            update, query, "word_guess", f"word_hint:{index}"
        )
        if not claimed:
            return
        if index >= len(hints):
            await query.answer("Açılacak ipucu kalmadı.", show_alert=True)
            return
        hint = str(hints[index])
        state["hint_index"] = index + 1
        await persist_game(self.data_port, update.effective_chat.id, "word_guess", state)
        await query.edit_message_text(
            render_word_guess_text(
                state,
                note=f"💡 İpucu {index + 1}: {hint}",
            ),
            reply_markup=word_guess_keyboard(state),
            parse_mode="HTML",
        )
        await query.answer("İpucu açıldı.")

    async def _handle_word_guess_stop(self, update, query, context, data: str = "word_guess_stop") -> None:
        claimed, _ = await self._claim_game_action(update, query, "word_guess", "word_guess_stop")
        if not claimed:
            return
        state = await self._load_word_guess_state(context, update.effective_chat.id)
        if not state:
            await query.answer("Aktif anlatmaca turu yok.", show_alert=True)
            return
        if ":" in data and str(state.get("round_id") or "") != str(data).split(":", 1)[1]:
            await self._safe_query_answer(query, "Bu düğme eski bir tura ait.", show_alert=True)
            return
        owner_id = state.get("owner_id")
        if owner_id and int(owner_id) != int(update.effective_user.id):
            if not await is_admin(update, context, self.sudo_manager, authorization=self.authorization):
                await query.answer(
                    "Bu turu yalnızca başlatan kişi veya grup yöneticisi kapatabilir.",
                    show_alert=True,
                )
                return
        chat_id = update.effective_chat.id
        self._remove_job_by_name(context, f"word_guess_timeout_{chat_id}")
        self._remove_job_by_name(context, f"word_guess_hint_{chat_id}")
        self._remove_job_by_name(context, f"word_guess_skip_{chat_id}")
        context.chat_data.pop("word_guess", None)
        await delete_game(self.data_port, chat_id, "word_guess")
        await query.edit_message_text("🛑 Kelime anlatmaca turu bitirildi.", parse_mode="HTML")
        await query.answer("Tur bitirildi.")

    async def _handle_word_skip(self, update, query, context, data: str = "word_skip"):
        claimed, _ = await self._claim_game_action(update, query, "word_guess", "word_skip")
        if not claimed:
            return
        chat_id = update.effective_chat.id
        game = await self._load_word_guess_state(context, chat_id)
        if not game:
            await query.answer("Aktif oyun yok!", show_alert=True)
            return
        if ":" in data and str(game.get("round_id") or "") != str(data).split(":", 1)[1]:
            await self._safe_query_answer(query, "Bu düğme eski bir tura ait.", show_alert=True)
            return

        word = html.escape(str(game.get('word', '???')))

        self._remove_job_by_name(context, f"word_guess_timeout_{chat_id}")
        self._remove_job_by_name(context, f"word_guess_hint_{chat_id}")
        self._remove_job_by_name(context, f"word_guess_skip_{chat_id}")

        del context.chat_data['word_guess']
        await delete_game(self.data_port, chat_id, "word_guess")

        await query.edit_message_text(
            f"⏭️ <b>Pas Geçildi!</b>\n\n"
            f"Doğru cevap: <b>{word}</b>\n\n"
            f"🔄 <i>Yeni kelime 3 saniye sonra gelecek...</i>",
            parse_mode='HTML'
        )
        await query.answer()

        context.job_queue.run_once(
            lambda ctx: self._auto_next_round(chat_id, ctx),
            when=3,
            chat_id=chat_id,
            name=f"auto_next_anlat_{chat_id}"
        )

    async def _auto_next_round(self, chat_id, context):
        from src.handlers.command_handler import CommandHandler
        cmd_handler = CommandHandler(
            self.chat_engine,
            self.sudo_manager,
            None,
            self.tool_manager,
            menu_service=self.menu_service,
            leaderboard_service=self.leaderboard_service,
        )
        await cmd_handler._start_word_guess_game(chat_id, context)

    def _remove_job_by_name(self, context, name):
        remove_job_by_name(context, name)

    async def _handle_quiz_category(self, update, query, context, data):
        claimed, _ = await self._claim_game_action(update, query, "quiz", data)
        if not claimed:
            return
        from src.games.quiz import QuizGame
        quiz_game = QuizGame()

        category = data.replace("quiz_cat_", "")
        q_data = quiz_game.get_question(category)
        round_id = uuid.uuid4().hex[:12]

        context.chat_data['quiz_answer'] = q_data['answer']
        context.chat_data['quiz_question'] = q_data['question']
        context.chat_data['quiz_round_id'] = round_id
        await self.data_port.save_game_state(update.effective_chat.id, "quiz", {
            "schema_version": 2,
            "round_id": round_id,
            "answer": q_data['answer'],
            "question": q_data['question'],
            "category": q_data.get('category', category),
            "options": list(q_data.get('options') or []),
        })

        options = q_data['options']
        keyboard = []
        for i in range(0, len(options), 2):
            row = []
            for j in range(i, min(i + 2, len(options))):
                row.append(InlineKeyboardButton(options[j], callback_data=f"quiz_ans:{round_id}:{j}"))
            keyboard.append(row)

        await query.edit_message_text(
            f"🎯 <b>Bilgi Yarışması</b> — {category.upper()}\n\n"
            f"❓ <b>{q_data['question']}</b>\n\n"
            f"Doğru cevabı seçin! ⏳",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='HTML'
        )
        await query.answer()

    async def _handle_quiz_answer(self, update, query, context, data):
        claimed, _ = await self._claim_game_action(update, query, "quiz", data)
        if not claimed:
            return
        parts = data.split(":")
        selected_index = None
        round_id = None
        if len(parts) == 3 and parts[0] == "quiz_ans":
            round_id = parts[1]
            try:
                selected_index = int(parts[2])
            except (TypeError, ValueError):
                await self._safe_query_answer(query, "Geçersiz quiz seçimi.", show_alert=True)
                return
        else:
            legacy = data.split("_", 3)
            selected_index = None
            selected = legacy[3] if len(legacy) > 3 else (legacy[2] if len(legacy) > 2 else "")
        correct = context.chat_data.get('quiz_answer', '')
        question = context.chat_data.get('quiz_question', '')
        saved = await self.data_port.get_game_state(update.effective_chat.id, "quiz") or {}
        if round_id and str(saved.get("round_id") or context.chat_data.get("quiz_round_id")) != round_id:
            await self._safe_query_answer(query, "Bu quiz düğmesi eski bir tura ait.", show_alert=True)
            return
        options = saved.get("options") or []
        if selected_index is not None:
            if selected_index < 0 or selected_index >= len(options):
                await self._safe_query_answer(query, "Geçersiz quiz seçimi.", show_alert=True)
                return
            selected = str(options[selected_index])
        if not correct:
            correct = saved.get("answer", "")
            question = saved.get("question", "")
        if not correct:
            await query.answer("Bu quiz oturumu sona ermiş.", show_alert=True)
            return

        user_name = update.effective_user.first_name

        if selected.startswith(correct[:20]):
            score_accepted = False
            if self.leaderboard_service:
                event_id = f"quiz:{update.effective_chat.id}:{getattr(query.message, 'message_id', data)}:{update.effective_user.id}"
                try:
                    score_accepted = await self.leaderboard_service.record_score(
                        update.effective_user.id,
                        update.effective_chat.id,
                        "quiz",
                        10,
                        user_name=user_name,
                        event_id=event_id,
                    )
                except Exception:
                    logger.warning("Could not record quiz score")
                if score_accepted and self.economy_service:
                    try:
                        await self.economy_service.credit(
                            update.effective_user.id,
                            update.effective_chat.id,
                            5,
                            reason="quiz_reward",
                            event_id=f"quiz-coin:{event_id}",
                        )
                    except Exception:
                        logger.warning("Could not record quiz economy reward")
            await query.edit_message_text(
                f"✅ <b>DOĞRU!</b> 🎉\n\n"
                f"❓ {question}\n"
                f"📝 Cevap: <b>{correct}</b>\n\n"
                f"🏆 Tebrikler <b>{user_name}</b>!",
                parse_mode='HTML'
            )
        else:
            await query.edit_message_text(
                f"❌ <b>YANLIŞ!</b>\n\n"
                f"❓ {question}\n"
                f"📝 Doğru cevap: <b>{correct}</b>\n\n"
                f"Tekrar denemek için /quiz yazın! 💪",
                parse_mode='HTML'
            )

        context.chat_data.pop('quiz_answer', None)
        context.chat_data.pop('quiz_question', None)
        context.chat_data.pop('quiz_round_id', None)
        await self.data_port.delete_game_state(update.effective_chat.id, "quiz")
        await query.answer()

    async def _handle_bim_page_callback(self, update, query, data: str):
        """Render a cached BİM catalogue page without doing HTTP in a callback."""
        parts = data.split(":")
        if len(parts) != 3:
            await query.edit_message_text("⚠️ BİM sayfası bağlantısı geçersiz.")
            return
        try:
            owner_id, page = int(parts[1]), int(parts[2])
        except ValueError:
            await query.edit_message_text("⚠️ BİM sayfası bağlantısı geçersiz.")
            return
        if owner_id != update.effective_user.id:
            await query.answer("Bu katalog başka bir kullanıcıya ait.", show_alert=True)
            return
        try:
            result = await self.chat_engine.feature_executor.execute("bim_deals", {})
            text, markup = bim_page_view(result, owner_id=owner_id, page=page)
            await query.edit_message_text(
                text,
                reply_markup=markup,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception:
            logger.exception("BIM page rendering failed")
            await query.edit_message_text("⚠️ BİM kataloğu şu an yenilenemedi; biraz sonra tekrar dene.")

    async def _handle_sticker_packs_refresh(self, update, query, data: str) -> None:
        """Refresh the owner's pack list without exposing another user's data."""
        try:
            owner_id = int(data.split(":", 1)[1])
        except (IndexError, ValueError):
            await query.answer("Geçersiz paket listesi.", show_alert=True)
            return
        if owner_id != int(update.effective_user.id):
            await query.answer("Bu liste başka bir kullanıcıya ait.", show_alert=True)
            return
        try:
            packs = await self.data_port.get_user_packs(owner_id)
        except Exception as exc:
            logger.error("Sticker pack refresh failed: %s", summarize_exception(exc, label="sticker_pack_refresh"))
            await query.answer("Paket listesi şu an yenilenemedi.", show_alert=True)
            return
        if not packs:
            await query.edit_message_text(
                "📭 Henüz kayıtlı bir sticker paketin yok. Bir sticker'a yanıt verip /sticker ile başlayabilirsin."
            )
            return
        rows = []
        lines = ["📦 <b>SİZİN STICKER PAKETLERİNİZ</b> 🌟", ""]
        for index, pack in enumerate(packs, 1):
            pack_name = str(pack.get("pack_name") or "").strip()
            if not pack_name:
                continue
            title = html.escape(str(pack.get("pack_title") or pack_name))
            link = f"https://t.me/addstickers/{pack_name}"
            lines.append(f"{index}. <b>{title}</b> — <a href=\"{html.escape(link, quote=True)}\">Paketi aç/gör</a>")
            rows.append([InlineKeyboardButton(f"📦 {pack.get('pack_title') or pack_name}", url=link)])
        rows.append([InlineKeyboardButton("🔄 Yenile", callback_data=f"sticker_packs_refresh:{owner_id}")])
        await query.edit_message_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(rows),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )

    async def _handle_sticker_callback(self, update, query, context, data):
        from telegram import InputSticker
        from telegram.constants import StickerFormat
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id

        if data.startswith("stk_confirm_pack:") or data.startswith("stk_cancel_pack:"):
            parts = data.split(":", 1)
            try:
                owner_id = int(parts[1])
            except (IndexError, ValueError):
                await query.answer("Geçersiz sticker paketi işlemi.", show_alert=True)
                return
            if owner_id != user_id:
                await query.answer("Bu onay başka bir kullanıcıya ait.", show_alert=True)
                return
            if data.startswith("stk_cancel_pack:"):
                if self.message_handler and hasattr(self.message_handler, "user_states"):
                    pop_scoped_state(
                        self.message_handler.user_states,
                        user_id=owner_id,
                        chat_id=chat_id,
                    )
                await query.edit_message_text("Sticker paketi oluşturma iptal edildi.")
                return
            if not self.message_handler:
                await query.answer("Sticker oluşturma servisi hazır değil.", show_alert=True)
                return
            try:
                result = await self.message_handler.confirm_pack_creation(owner_id, chat_id, context)
            except ValueError as exc:
                messages = {
                    "sticker_creation_expired": "Bu sticker oluşturma oturumunun süresi dolmuş.",
                    "sticker_creation_invalid": "Sticker oluşturma bilgileri eksik.",
                }
                code = normalize_error_code(exc, "STICKER_OPERATION_FAILED").lower()
                await query.edit_message_text(f"⚠️ {messages.get(code, 'Sticker paketi oluşturulamadı.')}")
                return
            except RuntimeError as exc:
                code = str(exc).strip().upper()
                if code == "STICKER_PACK_PERSISTENCE_UNAVAILABLE":
                    await query.edit_message_text(
                        "⚠️ Sticker paketi Telegram tarafında oluşturulamadı veya kalıcı kaydı tamamlanamadı. "
                        "Veritabanı bağlantısını kontrol edip paketi yeniden dene."
                    )
                else:
                    await query.edit_message_text("⚠️ Sticker paketi oluşturulamadı; biraz sonra tekrar dene.")
                return
            except Exception as exc:
                logger.error("Sticker pack creation confirmation failed: %s", summarize_exception(exc, label="sticker_pack_create"))
                await query.edit_message_text(
                    "⚠️ Sticker paketi oluşturulamadı. Telegram dosyanın formatını veya boyutunu kabul etmemiş olabilir; "
                    "paketi yeniden başlatıp tekrar dene."
                )
                return
            await query.answer("Paket kaydedildi.")
            await query.edit_message_text(
                f"✅ <b>Paket oluşturuldu</b>\n\nBaşlık: <b>{html.escape(str(result['title']))}</b>\n"
                f"🔗 <a href=\"{html.escape(result['link'], quote=True)}\">Paketi aç</a>",
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            return

        if data.startswith("stk_new_"):
            suffix = data.replace("stk_new_", "")
            sticker = query.message.sticker

            if not sticker and query.message.reply_to_message:
                sticker = query.message.reply_to_message.sticker

            if not sticker:
                await query.message.reply_text("❌ Sticker verisi bulunamadı.")
                return

            is_group = query.message.chat.type in ["group", "supergroup"]

            if is_group:
                bot_info = await context.bot.get_me()
                unique_id = str(uuid.uuid4())

                await self.data_port.save_creation_cache(
                    unique_id,
                    {'file_id': sticker.file_id, 'suffix': suffix}
                )

                deep_link = f"https://t.me/{bot_info.username}?start=stk_create_{unique_id}"
                keyboard = [[InlineKeyboardButton("🚀 Özel Mesajda Oluştur", url=deep_link)]]
                await query.message.reply_text(
                    "✨ Güvenlik için sticker paketlerini <b>özel sohbet</b> üzerinden oluşturuyorum.\n\n"
                    "Aşağıdaki düğmeye basıp akışı orada tamamla:",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="HTML",
                )
                await query.edit_message_reply_markup(reply_markup=None)
                return

            if not hasattr(self, 'user_states'):
                self.user_states = {}
            set_scoped_state(
                self.user_states,
                user_id=user_id,
                chat_id=chat_id,
                value={
                    'state': 'awaiting_pack_title',
                    'data': {
                        'file_id': sticker.file_id,
                        'is_video': suffix == "video"
                    }
                },
            )
            await query.message.reply_text(
                "📦 <b>Yeni Sticker Paketi Oluşturma</b>\n\n"
                "Lütfen paketiniz için <b>Başlık</b> yazın:\n"
                "<i>(Örn: Arel Komik Koleksiyonu)</i>",
                parse_mode='HTML'
            )
            await query.edit_message_reply_markup(reply_markup=None)

        elif data.startswith("stk_select_pack_"):
            suffix = data.replace("stk_select_pack_", "")
            sticker = query.message.sticker
            if not sticker and query.message.reply_to_message:
                sticker = query.message.reply_to_message.sticker
            if not sticker:
                await query.answer("❌ Sticker bulunamadı.", show_alert=True)
                return

            is_group = query.message.chat.type in ["group", "supergroup"]

            try:
                packs = await self.data_port.get_user_packs(user_id, pack_type=suffix)

                keyboard = []
                for p in packs:
                    keyboard.append([InlineKeyboardButton(f"📁 {p['pack_title']}", callback_data=f"stk_add_to_{p['_id']}")])

                if is_group:
                    bot_info = await context.bot.get_me()
                    unique_id = str(uuid.uuid4())
                    await self.data_port.save_creation_cache(
                        unique_id,
                        {'file_id': sticker.file_id, 'suffix': suffix}
                    )
                    deep_link = f"https://t.me/{bot_info.username}?start=stk_create_{unique_id}"
                    keyboard.append([InlineKeyboardButton("🚀 Yeni Paket Oluştur (DM)", url=deep_link)])
                else:
                    keyboard.append([InlineKeyboardButton("📦 Yeni Paket Oluştur", callback_data=f"stk_new_{suffix}")])

                await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
            except Exception as e:
                logger.error("Error listing packs: %s", summarize_exception(e, label="sticker_pack_list"))
                await query.answer("Menü yüklenirken hata oluştu.", show_alert=True)

        elif data.startswith("stk_add_to_"):
            pack_id = data.replace("stk_add_to_", "")
            sticker = query.message.sticker
            if not sticker and query.message.reply_to_message:
                sticker = query.message.reply_to_message.sticker
            if not sticker:
                await query.answer("❌ Sticker bulunamadı.", show_alert=True)
                return

            try:
                pack_rec = await self.data_port.get_pack_by_id(pack_id)
                if not pack_rec or pack_rec['user_id'] != user_id:
                    await query.answer("⚠️ Paket bulunamadı!", show_alert=True)
                    return

                sticker_format = StickerFormat.STATIC if pack_rec['pack_type'] == "static" else StickerFormat.VIDEO
                if self.sticker_pack_service is None:
                    await query.answer("Sticker paket servisi hazır değil.", show_alert=True)
                    return
                result = await self.sticker_pack_service.add_sticker(
                    context.bot,
                    user_id,
                    pack_rec['pack_name'],
                    sticker.file_id,
                    ["✨"],
                    sticker_format,
                )

                pack_link = f"https://t.me/addstickers/{pack_rec['pack_name']}"
                await query.edit_message_reply_markup(reply_markup=None)
                if result.get("status") == "already_exists":
                    await query.message.reply_text(
                        f"ℹ️ Bu sticker zaten <b>'{pack_rec['pack_title']}'</b> paketinde.",
                        parse_mode='HTML',
                    )
                    return
                if result.get("status") == "in_progress":
                    await query.message.reply_text(
                        "⏳ Bu sticker ekleme işlemi hala sürüyor; Telegram naz yaparsa bile ikinci kez eklemiyorum.",
                    )
                    return
                await query.message.reply_text(
                    f"✅ <b>Sticker '{pack_rec['pack_title']}' paketine eklendi!</b>\n🔗 <a href='{pack_link}'>Paketi Gör</a>",
                    parse_mode='HTML',
                    disable_web_page_preview=True
                )
            except Exception as pack_err:
                logger.error("Failed to add to specific pack: %s", summarize_exception(pack_err, label="sticker_pack_add"))
                await query.answer(
                    public_error_message(
                        pack_err,
                        "Sticker paketi güncellenemedi; Telegram servisi kısa süreliğine naz yapıyor olabilir.",
                    ),
                    show_alert=True,
                )

    async def _handle_persona_user_callback(self, update, query, data: str):
        """Persist a private user's tone choice with strict callback scope."""
        if (
            update.effective_chat.type != "private"
            or query.message.chat.id != update.effective_user.id
            or update.effective_chat.id != update.effective_user.id
        ):
            await query.answer("Bu kişilik menüsü yalnızca sahibine aittir.", show_alert=True)
            return
        raw = data.split(":", 1)[1] if ":" in data else ""
        if raw == "roast":
            await query.edit_message_text(
                "⚠️ <b>Roast / taşlama modu</b> yalnızca Arel'e doğrudan kaba çıkışlarda çalışır. "
                "Tehdit, nefret söylemi, cinsel aşağılama ve hedefli taciz üretmez. Etkinleştirmek için açıkça onay ver.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(
                    "✅ Roast modunu onayla", callback_data="persona_user:roast_confirm"
                )]]),
                parse_mode="HTML",
            )
            await query.answer("Onay bekleniyor.")
            return
        if raw == "roast_confirm":
            raw = "roast"
        if raw == "default":
            raw = None
        elif is_normal_mode(raw):
            raw = NORMAL_MODE
        elif raw not in PersonalityPolicy.MODE_LEVELS and raw not in PersonalityPolicy.MODE_ALIASES:
            await query.answer("Geçersiz kişilik seçimi.", show_alert=True)
            return
        else:
            raw = PersonalityPolicy.normalize_mode(raw)
        try:
            await self.data_port.update_user(update.effective_user.id, {"persona_mode": raw})
        except Exception:
            logger.warning("Could not persist private personality callback", exc_info=True)
            await query.answer("Üslup tercihi kaydedilemedi.", show_alert=True)
            return

        current_label = (
            "Varsayılan"
            if raw is None
            else (style_mode_label(raw) or PersonalityPolicy.mode_label(raw))
        )
        keyboard = []
        for mode in ("normal", "serious", "balanced", "needle", "roast"):
            marker = "✅ " if raw == mode else ""
            callback = "persona_user:roast" if mode == "roast" else f"persona_user:{mode}"
            keyboard.append([InlineKeyboardButton(
                f"{marker}{style_mode_label(mode) or PersonalityPolicy.mode_label(mode)}",
                callback_data=callback,
            )])
        keyboard.append([InlineKeyboardButton(
            "✅ Varsayılan aktif" if raw is None else "↩️ Varsayılanı kullan",
            callback_data="persona_user:default",
        )])
        await query.edit_message_text(
            "🎭 <b>Arel üslubu</b>\n\n"
            f"Aktif tercih: <b>{current_label}</b>\n"
            "Kısa kinaye yalnızca uygunsa kullanılır; hassas ve teknik konular daima ciddi kalır.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML",
        )
        await query.answer("Üslup tercihi güncellendi.")

    async def _handle_ayar_menu(self, update, query, context):
        if query.message.chat.type not in {"group", "supergroup"}:
            if self.menu_service:
                try:
                    settings = await self.data_port.get_chat_settings(query.message.chat.id)
                except Exception:
                    settings = {}
                text, markup = self.menu_service.personal_settings_menu(
                    update.effective_user.id,
                    quote_enabled=bool(settings.get("q_enabled", True)),
                    media_enabled=bool(settings.get("media_enabled", True)),
                )
                await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
            else:
                await query.answer("Kişisel ayarlar hazır değil.", show_alert=True)
            return
        if not await self._is_admin(update, context):
            await query.answer("Bu men\u00fcy\u00fc sadece y\u00f6neticiler a\u00e7abilir.", show_alert=True)
            return
        await query.edit_message_text(
            "<b>Grup ayarlar\u0131</b>\n\nModerasyon ve bot davran\u0131\u015f\u0131n\u0131 a\u015fa\u011f\u0131daki butonlarla y\u00f6netin.",
            reply_markup=await self._build_ayar_keyboard(query.message.chat.id),
            parse_mode="HTML",
        )

    async def _handle_ayar_callback(self, update, query, context, data):
        # Private chats own their settings; group settings remain admin-only.
        if data in {"settings_quote_toggle", "settings_media_toggle"}:
            chat = getattr(query.message, "chat", None) or update.effective_chat
            if getattr(chat, "type", None) in {"group", "supergroup"}:
                await self._safe_query_answer(query, "Bu ayarı grup yöneticileri değiştirebilir.", show_alert=True)
                return
            key = "q_enabled" if data == "settings_quote_toggle" else "media_enabled"
            current = await is_chat_feature_enabled(self.data_port, chat.id, key, True)
            try:
                await ChatFeaturePolicy(self.data_port).set_enabled(
                    chat.id,
                    key,
                    not current,
                    actor_id=getattr(update.effective_user, "id", None),
                )
            except Exception:
                await self._safe_query_answer(query, "Ayar kaydedilemedi; tekrar dene.", show_alert=True)
                return
            if self.menu_service:
                try:
                    settings = await self.data_port.get_chat_settings(chat.id)
                    await query.edit_message_reply_markup(
                        reply_markup=self.menu_service.personal_settings_menu(
                            getattr(update.effective_user, "id", chat.id),
                            quote_enabled=bool(settings.get("q_enabled", True)),
                            media_enabled=bool(settings.get("media_enabled", True)),
                        )[1]
                    )
                except Exception:
                    pass
            await self._safe_query_answer(
                f"{'Quote' if key == 'q_enabled' else 'Video indirici'}: {'açık' if not current else 'kapalı'}"
            )
            return
        if not await self._is_admin(update, context):
            await query.answer("Sadece grup y\u00f6neticileri de\u011fi\u015ftirebilir.", show_alert=True)
            return
        chat_id = query.message.chat.id
        if data == "ayar_ban_help":
            await query.edit_message_text(
                "🚫 <b>Üye yönetimi</b>\n\nBanlamak için bir mesaja yanıt verip <code>/ban</code> yazabilir veya <code>/ban kullanıcı_id</code> kullanabilirsin. Geri almak için aynı biçimde <code>/unban</code> yaz.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Ayarlara dön", callback_data="open_ayar_menu")]]),
                parse_mode="HTML",
            )
            return
        if data == "ayar_persona_roast_confirm":
            try:
                await self.data_port.update_chat_setting_key(chat_id, "persona_mode", "roast")
            except Exception:
                logger.warning("Could not persist group roast setting", exc_info=True)
                await query.answer("Roast ayar\u0131 kaydedilemedi.", show_alert=True)
                return
            await query.edit_message_reply_markup(reply_markup=await self._build_ayar_keyboard(chat_id))
            await query.answer("Roast / ta\u015flama modu etkinle\u015ftirildi.")
            return
        settings = await self.data_port.get_chat_settings(chat_id)
        toggle_keys = {
            "ayar_mod_toggle": "moderation_enabled",
            "ayar_link_toggle": "link_filter",
            "ayar_repeat_toggle": "repeat_filter",
            "ayar_welcome_toggle": "welcome_enabled",
            "ayar_goodbye_toggle": "goodbye_enabled",
            "ayar_ban_toggle": "auto_ban",
            "ayar_media_toggle": "media_enabled",
        }
        if data in toggle_keys:
            key = toggle_keys[data]
            # Quote and media are enabled by default for backwards
            # compatibility; moderation switches remain disabled by default.
            default_value = key in {"q_enabled", "media_enabled"}
            next_value = not bool(settings.get(key, default_value))
            if key in {"q_enabled", "media_enabled"}:
                await ChatFeaturePolicy(self.data_port).set_enabled(
                    chat_id,
                    key,
                    next_value,
                    actor_id=getattr(update.effective_user, "id", None),
                )
            else:
                await self.data_port.update_chat_setting_key(chat_id, key, next_value)
            await query.edit_message_reply_markup(reply_markup=await self._build_ayar_keyboard(chat_id))
            await query.answer("Ayar g\u00fcncellendi.")
            return
        if data.startswith("ayar_flood:"):
            try:
                value = max(3, min(30, int(data.split(":", 1)[1])))
            except (ValueError, IndexError):
                await query.answer("Ge\u00e7ersiz flood limiti.", show_alert=True)
                return
            await self.data_port.update_chat_setting_key(chat_id, "flood_limit", value)
            await query.edit_message_reply_markup(reply_markup=await self._build_ayar_keyboard(chat_id))
            await query.answer(f"Flood limiti: {value}")
            return
        if data == "ayar_q_toggle":
            await ChatFeaturePolicy(self.data_port).set_enabled(
                chat_id,
                "q_enabled",
                not settings.get("q_enabled", True),
                actor_id=getattr(update.effective_user, "id", None),
            )
            await query.edit_message_reply_markup(reply_markup=await self._build_ayar_keyboard(chat_id))
            await query.answer("Quote ayar\u0131 g\u00fcncellendi.")
            return
        if data == "ayar_persona_cycle":
            modes = (NORMAL_MODE, "balanced", "needle", "serious")
            stored_mode = settings.get("persona_mode")
            current = (
                NORMAL_MODE
                if stored_mode is None or is_normal_mode(stored_mode)
                else PersonalityPolicy.normalize_mode(stored_mode)
            )
            next_mode = modes[(modes.index(current) + 1) % len(modes)]
            try:
                await self.data_port.update_chat_setting_key(chat_id, "persona_mode", next_mode)
            except Exception:
                logger.warning("Could not persist group persona setting", exc_info=True)
                await query.answer("Kişilik ayarı kaydedilemedi.", show_alert=True)
                return
            await query.edit_message_reply_markup(reply_markup=await self._build_ayar_keyboard(chat_id))
            await query.answer(
                f"K\u0131silik: {style_mode_label(next_mode) or PersonalityPolicy.mode_label(next_mode)}"
            )
            return
        if data == "ayar_close":
            await query.edit_message_text("Ayarlar men\u00fcs\u00fc kapat\u0131ld\u0131.")

    async def _build_ayar_keyboard(self, chat_id):
        settings = await self.data_port.get_chat_settings(chat_id)
        def status(key, default=False):
            return "\u2705 A\u00c7IK" if settings.get(key, default) else "\u274c KAPALI"
        flood = max(3, min(30, int(settings.get("flood_limit", 8))))
        next_flood = {3: 5, 5: 8, 8: 12, 12: 20, 20: 30, 30: 3}.get(flood, 8)
        stored_persona = settings.get("persona_mode", NORMAL_MODE)
        persona = style_mode_label(stored_persona) or PersonalityPolicy.mode_label(stored_persona)
        keyboard = [
            [InlineKeyboardButton(f"Moderasyon: {status('moderation_enabled')}", callback_data="ayar_mod_toggle")],
            [InlineKeyboardButton(f"Link filtresi: {status('link_filter')}", callback_data="ayar_link_toggle")],
            [InlineKeyboardButton(f"Tekrar filtresi: {status('repeat_filter')}", callback_data="ayar_repeat_toggle")],
            [InlineKeyboardButton(f"Hoş geldin: {status('welcome_enabled')}", callback_data="ayar_welcome_toggle")],
            [InlineKeyboardButton(f"Güle güle: {status('goodbye_enabled')}", callback_data="ayar_goodbye_toggle")],
            [InlineKeyboardButton(f"Tekrarlı flood banı: {status('auto_ban')}", callback_data="ayar_ban_toggle")],
            [InlineKeyboardButton("\U0001f6ab Ban / unban nas\u0131l kullan\u0131l\u0131r?", callback_data="ayar_ban_help")],
            [InlineKeyboardButton(f"Flood limiti: {flood} (sonraki {next_flood})", callback_data=f"ayar_flood:{next_flood}")],
            [InlineKeyboardButton(f"Quote (/q): {status('q_enabled', True)}", callback_data="ayar_q_toggle")],
            [InlineKeyboardButton(f"Video indirici: {status('media_enabled', True)}", callback_data="ayar_media_toggle")],
            [InlineKeyboardButton(f"K\u0131silik: {persona}", callback_data="ayar_persona_cycle")],
            [InlineKeyboardButton("Men\u00fcy\u00fc kapat", callback_data="ayar_close")],
        ]
        return InlineKeyboardMarkup(keyboard)

    async def _handle_qtop_callback(self, update, query, context, data):
        if not await is_chat_feature_enabled(self.data_port, update.effective_chat.id, "q_enabled", True):
            return
        parts = data.split("_")
        action = parts[1]
        current_index = int(parts[2])

        top = await self.data_port.get_top_quotes(update.effective_chat.id, limit=10)
        if not top:
            return

        if action == "next":
            new_index = (current_index + 1) % len(top)
        else:
            new_index = (current_index - 1) % len(top)

        q = top[new_index]
        keyboard = [
            [
                InlineKeyboardButton("⬅️", callback_data=f"qtop_prev_{new_index}"),
                InlineKeyboardButton(f"{new_index + 1} / {len(top)}", callback_data="none"),
                InlineKeyboardButton("➡️", callback_data=f"qtop_next_{new_index}")
            ],
            [InlineKeyboardButton("🔥 Beğen", callback_data=f"qrate_up_{q['user_id']}")]
        ]

        try:
            await query.message.delete()
        except Exception:
            pass
        await query.message.reply_sticker(sticker=q['sticker_file_id'], reply_markup=InlineKeyboardMarkup(keyboard))

    async def _handle_qrate_callback(self, update, query, context, data):
        if not await is_chat_feature_enabled(self.data_port, update.effective_chat.id, "q_enabled", True):
            return
        parts = data.split("_")
        action = parts[1]
        target_user_id = int(parts[2])
        chat_id = update.effective_chat.id

        try:
            sticker_file_id = query.message.sticker.file_id

            vote_key = f"voted_{sticker_file_id}"
            if vote_key in context.user_data:
                await query.answer("⚠️ Bu alıntıyı zaten oyladın!", show_alert=True)
                return

            await self.data_port.update_quote_rating(
                sticker_file_id,
                "rating_up" if action == "up" else "rating_down",
                1
            )

            context.user_data[vote_key] = True
            await query.answer(f"✅ Oyun kaydedildi! 🚀")
        except Exception as e:
            logger.error("Rating error: %s", summarize_exception(e, label="quote_rating"))
            await query.answer("⚠️ Oylama sırasında hata oluştu.")

    async def _handle_tts_download(self, query, context, data):
        import os
        file_token = data.replace("tts_dl_", "")
        file_path = f"downloads/tts_{file_token}.mp3"
        if os.path.exists(file_path):
            await query.message.reply_document(
                document=open(file_path, 'rb'),
                filename=f"arel_seslendirme_{file_token}.mp3",
                caption="💾 İşte indirilebilir ses dosyanız."
            )
            await query.answer("Dosya gönderiliyor...")
        else:
            await query.answer("⚠️ Dosya süresi (1 saat) dolduğu için silinmiş.", show_alert=True)

    async def _handle_catalog_refresh(self, update, query, context):
        packs = await self.data_port.get_top_packs(5)

        if not packs:
            await query.message.edit_text("📭 Henüz katalogda paket yok.", parse_mode='HTML')
            return

        text = "🌟 <b>AREL STICKER KATALOĞU</b> 🌟\n\nEn popüler ve yeni paketleri keşfedin:\n\n"
        keyboard = []

        for p in packs:
            text += f"• <b>{p['pack_title']}</b> ({p['pack_type'].capitalize()})\n"
            keyboard.append([InlineKeyboardButton(f"📦 {p['pack_title']}", url=f"https://t.me/addstickers/{p['pack_name']}")])

        keyboard.append([InlineKeyboardButton("🔄 Yenile", callback_data="catalog_refresh")])

        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
        await query.answer("Katalog yenilendi! ✨")

    async def _is_admin(self, update, context):
        return await is_admin(update, context, self.sudo_manager, authorization=self.authorization)

    async def _is_group_admin(self, update, context):
        """Shared group schedules can only be changed by current chat admins."""
        return await is_group_admin(update, context, authorization=self.authorization)
