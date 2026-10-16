"""
Callback Handler - TÃ¼m inline button callback iÅŸlemleri

Oyun menÃ¼sÃ¼, help menÃ¼sÃ¼, sticker iÅŸlemleri callbackleri.
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
from .handler_utils import is_admin, remove_job_by_name
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
from src.services.culture_ui import culture_category_help, culture_category_panel
from src.services.bim_ui import bim_page_view
from src.ai.personality import PersonalityPolicy
from src.ai.conversation_style import NORMAL_MODE, is_normal_mode, style_mode_label
from src.storage.handler_data_port import HandlerDataPort
from src.features.chat_feature_policy import ChatFeaturePolicy, is_chat_feature_enabled
from src.storage.state_scope import get_scoped_state, pop_scoped_state, set_scoped_state
from src.services.hangman_ui import hangman_keyboard, render_hangman_text
from src.games.word_game import shuffle_rack, reveal_word_hint
from src.services.word_game_ui import word_game_keyboard, render_word_game_text
from src.services.word_guess_ui import word_guess_keyboard, render_word_guess_text

logger = logging.getLogger(__name__)


class CallbackHandler:
    NOTIFICATION_CITY_PAGE_SIZE = 8

    def __init__(self, chat_engine, sudo_manager, tool_manager, menu_service=None, leaderboard_service=None, economy_service=None, subscription_service=None, notification_preference_store=None, productivity_service=None, announcement_service=None, sticker_pack_service=None, game_action_ledger=None, blackjack_service=None, batak_service=None, roulette_service=None, data_port=None):
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
                await query.answer("Bu oyun iÅŸlemi zaten iÅŸlendi.", show_alert=True)
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
                "ğŸ§  <b>HafÄ±za tercihleri</b>\n\nHafÄ±za varsayÄ±lan olarak kapalÄ±dÄ±r. <code>/memory on</code>, <code>/memory off</code>, <code>/memory show</code> ve <code>/memory clear</code> ile yÃ¶netebilirsin.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("âš™ï¸ Ayarlara dÃ¶n", callback_data="open_ayar_menu"), InlineKeyboardButton("ğŸ  Ana menÃ¼", callback_data="menu_main")]]),
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
                await query.edit_message_text("GÃ¶rsel iÅŸlemi iptal edildi.")
                return
            if action not in {"vision", "ocr", "sticker"}:
                await query.answer("GeÃ§ersiz gÃ¶rsel iÅŸlemi.", show_alert=True)
                return
            set_scoped_state(
                self.user_states,
                user_id=user_id,
                chat_id=update.effective_chat.id,
                value={"state": "awaiting_intent_photo", "action": action},
            )
            await query.edit_message_text(
                "Tamam. Åimdi fotoÄŸrafÄ± gÃ¶nder; baÅŸka bir ÅŸeye atlamadan seÃ§tiÄŸin iÅŸlemi uygulayacaÄŸÄ±m.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Ä°ptal", callback_data="media_intent:cancel")]]),
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
                "ğŸ“‹ <b>PlanÄ±m</b>\n\n"
                "<code>/plan</code> bugÃ¼nÃ¼nÃ¼, <code>/plan hafta</code> haftanÄ± gÃ¶sterir.\n"
                "Yeni not iÃ§in <code>/not</code>, gÃ¶rev iÃ§in <code>/gorev</code>, hatÄ±rlatÄ±cÄ± iÃ§in <code>/hatirlat</code> yaz.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ğŸ  Ana MenÃ¼", callback_data="menu_main")]]),
                parse_mode="HTML",
            )
            return

        if data in {"city_help", "turkiye_pharmacy_help", "turkiye_fuel_help", "turkiye_finance_help"}:
            hints = {
                "city_help": "ğŸ™ <b>Åehir rehberi</b>\n\n<code>/sehir Ä°stanbul</code>\n<code>/plaka 34</code>\n<code>/telefon 212</code>\n<code>/ilce KadÄ±kÃ¶y</code>",
                "turkiye_pharmacy_help": "ğŸ’Š <b>NÃ¶betÃ§i eczane</b>\n\n<code>/eczane Ä°stanbul BeÅŸiktaÅŸ</code>",
                "turkiye_fuel_help": "â›½ <b>AkaryakÄ±t</b>\n\n<code>/akaryakit Ä°stanbul</code>",
                "turkiye_finance_help": "ğŸ’± <b>DÃ¶viz / altÄ±n</b>\n\n<code>/doviz</code> Â· <code>/altin</code>",
            }
            await query.edit_message_text(
                hints[data],
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ğŸŒ TÃ¼rkiye menÃ¼sÃ¼ne dÃ¶n", callback_data="menu_turkiye")]]),
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
            text, markup = self.menu_service.main_menu(language, user_id=user_id, chat_type=getattr(update.effective_chat, "type", None)) if self.menu_service else ("âœ… Dil gÃ¼ncellendi.", None)
            await query.edit_message_text(text, reply_markup=markup, parse_mode='HTML')
            return

        if data == "menu_leaderboard" and self.leaderboard_service:
            text = await self.leaderboard_service.render_leaderboard(update.effective_chat.id)
            keyboard = [[InlineKeyboardButton("ğŸ  Ana MenÃ¼", callback_data="menu_main")]]
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
            "media_photo_help": ("ğŸ” <b>GÃ¶rsel analizi</b>\n\nBir fotoÄŸraf gÃ¶nder, sonra bu menÃ¼den ne istediÄŸini seÃ§. Arel kendi kendine her gÃ¶rsele atlamaz.", "menu_media"),
            "media_ocr_help": ("ğŸ“ <b>OCR</b>\n\nFotoÄŸrafÄ± gÃ¶nderip bu seÃ§eneÄŸi kullan; iÃ§indeki metni Ã§Ä±karmaya Ã§alÄ±ÅŸÄ±rÄ±m.", "menu_media"),
            "media_image_help": ("ğŸ–¼ <b>GÃ¶rsel Ã¼ret</b>\n\n<code>/image neon Ä±ÅŸÄ±klÄ± Ä°stanbul gecesi</code> gibi bir aÃ§Ä±klama gÃ¶nder.", "menu_media"),
            "media_tts_help": ("ğŸ”Š <b>Metni seslendir</b>\n\n<code>/oku bugÃ¼n kÄ±sa bir Ã¶zet</code> yaz; ses dosyasÄ± olarak dÃ¶neyim.", "menu_media"),
            "news_latest_help": ("ğŸ— <b>Son haberler</b>\n\n<code>/haber teknoloji</code> veya <code>/haber son dakika</code> yaz. Kaynak ve zamanÄ± birlikte gÃ¶steririm.", "menu_news"),
            "news_digest_help": ("ğŸ§  <b>Haber Ã¶zeti</b>\n\n<code>/digest</code> yaz; uygun kaynaklarÄ± kÄ±sa bir Ã¶zet halinde toparlarÄ±m.", "menu_news"),
            "ai_chat_help": ("ğŸ’¬ <b>Sohbet</b>\n\nDirekt yaz. GÃ¼ncel bilgi gerekiyorsa web aramasÄ±nÄ± kendim devreye sokarÄ±m; modeli sana tÃ¶renle tanÄ±tmam.", "menu_ai"),
            "ai_web_help": ("ğŸŒ <b>Web aramasÄ±</b>\n\n<code>/ara KPSS ortaÃ¶ÄŸretim sÄ±nav tarihi</code> yazabilir veya soruyu normal cÃ¼mleyle sorabilirsin.", "menu_ai"),
            "ai_document_help": ("ğŸ“„ <b>Belge analizi</b>\n\nPDF, DOCX veya TXT gÃ¶nder; sonra Ã¶zet, soru-cevap ya da kaynaklÄ± aÃ§Ä±klama iste.", "menu_ai"),
        }
        if data in menu_hints:
            text, back = menu_hints[data]
            await query.edit_message_text(
                text,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("â†©ï¸ Geri dÃ¶n", callback_data=back), InlineKeyboardButton("ğŸ  Ana menÃ¼", callback_data="menu_main")]]),
                parse_mode="HTML",
            )
            return

        if data in {"plan_today", "plan_week", "plan_notes", "plan_tasks"}:
            instructions = {
                "plan_today": "ğŸ“… <b>BugÃ¼nÃ¼n planÄ±</b>\n\n<code>/plan</code> komutuyla gÃ¼nÃ¼n hatÄ±rlatÄ±cÄ± ve gÃ¶revlerini aÃ§abilirsin.",
                "plan_week": "ğŸ—“ <b>HaftalÄ±k plan</b>\n\n<code>/plan hafta</code> komutuyla Ã¶nÃ¼ndeki 7 gÃ¼nÃ¼ gÃ¶rÃ¼rsÃ¼n.",
                "plan_notes": "ğŸ“ <b>Notlar</b>\n\n<code>/not metin</code> ile not ekle; <code>/notlar</code> ile yÃ¶net.",
                "plan_tasks": "âœ… <b>GÃ¶revler</b>\n\n<code>/gorev metin</code> ile gÃ¶rev ekle; <code>/gorevler</code> ile butonlardan tamamla.",
            }
            await query.edit_message_text(
                instructions[data],
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ğŸ“‹ PlanÄ±ma dÃ¶n", callback_data="menu_plan"), InlineKeyboardButton("ğŸ  Ana menÃ¼", callback_data="menu_main")]]),
                parse_mode="HTML",
            )
            return

        if data == "menu_reminders":
            text = "ğŸ”” <b>HATIRLATICI</b>\n\n<code>/remind 10dk Su iÃ§</code>\n<code>/remind yarÄ±n 10:00 ToplantÄ±</code>\n<code>/remind her gÃ¼n 09:00 GÃ¼nlÃ¼k plan</code>"
            keyboard = [[InlineKeyboardButton("ğŸ  Ana MenÃ¼", callback_data="menu_main")]]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
            return

        if data == "weather_help":
            await query.edit_message_text(
                "ğŸŒ¤ <b>Hava durumu</b>\n\nBir ÅŸehri seÃ§ veya <code>/hava ist</code> yaz. SonuÃ§ ekranÄ±ndan gÃ¼nlÃ¼k, haftalÄ±k ya da saatlik bildirim kurabilirsin.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Ä°stanbul", callback_data=f"weather_show:{user_id}:34"), InlineKeyboardButton("Ankara", callback_data=f"weather_show:{user_id}:6")],
                    [InlineKeyboardButton("Ä°zmir", callback_data=f"weather_show:{user_id}:35"), InlineKeyboardButton("Antalya", callback_data=f"weather_show:{user_id}:7")],
                    [InlineKeyboardButton("ğŸ”” Bildirim merkezi", callback_data=f"notify_center:{user_id}"), InlineKeyboardButton("ğŸ  Ana menÃ¼", callback_data="menu_main")],
                ]),
                parse_mode="HTML",
            )
            return

        if data == "notification_noop":
            await query.answer("Bu sayfa gÃ¶stergesi.")
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
                await query.answer("Bu alarm baÅŸka bir kullanÄ±cÄ±ya ait.", show_alert=True)
                return
            if not self.subscription_service:
                await query.answer("Alarm servisi hazÄ±r deÄŸil.", show_alert=True)
                return
            stopped = await self.subscription_service.store.disable(update.effective_user.id, parts[2])
            await query.answer("Alarm kapatÄ±ldÄ±." if stopped else "Alarm zaten kapalÄ±.", show_alert=not stopped)
            if stopped:
                await query.edit_message_text("âœ… Fiyat alarmÄ± kapatÄ±ldÄ±.", parse_mode="HTML")
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
                await query.answer("Belge iÅŸlemi geÃ§ersiz.", show_alert=True)
                return
            if str(update.effective_user.id) != parts[2] or update.effective_chat.type != "private":
                await query.answer("Bu belge iÅŸlemi yalnÄ±zca belge sahibine aittir.", show_alert=True)
                return
            if parts[1] == "cancel":
                await query.edit_message_text("Belge silme iÅŸlemi iptal edildi.")
                return
            rag = getattr(self.chat_engine, "rag_engine", None)
            if rag is None:
                await query.answer("Belge hafÄ±zasÄ± hazÄ±r deÄŸil.", show_alert=True)
                return
            deleted = await rag.delete_document(
                update.effective_user.id,
                parts[3],
                actor_id=update.effective_user.id,
            )
            if deleted:
                await query.edit_message_text(f"âœ… Belge hafÄ±zadan silindi. Silinen parÃ§a: {deleted}")
            else:
                await query.edit_message_text("Belge bulunamadÄ± veya zaten silinmiÅŸ.")
            return

        if data == "weather_admin_only":
            await query.answer("Bu grupta bildirim ayarlarÄ±nÄ± yalnÄ±zca yÃ¶neticiler deÄŸiÅŸtirebilir.", show_alert=True)
            return

        if data == "earthquake_admin_only":
            await query.answer("Bu grupta deprem bildirimlerini yalnÄ±zca yÃ¶neticiler yÃ¶netebilir.", show_alert=True)
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

        if data == "word_hint":
            await self._handle_word_guess_hint(update, query, context)
            return

        if data.startswith("bj:"):
            await self._handle_blackjack_callback(update, query, context, data)
            return

        if data.startswith("batak:"):
            await self._handle_batak_round_callback(update, query, context, data)
            return

        if data.startswith("hangman_guess:"):
            await self._handle_hangman_guess_callback(update, query, context, data)
            return

        if data == "hangman_hint":
            await self._handle_hangman_hint_callback(update, query, context)
            return

        if data == "hangman_stop":
            await self._handle_hangman_stop_callback(update, query, context)
            return

        if data in {"word_game_shuffle", "word_game_hint"}:
            await self._handle_word_game_control(update, query, context, data)
            return

        if data == "word_game_stop":
            await self._handle_word_game_stop(update, query, context)
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

        if data == "game_word_skip":
            await self._handle_word_skip(update, query, context)
            return

        if data == "word_guess_stop":
            await self._handle_word_guess_stop(update, query, context)
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

        if data.startswith("quiz_ans_"):
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
            await query.answer("GeÃ§ersiz gÃ¶rsel iÅŸlemi.", show_alert=True)
            return
        try:
            owner_id = int(parts[1])
        except ValueError:
            await query.answer("GeÃ§ersiz gÃ¶rsel sahibi.", show_alert=True)
            return
        if owner_id != update.effective_user.id:
            await query.answer("Bu gÃ¶rsel iÅŸlemi baÅŸka bir kullanÄ±cÄ±ya ait.", show_alert=True)
            return
        state = get_scoped_state(
            getattr(self, "user_states", None),
            user_id=owner_id,
            chat_id=update.effective_chat.id,
        )
        if not state or state.get("state") != "awaiting_photo_action":
            await query.answer("Bu gÃ¶rsel seÃ§iminin sÃ¼resi dolmuÅŸ.", show_alert=True)
            return
        try:
            created_at = datetime.fromisoformat(str(state.get("created_at")))
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - created_at > timedelta(minutes=15):
                pop_scoped_state(self.user_states, user_id=owner_id, chat_id=update.effective_chat.id)
                await query.answer("Bu gÃ¶rsel seÃ§iminin sÃ¼resi dolmuÅŸ.", show_alert=True)
                return
        except (TypeError, ValueError):
            pass
        action = parts[2].casefold()
        if action == "cancel":
            pop_scoped_state(self.user_states, user_id=owner_id, chat_id=update.effective_chat.id)
            await query.edit_message_text("âœ… GÃ¶rsel iÅŸlemi iptal edildi.")
            return
        if action not in {"vision", "ocr", "sticker"} or self.media_handler is None:
            await query.answer("GÃ¶rsel iÅŸleme servisi hazÄ±r deÄŸil.", show_alert=True)
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
            await query.answer("GeÃ§ersiz gÃ¶rsel iÅŸlemi.", show_alert=True)
            return
        except Exception as exc:
            logger.warning("Photo action enqueue failed: %s", summarize_exception(exc, label="photo_action_enqueue"))
            await query.edit_message_text("âŒ GÃ¶rsel kuyruÄŸa alÄ±namadÄ±; biraz sonra tekrar dene.")
            return
        pop_scoped_state(self.user_states, user_id=owner_id, chat_id=update.effective_chat.id)
        label = {"vision": "gÃ¶rsel analizi", "ocr": "metin okuma", "sticker": "sticker Ã¼retimi"}[action]
        status = "Bu gÃ¶rsel iÃ§in aktif iÅŸ zaten var" if duplicate else f"{label.capitalize()} kuyruÄŸa alÄ±ndÄ±"
        await query.edit_message_text(f"â³ {status}.\nÄ°ÅŸ: <code>{html.escape(str(job_id))}</code>", parse_mode="HTML")

    async def _handle_daily_brief_callback(self, update, query, context, data: str):
        parts = data.split(":")
        if len(parts) < 2:
            await query.answer("GeÃ§ersiz gÃ¼nlÃ¼k Ã¶zet menÃ¼sÃ¼.", show_alert=True)
            return
        try:
            owner_id = int(parts[1]) if len(parts) == 2 else int(parts[2])
        except ValueError:
            await query.answer("GeÃ§ersiz gÃ¼nlÃ¼k Ã¶zet sahibi.", show_alert=True)
            return
        if update.effective_chat.type in {"group", "supergroup"}:
            text, markup = group_center(
                update.effective_user.id,
                is_admin=await self._is_group_admin(update, context),
            )
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
            return
        if owner_id != update.effective_user.id or update.effective_chat.type != "private":
            await query.answer("GÃ¼nlÃ¼k Ã¶zet ayarlarÄ±nÄ± yalnÄ±zca Ã¶zel sohbetinde deÄŸiÅŸtirebilirsin.", show_alert=True)
            return
        if self.notification_preference_store is None or self.menu_service is None:
            await query.answer("GÃ¼nlÃ¼k Ã¶zet servisi hazÄ±r deÄŸil.", show_alert=True)
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
                cities = {"istanbul": "Ä°stanbul", "ankara": "Ankara", "izmir": "Ä°zmir", "antalya": "Antalya"}
                if parts[3] not in cities:
                    raise ValueError("Åehir adÄ± geÃ§ersiz.")
                preferences = await self.notification_preference_store.update(owner_id, daily_brief_city=cities[parts[3]])
            else:
                await query.answer("GeÃ§ersiz gÃ¼nlÃ¼k Ã¶zet seÃ§imi.", show_alert=True)
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
            await query.answer("GeÃ§ersiz sticker paket iÅŸlemi.", show_alert=True)
            return
        try:
            owner_id = int(parts[2])
        except ValueError:
            await query.answer("GeÃ§ersiz sticker paket iÅŸlemi.", show_alert=True)
            return
        if owner_id != update.effective_user.id:
            await query.answer("Bu onay baÅŸka bir kullanÄ±cÄ±ya ait.", show_alert=True)
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
                await query.answer("Bu sticker silme onayÄ±nÄ±n sÃ¼resi dolmuÅŸ.", show_alert=True)
                return
            if parts[1] == "cancel_sticker":
                pop_scoped_state(
                    state_store,
                    user_id=owner_id,
                    chat_id=update.effective_chat.id,
                )
                await query.edit_message_text("Ä°ÅŸlem iptal edildi.")
                return
            if self.sticker_pack_service is None:
                await query.answer("Sticker paket servisi hazÄ±r deÄŸil.", show_alert=True)
                return
            try:
                deleted = await self.sticker_pack_service.delete_sticker(
                    context.bot,
                    owner_id,
                    str(state_data.get("pack_name") or ""),
                    str(state_data.get("file_id") or ""),
                )
            except PermissionError:
                await query.answer("Bu sticker paketi sana ait deÄŸil veya artÄ±k mevcut deÄŸil.", show_alert=True)
                return
            except ValueError:
                await query.answer("Sticker dosyasÄ± geÃ§ersiz.", show_alert=True)
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
                await query.answer("Sticker zaten silinmiÅŸ olabilir.", show_alert=True)
            return

        if parts[1] == "cancel":
            await query.edit_message_text("Ä°ÅŸlem iptal edildi.")
            return
        if self.sticker_pack_service is None:
            await query.answer("Sticker paket servisi hazÄ±r deÄŸil.", show_alert=True)
            return
        try:
            deleted = await self.sticker_pack_service.delete(context.bot, owner_id, parts[3])
        except PermissionError:
            await query.answer("Bu paket sana ait deÄŸil veya artÄ±k mevcut deÄŸil.", show_alert=True)
            return
        except ValueError:
            await query.answer("GeÃ§ersiz paket adÄ±.", show_alert=True)
            return
        except Exception as exc:
            logger.error("Sticker pack delete callback failed: %s", summarize_exception(exc, label="sticker_pack_delete"))
            await query.answer("Paket silinemedi.", show_alert=True)
            return
        if deleted:
            await query.edit_message_text("Sticker paketi silindi.", parse_mode="HTML")
        else:
            await query.answer("Paket zaten silinmiÅŸ olabilir.", show_alert=True)

    async def _handle_announcement_stop_callback(self, update, query, context, data: str):
        parts = data.split(":", 2)
        if len(parts) != 3 or update.effective_chat.type not in {"group", "supergroup"}:
            await query.answer("Bu duyuru yalnÄ±zca grup iÃ§inde yÃ¶netilebilir.", show_alert=True)
            return
        if not await self._is_group_admin(update, context):
            await query.answer("DuyurularÄ± yalnÄ±zca grup yÃ¶neticileri yÃ¶netebilir.", show_alert=True)
            return
        if not self.announcement_service:
            await query.answer("Duyuru servisi hazÄ±r deÄŸil.", show_alert=True)
            return
        stopped = await self.announcement_service.disable(update.effective_chat.id, parts[2])
        await query.answer("Duyuru durduruldu." if stopped else "Duyuru zaten kapalÄ±.", show_alert=not stopped)
        if stopped:
            await query.edit_message_text("âœ… Grup duyurusu durduruldu.", parse_mode="HTML")

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
            await query.answer("GeÃ§ersiz bildirim kategorisi.", show_alert=True)
            return
        cities = tuple(self.chat_engine.city_directory.all())
        page_size = self.NOTIFICATION_CITY_PAGE_SIZE
        page_count = max(1, math.ceil(len(cities) / page_size))
        if page < 0 or page >= page_count:
            await query.answer("GeÃ§ersiz ÅŸehir sayfasÄ±.", show_alert=True)
            return
        current = cities[page * page_size : (page + 1) * page_size]
        if group:
            pick_prefix = "notify_group_weather_pick" if feature == "weather" else "notify_group_air_quality_pick"
            page_prefix = "notify_group_weather_page" if feature == "weather" else "notify_group_air_quality_page"
            back = f"notify_center:{owner_id}"
            title = (
                "ğŸŒ¤ <b>Grup hava bildirimi</b>\n\nÅehir seÃ§; frekans ve saati bir sonraki ekranda belirle."
                if feature == "weather"
                else "ğŸŒ¿ <b>Grup hava kalitesi</b>\n\nÅehir seÃ§; frekans ve saati sonraki ekranda belirle."
            )
        else:
            pick_prefix = "notify_weather_pick" if feature == "weather" else "notify_air_quality_pick"
            page_prefix = "notify_weather_page" if feature == "weather" else "notify_air_quality_page"
            back = f"notify_center:{owner_id}"
            title = (
                "ğŸŒ¤ <b>Hava bildirimi</b>\n\nÅehir seÃ§; sonraki ekranda gÃ¼nlÃ¼k, haftalÄ±k veya saatlik planÄ± kurarsÄ±n."
                if feature == "weather"
                else "ğŸŒ¿ <b>Hava kalitesi bildirimi</b>\n\nÅehir seÃ§; sonraki ekranda bildirim sÄ±klÄ±ÄŸÄ±nÄ± ayarla."
            )
        rows = [
            [InlineKeyboardButton(city.name, callback_data=f"{pick_prefix}:{owner_id}:{city.plate}")]
            for city in current
        ]
        navigation = []
        if page > 0:
            navigation.append(InlineKeyboardButton("â¬…ï¸ Ã–nceki", callback_data=f"{page_prefix}:{owner_id}:{page - 1}"))
        if page + 1 < page_count:
            navigation.append(InlineKeyboardButton("Sonraki â¡ï¸", callback_data=f"{page_prefix}:{owner_id}:{page + 1}"))
        if navigation:
            rows.append(navigation)
        rows.append([InlineKeyboardButton(f"Sayfa {page + 1}/{page_count}", callback_data="notification_noop")])
        rows.append([InlineKeyboardButton("ğŸ“£ Merkeze dÃ¶n" if group else "ğŸ”” Merkeze dÃ¶n", callback_data=back)])
        await query.edit_message_text(
            f"{title}\n\n<i>Ä°l sayfasÄ± {page + 1}/{page_count}</i>",
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
            await query.answer("GeÃ§ersiz bildirim menÃ¼sÃ¼.", show_alert=True)
            return
        is_group = update.effective_chat.type in {"group", "supergroup"}
        if not is_group and owner_id != update.effective_user.id:
            await query.answer("Bu bildirim menÃ¼sÃ¼ baÅŸka bir kullanÄ±cÄ±ya ait.", show_alert=True)
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
                await query.answer("Bu grupta ortak bildirimleri yalnÄ±zca yÃ¶neticiler yÃ¶netebilir.", show_alert=True)
                return
            if action == "notify_weather_page":
                if len(parts) != 3 or owner_id != update.effective_user.id:
                    await query.answer("GeÃ§ersiz ÅŸehir sayfasÄ±.", show_alert=True)
                    return
                try:
                    page = int(parts[2])
                except ValueError:
                    await query.answer("GeÃ§ersiz ÅŸehir sayfasÄ±.", show_alert=True)
                    return
            else:
                page = 0
            await self._show_notification_city_page(update, query, owner_id, "weather", page, group=False)
            return
        if action in {"notify_air_quality", "notify_air_quality_page"}:
            if is_group:
                await query.answer("KiÅŸisel hava kalitesi bildirimini Ã¶zel sohbette kurabilirsin.", show_alert=True)
                return
            if owner_id != update.effective_user.id:
                await query.answer("Bu bildirim menÃ¼sÃ¼ baÅŸka bir kullanÄ±cÄ±ya ait.", show_alert=True)
                return
            if action == "notify_air_quality_page":
                if len(parts) != 3:
                    await query.answer("GeÃ§ersiz ÅŸehir sayfasÄ±.", show_alert=True)
                    return
                try:
                    page = int(parts[2])
                except ValueError:
                    await query.answer("GeÃ§ersiz ÅŸehir sayfasÄ±.", show_alert=True)
                    return
            else:
                page = 0
            await self._show_notification_city_page(update, query, owner_id, "air_quality", page, group=False)
            return
        if action == "notify_air_quality_pick":
            if is_group or owner_id != update.effective_user.id or len(parts) != 3:
                await query.answer("GeÃ§ersiz ÅŸehir seÃ§imi.", show_alert=True)
                return
            try:
                plate = int(parts[2])
            except ValueError:
                await query.answer("GeÃ§ersiz ÅŸehir seÃ§imi.", show_alert=True)
                return
            await query.edit_message_text(
                "ğŸŒ¿ <b>Hava kalitesi bildirimi</b>\n\nBildirim sÄ±klÄ±ÄŸÄ±nÄ± seÃ§:",
                reply_markup=air_quality_intervals(owner_id, plate),
                parse_mode="HTML",
            )
            return
        if action == "notify_air_quality_add":
            if is_group or not self.subscription_service or len(parts) != 4 or owner_id != update.effective_user.id:
                await query.answer("Bu bildirimi yÃ¶netemezsin.", show_alert=True)
                return
            try:
                plate = int(parts[2])
                interval = int(parts[3])
            except ValueError:
                await query.answer("GeÃ§ersiz bildirim seÃ§imi.", show_alert=True)
                return
            if interval not in {10800, 21600, 86400, 604800}:
                await query.answer("GeÃ§ersiz bildirim aralÄ±ÄŸÄ±.", show_alert=True)
                return
            city = self.chat_engine.city_directory.by_plate(plate)
            if city is None:
                await query.answer("Åehir bulunamadÄ±.", show_alert=True)
                return
            item = await self.subscription_service.create(
                update.effective_user.id,
                update.effective_chat.id,
                SubscriptionRequest("air_quality", {"city": city.name}, interval, "inline"),
            )
            await query.edit_message_text(
                f"âœ… <b>{html.escape(city.name)}</b> iÃ§in hava kalitesi bildirimi kuruldu.\n"
                f"â± {html.escape(format_interval(interval))}\n"
                f"ID: <code>{item['subscription_id'][:8]}</code>",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Bildirimlerim", callback_data=f"notify_list:{owner_id}")],
                    [InlineKeyboardButton("Merkeze dÃ¶n", callback_data=f"notify_center:{owner_id}")],
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
                await query.answer("KiÅŸisel fiyat ve haber takiplerini Ã¶zel sohbette kurabilirsin.", show_alert=True)
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
                await query.answer("Bu yardÄ±m menÃ¼sÃ¼ kiÅŸisel bildirimlerine aittir.", show_alert=True)
                return
            text, markup = category_help("prices", owner_id)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
            return
        if action == "notify_price_add":
            if is_group or owner_id != update.effective_user.id or len(parts) != 4:
                await query.answer("Bu bildirimi yalnÄ±zca sahibi kurabilir.", show_alert=True)
                return
            feature_key = parts[2]
            try:
                interval = int(parts[3])
            except ValueError:
                await query.answer("GeÃ§ersiz takip aralÄ±ÄŸÄ±.", show_alert=True)
                return
            allowed = {"currency": {3600, 21600}, "fuel": {21600}}
            if feature_key not in allowed or interval not in allowed[feature_key]:
                await query.answer("GeÃ§ersiz fiyat takip seÃ§imi.", show_alert=True)
                return
            if self.subscription_service is None:
                await query.answer("Bildirim servisi hazÄ±r deÄŸil.", show_alert=True)
                return
            item = await self.subscription_service.create(
                owner_id,
                update.effective_chat.id,
                SubscriptionRequest(feature_key, {}, interval, format_interval(interval)),
            )
            label = {"currency": "Kur", "fuel": "AkaryakÄ±t"}[feature_key]
            await query.edit_message_text(
                f"âœ… <b>{label} takibi kuruldu.</b>\n"
                f"â± {html.escape(format_interval(interval))}\n"
                f"ID: <code>{html.escape(item['subscription_id'][:8])}</code>",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("ğŸ“‹ Bildirimlerim", callback_data=f"notify_list:{owner_id}")],
                    [InlineKeyboardButton("ğŸ”” Merkeze dÃ¶n", callback_data=f"notify_center:{owner_id}")],
                ]),
                parse_mode="HTML",
            )
            return
        if action == "notify_crypto_add":
            if is_group or owner_id != update.effective_user.id or len(parts) != 4:
                await query.answer("Bu bildirimi yalnÄ±zca sahibi kurabilir.", show_alert=True)
                return
            symbol = parts[2].casefold()
            try:
                interval = int(parts[3])
            except ValueError:
                await query.answer("GeÃ§ersiz takip aralÄ±ÄŸÄ±.", show_alert=True)
                return
            if symbol not in {"bitcoin", "ethereum", "solana"} or interval not in {3600, 86400}:
                await query.answer("GeÃ§ersiz kripto takip seÃ§imi.", show_alert=True)
                return
            if self.subscription_service is None:
                await query.answer("Bildirim servisi hazÄ±r deÄŸil.", show_alert=True)
                return
            item = await self.subscription_service.create(
                owner_id,
                update.effective_chat.id,
                SubscriptionRequest("crypto", {"symbol": symbol}, interval, format_interval(interval)),
            )
            await query.edit_message_text(
                f"âœ… <b>{html.escape(symbol.title())} takibi kuruldu.</b>\n"
                f"â± {html.escape(format_interval(interval))}\n"
                f"ID: <code>{html.escape(item['subscription_id'][:8])}</code>",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("ğŸ“‹ Bildirimlerim", callback_data=f"notify_list:{owner_id}")],
                    [InlineKeyboardButton("ğŸ”” Merkeze dÃ¶n", callback_data=f"notify_center:{owner_id}")],
                ]),
                parse_mode="HTML",
            )
            return
        if action == "notify_feed_add":
            if is_group or owner_id != update.effective_user.id or len(parts) != 4:
                await query.answer("Bu bildirimi yalnÄ±zca sahibi kurabilir.", show_alert=True)
                return
            feature_key = parts[2]
            try:
                interval = int(parts[3])
            except ValueError:
                await query.answer("GeÃ§ersiz takip aralÄ±ÄŸÄ±.", show_alert=True)
                return
            allowed = {"breaking_news": {7200}, "bim_deals": {43200}}
            if feature_key not in allowed or interval not in allowed[feature_key]:
                await query.answer("GeÃ§ersiz iÃ§erik takip seÃ§imi.", show_alert=True)
                return
            if self.subscription_service is None:
                await query.answer("Bildirim servisi hazÄ±r deÄŸil.", show_alert=True)
                return
            item = await self.subscription_service.create(
                owner_id,
                update.effective_chat.id,
                SubscriptionRequest(feature_key, {}, interval, format_interval(interval)),
            )
            label = {"breaking_news": "Haber Ã¶zeti", "bim_deals": "BÄ°M aktÃ¼el"}[feature_key]
            await query.edit_message_text(
                f"âœ… <b>{label} takibi kuruldu.</b>\n"
                f"â± {html.escape(format_interval(interval))}\n"
                f"ID: <code>{html.escape(item['subscription_id'][:8])}</code>",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("ğŸ“‹ Bildirimlerim", callback_data=f"notify_list:{owner_id}")],
                    [InlineKeyboardButton("ğŸ”” Merkeze dÃ¶n", callback_data=f"notify_center:{owner_id}")],
                ]),
                parse_mode="HTML",
            )
            return
        if action == "notify_list":
            if not self.subscription_service:
                await query.answer("Bildirim servisi hazÄ±r deÄŸil.", show_alert=True)
                return
            rows = await self.subscription_service.store.list_for_user(owner_id)
            text, markup = render_rows(rows, owner_id=owner_id)
            back = [InlineKeyboardButton("ğŸ”” Bildirim merkezine dÃ¶n", callback_data=f"notify_center:{owner_id}")]
            keyboard = list(markup.inline_keyboard) if markup else []
            keyboard.append(back)
            await query.edit_message_text(
                text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
            )
            return
        if action == "notify_stop":
            if not self.subscription_service or len(parts) != 3 or owner_id != update.effective_user.id:
                await query.answer("Bu bildirimi yÃ¶netemezsin.", show_alert=True)
                return
            stopped = await self.subscription_service.store.disable(update.effective_user.id, parts[2])
            await query.answer("Bildirim durduruldu." if stopped else "Bildirim zaten kapalÄ±.", show_alert=not stopped)
            rows = await self.subscription_service.store.list_for_user(owner_id)
            text, markup = render_rows(rows, owner_id=owner_id)
            keyboard = list(markup.inline_keyboard) if markup else []
            keyboard.append([InlineKeyboardButton("ğŸ”” Merkeze dÃ¶n", callback_data=f"notify_center:{owner_id}")])
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
            return
        if action == "notify_group_weather":
            if not is_group or not await self._is_group_admin(update, context):
                await query.answer("Bu ayarÄ± yalnÄ±zca grup yÃ¶neticileri deÄŸiÅŸtirebilir.", show_alert=True)
                return
            await self._show_notification_city_page(update, query, owner_id, "weather", 0, group=True)
            return
        if action == "notify_group_weather_page":
            if not is_group or not await self._is_group_admin(update, context) or len(parts) != 3:
                await query.answer("Bu ÅŸehir sayfasÄ±nÄ± yalnÄ±zca grup yÃ¶neticileri aÃ§abilir.", show_alert=True)
                return
            try:
                page = int(parts[2])
            except ValueError:
                await query.answer("GeÃ§ersiz ÅŸehir sayfasÄ±.", show_alert=True)
                return
            await self._show_notification_city_page(update, query, owner_id, "weather", page, group=True)
            return
        if action == "notify_group_weather_pick":
            if not is_group or not await self._is_group_admin(update, context) or len(parts) != 3:
                await query.answer("Bu ayarÄ± yalnÄ±zca grup yÃ¶neticileri deÄŸiÅŸtirebilir.", show_alert=True)
                return
            await self._handle_weather_callback(
                update, query, context,
                f"weather_group_opts:{update.effective_user.id}:{parts[2]}",
            )
            return
        if action == "notify_group_air_quality":
            if not is_group or not await self._is_group_admin(update, context):
                await query.answer("Bu ayarÄ± yalnÄ±zca grup yÃ¶neticileri deÄŸiÅŸtirebilir.", show_alert=True)
                return
            await self._show_notification_city_page(update, query, update.effective_user.id, "air_quality", 0, group=True)
            return
        if action == "notify_group_air_quality_page":
            if not is_group or not await self._is_group_admin(update, context) or len(parts) != 3:
                await query.answer("Bu ÅŸehir sayfasÄ±nÄ± yalnÄ±zca grup yÃ¶neticileri aÃ§abilir.", show_alert=True)
                return
            try:
                page = int(parts[2])
            except ValueError:
                await query.answer("GeÃ§ersiz ÅŸehir sayfasÄ±.", show_alert=True)
                return
            await self._show_notification_city_page(update, query, update.effective_user.id, "air_quality", page, group=True)
            return
        if action == "notify_group_air_quality_pick":
            if not is_group or not await self._is_group_admin(update, context) or len(parts) != 3:
                await query.answer("Bu ayarÄ± yalnÄ±zca grup yÃ¶neticileri deÄŸiÅŸtirebilir.", show_alert=True)
                return
            try:
                plate = int(parts[2])
            except ValueError:
                await query.answer("Åehir seÃ§imi geÃ§ersiz.", show_alert=True)
                return
            await query.edit_message_text(
                "ğŸŒ¿ <b>Grup hava kalitesi</b>\n\nBildirim sÄ±klÄ±ÄŸÄ±nÄ± seÃ§:",
                reply_markup=air_quality_group_frequencies(update.effective_user.id, plate),
                parse_mode="HTML",
            )
            return
        if action == "notify_group_air_quality_freq":
            if not is_group or not await self._is_group_admin(update, context) or len(parts) != 4:
                await query.answer("Bu ayarÄ± yalnÄ±zca grup yÃ¶neticileri deÄŸiÅŸtirebilir.", show_alert=True)
                return
            if parts[3] not in {"daily", "weekly"}:
                await query.answer("GeÃ§ersiz bildirim sÄ±klÄ±ÄŸÄ±.", show_alert=True)
                return
            try:
                plate = int(parts[2])
            except ValueError:
                await query.answer("Åehir seÃ§imi geÃ§ersiz.", show_alert=True)
                return
            await query.edit_message_text(
                "Bildirim saatini seÃ§ (TÃ¼rkiye saati):",
                reply_markup=air_quality_group_times(update.effective_user.id, plate, parts[3]),
                parse_mode="HTML",
            )
            return
        if action == "notify_group_air_quality_time":
            if not is_group or not await self._is_group_admin(update, context) or len(parts) != 6:
                await query.answer("Grup bildirim servisi hazÄ±r deÄŸil veya iÅŸlem geÃ§ersiz.", show_alert=True)
                return
            schedule_kind = parts[3]
            local_time = f"{parts[4]}:{parts[5]}"
            if schedule_kind not in {"daily", "weekly"} or parts[4] not in {"08", "10", "18", "22"} or parts[5] != "00":
                await query.answer("GeÃ§ersiz bildirim saati.", show_alert=True)
                return
            try:
                plate = int(parts[2])
            except ValueError:
                await query.answer("Åehir seÃ§imi geÃ§ersiz.", show_alert=True)
                return
            city = self.chat_engine.city_directory.by_plate(plate)
            if city is None or self.subscription_service is None:
                await query.answer("Åehir veya bildirim servisi bulunamadÄ±.", show_alert=True)
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
            frequency = "her gÃ¼n" if schedule_kind == "daily" else "her pazartesi"
            await query.edit_message_text(
                f"âœ… <b>{html.escape(city.name)}</b> iÃ§in grup hava kalitesi bildirimi ayarlandÄ±.\n"
                f"ğŸ—“ {frequency} Â· â° {html.escape(local_time)} (TÃ¼rkiye saati)\n"
                f"ID: <code>{item['subscription_id'][:8]}</code>",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("ğŸ“‹ Grup bildirimleri", callback_data=f"notify_group_list:{update.effective_user.id}")],
                    [InlineKeyboardButton("ğŸ“£ Merkeze dÃ¶n", callback_data=f"notify_center:{update.effective_user.id}")],
                ]),
                parse_mode="HTML",
            )
            return
        if action == "notify_group_news":
            if not is_group or not await self._is_group_admin(update, context):
                await query.answer("Bu ayarÄ± yalnÄ±zca grup yÃ¶neticileri deÄŸiÅŸtirebilir.", show_alert=True)
                return
            await query.edit_message_text(
                "ğŸ“° <b>Grup haber Ã¶zeti</b>\n\nBildirim sÄ±klÄ±ÄŸÄ±nÄ± seÃ§:",
                reply_markup=news_group_frequencies(update.effective_user.id),
                parse_mode="HTML",
            )
            return
        if action == "notify_group_news_freq":
            if not is_group or not await self._is_group_admin(update, context) or len(parts) != 3:
                await query.answer("Bu ayarÄ± yalnÄ±zca grup yÃ¶neticileri deÄŸiÅŸtirebilir.", show_alert=True)
                return
            if parts[2] not in {"daily", "weekly"}:
                await query.answer("GeÃ§ersiz bildirim sÄ±klÄ±ÄŸÄ±.", show_alert=True)
                return
            await query.edit_message_text(
                "Haber Ã¶zetinin gÃ¶nderileceÄŸi saati seÃ§ (TÃ¼rkiye saati):",
                reply_markup=news_group_times(update.effective_user.id, parts[2]),
                parse_mode="HTML",
            )
            return
        if action == "notify_group_news_time":
            if not is_group or not await self._is_group_admin(update, context) or len(parts) != 5:
                await query.answer("Grup bildirim servisi hazÄ±r deÄŸil veya iÅŸlem geÃ§ersiz.", show_alert=True)
                return
            schedule_kind = parts[2]
            local_time = f"{parts[3]}:{parts[4]}"
            if schedule_kind not in {"daily", "weekly"} or parts[3] not in {"08", "10", "18", "22"} or parts[4] != "00":
                await query.answer("GeÃ§ersiz bildirim saati.", show_alert=True)
                return
            if self.subscription_service is None:
                await query.answer("Grup bildirim servisi hazÄ±r deÄŸil.", show_alert=True)
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
            frequency = "her gÃ¼n" if schedule_kind == "daily" else "her pazartesi"
            await query.edit_message_text(
                "âœ… <b>Grup haber Ã¶zeti ayarlandÄ±.</b>\n"
                f"ğŸ—“ {frequency} Â· â° {html.escape(local_time)} (TÃ¼rkiye saati)\n"
                f"ID: <code>{item['subscription_id'][:8]}</code>",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("ğŸ“‹ Grup bildirimleri", callback_data=f"notify_group_list:{update.effective_user.id}")],
                    [InlineKeyboardButton("ğŸ“£ Merkeze dÃ¶n", callback_data=f"notify_center:{update.effective_user.id}")],
                ]),
                parse_mode="HTML",
            )
            return
        if action == "notify_group_earthquake":
            if not is_group or not await self._is_group_admin(update, context):
                await query.answer("Bu ayarÄ± yalnÄ±zca grup yÃ¶neticileri deÄŸiÅŸtirebilir.", show_alert=True)
                return
            text, markup = await self._earthquake_result_text(update, context, update.effective_user.id)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
            return
        if action == "notify_group_announcement":
            if not is_group or not await self._is_group_admin(update, context):
                await query.answer("PlanlÄ± duyuruyu yalnÄ±zca grup yÃ¶neticileri yÃ¶netebilir.", show_alert=True)
                return
            await query.edit_message_text(
                "ğŸ“£ <b>PlanlÄ± grup duyurusu</b>\n\n"
                "Komut biÃ§imi:\n<code>/duyuru her gÃ¼n 10:00 ToplantÄ± baÅŸlÄ±yor</code>\n"
                "veya\n<code>/duyuru her pazartesi 09:00 HaftalÄ±k Ã¶zet</code>",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("ğŸ“£ Merkeze dÃ¶n", callback_data=f"notify_center:{owner_id}")]]
                ),
                parse_mode="HTML",
            )
            return
        if action == "notify_group_list":
            if not self.subscription_service:
                await query.answer("Bildirim servisi hazÄ±r deÄŸil.", show_alert=True)
                return
            rows = await self.subscription_service.store.list_for_chat(update.effective_chat.id)
            text, markup = render_rows(rows, group=True, owner_id=owner_id)
            keyboard = list(markup.inline_keyboard) if markup else []
            keyboard.append([InlineKeyboardButton("ğŸ“£ Merkeze dÃ¶n", callback_data=f"notify_center:{owner_id}")])
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
            return
        if action == "notify_group_stop":
            if not self.subscription_service or not is_group or len(parts) != 3:
                await query.answer("Grup bildirimi bulunamadÄ±.", show_alert=True)
                return
            if not await self._is_group_admin(update, context):
                await query.answer("Bu iÅŸlemi yalnÄ±zca grup yÃ¶neticileri yapabilir.", show_alert=True)
                return
            stopped = await self.subscription_service.store.disable_for_chat(
                update.effective_chat.id, parts[2]
            )
            await query.answer("Grup bildirimi durduruldu." if stopped else "Bildirim zaten kapalÄ±.", show_alert=not stopped)
            rows = await self.subscription_service.store.list_for_chat(update.effective_chat.id)
            text, markup = render_rows(rows, group=True, owner_id=update.effective_user.id)
            keyboard = list(markup.inline_keyboard) if markup else []
            keyboard.append([InlineKeyboardButton("ğŸ“£ Merkeze dÃ¶n", callback_data=f"notify_center:{update.effective_user.id}")])
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
            return
        await query.answer("Bilinmeyen bildirim iÅŸlemi.", show_alert=True)

    async def _handle_notification_preference_callback(self, update, query, data: str):
        if not self.notification_preference_store:
            await query.answer("Bildirim ayar servisi hazÄ±r deÄŸil.", show_alert=True)
            return
        parts = data.split(":")
        if len(parts) < 3:
            await query.answer("GeÃ§ersiz bildirim ayarÄ±.", show_alert=True)
            return
        try:
            owner_id = int(parts[2])
        except ValueError:
            await query.answer("GeÃ§ersiz bildirim ayarÄ±.", show_alert=True)
            return
        if owner_id != update.effective_user.id:
            await query.answer("Bu menÃ¼ baÅŸka bir kullanÄ±cÄ±ya ait.", show_alert=True)
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
                await query.answer("GeÃ§ersiz saat dilimi seÃ§imi.", show_alert=True)
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
                await query.answer("GeÃ§ersiz sessiz saat seÃ§imi.", show_alert=True)
                return
            start, end = presets[parts[3]]
            await self.notification_preference_store.update(
                owner_id, quiet_hours_enabled=True, quiet_start=start, quiet_end=end
            )
        else:
            await query.answer("GeÃ§ersiz bildirim ayarÄ±.", show_alert=True)
            return
        prefs = await self.notification_preference_store.get(owner_id)
        text, markup = notification_preferences_panel(owner_id, prefs)
        await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")

    async def _handle_culture_callback(self, update, query, data: str):
        """Handle the owner-scoped category picker without doing provider work."""

        parts = data.split(":")
        user_id = int(update.effective_user.id)
        if len(parts) < 2 or parts[1] != str(user_id):
            await query.answer("Bu Ã¶neri menÃ¼sÃ¼ baÅŸka bir kullanÄ±cÄ±ya ait.", show_alert=True)
            return
        if parts[0] == "culture_menu":
            if len(parts) != 2:
                await query.answer("GeÃ§ersiz Ã¶neri menÃ¼sÃ¼.", show_alert=True)
                return
            text, markup = culture_category_panel(user_id)
        elif parts[0] == "culture":
            # A category callback has one additional segment and is validated
            # before it reaches the pure presentation helper.
            if len(parts) != 3:
                await query.answer("GeÃ§ersiz Ã¶neri menÃ¼sÃ¼.", show_alert=True)
                return
            text, markup = culture_category_help(user_id, parts[2])
        else:
            await query.answer("GeÃ§ersiz Ã¶neri menÃ¼sÃ¼.", show_alert=True)
            return
        await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")

    async def _handle_productivity_callback(self, update, query, data: str):
        if not self.productivity_service:
            await query.answer("Ãœretkenlik servisi hazÄ±r deÄŸil.", show_alert=True)
            return
        parts = data.split(":", 3)
        if len(parts) != 4:
            await query.answer("GeÃ§ersiz iÅŸlem.", show_alert=True)
            return
        action, owner_raw, item_id = parts[1], parts[2], parts[3]
        try:
            owner_id = int(owner_raw)
        except ValueError:
            await query.answer("GeÃ§ersiz iÅŸlem.", show_alert=True)
            return
        if owner_id != update.effective_user.id:
            await query.answer("Bu liste baÅŸka bir kullanÄ±cÄ±ya ait.", show_alert=True)
            return
        if action == "confirmclear":
            labels = {"notes": "notlar", "tasks": "gÃ¶revler", "reminders": "hatÄ±rlatÄ±cÄ±lar"}
            label = labels.get(item_id)
            if not label:
                await query.answer("GeÃ§ersiz toplu iÅŸlem.", show_alert=True)
                return
            await query.edit_message_text(
                f"âš ï¸ <b>{label.title()} tamamen silinsin mi?</b>\n\nBu iÅŸlem geri alÄ±namaz.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("âœ… Evet, sil", callback_data=f"prod:clear:{owner_id}:{item_id}"),
                    InlineKeyboardButton("Ä°ptal", callback_data=f"prod:cancelclear:{owner_id}:{item_id}"),
                ]]),
                parse_mode="HTML",
            )
            return
        if action == "cancelclear":
            await query.edit_message_text("Ä°ÅŸlem iptal edildi.", parse_mode="HTML")
            return
        if action == "clear":
            if item_id not in {"notes", "tasks", "reminders"}:
                await query.answer("GeÃ§ersiz toplu iÅŸlem.", show_alert=True)
                return
            deleted = await self.productivity_service.clear_user_data(owner_id, sections={item_id})
            await query.answer("Veriler silindi.")
            await query.edit_message_text(
                f"âœ… {deleted[item_id]} kayÄ±t silindi.", parse_mode="HTML"
            )
            return
        actions = {
            "td": (self.productivity_service.complete_task, "GÃ¶rev tamamlandÄ±.", "GÃ¶rev zaten tamamlanmÄ±ÅŸ veya bulunamadÄ±."),
            "tx": (self.productivity_service.delete_task, "GÃ¶rev silindi.", "GÃ¶rev bulunamadÄ±."),
            "nd": (self.productivity_service.delete_note, "Not silindi.", "Not bulunamadÄ±."),
            "rd": (self.productivity_service.delete_reminder, "HatÄ±rlatÄ±cÄ± iptal edildi.", "HatÄ±rlatÄ±cÄ± bulunamadÄ±."),
        }
        operation = actions.get(action)
        if operation is None:
            await query.answer("GeÃ§ersiz iÅŸlem.", show_alert=True)
            return
        ok = await operation[0](owner_id, item_id)
        await query.answer(operation[1] if ok else operation[2], show_alert=not ok)
        if ok:
            await query.edit_message_text(f"\u2705 {operation[1]}", parse_mode="HTML")

    async def _weather_result_text(self, update, context, user_id: int, plate: int, days: int = 3):
        city = self.chat_engine.city_directory.by_plate(plate)
        if city is None:
            return "âš ï¸ Åehir bulunamadÄ±.", None
        if days not in {1, 3, 7}:
            days = 3
        result = await self.chat_engine.feature_executor.execute("weather", {"city": city.name, "days": days})
        if not result.ok:
            return "âš ï¸ Hava durumu ÅŸu an alÄ±namÄ±yor.", None
        content = format_feature_result(result, "âš ï¸ Hava durumu ÅŸu an alÄ±namÄ±yor.")
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
            await query.answer("GeÃ§ersiz hava durumu iÅŸlemi.", show_alert=True)

    async def _handle_weather_callback_unchecked(self, update: Update, query, context, data: str) -> None:
        parts = data.split(":")
        action = parts[0]
        try:
            owner_id = int(parts[1])
        except (IndexError, ValueError):
            await query.answer("GeÃ§ersiz hava durumu iÅŸlemi.", show_alert=True)
            return
        is_group = update.effective_chat.type in {"group", "supergroup"}
        if action.startswith("weather_group"):
            if not is_group or not await self._is_group_admin(update, context):
                await query.answer("Bu ayarÄ± yalnÄ±zca grup yÃ¶neticileri deÄŸiÅŸtirebilir.", show_alert=True)
                return
            # Group controls belong to the chat, so any current administrator
            # may operate a menu originally opened by another administrator.
            owner_id = update.effective_user.id
        elif is_group:
            if action not in {"weather_show", "weather_pick", "weather_forecast"}:
                await query.answer("Grup bildirimlerini yÃ¶neticiler yÃ¶netir.", show_alert=True)
                return
            owner_id = update.effective_user.id
        elif owner_id != update.effective_user.id:
            await query.answer("Bu hava durumu menÃ¼sÃ¼ baÅŸka bir kullanÄ±cÄ±ya ait.", show_alert=True)
            return
        if action == "weather_group_opts":
            plate = int(parts[2])
            await query.edit_message_text(
                "ğŸ“£ <b>Grup hava durumu bildirimi</b>\n\nBildirim sÄ±klÄ±ÄŸÄ±nÄ± seÃ§:",
                reply_markup=weather_group_frequencies(owner_id, plate),
                parse_mode="HTML",
            )
            return
        if action == "weather_group_freq":
            plate = int(parts[2])
            schedule_kind = parts[3]
            await query.edit_message_text(
                "Bildirim saatini seÃ§ (TÃ¼rkiye saati):",
                reply_markup=weather_group_times(owner_id, plate, schedule_kind),
                parse_mode="HTML",
            )
            return
        if action == "weather_group_time":
            if self.subscription_service is None or len(parts) != 6:
                await query.answer("Grup bildirim servisi hazÄ±r deÄŸil.", show_alert=True)
                return
            plate = int(parts[2])
            schedule_kind = parts[3]
            local_time = f"{parts[4]}:{parts[5]}"
            city = self.chat_engine.city_directory.by_plate(plate)
            if city is None:
                await query.answer("Åehir bulunamadÄ±.", show_alert=True)
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
            frequency = "her gÃ¼n" if schedule_kind == "daily" else "her pazartesi"
            await query.edit_message_text(
                f"âœ… <b>{html.escape(city.name)}</b> iÃ§in grup bildirimi ayarlandÄ±.\n"
                f"ğŸ—“ {frequency} Â· â° {html.escape(local_time)} (TÃ¼rkiye saati)\n"
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
                city = html.escape(str((row.get("params") or {}).get("city", "Åehir")))
                schedule = "her gÃ¼n" if row.get("schedule_kind") == "daily" else "her pazartesi"
                lines.append(f"â€¢ {city} Â· {schedule} Â· {html.escape(str(row.get('local_time', '10:00')))}")
                keyboard.append([
                    InlineKeyboardButton(
                        f"ğŸ›‘ {city} bildirimini durdur",
                        callback_data=f"weather_group_stop:{owner_id}:{row['subscription_id']}",
                    )
                ])
            if not active_rows:
                lines.append("\nBu grup iÃ§in henÃ¼z bildirim kurulmamÄ±ÅŸ.")
            plate = parts[2] if len(parts) > 2 else "34"
            keyboard.append([InlineKeyboardButton("ğŸ  Hava durumuna dÃ¶n", callback_data=f"weather_show:{owner_id}:{plate}")])
            await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
            return
        if action == "weather_group_stop":
            if self.subscription_service is None or len(parts) != 3:
                await query.answer("Bildirim bulunamadÄ±.", show_alert=True)
                return
            await self.subscription_service.store.disable_for_chat(update.effective_chat.id, parts[2])
            await query.answer("Grup bildirimi durduruldu.")
            await query.edit_message_text("âœ… Grup hava durumu bildirimi durduruldu.", parse_mode="HTML")
            return
        if action == "weather_list":
            rows = await self.subscription_service.store.list_for_user(owner_id) if self.subscription_service else []
            lines = ["<b>Hava durumu takiplerin</b>"]
            keyboard = []
            weather_rows = [row for row in rows if row.get("feature_key") == "weather" and row.get("enabled")]
            for row in weather_rows:
                city = html.escape(str((row.get("params") or {}).get("city", "Åehir")))
                interval_label = format_interval(int(row.get("interval_seconds", 3600)))
                lines.append(f"â€¢ {city} Â· {interval_label}")
                keyboard.append([InlineKeyboardButton(f"ğŸ›‘ {city} takibini durdur", callback_data=f"weather_stop:{owner_id}:{row['subscription_id']}")])
            if not weather_rows:
                lines.append("\nHenÃ¼z hava durumu takibin yok.")
            fallback_plate = parts[2] if len(parts) > 2 else "34"
            keyboard.append([InlineKeyboardButton("ğŸ  Hava durumuna dÃ¶n", callback_data=f"weather_show:{owner_id}:{fallback_plate}")])
            await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
            return
        if action == "weather_opts":
            plate = int(parts[2])
            city = self.chat_engine.city_directory.by_plate(plate)
            label = html.escape(city.name if city else "ÅŸehir")
            await query.edit_message_text(
                f"<b>{label}</b> iÃ§in bildirim sÄ±klÄ±ÄŸÄ±nÄ± seÃ§:",
                reply_markup=weather_intervals(owner_id, plate),
                parse_mode="HTML",
            )
            return
        if action == "weather_add":
            plate = int(parts[2])
            interval = int(parts[3])
            city = self.chat_engine.city_directory.by_plate(plate)
            if city is None or self.subscription_service is None:
                await query.answer("Åehir bulunamadÄ±.", show_alert=True)
                return
            request = SubscriptionRequest("weather", {"city": city.name}, interval, format_interval(interval))
            item = await self.subscription_service.create(owner_id, update.effective_chat.id, request)
            await query.edit_message_text(
                f"âœ… <b>{html.escape(city.name)}</b> hava durumu takibi kuruldu.\n"
                f"Bildirim sÄ±klÄ±ÄŸÄ±: {request.human_interval}\nID: <code>{item['subscription_id'][:8]}</code>",
                reply_markup=weather_actions(owner_id, city.plate),
                parse_mode="HTML",
            )
            return
        if action == "weather_stop":
            if self.subscription_service is None or len(parts) != 3:
                await query.answer("Takip bulunamadÄ±.", show_alert=True)
                return
            subscription_id = parts[2]
            rows = await self.subscription_service.store.list_for_user(owner_id)
            row = next((item for item in rows if item["subscription_id"] == subscription_id), None)
            if row:
                await self.subscription_service.store.disable(owner_id, subscription_id)
            await query.answer("Hava durumu takibi durduruldu.")
            await query.edit_message_text("âœ… Hava durumu bildirimi durduruldu.", parse_mode="HTML")
            return
        if action == "weather_forecast":
            try:
                plate = int(parts[2])
                days = int(parts[3])
            except (IndexError, ValueError):
                await query.answer("GeÃ§ersiz tahmin seÃ§imi.", show_alert=True)
                return
            text, markup = await self._weather_result_text(update, context, owner_id, plate, days)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
            return
        if action in {"weather_show", "weather_pick"}:
            plate = int(parts[2])
            text, markup = await self._weather_result_text(update, context, owner_id, plate)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
            return
        await query.answer("Bilinmeyen hava durumu iÅŸlemi.", show_alert=True)

    async def _handle_air_quality_callback(self, update, query, context, data: str) -> None:
        """Render AQI from the same typed provider used by the command surface."""
        parts = data.split(":")
        if len(parts) != 3:
            await query.answer("GeÃ§ersiz hava kalitesi iÅŸlemi.", show_alert=True)
            return
        try:
            owner_id = int(parts[1])
            plate = int(parts[2])
        except ValueError:
            await query.answer("GeÃ§ersiz ÅŸehir seÃ§imi.", show_alert=True)
            return
        is_group = update.effective_chat.type in {"group", "supergroup"}
        if not is_group and owner_id != update.effective_user.id:
            await query.answer("Bu hava kalitesi menÃ¼sÃ¼ baÅŸka bir kullanÄ±cÄ±ya ait.", show_alert=True)
            return
        city = self.chat_engine.city_directory.by_plate(plate)
        if city is None:
            await query.answer("Åehir bulunamadÄ±.", show_alert=True)
            return
        result = await self.chat_engine.feature_executor.execute("air_quality", {"city": city.name})
        if not result.ok:
            await query.edit_message_text(
                "âš ï¸ Hava kalitesi verisi ÅŸu an alÄ±namÄ±yor.",
                reply_markup=air_quality_actions(update.effective_user.id, plate),
                parse_mode="HTML",
            )
            return
        content = format_feature_result(result, "âš ï¸ Hava kalitesi verisi ÅŸu an alÄ±namÄ±yor.")
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
            return "âš ï¸ Deprem verileri ÅŸu an alÄ±namÄ±yor.", earthquake_actions(
                user_id, is_group=is_group, can_manage=can_manage
            )
        content = format_feature_result(result, "âš ï¸ Deprem verileri ÅŸu an alÄ±namÄ±yor.")
        return content, earthquake_actions(
            user_id, is_group=is_group, can_manage=can_manage
        )

    async def _handle_earthquake_callback(self, update: Update, query, context, data: str) -> None:
        """Handle earthquake callbacks with a bounded malformed-input guard."""
        try:
            await self._handle_earthquake_callback_unchecked(update, query, context, data)
        except (IndexError, TypeError, ValueError):
            await query.answer("GeÃ§ersiz deprem iÅŸlemi.", show_alert=True)

    async def _handle_earthquake_callback_unchecked(self, update: Update, query, context, data: str) -> None:
        parts = data.split(":")
        action = parts[0]
        try:
            owner_id = int(parts[1])
        except (IndexError, ValueError):
            await query.answer("GeÃ§ersiz deprem iÅŸlemi.", show_alert=True)
            return
        is_group = update.effective_chat.type in {"group", "supergroup"}
        if action.startswith("earthquake_group"):
            if not is_group or not await self._is_group_admin(update, context):
                await query.answer("Bu ayarÄ± yalnÄ±zca grup yÃ¶neticileri deÄŸiÅŸtirebilir.", show_alert=True)
                return
            # The group owns the schedule; any current admin can manage it.
            owner_id = update.effective_user.id
        elif is_group:
            if action != "earthquake_show":
                await query.answer("Grup bildirimlerini yÃ¶neticiler yÃ¶netir.", show_alert=True)
                return
            owner_id = update.effective_user.id
        elif owner_id != update.effective_user.id:
            await query.answer("Bu deprem menÃ¼sÃ¼ baÅŸka bir kullanÄ±cÄ±ya ait.", show_alert=True)
            return

        if action == "earthquake_group_opts":
            await query.edit_message_text(
                "ğŸ“£ <b>Grup deprem bildirimi</b>\n\nBildirim sÄ±klÄ±ÄŸÄ±nÄ± seÃ§:",
                reply_markup=earthquake_group_frequencies(owner_id), parse_mode="HTML"
            )
            return
        if action == "earthquake_group_freq":
            schedule_kind = parts[2] if len(parts) > 2 else ""
            if schedule_kind not in {"daily", "weekly"}:
                await query.answer("GeÃ§ersiz bildirim sÄ±klÄ±ÄŸÄ±.", show_alert=True)
                return
            await query.edit_message_text(
                "Bildirim saatini seÃ§ (TÃ¼rkiye saati):",
                reply_markup=earthquake_group_times(owner_id, schedule_kind), parse_mode="HTML"
            )
            return
        if action == "earthquake_group_time":
            if self.subscription_service is None or len(parts) != 5:
                await query.answer("Grup bildirim servisi hazÄ±r deÄŸil.", show_alert=True)
                return
            schedule_kind = parts[2]
            local_time = f"{parts[3]}:{parts[4]}"
            if schedule_kind not in {"daily", "weekly"}:
                await query.answer("GeÃ§ersiz bildirim sÄ±klÄ±ÄŸÄ±.", show_alert=True)
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
            frequency = "her gÃ¼n" if schedule_kind == "daily" else "her pazartesi"
            await query.edit_message_text(
                "âœ… <b>Grup deprem bildirimi ayarlandÄ±.</b>\n"
                f"ğŸ—“ {frequency} Â· â° {html.escape(local_time)} (TÃ¼rkiye saati)\n"
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
                schedule = "her gÃ¼n" if row.get("schedule_kind") == "daily" else "her pazartesi"
                local_time = html.escape(str(row.get("local_time", "10:00")))
                lines.append(f"â€¢ {schedule} Â· {local_time} (TÃ¼rkiye saati)")
                keyboard.append([InlineKeyboardButton(
                    "ğŸ›‘ Deprem bildirimini durdur",
                    callback_data=f"earthquake_group_stop:{owner_id}:{row['subscription_id']}",
                )])
            if not active:
                lines.append("\nBu grup iÃ§in henÃ¼z deprem bildirimi kurulmamÄ±ÅŸ.")
            keyboard.append([InlineKeyboardButton("ğŸ  Deprem akÄ±ÅŸÄ±na dÃ¶n", callback_data=f"earthquake_show:{owner_id}")])
            await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
            return
        if action == "earthquake_group_stop":
            if self.subscription_service is None or len(parts) != 3:
                await query.answer("Bildirim bulunamadÄ±.", show_alert=True)
                return
            stopped = await self.subscription_service.store.disable_for_chat(update.effective_chat.id, parts[2])
            await query.answer("Grup deprem bildirimi durduruldu." if stopped else "Bildirim zaten kapalÄ±.")
            await query.edit_message_text("âœ… Grup deprem bildirimi durduruldu.", parse_mode="HTML")
            return
        if action == "earthquake_opts":
            await query.edit_message_text(
                "<b>Deprem bildirimi sÄ±klÄ±ÄŸÄ±nÄ± seÃ§:</b>",
                reply_markup=earthquake_intervals(owner_id), parse_mode="HTML"
            )
            return
        if action == "earthquake_add":
            if self.subscription_service is None or len(parts) != 3:
                await query.answer("Bildirim servisi hazÄ±r deÄŸil.", show_alert=True)
                return
            interval = int(parts[2])
            request = SubscriptionRequest("earthquakes", {}, interval, format_interval(interval))
            item = await self.subscription_service.create(owner_id, update.effective_chat.id, request)
            await query.edit_message_text(
                f"âœ… Deprem bildirimi kuruldu.\nSÄ±klÄ±k: {html.escape(request.human_interval)}\n"
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
                lines.append(f"â€¢ {html.escape(label)}")
                keyboard.append([InlineKeyboardButton(
                    "ğŸ›‘ Bu takibi durdur",
                    callback_data=f"earthquake_stop:{owner_id}:{row['subscription_id']}",
                )])
            if not active:
                lines.append("\nHenÃ¼z deprem takibin yok.")
            keyboard.append([InlineKeyboardButton("ğŸ  Deprem akÄ±ÅŸÄ±na dÃ¶n", callback_data=f"earthquake_show:{owner_id}")])
            await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
            return
        if action == "earthquake_stop":
            if self.subscription_service is None or len(parts) != 3:
                await query.answer("Takip bulunamadÄ±.", show_alert=True)
                return
            await self.subscription_service.store.disable(owner_id, parts[2])
            await query.answer("Deprem takibi durduruldu.")
            await query.edit_message_text("âœ… Deprem bildirimi durduruldu.", parse_mode="HTML")
            return
        if action == "earthquake_show":
            text, markup = await self._earthquake_result_text(update, context, owner_id)
            await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
            return
        await query.answer("Bilinmeyen deprem iÅŸlemi.", show_alert=True)

    async def _show_help_main(self, query):
        help_text = (
            "ğŸŒŸ <b>AREL ASÄ°STAN - KOMUT REHBERÄ°</b> ğŸŒŸ\n\n"
            "AÅŸaÄŸÄ±daki kategorilere tÄ±klayarak tÃ¼m yeteneklerimi keÅŸfedebilirsin. "
            "Her kategori altÄ±nda daha spesifik Ã¶zellik butonlarÄ± bulunmaktadÄ±r. ğŸ‘‡"
        )
        keyboard = [
            [InlineKeyboardButton("ğŸ“¹ Medya Ä°ndirici", callback_data="help_cat_download"), InlineKeyboardButton("ğŸ¤– Yapay Zeka", callback_data="help_cat_ai")],
            [InlineKeyboardButton("ğŸ® Oyunlar", callback_data="help_cat_games"), InlineKeyboardButton("ğŸ’¬ Quotly (AlÄ±ntÄ±)", callback_data="help_cat_quote")],
            [InlineKeyboardButton("ğŸ¨ Sticker Sistemi", callback_data="help_cat_sticker"), InlineKeyboardButton("ğŸ“Š Finans & KÃ¼ltÃ¼r", callback_data="help_cat_finance")],
            [InlineKeyboardButton("âš™ï¸ AraÃ§lar & DiÄŸer", callback_data="help_cat_tools"), InlineKeyboardButton("ğŸš€ GeliÅŸtirme Fikirleri", callback_data="help_cat_suggestions")]
        ]
        await query.edit_message_text(help_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

    async def _show_help_category(self, query, data):
        btn_back_main = InlineKeyboardButton("ğŸ  Ana MenÃ¼ye DÃ¶n", callback_data="help_main")
        category_map = {
            "help_cat_download": ("ğŸ“¹", "MEDYA Ä°NDÄ°RÄ°CÄ° SÄ°STEMÄ°", [
                [InlineKeyboardButton("ğŸ“¸ Instagram", callback_data="help_feat_ig"), InlineKeyboardButton("ğŸµ TikTok", callback_data="help_feat_tt")],
                [InlineKeyboardButton("â–¶ï¸ YouTube", callback_data="help_feat_yt"), InlineKeyboardButton("ğŸ¦ X (Twitter)", callback_data="help_feat_x")],
                [InlineKeyboardButton("ğŸ“˜ Facebook", callback_data="help_feat_fb")]
            ]),
            "help_cat_ai": ("ğŸ¤–", "AREL AI - YAPAY ZEKA MERKEZÄ°", [
                [InlineKeyboardButton("ğŸ’¬ Sohbet & Bilgi", callback_data="help_feat_chat"), InlineKeyboardButton("ğŸ–¼ï¸ GÃ¶rsel Ãœretimi", callback_data="help_feat_image")],
                [InlineKeyboardButton("ğŸ“„ Dosya & Belge", callback_data="help_feat_file"), InlineKeyboardButton("ğŸŒ AkÄ±llÄ± Ã‡eviri", callback_data="help_feat_trans")]
            ]),
            "help_cat_games": ("ğŸ®", "OYUN MERKEZÄ°", [
                [InlineKeyboardButton("ğŸ—£ï¸ Anlatmaca", callback_data="help_feat_anlat"), InlineKeyboardButton("ğŸª‘ Adam Asmaca", callback_data="help_feat_hangman")],
                [InlineKeyboardButton("ğŸ¯ Quiz / Bilgi", callback_data="help_feat_quiz"), InlineKeyboardButton("ğŸ¾ ÅiÅŸe Ã‡evirme", callback_data="help_feat_bottle")],
                [InlineKeyboardButton("ğŸƒ Batak", callback_data="help_feat_batak"), InlineKeyboardButton("âš”ï¸ DÃ¼ello", callback_data="help_feat_duello")],
                [InlineKeyboardButton("ğŸ° Kumar / DiÄŸer", callback_data="help_feat_gambling")]
            ]),
            "help_cat_quote": ("ğŸ’¬", "QUOTLY (MÃœKEMMEL ALINTILAR)", [
                [InlineKeyboardButton("ğŸ“Œ Temel KullanÄ±m", callback_data="help_feat_q_basic"), InlineKeyboardButton("ğŸ¨ TasarÄ±m (Flag)", callback_data="help_feat_q_flags")],
                [InlineKeyboardButton("ğŸ‘‘ Admin AyarlarÄ±", callback_data="help_feat_q_admin")]
            ]),
            "help_cat_sticker": ("ğŸ¨", "AREL STÄ°CKER FABRÄ°KASI", [
                [InlineKeyboardButton("âœ‚ï¸ Sticker Yap", callback_data="help_feat_st_make"), InlineKeyboardButton("ğŸ” AkÄ±llÄ± Arama", callback_data="help_feat_st_search")],
                [InlineKeyboardButton("ğŸ“¦ Paket & Katalog", callback_data="help_feat_st_manage")]
            ]),
            "help_cat_finance": ("ğŸ“Š", "FÄ°NANS & KÃœLTÃœR DURAÄI", [
                [InlineKeyboardButton("ğŸ“ˆ Borsa / Kripto", callback_data="help_feat_finance"), InlineKeyboardButton("ğŸ¬ AI Ã–nerileri", callback_data="help_feat_suggest")]
            ]),
            "help_cat_tools": ("âš™ï¸", "ARAÃ‡LAR & YARDIMCI KOMUTLAR", [
                [InlineKeyboardButton("â° HatÄ±rlatÄ±cÄ±", callback_data="help_feat_remind"), InlineKeyboardButton("ğŸ”Š TTS (Seslendirme)", callback_data="help_feat_tts")],
                [InlineKeyboardButton("ğŸ² Mini AraÃ§lar", callback_data="help_feat_mini_tools")]
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
            text = f"{icon} <b>{title}</b>\n\nLinkleri Ä±ÅŸÄ±k hÄ±zÄ±nda videoya dÃ¶nÃ¼ÅŸtÃ¼rÃ¼rÃ¼m. Platform detaylarÄ± iÃ§in butonlara dokun:"
            keyboard = keyboard_content + [[btn_back_main]]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

    async def _show_help_feature(self, query, data):
        btn_back_main = InlineKeyboardButton("ğŸ  Ana MenÃ¼ye DÃ¶n", callback_data="help_main")
        feat = data.replace("help_feat_", "")
        if feat == "q_admin" and not self.sudo_manager.is_owner(query.from_user.id):
            await query.answer("Bu bÃ¶lÃ¼m yalnÄ±zca bot sahibine aÃ§Ä±ktÄ±r.", show_alert=True)
            return
        detail_text = ""

        feat_map = {
            "ig": "download", "tt": "download", "yt": "download", "x": "download", "fb": "download",
            "chat": "ai", "image": "ai", "file": "ai", "trans": "ai",
            "anlat": "games", "hangman": "games", "quiz": "games", "bottle": "games", "batak": "games", "duello": "games", "gambling": "games",
            "q_basic": "quote", "q_flags": "quote", "q_admin": "quote",
            "st_make": "sticker", "st_search": "sticker", "st_manage": "sticker",
            "finance": "finance", "suggest": "finance",
            "remind": "tools", "tts": "tools", "mini_tools": "tools"
        }
        back_cat = f"help_cat_{feat_map.get(feat, 'main')}"

        feat_details = {
            "ig": "ğŸ“¸ <b>INSTAGRAM Ä°NDÄ°RÄ°CÄ°</b>\n\nInstagram'dan dilediÄŸin medyayÄ± saniyeler iÃ§inde kalitesini bozmadan indiririm.\n\nğŸŒ <b>Desteklenen Ä°Ã§erikler:</b>\nâ€¢ <b>Reels:</b> TÃ¼m makaralar.\nâ€¢ <b>Posts:</b> FotoÄŸraflar ve Ã§oklu kaydÄ±rmalÄ± gÃ¶nderiler.\nâ€¢ <b>Stories:</b> Hikaye linkleri.\n\nğŸ’¡ <b>KullanÄ±m:</b> GirdiÄŸin linki otomatik tanÄ±rÄ±m.",
            "tt": "ğŸµ <b>TIKTOK Ä°NDÄ°RÄ°CÄ°</b>\n\nTikTok videolarÄ±nÄ± filigransÄ±z (yazÄ±sÄ±z) ve HD kalitede indiririm.\n\nâœ¨ <b>Ã–zellikler:</b>\nâ€¢ YazÄ±sÄ±z (No Watermark) indirme.\nâ€¢ MP3 olarak ses ayÄ±klama.\nâ€¢ FotoÄŸraf slaytlarÄ±nÄ± video olarak birleÅŸtirme.",
            "yt": "â–¶ï¸ <b>YOUTUBE / SHORTS Ä°NDÄ°RÄ°CÄ°</b>\n\nYouTube videolarÄ±nÄ± ve Shorts videolarÄ±nÄ± indirir, istersen sese dÃ¶nÃ¼ÅŸtÃ¼rÃ¼rÃ¼m.\n\nğŸ“Š <b>Formatlar:</b>\nâ€¢ <b>MP4:</b> 720p/1080p\nâ€¢ <b>MP3:</b> MÃ¼zik olarak indirme.",
            "chat": "ğŸ’¬ <b>AREL AI SOHBET</b>\n\nGemini 2.0 Flash mimarisi ile her konuda uzman seviyesinde sohbet.\n\nğŸ§  <b>Neler Sorabilirsin?</b>\nâ€¢ <b>YazÄ±lÄ±m:</b> <i>\"Python'da asenkron fonksiyon nasÄ±l yazÄ±lÄ±r?\"</i>\nâ€¢ <b>EÄŸitim:</b> <i>\"Kuantum fiziÄŸini aÃ§Ä±kla\"</i>",
            "image": "ğŸ¨ <b>AI GÃ–RSEL ÃœRETÄ°MÄ°</b>\n\nKelimelerini sanata dÃ¶nÃ¼ÅŸtÃ¼ren gÃ¼Ã§lÃ¼ bir motor.\n\nğŸ› ï¸ <b>Komut:</b> <code>/image [prompt]</code>\n\nğŸ’¡ <b>GeliÅŸmiÅŸ Prompt:</b> <i>...cinematic lighting, digital art, 8k</i>",
            "anlat": "ğŸ—£ï¸ <b>KELÄ°ME ANLATMACA</b>\n\nArkadaÅŸlarÄ±nla grupta oynayabileceÄŸin en eÄŸlenceli oyun.\n\nğŸ® <b>AkÄ±ÅŸ:</b>\n1. <code>/anlat</code> yazarak lobi aÃ§.\n2. Bot sana Ã¶zelden <b>Hedef Kelime</b> verir.\n3. Grubun diÄŸerlerine anlatmaya Ã§alÄ±ÅŸ.",
            "hangman": "ğŸª‘ <b>ADAM ASMACA</b>\n\nKlasik harf tahmin etme oyunu.\n\nğŸ® <b>Komut:</b> <code>/adamasmaca</code>\n\nğŸ’¡ <b>NasÄ±l OynanÄ±r?</b>\nâ€¢ Bot gizli bir kelime tutar.\nâ€¢ Harf gÃ¶nderirsin.\nâ€¢ 6 yanlÄ±ÅŸ hakkÄ±n var!",
            "quiz": "ğŸ¯ <b>BÄ°LGÄ° YARIÅMASI</b>\n\nGenel kÃ¼ltÃ¼rÃ¼nÃ¼ test et.\n\nğŸ® <b>Komut:</b> <code>/quiz</code>\n\nğŸ“š <b>Kategoriler:</b> Tarih, CoÄŸrafya, Bilim, Sanat, Spor.",
            "bottle": "ğŸ¾ <b>ÅÄ°ÅE Ã‡EVÄ°RME</b>\n\nGrup iÃ§inde ÅŸiÅŸe dÃ¶ner, ÅŸanslÄ± kiÅŸi belirlenir.\n\nğŸ® <b>Komutlar:</b>\nâ€¢ <code>/sisecevir</code> â€” Lobi oluÅŸturur.\nâ€¢ <code>/d</code> â€” DoÄŸruluk sorusu.\nâ€¢ <code>/c</code> â€” Cesaret gÃ¶revi.",
            "batak": "ğŸƒ <b>KOZ MAÃ‡A (BATAK)</b>\n\nTelegram Ã¼zerindeki en geliÅŸmiÅŸ Batak sistemi.\n\nğŸ® <b>Komut:</b> <code>/batak</code>",
            "duello": "âš”ï¸ <b>DÃœELLO</b>\n\nGruptan birine meydan oku!\n\nğŸ® <b>KullanÄ±m:</b> Birinin mesajÄ±na yanÄ±t vererek <code>/duello</code> yaz.\n\nğŸ”« Bot geri sayÄ±m yapar. Ä°lk basan kazanÄ±r!",
            "gambling": "ğŸ° <b>KUMAR VE ÅANS OYUNLARI</b>\n\nğŸ² <b>Zar:</b> <code>/zar</code>\nğŸª™ <b>YazÄ±-Tura:</b> <code>/yazitura</code>\nğŸ° <b>Slot:</b> <code>/slot</code>\nğŸ”« <b>Rus Ruleti:</b> <code>/rusruleti</code>",
            "q_basic": "ğŸ“Œ <b>QUOTLY - TEMEL KULLANIM</b>\n\nMesajlarÄ±nÄ± stickerlara dÃ¶nÃ¼ÅŸtÃ¼r.\n\nğŸ”§ <b>Komutlar:</b>\nâ€¢ <code>/q</code> â€” YanÄ±tlanan tek mesajÄ± alÄ±ntÄ±lar.\nâ€¢ <code>/q [sayÄ±]</code> â€” X adet mesajÄ± alÄ±ntÄ±lar.\nâ€¢ <code>/q * [metin]</code> â€” GeÃ§miÅŸte arama yap.",
            "q_flags": "ğŸ¨ <b>QUOTLY - TASARIM</b>\n\nAlÄ±ntÄ±nÄ± kiÅŸiselleÅŸtir.\n\nğŸ› ï¸ <b>Mevcut Bayraklar:</b>\nâ€¢ <code>p</code> â€” PNG (Åeffaf).\nâ€¢ <code>rate</code> â€” BeÄŸen butonlarÄ±.\nâ€¢ <code>#ffff00</code> â€” Arka plan rengi.\nâ€¢ <code>s1.2</code> â€” Ã–lÃ§ekleme.",
            "finance": "ğŸ“ˆ <b>FÄ°NANSAL VERÄ° TAKÄ°BÄ°</b>\n\nğŸ’° <b>Kripto:</b> <code>/kripto [coin]</code>\nğŸ“Š <b>Borsa:</b> <code>/borsa [hisse]</code>\n\n7 gÃ¼nlÃ¼k grafik desteÄŸi!",
            "suggest": "ğŸ¬ <b>AREL AI Ã–NERÄ° SÄ°STEMÄ°</b>\n\nğŸ’¡ <b>Komutlar:</b>\nâ€¢ <code>/filmoneri [tÃ¼r]</code>\nâ€¢ <code>/dizioneri [tÃ¼r]</code>\nâ€¢ <code>/kitaponeri [tÃ¼r]</code>\nâ€¢ <code>/sarkioneri [mood]</code>",
            "remind": "â° <b>AKILLI HATIRLATICI</b>\n\nğŸ› ï¸ <b>Komut:</b> <code>/remind [zaman] [not]</code>\n\nğŸš€ <b>Formatlar:</b>\nâ€¢ <code>10dk</code>, <code>2sa</code>, <code>1gÃ¼n</code>\nâ€¢ <code>yarÄ±n 10:00 ToplantÄ±</code>\nâ€¢ <code>her gÃ¼n 09:00 GÃ¼nlÃ¼k plan</code>\nâ€¢ <code>her pazartesi 09:00 HaftalÄ±k plan</code>",
            "tts": "ğŸ”Š <b>SESLENDÄ°RME (TTS)</b>\n\nYazÄ±larÄ± doÄŸal insan sesine dÃ¶nÃ¼ÅŸtÃ¼rÃ¼rÃ¼m.\n\nğŸ› ï¸ <b>KullanÄ±m:</b>\nâ€¢ <code>/oku [metin]</code>\nâ€¢ Bir mesaja yanÄ±t verip <code>/oku</code>",
            "st_make": "âœ‚ï¸ <b>STÄ°CKER OLUÅTURMA</b>\n\nKendi kiÅŸisel paketlerini kur.\n\nğŸ› ï¸ <b>AdÄ±mlar:</b>\n1. FotoÄŸraf veya video gÃ¶nder.\n2. GÃ¶nderiye cevap olarak <code>/sticker</code>.\n3. Paket oluÅŸtur veya mevcuta ekle!",
            "st_search": "ğŸ” <b>AKILLI STÄ°CKER ARAMA</b>\n\nMilyarlarca sticker arasÄ±ndan AI ile bul.\n\nğŸ› ï¸ <b>Komut:</b> <code>/st [kelime]</code>\n\nğŸš€ <b>Ã–rnek:</b> <code>/st komik kedi</code>",
            "st_manage": "ğŸ“¦ <b>PAKET VE KATALOG</b>\n\nğŸ“‚ <b>Komutlar:</b>\nâ€¢ <code>/catalog</code> â€” Genel katalog.\nâ€¢ <code>/packs</code> â€” Kendi paketlerin.\nâ€¢ <code>/stdelete</code> â€” Sticker sil."
        }

        detail_text = feat_details.get(feat, f"ğŸ’¡ <b>{feat.capitalize()}</b> Ã¶zelliÄŸi hakkÄ±nda detaylÄ± rehber hazÄ±rlanÄ±yor...\n\nKomutlarÄ± kullanmaya hemen baÅŸlayabilirsin!")

        keyboard = [[InlineKeyboardButton("ğŸ”™ Geri", callback_data=back_cat), btn_back_main]]
        await query.edit_message_text(detail_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

    async def _show_suggestions(self, query):
        text = (
            "ğŸš€ <b>GELECEK PLANLARI & GELÄ°ÅTÄ°RME Ã–NERÄ°LERÄ°</b>\n\n"
            "1ï¸âƒ£ <b>Ä°leri Seviye AI Vizyonu:</b>\n"
            "â€¢ <b>KiÅŸilikler:</b> Botun konuÅŸma tarzÄ±nÄ± deÄŸiÅŸtiren modlar.\n"
            "â€¢ <b>Uzun SÃ¼reli HafÄ±za:</b> GÃ¼nler sonra bile hatÄ±rlayan sistem.\n"
            "â€¢ <b>Sesli Cevaplar:</b> Metinleri sesli mesaja dÃ¶nÃ¼ÅŸtÃ¼ren.\n\n"
            "2ï¸âƒ£ <b>EÄŸlence & Sosyal:</b>\n"
            "â€¢ <b>MÃ¼zik Botu:</b> VC Ã¼zerinden mÃ¼zik yayÄ±nÄ±.\n"
            "â€¢ <b>Liderlik Tablosu:</b> En aktif Ã¼yeler.\n\n"
            "ğŸ“© <b>Fikirlerin mi var?</b> @pastalikek"
        )
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ğŸ”™ Geri", callback_data="help_main")]]), parse_mode='HTML')

    async def _handle_blackjack_callback(self, update, query, context, data: str):
        """Apply one owner-scoped Blackjack action and refresh the message."""
        parts = data.split(":", 2)
        if len(parts) != 3 or self.blackjack_service is None:
            await query.answer("GeÃ§ersiz Blackjack iÅŸlemi.", show_alert=True)
            return
        try:
            owner_id = int(parts[1])
        except (TypeError, ValueError):
            await query.answer("GeÃ§ersiz Blackjack oyuncusu.", show_alert=True)
            return
        if owner_id != int(update.effective_user.id):
            await query.answer("Bu el baÅŸka bir oyuncuya ait.", show_alert=True)
            return
        action = parts[2].casefold()
        if action not in {"hit", "stand", "double"}:
            await query.answer("GeÃ§ersiz Blackjack hamlesi.", show_alert=True)
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
                "duplicate_action": "Bu hamle zaten iÅŸlendi.",
                "game_expired": "Bu Blackjack eli artÄ±k aktif deÄŸil.",
                "game_finished": "Bu el sona ermiÅŸ.",
                "double_only_initial_hand": "Double yalnÄ±zca ilk iki kartta kullanÄ±labilir.",
                "insufficient_balance": "Double iÃ§in yeterli jetonun yok.",
                "not_owner": "Bu el baÅŸka bir oyuncuya ait.",
            }
            code = normalize_error_code(exc, "GAME_ERROR").lower()
            await query.answer(messages.get(code, "Bu hamle uygulanamadÄ±."), show_alert=True)
            return
        except Exception as exc:
            logger.error("Blackjack callback failed: %s", summarize_exception(exc, label="blackjack_callback"))
            await query.answer("Hamle uygulanamadÄ±. LÃ¼tfen tekrar dene.", show_alert=True)
            return

        markup = None
        if not result.get("terminal"):
            markup = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("â• Kart Ã§ek", callback_data=f"bj:{owner_id}:hit"),
                    InlineKeyboardButton("âœ… Dur", callback_data=f"bj:{owner_id}:stand"),
                ],
                [InlineKeyboardButton("2ï¸âƒ£ Double", callback_data=f"bj:{owner_id}:double")],
            ])
        try:
            await query.edit_message_text(
                sanitize_html(str(result.get("text", "Blackjack"))),
                reply_markup=markup,
                parse_mode="HTML",
            )
            await query.answer("Hamle uygulandÄ±.")
        except Exception as exc:
            logger.error("Could not refresh Blackjack message: %s", summarize_exception(exc, label="blackjack_refresh"))

    async def _handle_batak_round_callback(self, update, query, context, data: str):
        if self.batak_service is None:
            await query.answer("Batak servisi hazÄ±r deÄŸil.", show_alert=True)
            return
        parts = data.split(":")
        if len(parts) < 2 or parts[1] not in {"join", "start", "play"}:
            await query.answer("GeÃ§ersiz Batak iÅŸlemi.", show_alert=True)
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
                    await query.answer("Bu kart seÃ§imi baÅŸka bir oyuncuya ait.", show_alert=True)
                    return
                result = await self.batak_service.play(
                    chat_id=update.effective_chat.id,
                    user_id=user_id,
                    card_index=int(parts[3]) - 1,
                    action_id=action_id,
                )
        except (TypeError, ValueError) as exc:
            messages = {
                "duplicate_action": "Bu hamle zaten iÅŸlendi.",
                "game_expired": "Bu Batak masasÄ± artÄ±k aktif deÄŸil.",
                "active_game": "Bu grupta zaten aktif bir Batak masasÄ± var.",
                "game_started": "Batak eli zaten baÅŸladÄ±.",
                "table_full": "Masa dolu (en fazla 4 oyuncu).",
                "minimum_players": "BaÅŸlamak iÃ§in en az 2 oyuncu gerekir.",
                "not_owner": "MasayÄ± yalnÄ±zca kuran oyuncu baÅŸlatabilir.",
                "not_your_turn": "SÄ±ra sende deÄŸil.",
                "invalid_card": "GeÃ§erli bir kart seÃ§.",
                "must_follow_suit": "Elinde aynÄ± renkten kart varken o renkten oynamalÄ±sÄ±n.",
                "game_not_playing": "Bu el artÄ±k oynanmÄ±yor.",
            }
            code = normalize_error_code(exc, "GAME_ERROR").lower()
            await query.answer(messages.get(code, "Batak iÅŸlemi uygulanamadÄ±."), show_alert=True)
            return
        except Exception as exc:
            logger.error("Batak callback failed: %s", summarize_exception(exc, label="batak_callback"))
            await query.answer("Batak iÅŸlemi ÅŸu anda uygulanamadÄ±.", show_alert=True)
            return

        state = result.get("state", {})
        markup = None
        if result.get("status") == "created" or state.get("phase") == "lobby":
            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("ğŸƒ Masaya katÄ±l", callback_data="batak:join")],
                [InlineKeyboardButton("ğŸ Oyunu baÅŸlat", callback_data="batak:start")],
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
            await query.answer("Ä°ÅŸlem uygulandÄ±.")
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
                await query.edit_message_text(f"{query.message.text}\n\nâš ï¸ Masa dolu!", parse_mode='HTML')
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
                keyboard = [[InlineKeyboardButton("ğŸƒ Masaya KatÄ±l", callback_data="game_batak_join")]]
                await query.edit_message_text(
                    f"ğŸƒ <b>Batak MasasÄ± Kuruldu!</b>\n\nKatÄ±lmak iÃ§in butona basÄ±n. ({4 - len(players)} kiÅŸi lazÄ±m)\n\nğŸ‘¥ <b>Oyuncular:</b>\n{names_text}",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode='HTML'
                )
            else:
                suits = ["MaÃ§a", "Kupa", "Karo", "Sinek"]
                koz = random.choice(suits)
                await query.edit_message_text(
                    f"ğŸƒ <b>Batak MasasÄ± BaÅŸladÄ±!</b> ğŸ”¥\n\nğŸ‘¥ <b>Kadro:</b>\n{names_text}\n\nğŸ¯ Koz: <b>{koz}</b>\n\nArel kaÄŸÄ±tlarÄ± daÄŸÄ±tÄ±yor... ğŸƒâœ¨",
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
                [InlineKeyboardButton("ğŸ¾ Masaya KatÄ±l", callback_data="game_bottle_join")],
                [InlineKeyboardButton("ğŸ Oyunu BaÅŸlat", callback_data="game_bottle_spin")]
            ]
            await query.edit_message_text(
                f"ğŸ¾ <b>ÅiÅŸe Ã‡evirmece Lobisi</b>\n\nKatÄ±lmak iÃ§in butona basÄ±n.\n\nğŸ‘¥ <b>Oyuncular:</b> {names_text}",
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
            await query.edit_message_text(f"ğŸ¾ <b>ÅiÅŸe Ã‡evirmece</b>\n\n{result}", parse_mode='HTML')
            context.chat_data['bottle_players'] = []
            await delete_game(self.data_port, update.effective_chat.id, "bottle")

    @staticmethod
    def _roulette_keyboard(state: dict) -> InlineKeyboardMarkup:
        if state.get("phase") == "lobby":
            return InlineKeyboardMarkup([
                [InlineKeyboardButton("ğŸ‘¤ KatÄ±l", callback_data="roulette:join")],
                [InlineKeyboardButton("ğŸ Oyunu baÅŸlat", callback_data="roulette:start")],
                [InlineKeyboardButton("ğŸ›‘ Lobiyi kapat", callback_data="roulette:cancel")],
            ])
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("ğŸ”« TetiÄŸi Ã§ek", callback_data="roulette:pull")],
            [InlineKeyboardButton("ğŸ›‘ Oyunu bitir", callback_data="roulette:cancel")],
        ])

    async def _handle_roulette_callback(self, update, query, context, data: str) -> None:
        service = self.roulette_service
        if service is None:
            await query.answer("Rulet servisi kullanÄ±lamÄ±yor.", show_alert=True)
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
                moderator = await is_admin(update, context, self.sudo_manager)
                result = await service.cancel(
                    chat_id=update.effective_chat.id,
                    user_id=user.id,
                    moderator=moderator,
                )
            else:
                await query.answer("GeÃ§ersiz rulet iÅŸlemi.", show_alert=True)
                return
        except ValueError as exc:
            messages = {
                "duplicate_action": "Bu iÅŸlem zaten iÅŸlendi.",
                "game_not_lobby": "Rulet lobisi artÄ±k aÃ§Ä±k deÄŸil.",
                "game_not_playing": "Rulet eli artÄ±k oynanmÄ±yor.",
                "lobby_full": "Rulet masasÄ± dolu.",
                "need_two_players": "BaÅŸlamak iÃ§in en az iki oyuncu gerekir.",
                "not_owner": "Bu iÅŸlemi yalnÄ±zca masa sahibi yapabilir.",
                "not_turn": "SÄ±ra sende deÄŸil; biraz sabÄ±r, namlu bile bekliyor.",
                "game_expired": "Bu rulet masasÄ± artÄ±k aktif deÄŸil.",
            }
            code = normalize_error_code(exc, "GAME_ERROR").lower()
            await query.answer(messages.get(code, "Rulet iÅŸlemi uygulanamadÄ±."), show_alert=True)
            return
        except Exception as exc:
            logger.error("Roulette callback failed: %s", summarize_exception(exc, label="roulette_callback"))
            await query.answer("Rulet iÅŸlemi ÅŸu anda uygulanamadÄ±.", show_alert=True)
            return

        if result.get("status") == "duplicate_action":
            await query.answer("Bu iÅŸlem zaten iÅŸlendi.", show_alert=True)
            return
        state = result.get("state") or {}
        markup = None if result.get("terminal") else self._roulette_keyboard(state)
        await query.edit_message_text(
            sanitize_html(str(result.get("text", "Rulet"))),
            reply_markup=markup,
            parse_mode="HTML",
        )
        await query.answer("Rulet gÃ¼ncellendi.")

    async def _handle_game_start_callback(self, update, query, context, data):
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
            "game_start_duello": cmd_handler.handle_duello_command
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
        state = await self._load_word_game_state(context, update.effective_chat.id)
        if not state:
            await query.answer("Aktif kelime oyunu yok.", show_alert=True)
            return

        sequence = (
            int(state.get("shuffle_count", 0) or 0)
            if data == "word_game_shuffle"
            else int(state.get("hints_used", 0) or 0)
        )
        claimed, _ = await self._claim_game_action(
            update, query, "word_game", f"{data}:{sequence}"
        )
        if not claimed:
            return

        if data == "word_game_shuffle":
            shuffle_rack(state)
            await persist_game(self.data_port, update.effective_chat.id, "word_game", state)
            await query.edit_message_text(
                render_word_game_text(state),
                reply_markup=word_game_keyboard(state),
                parse_mode="HTML",
            )
            await query.answer("Harfler karÄ±ÅŸtÄ±rÄ±ldÄ±.")
            return

        result = reveal_word_hint(state)
        if result["status"] == "no_hint":
            await query.answer("AÃ§Ä±lacak ipucu kalmadÄ±.", show_alert=True)
            return
        await persist_game(self.data_port, update.effective_chat.id, "word_game", state)
        hint = result["hint"]
        note = (
            f"ğŸ’¡ Ä°pucu: <b>{hint['length']}</b> harfli, "
            f"<b>{hint['first']}</b> ile baÅŸlÄ±yor ve <b>{hint['last']}</b> ile bitiyor."
        )
        await query.edit_message_text(
            render_word_game_text(state, note=note),
            reply_markup=word_game_keyboard(state),
            parse_mode="HTML",
        )
        await query.answer("Ä°pucu uygulandÄ±.")

    async def _handle_word_game_stop(self, update, query, context) -> None:
        claimed, _ = await self._claim_game_action(update, query, "word_game", "word_game_stop")
        if not claimed:
            return
        state = await self._load_word_game_state(context, update.effective_chat.id)
        if not state:
            await query.answer("Aktif kelime oyunu yok.", show_alert=True)
            return
        owner_id = state.get("owner_id")
        if owner_id and int(owner_id) != int(update.effective_user.id):
            if not await is_admin(update, context, self.sudo_manager):
                await query.answer(
                    "Bu oyunu yalnÄ±zca baÅŸlatan kiÅŸi veya grup yÃ¶neticisi kapatabilir.",
                    show_alert=True,
                )
                return
        context.chat_data.pop("word_game", None)
        await delete_game(self.data_port, update.effective_chat.id, "word_game")
        await query.edit_message_text("ğŸ›‘ Kelime oyunu bitirildi.", parse_mode="HTML")
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
        await query.answer("Tur tamamlandÄ±.")

    async def _handle_hangman_guess_callback(self, update, query, context, data: str) -> None:
        claimed, _ = await self._claim_game_action(update, query, "hangman", data)
        if not claimed:
            return
        state = await self._load_hangman_state(context, update.effective_chat.id)
        if not state:
            await query.answer("Bu adam asmaca turu sona ermiÅŸ.", show_alert=True)
            return
        result = apply_guess(state, data.split(":", 1)[1])
        if result["status"] in {"invalid", "duplicate"}:
            await query.answer(
                "Bu harfi zaten denedin." if result["status"] == "duplicate" else "GeÃ§ersiz harf.",
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
        await query.answer("DoÄŸru harf." if result["status"] == "hit" else "Bu harf kelimede yok.")

    async def _handle_hangman_hint_callback(self, update, query, context) -> None:
        state = await self._load_hangman_state(context, update.effective_chat.id)
        if not state:
            await query.answer("Aktif adam asmaca turu yok.", show_alert=True)
            return
        sequence = int(state.get("hints_used", 0) or 0)
        claimed, _ = await self._claim_game_action(
            update, query, "hangman", f"hangman_hint:{sequence}"
        )
        if not claimed:
            return
        result = reveal_hint(state)
        if result["status"] == "no_hint":
            await query.answer("AÃ§Ä±lacak harf kalmadÄ±.", show_alert=True)
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
        await query.answer(f"Ä°pucu: {str(result.get('letter', '')).upper()}")

    async def _handle_hangman_stop_callback(self, update, query, context) -> None:
        state = await self._load_hangman_state(context, update.effective_chat.id)
        if not state:
            await query.answer("Aktif adam asmaca turu yok.", show_alert=True)
            return
        owner_id = state.get("owner_id")
        if owner_id and int(owner_id) != int(update.effective_user.id):
            if not await is_admin(update, context, self.sudo_manager):
                await query.answer("Bu turu yalnÄ±zca baÅŸlatan kiÅŸi veya grup yÃ¶neticisi kapatabilir.", show_alert=True)
                return
        context.chat_data.pop("hangman", None)
        await delete_game(self.data_port, update.effective_chat.id, "hangman")
        await query.edit_message_text("ğŸ›‘ Adam asmaca turu kapatÄ±ldÄ±.", parse_mode="HTML")
        await query.answer("Tur kapatÄ±ldÄ±.")

    async def _handle_game_stop(self, update, query, context):
        if self.roulette_service is not None:
            try:
                moderator = await is_admin(update, context, self.sudo_manager)
                await self.roulette_service.cancel(
                    chat_id=update.effective_chat.id,
                    user_id=update.effective_user.id,
                    moderator=moderator,
                )
            except ValueError as exc:
                if str(exc) not in {"game_expired", "not_owner"}:
                    logger.warning("Roulette stop rejected: %s", summarize_exception(exc, label="roulette_stop"))

        game_keys = ['word_guess', 'hangman', 'word_game', 'batak_players', 'sise_lobby', 'bottle_players']
        cleared = False
        for key in game_keys:
            if key in context.chat_data:
                del context.chat_data[key]
                cleared = True
        for game_type in ("word_guess", "hangman", "word_game", "batak", "bottle", "quiz"):
            await delete_game(self.data_port, update.effective_chat.id, game_type)

        await query.answer("âœ… Oyunlar sonlandÄ±rÄ±ldÄ±!" if cleared else "ğŸ¤” Aktif oyun yok!")

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

    async def _handle_word_guess_hint(self, update, query, context) -> None:
        state = await self._load_word_guess_state(context, update.effective_chat.id)
        if not state:
            await query.answer("Aktif anlatmaca turu yok.", show_alert=True)
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
            await query.answer("AÃ§Ä±lacak ipucu kalmadÄ±.", show_alert=True)
            return
        hint = str(hints[index])
        state["hint_index"] = index + 1
        await persist_game(self.data_port, update.effective_chat.id, "word_guess", state)
        await query.edit_message_text(
            render_word_guess_text(
                state,
                note=f"ğŸ’¡ Ä°pucu {index + 1}: {html.escape(hint)}",
            ),
            reply_markup=word_guess_keyboard(state),
            parse_mode="HTML",
        )
        await query.answer("Ä°pucu aÃ§Ä±ldÄ±.")

    async def _handle_word_guess_stop(self, update, query, context) -> None:
        claimed, _ = await self._claim_game_action(update, query, "word_guess", "word_guess_stop")
        if not claimed:
            return
        state = await self._load_word_guess_state(context, update.effective_chat.id)
        if not state:
            await query.answer("Aktif anlatmaca turu yok.", show_alert=True)
            return
        owner_id = state.get("owner_id")
        if owner_id and int(owner_id) != int(update.effective_user.id):
            if not await is_admin(update, context, self.sudo_manager):
                await query.answer(
                    "Bu turu yalnÄ±zca baÅŸlatan kiÅŸi veya grup yÃ¶neticisi kapatabilir.",
                    show_alert=True,
                )
                return
        chat_id = update.effective_chat.id
        self._remove_job_by_name(context, f"word_guess_timeout_{chat_id}")
        self._remove_job_by_name(context, f"word_guess_hint_{chat_id}")
        self._remove_job_by_name(context, f"word_guess_skip_{chat_id}")
        context.chat_data.pop("word_guess", None)
        await delete_game(self.data_port, chat_id, "word_guess")
        await query.edit_message_text("ğŸ›‘ Kelime anlatmaca turu bitirildi.", parse_mode="HTML")
        await query.answer("Tur bitirildi.")

    async def _handle_word_skip(self, update, query, context):
        claimed, _ = await self._claim_game_action(update, query, "word_guess", "word_skip")
        if not claimed:
            return
        chat_id = update.effective_chat.id
        game = await self._load_word_guess_state(context, chat_id)
        if not game:
            await query.answer("Aktif oyun yok!", show_alert=True)
            return

        word = html.escape(str(game.get('word', '???')))

        self._remove_job_by_name(context, f"word_guess_timeout_{chat_id}")
        self._remove_job_by_name(context, f"word_guess_hint_{chat_id}")
        self._remove_job_by_name(context, f"word_guess_skip_{chat_id}")

        del context.chat_data['word_guess']
        await delete_game(self.data_port, chat_id, "word_guess")

        await query.edit_message_text(
            f"â­ï¸ <b>Pas GeÃ§ildi!</b>\n\n"
            f"DoÄŸru cevap: <b>{word}</b>\n\n"
            f"ğŸ”„ <i>Yeni kelime 3 saniye sonra gelecek...</i>",
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

        context.chat_data['quiz_answer'] = q_data['answer']
        context.chat_data['quiz_question'] = q_data['question']
        await self.data_port.save_game_state(update.effective_chat.id, "quiz", {
            "answer": q_data['answer'], "question": q_data['question']
        })

        options = q_data['options']
        keyboard = []
        for i in range(0, len(options), 2):
            row = []
            for j in range(i, min(i + 2, len(options))):
                row.append(InlineKeyboardButton(options[j], callback_data=f"quiz_ans_{j}_{options[j][:20]}"))
            keyboard.append(row)

        await query.edit_message_text(
            f"ğŸ¯ <b>Bilgi YarÄ±ÅŸmasÄ±</b> â€” {category.upper()}\n\n"
            f"â“ <b>{q_data['question']}</b>\n\n"
            f"DoÄŸru cevabÄ± seÃ§in! â³",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='HTML'
        )
        await query.answer()

    async def _handle_quiz_answer(self, update, query, context, data):
        claimed, _ = await self._claim_game_action(update, query, "quiz", data)
        if not claimed:
            return
        parts = data.split("_", 3)
        selected = parts[3] if len(parts) > 3 else parts[2]
        correct = context.chat_data.get('quiz_answer', '')
        question = context.chat_data.get('quiz_question', '')
        if not correct:
            saved = await self.data_port.get_game_state(update.effective_chat.id, "quiz") or {}
            correct = saved.get("answer", "")
            question = saved.get("question", "")
        if not correct:
            await query.answer("Bu quiz oturumu sona ermiÅŸ.", show_alert=True)
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
                f"âœ… <b>DOÄRU!</b> ğŸ‰\n\n"
                f"â“ {question}\n"
                f"ğŸ“ Cevap: <b>{correct}</b>\n\n"
                f"ğŸ† Tebrikler <b>{user_name}</b>!",
                parse_mode='HTML'
            )
        else:
            await query.edit_message_text(
                f"âŒ <b>YANLIÅ!</b>\n\n"
                f"â“ {question}\n"
                f"ğŸ“ DoÄŸru cevap: <b>{correct}</b>\n\n"
                f"Tekrar denemek iÃ§in /quiz yazÄ±n! ğŸ’ª",
                parse_mode='HTML'
            )

        context.chat_data.pop('quiz_answer', None)
        context.chat_data.pop('quiz_question', None)
        await self.data_port.delete_game_state(update.effective_chat.id, "quiz")
        await query.answer()

    async def _handle_bim_page_callback(self, update, query, data: str):
        """Render a cached BÄ°M catalogue page without doing HTTP in a callback."""
        parts = data.split(":")
        if len(parts) != 3:
            await query.edit_message_text("âš ï¸ BÄ°M sayfasÄ± baÄŸlantÄ±sÄ± geÃ§ersiz.")
            return
        try:
            owner_id, page = int(parts[1]), int(parts[2])
        except ValueError:
            await query.edit_message_text("âš ï¸ BÄ°M sayfasÄ± baÄŸlantÄ±sÄ± geÃ§ersiz.")
            return
        if owner_id != update.effective_user.id:
            await query.answer("Bu katalog baÅŸka bir kullanÄ±cÄ±ya ait.", show_alert=True)
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
            await query.edit_message_text("âš ï¸ BÄ°M kataloÄŸu ÅŸu an yenilenemedi; biraz sonra tekrar dene.")

    async def _handle_sticker_packs_refresh(self, update, query, data: str) -> None:
        """Refresh the owner's pack list without exposing another user's data."""
        try:
            owner_id = int(data.split(":", 1)[1])
        except (IndexError, ValueError):
            await query.answer("GeÃ§ersiz paket listesi.", show_alert=True)
            return
        if owner_id != int(update.effective_user.id):
            await query.answer("Bu liste baÅŸka bir kullanÄ±cÄ±ya ait.", show_alert=True)
            return
        try:
            packs = await self.data_port.get_user_packs(owner_id)
        except Exception as exc:
            logger.error("Sticker pack refresh failed: %s", summarize_exception(exc, label="sticker_pack_refresh"))
            await query.answer("Paket listesi ÅŸu an yenilenemedi.", show_alert=True)
            return
        if not packs:
            await query.edit_message_text(
                "ğŸ“­ HenÃ¼z kayÄ±tlÄ± bir sticker paketin yok. Bir sticker'a yanÄ±t verip /sticker ile baÅŸlayabilirsin."
            )
            return
        rows = []
        lines = ["ğŸ“¦ <b>SÄ°ZÄ°N STICKER PAKETLERÄ°NÄ°Z</b> ğŸŒŸ", ""]
        for index, pack in enumerate(packs, 1):
            pack_name = str(pack.get("pack_name") or "").strip()
            if not pack_name:
                continue
            title = html.escape(str(pack.get("pack_title") or pack_name))
            link = f"https://t.me/addstickers/{pack_name}"
            lines.append(f"{index}. <b>{title}</b> â€” <a href=\"{html.escape(link, quote=True)}\">Paketi aÃ§/gÃ¶r</a>")
            rows.append([InlineKeyboardButton(f"ğŸ“¦ {pack.get('pack_title') or pack_name}", url=link)])
        rows.append([InlineKeyboardButton("ğŸ”„ Yenile", callback_data=f"sticker_packs_refresh:{owner_id}")])
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
                await query.answer("GeÃ§ersiz sticker paketi iÅŸlemi.", show_alert=True)
                return
            if owner_id != user_id:
                await query.answer("Bu onay baÅŸka bir kullanÄ±cÄ±ya ait.", show_alert=True)
                return
            if data.startswith("stk_cancel_pack:"):
                if self.message_handler and hasattr(self.message_handler, "user_states"):
                    pop_scoped_state(
                        self.message_handler.user_states,
                        user_id=owner_id,
                        chat_id=chat_id,
                    )
                await query.edit_message_text("Sticker paketi oluÅŸturma iptal edildi.")
                return
            if not self.message_handler:
                await query.answer("Sticker oluÅŸturma servisi hazÄ±r deÄŸil.", show_alert=True)
                return
            try:
                result = await self.message_handler.confirm_pack_creation(owner_id, chat_id, context)
            except ValueError as exc:
                messages = {
                    "sticker_creation_expired": "Bu sticker oluÅŸturma oturumunun sÃ¼resi dolmuÅŸ.",
                    "sticker_creation_invalid": "Sticker oluÅŸturma bilgileri eksik.",
                }
                code = normalize_error_code(exc, "STICKER_OPERATION_FAILED").lower()
                await query.edit_message_text(f"âš ï¸ {messages.get(code, 'Sticker paketi oluÅŸturulamadÄ±.')}")
                return
            except RuntimeError as exc:
                code = str(exc).strip().upper()
                if code == "STICKER_PACK_PERSISTENCE_UNAVAILABLE":
                    await query.edit_message_text(
                        "âš ï¸ Sticker paketi Telegram tarafÄ±nda oluÅŸturulamadÄ± veya kalÄ±cÄ± kaydÄ± tamamlanamadÄ±. "
                        "VeritabanÄ± baÄŸlantÄ±sÄ±nÄ± kontrol edip paketi yeniden dene."
                    )
                else:
                    await query.edit_message_text("âš ï¸ Sticker paketi oluÅŸturulamadÄ±; biraz sonra tekrar dene.")
                return
            except Exception as exc:
                logger.error("Sticker pack creation confirmation failed: %s", summarize_exception(exc, label="sticker_pack_create"))
                await query.edit_message_text(
                    "âš ï¸ Sticker paketi oluÅŸturulamadÄ±. Telegram dosyanÄ±n formatÄ±nÄ± veya boyutunu kabul etmemiÅŸ olabilir; "
                    "paketi yeniden baÅŸlatÄ±p tekrar dene."
                )
                return
            await query.answer("Paket kaydedildi.")
            await query.edit_message_text(
                f"âœ… <b>Paket oluÅŸturuldu</b>\n\nBaÅŸlÄ±k: <b>{html.escape(str(result['title']))}</b>\n"
                f"ğŸ”— <a href=\"{html.escape(result['link'], quote=True)}\">Paketi aÃ§</a>",
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
                await query.message.reply_text("âŒ Sticker verisi bulunamadÄ±.")
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
                keyboard = [[InlineKeyboardButton("ğŸš€ Ã–zel Mesajda OluÅŸtur", url=deep_link)]]
                await query.message.reply_text(
                    "âœ¨ GÃ¼venlik iÃ§in sticker paketlerini <b>Ã¶zel sohbet</b> Ã¼zerinden oluÅŸturuyorum.\n\n"
                    "AÅŸaÄŸÄ±daki dÃ¼ÄŸmeye basÄ±p akÄ±ÅŸÄ± orada tamamla:",
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
                "ğŸ“¦ <b>Yeni Sticker Paketi OluÅŸturma</b>\n\n"
                "LÃ¼tfen paketiniz iÃ§in <b>BaÅŸlÄ±k</b> yazÄ±n:\n"
                "<i>(Ã–rn: Arel Komik Koleksiyonu)</i>",
                parse_mode='HTML'
            )
            await query.edit_message_reply_markup(reply_markup=None)

        elif data.startswith("stk_select_pack_"):
            suffix = data.replace("stk_select_pack_", "")
            sticker = query.message.sticker
            if not sticker and query.message.reply_to_message:
                sticker = query.message.reply_to_message.sticker
            if not sticker:
                await query.answer("âŒ Sticker bulunamadÄ±.", show_alert=True)
                return

            is_group = query.message.chat.type in ["group", "supergroup"]

            try:
                packs = await self.data_port.get_user_packs(user_id, pack_type=suffix)

                keyboard = []
                for p in packs:
                    keyboard.append([InlineKeyboardButton(f"ğŸ“ {p['pack_title']}", callback_data=f"stk_add_to_{p['_id']}")])

                if is_group:
                    bot_info = await context.bot.get_me()
                    unique_id = str(uuid.uuid4())
                    await self.data_port.save_creation_cache(
                        unique_id,
                        {'file_id': sticker.file_id, 'suffix': suffix}
                    )
                    deep_link = f"https://t.me/{bot_info.username}?start=stk_create_{unique_id}"
                    keyboard.append([InlineKeyboardButton("ğŸš€ Yeni Paket OluÅŸtur (DM)", url=deep_link)])
                else:
                    keyboard.append([InlineKeyboardButton("ğŸ“¦ Yeni Paket OluÅŸtur", callback_data=f"stk_new_{suffix}")])

                await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
            except Exception as e:
                logger.error("Error listing packs: %s", summarize_exception(e, label="sticker_pack_list"))
                await query.answer("MenÃ¼ yÃ¼klenirken hata oluÅŸtu.", show_alert=True)

        elif data.startswith("stk_add_to_"):
            pack_id = data.replace("stk_add_to_", "")
            sticker = query.message.sticker
            if not sticker and query.message.reply_to_message:
                sticker = query.message.reply_to_message.sticker
            if not sticker:
                await query.answer("âŒ Sticker bulunamadÄ±.", show_alert=True)
                return

            try:
                pack_rec = await self.data_port.get_pack_by_id(pack_id)
                if not pack_rec or pack_rec['user_id'] != user_id:
                    await query.answer("âš ï¸ Paket bulunamadÄ±!", show_alert=True)
                    return

                sticker_format = StickerFormat.STATIC if pack_rec['pack_type'] == "static" else StickerFormat.VIDEO
                if self.sticker_pack_service is None:
                    await query.answer("Sticker paket servisi hazÄ±r deÄŸil.", show_alert=True)
                    return
                result = await self.sticker_pack_service.add_sticker(
                    context.bot,
                    user_id,
                    pack_rec['pack_name'],
                    sticker.file_id,
                    ["âœ¨"],
                    sticker_format,
                )

                pack_link = f"https://t.me/addstickers/{pack_rec['pack_name']}"
                await query.edit_message_reply_markup(reply_markup=None)
                if result.get("status") == "already_exists":
                    await query.message.reply_text(
                        f"â„¹ï¸ Bu sticker zaten <b>'{pack_rec['pack_title']}'</b> paketinde.",
                        parse_mode='HTML',
                    )
                    return
                if result.get("status") == "in_progress":
                    await query.message.reply_text(
                        "â³ Bu sticker ekleme iÅŸlemi hala sÃ¼rÃ¼yor; Telegram naz yaparsa bile ikinci kez eklemiyorum.",
                    )
                    return
                await query.message.reply_text(
                    f"âœ… <b>Sticker '{pack_rec['pack_title']}' paketine eklendi!</b>\nğŸ”— <a href='{pack_link}'>Paketi GÃ¶r</a>",
                    parse_mode='HTML',
                    disable_web_page_preview=True
                )
            except Exception as pack_err:
                logger.error("Failed to add to specific pack: %s", summarize_exception(pack_err, label="sticker_pack_add"))
                await query.answer(
                    public_error_message(
                        pack_err,
                        "Sticker paketi gÃ¼ncellenemedi; Telegram servisi kÄ±sa sÃ¼reliÄŸine naz yapÄ±yor olabilir.",
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
            await query.answer("Bu kiÅŸilik menÃ¼sÃ¼ yalnÄ±zca sahibine aittir.", show_alert=True)
            return
        raw = data.split(":", 1)[1] if ":" in data else ""
        if raw == "roast":
            await query.edit_message_text(
                "âš ï¸ <b>Roast / taÅŸlama modu</b> yalnÄ±zca Arel'e doÄŸrudan kaba Ã§Ä±kÄ±ÅŸlarda Ã§alÄ±ÅŸÄ±r. "
                "Tehdit, nefret sÃ¶ylemi, cinsel aÅŸaÄŸÄ±lama ve hedefli taciz Ã¼retmez. EtkinleÅŸtirmek iÃ§in aÃ§Ä±kÃ§a onay ver.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(
                    "âœ… Roast modunu onayla", callback_data="persona_user:roast_confirm"
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
            await query.answer("GeÃ§ersiz kiÅŸilik seÃ§imi.", show_alert=True)
            return
        else:
            raw = PersonalityPolicy.normalize_mode(raw)
        try:
            await self.data_port.update_user(update.effective_user.id, {"persona_mode": raw})
        except Exception:
            logger.warning("Could not persist private personality callback", exc_info=True)
            await query.answer("Ãœslup tercihi kaydedilemedi.", show_alert=True)
            return

        current_label = (
            "VarsayÄ±lan"
            if raw is None
            else (style_mode_label(raw) or PersonalityPolicy.mode_label(raw))
        )
        keyboard = []
        for mode in ("normal", "serious", "balanced", "needle", "roast"):
            marker = "âœ… " if raw == mode else ""
            callback = "persona_user:roast" if mode == "roast" else f"persona_user:{mode}"
            keyboard.append([InlineKeyboardButton(
                f"{marker}{style_mode_label(mode) or PersonalityPolicy.mode_label(mode)}",
                callback_data=callback,
            )])
        keyboard.append([InlineKeyboardButton(
            "âœ… VarsayÄ±lan aktif" if raw is None else "â†©ï¸ VarsayÄ±lanÄ± kullan",
            callback_data="persona_user:default",
        )])
        await query.edit_message_text(
            "ğŸ­ <b>Arel Ã¼slubu</b>\n\n"
            f"Aktif tercih: <b>{current_label}</b>\n"
            "KÄ±sa kinaye yalnÄ±zca uygunsa kullanÄ±lÄ±r; hassas ve teknik konular daima ciddi kalÄ±r.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML",
        )
        await query.answer("Ãœslup tercihi gÃ¼ncellendi.")

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
                await query.answer("KiÅŸisel ayarlar hazÄ±r deÄŸil.", show_alert=True)
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
                await self._safe_query_answer("Bu ayarÄ± grup yÃ¶neticileri deÄŸiÅŸtirebilir.", show_alert=True)
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
                await self._safe_query_answer("Ayar kaydedilemedi; tekrar dene.", show_alert=True)
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
                f"{'Quote' if key == 'q_enabled' else 'Video indirici'}: {'aÃ§Ä±k' if not current else 'kapalÄ±'}"
            )
            return
        if not await self._is_admin(update, context):
            await query.answer("Sadece grup y\u00f6neticileri de\u011fi\u015ftirebilir.", show_alert=True)
            return
        chat_id = query.message.chat.id
        if data == "ayar_ban_help":
            await query.edit_message_text(
                "ğŸš« <b>Ãœye yÃ¶netimi</b>\n\nBanlamak iÃ§in bir mesaja yanÄ±t verip <code>/ban</code> yazabilir veya <code>/ban kullanÄ±cÄ±_id</code> kullanabilirsin. Geri almak iÃ§in aynÄ± biÃ§imde <code>/unban</code> yaz.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("âš™ï¸ Ayarlara dÃ¶n", callback_data="open_ayar_menu")]]),
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
                await query.answer("KiÅŸilik ayarÄ± kaydedilemedi.", show_alert=True)
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
            [InlineKeyboardButton(f"HoÅŸ geldin: {status('welcome_enabled')}", callback_data="ayar_welcome_toggle")],
            [InlineKeyboardButton(f"GÃ¼le gÃ¼le: {status('goodbye_enabled')}", callback_data="ayar_goodbye_toggle")],
            [InlineKeyboardButton(f"TekrarlÄ± flood banÄ±: {status('auto_ban')}", callback_data="ayar_ban_toggle")],
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
                InlineKeyboardButton("â¬…ï¸", callback_data=f"qtop_prev_{new_index}"),
                InlineKeyboardButton(f"{new_index + 1} / {len(top)}", callback_data="none"),
                InlineKeyboardButton("â¡ï¸", callback_data=f"qtop_next_{new_index}")
            ],
            [InlineKeyboardButton("ğŸ”¥ BeÄŸen", callback_data=f"qrate_up_{q['user_id']}")]
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
                await query.answer("âš ï¸ Bu alÄ±ntÄ±yÄ± zaten oyladÄ±n!", show_alert=True)
                return

            await self.data_port.update_quote_rating(
                sticker_file_id,
                "rating_up" if action == "up" else "rating_down",
                1
            )

            context.user_data[vote_key] = True
            await query.answer(f"âœ… Oyun kaydedildi! ğŸš€")
        except Exception as e:
            logger.error("Rating error: %s", summarize_exception(e, label="quote_rating"))
            await query.answer("âš ï¸ Oylama sÄ±rasÄ±nda hata oluÅŸtu.")

    async def _handle_tts_download(self, query, context, data):
        import os
        file_token = data.replace("tts_dl_", "")
        file_path = f"downloads/tts_{file_token}.mp3"
        if os.path.exists(file_path):
            await query.message.reply_document(
                document=open(file_path, 'rb'),
                filename=f"arel_seslendirme_{file_token}.mp3",
                caption="ğŸ’¾ Ä°ÅŸte indirilebilir ses dosyanÄ±z."
            )
            await query.answer("Dosya gÃ¶nderiliyor...")
        else:
            await query.answer("âš ï¸ Dosya sÃ¼resi (1 saat) dolduÄŸu iÃ§in silinmiÅŸ.", show_alert=True)

    async def _handle_catalog_refresh(self, update, query, context):
        packs = await self.data_port.get_top_packs(5)

        if not packs:
            await query.message.edit_text("ğŸ“­ HenÃ¼z katalogda paket yok.", parse_mode='HTML')
            return

        text = "ğŸŒŸ <b>AREL STICKER KATALOÄU</b> ğŸŒŸ\n\nEn popÃ¼ler ve yeni paketleri keÅŸfedin:\n\n"
        keyboard = []

        for p in packs:
            text += f"â€¢ <b>{p['pack_title']}</b> ({p['pack_type'].capitalize()})\n"
            keyboard.append([InlineKeyboardButton(f"ğŸ“¦ {p['pack_title']}", url=f"https://t.me/addstickers/{p['pack_name']}")])

        keyboard.append([InlineKeyboardButton("ğŸ”„ Yenile", callback_data="catalog_refresh")])

        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
        await query.answer("Katalog yenilendi! âœ¨")

    async def _is_admin(self, update, context):
        return await is_admin(update, context, self.sudo_manager)

    async def _is_group_admin(self, update, context):
        """Shared group schedules can only be changed by current chat admins."""
        if update.effective_chat.type not in {"group", "supergroup"}:
            return False
        try:
            member = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
            return member.status in {"creator", "administrator"}
        except Exception:
            return False
