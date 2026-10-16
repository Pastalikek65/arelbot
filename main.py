from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    InlineQueryHandler,
    ChatMemberHandler,
    filters,
)
from config import Config
from src.orchestrator import Orchestrator
from src.telegram_request import build_request_pair
from src.telegram_transport import transport_from_config
from src.security.log_redaction import summarize_exception
import logging
import asyncio
import sys

# High Performance Event Loop (Linux)
try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    print("âš¡ uvloop activated for maximum speed.")
except ImportError:
    pass

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

# Telegram rejects a command list above 100 entries. Keep the complete
# command/handler surface below for direct use and architecture auditing, but
# reserve the menu for canonical commands; aliases remain callable in chat.
TELEGRAM_COMMAND_LIMIT = 100
MENU_ALIAS_COMMANDS = frozenset(
    {
        "dl", "download", "liderlik", "lb", "profil", "mod", "documents",
        "hatirlat", "bildirimayar", "bakiye", "bildirimler", "takipler",
        "filmoneri", "dizioneri", "kitaponeri", "sarkioneri", "soz", "sarki",
        "lyrics", "search", "gunlukplan", "haftalikplan",
    }
)

# Operational commands are published only in the owner's private menu. The
# handlers enforce the same owner boundary for direct invocations.
OWNER_ONLY_COMMANDS = frozenset({
    "medya_cookie", "jobs", "metrics", "health", "providers", "features",
    "cache", "maintenance", "audit",
})


def build_bot_command_menu(commands, *, excluded_commands=frozenset()):
    """Return a bounded, duplicate-free Telegram command menu.

    Telegram's 100-command API limit applies per scope. Hiding only aliases
    keeps useful canonical commands visible while preserving every alias in
    ``register_handlers`` for backwards-compatible direct invocation.
    """

    visible = []
    seen = set()
    for command, description in commands:
        name = str(command).strip().casefold()
        if not name or name in seen or name in MENU_ALIAS_COMMANDS or name in excluded_commands:
            continue
        seen.add(name)
        visible.append((name, str(description)[:256]))
        if len(visible) >= TELEGRAM_COMMAND_LIMIT:
            break
    return visible

async def post_init(application):
    orchestrator = application.bot_data.get("orchestrator")
    if orchestrator is None:
        raise RuntimeError("orchestrator_not_initialized")
    logger.info("Bot initializing components")
    await orchestrator.start_background_tasks(application.bot, application.job_queue)
    await orchestrator.connect_mcp()
    await register_bot_commands(application)

async def post_shutdown(application):
    orchestrator = application.bot_data.get("orchestrator")
    if orchestrator is None:
        return
    logger.info("Bot shutting down")
    await orchestrator.shutdown()
    logger.info("Shutdown complete")

async def register_bot_commands(application):
    # Set Bot Commands for the inline menu (Exhaustive List)
    commands = [
        ("indir", "Sosyal medya baÄŸlantÄ±sÄ±ndan medya indirir"),
        ("dl", "Sosyal medya medyasÄ± indirir"),
        ("download", "Sosyal medya medyasÄ± indirir"),
        ("medya_cookie", "YÃ¶netici medya cookie kasasÄ±"),
        ("jobs", "Medya iÅŸi durumunu gÃ¶sterir"),
        ("metrics", "Medya kuyruÄŸu metriklerini gÃ¶sterir"),
        ("health", "Sistem saÄŸlÄ±k durumunu gÃ¶sterir"),
        ("providers", "Medya saÄŸlayÄ±cÄ± durumlarÄ±"),
        ("features", "Kayitli feature manifesti (yonetici)"),
        ("cache", "Medya cache yÃ¶netimi"),
        ("maintenance", "BakÄ±m modu"),
        ("audit", "Denetim kayÄ±tlarÄ±"),
        ("start", "Botu baÅŸlatÄ±r"),
        ("menu", "Modern ana menÃ¼yÃ¼ aÃ§ar"),
        ("help", "YardÄ±m menÃ¼sÃ¼nÃ¼ aÃ§ar"),
        ("leaderboard", "Oyun liderlik tablosu"),
        ("liderlik", "Oyun liderlik tablosu"),
        ("lb", "Oyun liderlik tablosu"),
        ("profile", "Oyun profilini gÃ¶sterir"),
        ("profil", "Oyun profilini gÃ¶sterir"),
        ("basarimlar", "Oyun baÅŸarÄ±mlarÄ±nÄ± gÃ¶sterir"),
        ("sezon", "Mevcut sezon liderliÄŸini gÃ¶sterir"),
        ("lang", "Dil seÃ§imi: tr/en"),
        ("digest", "AI haber Ã¶zeti"),
        ("istatistik", "Grubun haftalÄ±k aktivite Ã¶zetini gÃ¶sterir (yÃ¶netici)"),
        ("moderasyon", "Grup moderasyon ihlallerini gÃ¶sterir (yÃ¶netici)"),
        ("mod", "Grup moderasyon ihlallerini gÃ¶sterir (yÃ¶netici)"),
        # --- Yeni AraÃ§lar ---
        ("haber", "Son dakika haberlerini getirir"),
        ("deprem", "Son depremleri listeler"),
        ("hava", "Hava durumu bilgisi (Ã–rn: /hava istanbul)"),
        ("hava_kalite", "AQI, partikÃ¼l ve polen bilgisi"),
        ("akaryakit", "GÃ¼ncel yakÄ±t fiyatlarÄ±"),
        ("doviz", "CanlÄ± dÃ¶viz kurlarÄ± ve altÄ±n"),
        ("altin", "AltÄ±n kurlarÄ±nÄ± getirir"),
        ("eczane", "NÃ¶betÃ§i eczaneler (Ã–rn: /eczane istanbul)"),
        ("ezan", "Ezan vakitlerini sorgular"),
        ("ucak", "UÃ§ak bileti fÄ±rsatlarÄ±nÄ± listeler"),
        ("bim", "HaftalÄ±k Bim aktÃ¼el Ã¼rÃ¼nleri"),
        ("sehir", "Ä°l, plaka, telefon ve ilÃ§e bilgisi"),
        ("plaka", "Plaka kodundan ÅŸehir bul"),
        ("telefon", "Telefon alan kodundan ÅŸehir bul"),
        ("ilce", "Ä°lÃ§e adÄ±ndan ÅŸehir bul"),
        ("not", "KiÅŸisel not kaydet"),
        ("notlar", "NotlarÄ±nÄ± listele"),
        ("belgeler", "YÃ¼klenen kiÅŸisel belgeleri yÃ¶net"),
        ("documents", "Y\u00fcklenmi\u015f ki\u015fisel belgeleri y\u00f6net"),
        ("gorev", "GÃ¶rev ekle veya tamamla"),
        ("gorevler", "AÃ§Ä±k gÃ¶revlerini listele"),
        ("hatirlatmalar", "Bekleyen hatÄ±rlatÄ±cÄ±lar"),
        # --- Mevcut Komutlar ---
        ("ayar", "Grup ayarlarÄ±nÄ± yapÄ±landÄ±rÄ±r"),
        ("persona", "Ã–zel sohbet Arel Ã¼slubunu seÃ§"),
        ("uslup", "Ã–zel sohbet Arel Ã¼slubunu seÃ§"),
        ("filmoneri", "Film Ã¶nerisi sunar"),
        ("dizioneri", "Dizi Ã¶nerisi sunar"),
        ("kitaponeri", "Kitap Ã¶nerisi sunar"),
        ("sarkioneri", "ÅarkÄ± Ã¶nerisi sunar"),
        ("kultur", "Film, dizi, kitap veya mÃ¼zik Ã¶nerisi"),
        ("soz", "ÅarkÄ± sÃ¶zÃ¼ kaynaÄŸÄ±ndan kÄ±sa alÄ±ntÄ± gÃ¶sterir"),
        ("sarki", "ÅarkÄ± sÃ¶zÃ¼ kaynaÄŸÄ±ndan kÄ±sa alÄ±ntÄ± gÃ¶sterir"),
        ("lyrics", "ÅarkÄ± sÃ¶zÃ¼ kaynaÄŸÄ±ndan kÄ±sa alÄ±ntÄ± gÃ¶sterir"),
        ("ara", "KaynaklÄ± gÃ¼ncel web aramasÄ± yapar"),
        ("search", "KaynaklÄ± gÃ¼ncel web aramasÄ± yapar"),
        ("hikaye", "AI ile hikaye oluÅŸturur"),
        ("oyun", "Oyun menÃ¼sÃ¼nÃ¼ aÃ§ar ğŸ®"),
        ("iptal", "Oyunu bitir ğŸ›‘"),
        ("cevir", "AI Destekli Ã‡eviri"),
        ("borsa", "Borsa takibi & Grafik"),
        ("kripto", "Kripto takibi & Grafik"),
        ("sticker", "Sticker yap (YanÄ±tla)"),
        ("st", "Sticker ara"),
        ("catalog", "Sticker kataloÄŸu"),
        ("packs", "Paketlerim"),
        ("stdelete", "Sticker sil"),
        ("stpack", "Sticker paketini yÃ¶net"),
        ("image", "GÃ¶rsel oluÅŸtur (AI)"),
        ("remind", "HatÄ±rlatÄ±cÄ± kur"),
        ("hatirlat", "Hat\u0131rlat\u0131c\u0131 kur"),
        ("takip", "Hava, deprem ve haber bildirimlerini yÃ¶net"),
        ("takipler", "Aktif bildirim takiplerini gÃ¶ster"),
        ("bildirim", "KiÅŸisel veya grup bildirim merkezini aÃ§"),
        ("bildirimler", "Aktif bildirimlerini gÃ¶ster"),
        ("bildirim_ayar", "Saat dilimi ve sessiz saatleri ayarla"),
        ("bildirimayar", "Saat dilimi ve sessiz saatleri ayarla"),
        ("plan", "GÃ¶rev ve hatÄ±rlatÄ±cÄ± planÄ±nÄ± gÃ¶ster"),
        ("gunluk_ozet", "G\u00fcnl\u00fck hava, deprem, haber ve plan \u00f6zeti"),
        ("daily_brief", "Personal daily digest settings"),
        ("gunlukplan", "G\u00fcnl\u00fck plan\u0131n\u0131 y\u00f6netir"),
        ("haftalikplan", "Haftal\u0131k plan\u0131n\u0131 y\u00f6netir"),
        ("alarm", "DÃ¶viz ve yakÄ±t eÅŸik alarmÄ± kur"),
        ("ekonomi", "Grup coin bakiyesi ve iÅŸlemler"),
        ("bakiye", "Grup coin bakiyeni g\u00f6sterir"),
        ("gunluk", "GÃ¼nlÃ¼k coin Ã¶dÃ¼lÃ¼nÃ¼ al"),
        ("duyuru", "Grup yÃ¶neticileri iÃ§in planlÄ± duyuru"),
        ("oku", "YazÄ±yÄ± oku (TTS)"),
        ("seslendir", "Yaz\u0131y\u0131 seslendirir (TTS)"),
        ("q", "MesajÄ± alÄ±ntÄ± gÃ¶rseline dÃ¶nÃ¼ÅŸtÃ¼r"),
        ("qtop", "En yÃ¼ksek puanlÄ± quote'larÄ± gÃ¶ster"),
        ("qrand", "Rastgele quote gÃ¶ster"),
        ("qtemplate", "Quote ÅŸablonlarÄ±nÄ± yÃ¶net"),
        ("sisecevir", "ÅiÅŸe Ã§evirmece ğŸ¾"),
        ("d", "DoÄŸruluk mu? (AI) ğŸ•µï¸â€â™‚ï¸"),
        ("c", "Cesaret mi? (AI) ğŸ”¥"),
        ("batak", "Batak kur ğŸƒ"),
        ("blackjack", "Blackjack oyna"),
        ("anlat", "Kelime Anlatmaca"),
        ("quiz", "Quiz baÅŸlat ğŸ¯"),
        ("adamasmaca", "Adam asmaca"),
        ("kelimeoyunu", "Kelime oyunu"),
        ("rusruleti", "Rus ruleti"),
        ("duello", "DÃ¼ello baÅŸlat"),
        ("zar", "Zar at"),
        ("yazitura", "YazÄ±-Tura at"),
        ("slot", "Slot Ã§evir"),
        ("clear", "GeÃ§miÅŸi temizle"),
        ("hafiza", "KiÅŸisel AI hafÄ±za onayÄ±"),
        ("sehirler", "T\u00fcrkiye \u015fehir katalo\u011funu arar"),
    ]
    # Register canonical commands in Telegram menu. Telegram enforces a hard
    # limit of 100 commands per scope; aliases remain valid but are exposed by
    # /help and /menu instead of causing Bot_commands_too_much.
    from telegram import BotCommand, BotCommandScopeAllPrivateChats, BotCommandScopeChat
    public_commands = [
        BotCommand(cmd, desc)
        for cmd, desc in build_bot_command_menu(
            commands, excluded_commands=OWNER_ONLY_COMMANDS
        )
    ]
    owner_commands = [BotCommand(cmd, desc) for cmd, desc in build_bot_command_menu(commands)]
    # Keep the default and private scopes clean for regular users. Telegram
    # applies the owner-specific scope only to the configured owner chat.
    await application.bot.set_my_commands(public_commands)
    await application.bot.set_my_commands(public_commands, scope=BotCommandScopeAllPrivateChats())
    owner_id = int(getattr(Config, "OWNER_ID", 0) or 0)
    if owner_id > 0:
        await application.bot.set_my_commands(
            owner_commands,
            scope=BotCommandScopeChat(chat_id=owner_id),
        )

    logger.info(
        "Bot commands registered successfully: public=%s owner=%s",
        len(public_commands), len(owner_commands),
    )


def register_handlers(application, orchestrator):
    """Register Telegram surfaces against an explicitly injected orchestrator."""
    # Register handlers
    application.add_handler(CommandHandler("start", orchestrator.handle_start_command))
    application.add_handler(CommandHandler("image", orchestrator.handle_image_command))
    application.add_handler(CommandHandler(["indir", "dl", "download"], orchestrator.handle_download_command))
    application.add_handler(CommandHandler("medya_cookie", orchestrator.handle_media_cookie_command))
    application.add_handler(CommandHandler("jobs", orchestrator.handle_media_job_command))
    application.add_handler(CommandHandler("metrics", orchestrator.handle_media_metrics_command))
    application.add_handler(CommandHandler("health", orchestrator.handle_health_command))
    application.add_handler(CommandHandler("providers", orchestrator.handle_providers_command))
    application.add_handler(CommandHandler("features", orchestrator.handle_features_command))
    application.add_handler(CommandHandler("cache", orchestrator.handle_cache_command))
    application.add_handler(CommandHandler("maintenance", orchestrator.handle_maintenance_command))
    application.add_handler(CommandHandler("audit", orchestrator.handle_audit_command))
    application.add_handler(CommandHandler("menu", orchestrator.handle_menu_command))
    application.add_handler(CommandHandler("clear", orchestrator.handle_clear_command))
    application.add_handler(CommandHandler(["belgeler", "documents"], orchestrator.handle_documents_command))
    application.add_handler(CommandHandler(["hafiza", "memory"], orchestrator.handle_memory_consent_command))
    application.add_handler(CommandHandler("help", orchestrator.handle_help_command))
    application.add_handler(CommandHandler(["leaderboard", "liderlik", "lb"], orchestrator.handle_leaderboard_command))
    application.add_handler(CommandHandler(["profile", "profil"], orchestrator.handle_profile_command))
    application.add_handler(CommandHandler(["basarimlar", "achievements"], orchestrator.handle_achievements_command))
    application.add_handler(CommandHandler(["sezon", "season"], orchestrator.handle_season_command))
    application.add_handler(CommandHandler(["ekonomi", "bakiye"], orchestrator.handle_economy_command))
    application.add_handler(CommandHandler(["gunluk", "daily"], orchestrator.handle_daily_reward_command))
    application.add_handler(CommandHandler("lang", orchestrator.handle_lang_command))
    application.add_handler(CommandHandler("digest", orchestrator.handle_digest_command))
    application.add_handler(CommandHandler("istatistik", orchestrator.handle_group_analytics_command))
    application.add_handler(CommandHandler(["moderasyon", "mod"], orchestrator.handle_moderation_command))
    application.add_handler(CommandHandler("sudohelp", orchestrator.handle_sudohelp_command))

    # Sudo Commands
    application.add_handler(CommandHandler("sudo", orchestrator.handle_sudo_command))
    application.add_handler(CommandHandler("unsudo", orchestrator.handle_unsudo_command))
    application.add_handler(CommandHandler("ban", orchestrator.handle_ban_command))
    application.add_handler(CommandHandler("unban", orchestrator.handle_unban_command))
    application.add_handler(CommandHandler("broadcast", orchestrator.handle_broadcast_command))
    application.add_handler(CommandHandler("groups", orchestrator.handle_groups_command))
    application.add_handler(CommandHandler("sudolist", orchestrator.handle_sudolist_command))
    application.add_handler(CommandHandler("obs", orchestrator.toggle_observation))
    application.add_handler(CommandHandler("sohbet", orchestrator.handle_sohbet_command))
    application.add_handler(CommandHandler("ayar", orchestrator.handle_ayar_command))
    application.add_handler(CommandHandler(["persona", "uslup"], orchestrator.handle_personality_command))

    # Culture & Intelligence
    application.add_handler(CommandHandler("filmoneri", orchestrator.handle_culture_command))
    application.add_handler(CommandHandler("dizioneri", orchestrator.handle_culture_command))
    application.add_handler(CommandHandler("kitaponeri", orchestrator.handle_culture_command))
    application.add_handler(CommandHandler("sarkioneri", orchestrator.handle_culture_command))
    application.add_handler(CommandHandler("kultur", orchestrator.handle_culture_command))
    application.add_handler(CommandHandler("hikaye", orchestrator.handle_story_command))
    application.add_handler(CommandHandler(["soz", "sarki", "lyrics"], orchestrator.handle_lyrics_command))
    application.add_handler(CommandHandler(["ara", "search"], orchestrator.handle_search_command))
    application.add_handler(CommandHandler("cevir", orchestrator.handle_cevir_command))
    application.add_handler(CommandHandler("borsa", orchestrator.handle_finance_command))
    application.add_handler(CommandHandler("kripto", orchestrator.handle_finance_command))
    application.add_handler(CommandHandler("sticker", orchestrator.handle_sticker_command))
    application.add_handler(CommandHandler("st", orchestrator.handle_sticker_search))
    application.add_handler(CommandHandler("catalog", orchestrator.handle_catalog))
    application.add_handler(CommandHandler("packs", orchestrator.handle_packs_command))
    application.add_handler(CommandHandler("stdelete", orchestrator.handle_stdelete_command))
    application.add_handler(CommandHandler("stpack", orchestrator.handle_stpack_command))

    # Inline Query Handler for Global Sticker Search
    from telegram.ext import InlineQueryHandler
    application.add_handler(InlineQueryHandler(orchestrator.handle_inline_query))

    # General Message Handlers
    application.add_handler(CommandHandler(["remind", "hatirlat"], orchestrator.handle_remind_command))
    application.add_handler(CommandHandler("hatirlatmalar", orchestrator.handle_reminders_command))
    application.add_handler(CommandHandler(["bildirim_ayar", "bildirimayar"], orchestrator.handle_notification_settings_command))
    application.add_handler(CommandHandler(["takip", "bildirim"], orchestrator.handle_subscription_command))
    application.add_handler(CommandHandler(["takipler", "bildirimler"], orchestrator.handle_subscription_command))
    application.add_handler(CommandHandler("not", orchestrator.handle_note_command))
    application.add_handler(CommandHandler("notlar", orchestrator.handle_notes_command))
    application.add_handler(CommandHandler("gorev", orchestrator.handle_task_command))
    application.add_handler(CommandHandler("gorevler", orchestrator.handle_tasks_command))
    application.add_handler(CommandHandler(["plan", "gunlukplan", "haftalikplan"], orchestrator.handle_plan_command))
    application.add_handler(CommandHandler(["gunluk_ozet", "daily_brief"], orchestrator.handle_daily_brief_command))
    application.add_handler(CommandHandler("quiz", orchestrator.handle_quiz_command))
    application.add_handler(CommandHandler("q", orchestrator.handle_q_command))
    application.add_handler(CommandHandler("qtop", orchestrator.handle_qtop_command))
    application.add_handler(CommandHandler("qrand", orchestrator.handle_qrand_command))
    application.add_handler(CommandHandler("qd", orchestrator.handle_qd_command))
    application.add_handler(CommandHandler("qtemplate", orchestrator.handle_qtemplate_command))

    # Localized Game Commands
    application.add_handler(CommandHandler("sisecevir", orchestrator.handle_sisecevir_command))
    application.add_handler(CommandHandler("rusruleti", orchestrator.handle_rusruleti_command))
    application.add_handler(CommandHandler("duello", orchestrator.handle_duello_command))
    application.add_handler(CommandHandler("zar", orchestrator.handle_zar_command))
    application.add_handler(CommandHandler("yazitura", orchestrator.handle_yazitura_command))
    application.add_handler(CommandHandler("slot", orchestrator.handle_slot_command))
    application.add_handler(CommandHandler("adamasmaca", orchestrator.handle_hangman_command))
    application.add_handler(CommandHandler("kelimeoyunu", orchestrator.handle_wordgame_command))
    application.add_handler(CommandHandler("batak", orchestrator.handle_batak_command))
    application.add_handler(CommandHandler("blackjack", orchestrator.handle_blackjack_command))
    application.add_handler(CommandHandler("anlat", orchestrator.handle_anlat_command))
    application.add_handler(CommandHandler("oyun", orchestrator.handle_oyun_command))
    application.add_handler(CommandHandler("iptal", orchestrator.handle_oyunbitir_command))
    application.add_handler(CommandHandler("bitir", orchestrator.handle_oyunbitir_command))
    application.add_handler(CommandHandler("d", orchestrator.handle_truth_dare_command))
    application.add_handler(CommandHandler("c", orchestrator.handle_truth_dare_command))

    application.add_handler(CommandHandler("test", orchestrator.handle_test_command))
    application.add_handler(CommandHandler("ping", orchestrator.handle_ping_command))
    application.add_handler(CommandHandler("haber", orchestrator.handle_haber_command))
    application.add_handler(CommandHandler("deprem", orchestrator.handle_deprem_command))
    application.add_handler(CommandHandler("ucak", orchestrator.handle_ucak_command))
    application.add_handler(CommandHandler("hava", orchestrator.handle_hava_command))
    application.add_handler(CommandHandler("hava_kalite", orchestrator.handle_air_quality_command))
    application.add_handler(CommandHandler(["sehir", "plaka", "telefon", "ilce", "sehirler"], orchestrator.handle_city_command))
    application.add_handler(CommandHandler("eczane", orchestrator.handle_eczane_command))
    application.add_handler(CommandHandler("akaryakit", orchestrator.handle_akaryakit_command))
    application.add_handler(CommandHandler(["doviz", "altin"], orchestrator.handle_doviz_command))
    application.add_handler(CommandHandler("bim", orchestrator.handle_bim_command))
    application.add_handler(CommandHandler("ezan", orchestrator.handle_ezan_command))
    application.add_handler(CommandHandler("alarm", orchestrator.handle_alert_command))
    application.add_handler(CommandHandler("duyuru", orchestrator.handle_announcement_command))
    application.add_handler(CommandHandler("oku", orchestrator.handle_tts_command))
    application.add_handler(CommandHandler("seslendir", orchestrator.handle_tts_command))
    application.add_handler(CommandHandler("qcolor", orchestrator.handle_qcolor_command))
    application.add_handler(CommandHandler("qemoji", orchestrator.handle_qemoji_command))
    application.add_handler(CommandHandler("qrate", orchestrator.handle_qrate_command))
    application.add_handler(CommandHandler("qtheme", orchestrator.handle_qtheme_command))

    # Register global callback query handler
    application.add_handler(CallbackQueryHandler(orchestrator.handle_callback_query))
    application.add_handler(ChatMemberHandler(orchestrator.handle_chat_member_update, ChatMemberHandler.CHAT_MEMBER))

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, orchestrator.handle_message))
    application.add_handler(MessageHandler(filters.ANIMATION, orchestrator.handle_animation))
    application.add_handler(MessageHandler(filters.CAPTION, orchestrator.handle_caption))
    application.add_handler(MessageHandler(filters.PHOTO, orchestrator.handle_photo))
    application.add_handler(MessageHandler(filters.Document.ALL, orchestrator.handle_document))
    application.add_handler(MessageHandler(filters.VOICE, orchestrator.handle_voice))

    # Global Error Handler (FAZ 1)
    from src.core.error_handler import ErrorHandler
    application.add_error_handler(ErrorHandler.handle_telegram_error)


def run_transport(application, transport):
    """Start Telegram through the validated polling/webhook contract."""

    if transport.mode == "webhook":
        logger.info("Bot is starting in webhook mode")
        return application.run_webhook(**transport.webhook_kwargs())
    if transport.mode == "polling":
        logger.info("Bot is polling...")
        return application.run_polling(**transport.polling_kwargs())
    raise ValueError("Unsupported Telegram transport mode")


if __name__ == '__main__':
    orchestrator = Orchestrator()
    try:
        Config.validate()
    except ValueError as e:
        logger.error("Configuration Error: %s", summarize_exception(e, label="configuration"))
        exit(1)

    # Build Application with High Concurrency. Both Telegram clients use the
    # shared verified TLS policy (native Windows trust or explicit CA bundle).
    application_builder = (
        ApplicationBuilder()
        .token(Config.TELEGRAM_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .concurrent_updates(True) # Multi-group parallel processing
    )
    request, get_updates_request = build_request_pair()
    application_builder = application_builder.request(request)
    application_builder = application_builder.get_updates_request(get_updates_request)
    application = application_builder.build()

    application.bot_data["orchestrator"] = orchestrator
    register_handlers(application, orchestrator)
    transport = transport_from_config(Config)
    run_transport(application, transport)
