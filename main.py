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
from src.telegram.command_registry import (
    CommandRegistry,
    CommandScope,
    EXPLICIT_HANDLER_MAP,
    LEGACY_ALIAS_MAP,
    build_arel_command_registry,
)
import logging
import asyncio
import sys

# High Performance Event Loop (Linux)
try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    print("⚡ uvloop activated for maximum speed.")
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
        "lyrics", "search", "gunlukplan", "haftalikplan", "daily_brief",
    }
)

# Operational commands are published only in the owner's private menu. The
# handlers enforce the same owner boundary for direct invocations.
OWNER_ONLY_COMMANDS = frozenset({
    "medya_cookie", "jobs", "metrics", "health", "providers", "features",
    "cache", "maintenance", "audit",
})

# These commands change the current group's state or expose moderator data.
# Telegram gets them through ``BotCommandScopeAllChatAdministrators`` so a
# regular member does not see them in the command picker.  Handler-level
# permission checks remain authoritative for direct invocations.
GROUP_ADMIN_COMMANDS = frozenset({
    "istatistik", "moderasyon", "ayar", "duyuru", "obs", "sohbet",
    "ban", "unban", "qcolor", "qemoji", "qrate", "qtheme",
})

# Arel-specific moderation labels are no longer published.  The embedded Kek
# Guardian vocabulary owns the group administration surface; old Turkish
# spellings remain callable only as compatibility aliases.
GUARDIAN_HIDDEN_MIGRATION_COMMANDS = frozenset({
    "ayar", "grupayari", "ayarlar", "sohbet", "sohbetmodu",
    "risk", "riskraporu", "guvenliliste",
})
GUARDIAN_MENU_NAMES = {
    # Guardian's canonical command vocabulary is the public surface.  Arel's
    # earlier Turkish labels remain accepted by the registry as compatibility
    # aliases, but are never published in Telegram's picker.
    "grupkurallari": "rules",
    "sustur": "mute",
    "susturac": "unmute",
    "yasakla": "ban",
    "yasagiac": "unban",
    "yetkiver": "promote",
    "yetkial": "demote",
    "uyar": "warn",
    "uyarlar": "warns",
    "uyarisifirla": "resetwarn",
    "tekme": "kick",
    "geciciyasa": "tban",
    "hayalet": "sban",
    "mesajisil": "del",
    "mesajtemizle": "purge",
    "sabitle": "pin",
    "sabitlekaldir": "unpin",
    "rapor": "report",
    "yasaklikelimeler": "blocklist",
    "iznilinkler": "whitelist",
    "floodlimiti": "setflood",
    "floodcezasi": "setfloodmode",
    "filtre": "filter",
    "filtreler": "filters",
    "filtresil": "stop",
    "agkur": "newfed",
    "agaekle": "joinfed",
    "agdanayril": "leavefed",
    "agdurum": "fedstat",
    "agban": "fban",
    "agbanac": "funban",
    "agbanlar": "fedbanlist",
    "golgeban": "sban",
    "golgeac": "unsban",
    # Arel's earlier Turkish canonical labels map to the upstream Guardian
    # vocabulary in menus; the original labels remain callable aliases.
    "hosgeldin": "welcome",
    "veda": "goodbye",
    "gecemodu": "nightmode",
    "dogrulama": "captcha",
    "raidkoruma": "antiraid",
    "floodlimiti": "setflood",
    "floodcezasi": "setfloodmode",
    "agkur": "newfed",
    "agaekle": "joinfed",
    "agdanayril": "leavefed",
    "agdurum": "fedstat",
    "agban": "fban",
    "agbanac": "funban",
    "agbanlar": "fedbanlist",
}


def _guardian_menu_rows(rows, *, guardian_first: bool = False):
    """Return non-Guardian rows for Telegram's slash-command picker.

    Guardian commands remain callable through the compatibility dispatcher,
    but are intentionally discoverable from the button-based Guardian help
    center only.  Publishing the whole compatibility catalogue in Telegram's
    ``/`` picker both overwhelms users and leaks group-admin controls to the
    wrong scope.
    """
    from src.moderation.guardian_surface import GuardianSurface

    guardian_names = {str(item).casefold() for item in GuardianSurface.COMMANDS}
    # ``/start`` is the Telegram bootstrap command; all other Guardian
    # commands are discoverable from the button center instead.
    guardian_names.difference_update({"start"})
    guardian_names.update({"linkkoruma", "reklamkoruma", "grupdili"})
    guardian_names.update(str(item).casefold() for item in GUARDIAN_MENU_NAMES)
    guardian_names.update(str(item).casefold() for item in GUARDIAN_MENU_NAMES.values())
    result = []
    seen = set()
    for name, description in rows:
        key = str(name).casefold()
        if key in GUARDIAN_HIDDEN_MIGRATION_COMMANDS or key in guardian_names:
            continue
        preferred = GUARDIAN_MENU_NAMES.get(key, key)
        if preferred.casefold() in guardian_names:
            continue
        if preferred in seen:
            continue
        seen.add(preferred)
        result.append((preferred, description))
    # Telegram caps a scope at 100 commands.  The full Guardian catalogue is
    # intentionally larger, so keep the two core administrator controls in
    # the picker even when later compatibility rows consume the limit.
    # The group-administrator scope has a hard Telegram limit of 100 entries.
    # Keep the embedded Guardian surface ahead of unrelated legacy rows so
    # every Guardian control remains discoverable in the picker.  Direct
    # command resolution still exposes the complete catalogue beyond the cap.
    priority = {"moderasyon": 0, "hizlikur": 1, "rules": 2, "mute": 3, "ban": 4}
    indexed = list(enumerate(result))
    indexed.sort(
        key=lambda item: (
            0 if item[1][0] in priority else (
                1 if guardian_first and item[1][0].casefold() in guardian_names else 2
            ),
            priority.get(item[1][0], 100),
            item[0],
        )
    )
    return [row for _index, row in indexed]

# Arel's canonical command catalogue is intentionally ahead of the Guardian
# migration.  A few canonical names already have a first-party Arel handler,
# while their Guardian-shaped definitions still point at a future service.
# Keep the old, tested handler reachable for those commands during the rolling
# migration.  This map is deliberately explicit: a semantic guess here would
# silently route a moderation command to the wrong operation.
AREL_HANDLER_FALLBACKS = {
    "grupayari": "handle_ayar_command",
    "yasakla": "handle_ban_command",
    "yasagiac": "handle_unban_command",
    "grupistatistik": "handle_group_analytics_command",
    "sohbetmodu": "handle_sohbet_command",
    "areluslubu": "handle_personality_command",
    "oyunbitir": "handle_oyunbitir_command",
}


def build_bot_command_menu(commands, *, excluded_commands=frozenset()):
    """Return a bounded, duplicate-free Telegram command menu.

    Telegram's 100-command API limit applies per scope. Hiding only aliases
    keeps useful canonical commands visible while preserving every alias in
    ``register_handlers`` for backwards-compatible direct invocation.
    """

    registry = _build_runtime_command_registry(commands)
    excluded = {str(item).strip().casefold() for item in excluded_commands}
    return [
        row for row in _guardian_menu_rows(registry.menu_rows(CommandScope.PUBLIC, limit=TELEGRAM_COMMAND_LIMIT))
        if row[0] not in excluded
    ]


def _build_runtime_command_registry(commands):
    """Merge Arel's canonical catalogue with the legacy bootstrap rows.

    The Arel catalogue is deliberately first: it owns the public command
    names and their hidden compatibility aliases.  The older list still
    contributes commands that have not migrated yet, while a colliding legacy
    row is skipped instead of creating two Telegram handlers for one protocol
    name.  This gives us a safe rolling migration: new names are visible and
    callable immediately, old names keep resolving through their Arel alias.
    """

    legacy_registry = CommandRegistry.from_rows(
        commands,
        hidden_aliases=MENU_ALIAS_COMMANDS,
        hidden_alias_map=LEGACY_ALIAS_MAP,
        group_admin_commands=GROUP_ADMIN_COMMANDS,
        owner_commands=OWNER_ONLY_COMMANDS,
        handler_by_command=EXPLICIT_HANDLER_MAP,
    )
    canonical_registry = build_arel_command_registry()
    definitions = []
    claimed_names = set()
    for registry in (canonical_registry, legacy_registry):
        for definition in registry.all_definitions():
            names = (definition.name, *definition.aliases)
            if any(name in claimed_names for name in names):
                continue
            definitions.append(definition)
            claimed_names.update(names)
    return CommandRegistry(definitions)

async def post_init(application):
    orchestrator = application.bot_data.get("orchestrator")
    if orchestrator is None:
        raise RuntimeError("orchestrator_not_initialized")
    logger.info("Bot initializing components")
    configure = getattr(orchestrator, "configure_telegram_dependencies", None)
    if callable(configure):
        configure(application.bot)
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
        ("indir", "Sosyal medya bağlantısından medya indirir"),
        ("dl", "Sosyal medya medyası indirir"),
        ("download", "Sosyal medya medyası indirir"),
        ("medya_cookie", "Yönetici medya cookie kasası"),
        ("jobs", "Medya işi durumunu gösterir"),
        ("metrics", "Medya kuyruğu metriklerini gösterir"),
        ("health", "Sistem sağlık durumunu gösterir"),
        ("providers", "Medya sağlayıcı durumları"),
        ("features", "Kayitli feature manifesti (yonetici)"),
        ("cache", "Medya cache yönetimi"),
        ("maintenance", "Bakım modu"),
        ("audit", "Denetim kayıtları"),
        ("start", "Botu başlatır"),
        ("menu", "Modern ana menüyü açar"),
        ("help", "Yardım menüsünü açar"),
        ("leaderboard", "Oyun liderlik tablosu"),
        ("liderlik", "Oyun liderlik tablosu"),
        ("lb", "Oyun liderlik tablosu"),
        ("profile", "Oyun profilini gösterir"),
        ("profil", "Oyun profilini gösterir"),
        ("basarimlar", "Oyun başarımlarını gösterir"),
        ("sezon", "Mevcut sezon liderliğini gösterir"),
        ("lang", "Dil seçimi: tr/en"),
        ("digest", "AI haber özeti"),
        ("istatistik", "Grubun haftalık aktivite özetini gösterir (yönetici)"),
        ("moderasyon", "Grup moderasyon ihlallerini gösterir (yönetici)"),
        ("mod", "Grup moderasyon ihlallerini gösterir (yönetici)"),
        # --- Yeni Araçlar ---
        ("haber", "Son dakika haberlerini getirir"),
        ("deprem", "Son depremleri listeler"),
        ("hava", "Hava durumu bilgisi (Örn: /hava istanbul)"),
        ("hava_kalite", "AQI, partikül ve polen bilgisi"),
        ("akaryakit", "Güncel yakıt fiyatları"),
        ("doviz", "Canlı döviz kurları ve altın"),
        ("altin", "Altın kurlarını getirir"),
        ("eczane", "Nöbetçi eczaneler (Örn: /eczane istanbul)"),
        ("ezan", "Ezan vakitlerini sorgular"),
        ("ucak", "Uçak bileti fırsatlarını listeler"),
        ("bim", "Haftalık Bim aktüel ürünleri"),
        ("sehir", "İl, plaka, telefon ve ilçe bilgisi"),
        ("plaka", "Plaka kodundan şehir bul"),
        ("telefon", "Telefon alan kodundan şehir bul"),
        ("ilce", "İlçe adından şehir bul"),
        ("not", "Kişisel not kaydet"),
        ("notlar", "Notlarını listele"),
        ("belgeler", "Yüklenen kişisel belgeleri yönet"),
        ("documents", "Y\u00fcklenmi\u015f ki\u015fisel belgeleri y\u00f6net"),
        ("gorev", "Görev ekle veya tamamla"),
        ("gorevler", "Açık görevlerini listele"),
        ("hatirlatmalar", "Bekleyen hatırlatıcılar"),
        # --- Mevcut Komutlar ---
        ("ayar", "Grup ayarlarını yapılandırır"),
        ("persona", "Özel sohbet Arel üslubunu seç"),
        ("uslup", "Özel sohbet Arel üslubunu seç"),
        ("filmoneri", "Film önerisi sunar"),
        ("dizioneri", "Dizi önerisi sunar"),
        ("kitaponeri", "Kitap önerisi sunar"),
        ("sarkioneri", "Şarkı önerisi sunar"),
        ("kultur", "Film, dizi, kitap veya müzik önerisi"),
        ("soz", "Şarkı sözü kaynağından kısa alıntı gösterir"),
        ("sarki", "Şarkı sözü kaynağından kısa alıntı gösterir"),
        ("lyrics", "Şarkı sözü kaynağından kısa alıntı gösterir"),
        ("ara", "Kaynaklı güncel web araması yapar"),
        ("search", "Kaynaklı güncel web araması yapar"),
        ("hikaye", "AI ile hikaye oluşturur"),
        ("oyun", "Oyun menüsünü açar 🎮"),
        ("iptal", "Oyunu bitir 🛑"),
        ("cevir", "AI Destekli Çeviri"),
        ("borsa", "Borsa takibi & Grafik"),
        ("kripto", "Kripto takibi & Grafik"),
        ("sticker", "Sticker yap (Yanıtla)"),
        ("st", "Sticker ara"),
        ("catalog", "Sticker kataloğu"),
        ("packs", "Paketlerim"),
        ("stdelete", "Sticker sil"),
        ("stpack", "Sticker paketini yönet"),
        ("image", "Görsel oluştur (AI)"),
        ("remind", "Hatırlatıcı kur"),
        ("hatirlat", "Hat\u0131rlat\u0131c\u0131 kur"),
        ("takip", "Hava, deprem ve haber bildirimlerini yönet"),
        ("takipler", "Aktif bildirim takiplerini göster"),
        ("bildirim", "Kişisel veya grup bildirim merkezini aç"),
        ("bildirimler", "Aktif bildirimlerini göster"),
        ("bildirim_ayar", "Saat dilimi ve sessiz saatleri ayarla"),
        ("bildirimayar", "Saat dilimi ve sessiz saatleri ayarla"),
        ("plan", "Görev ve hatırlatıcı planını göster"),
        ("gunluk_ozet", "G\u00fcnl\u00fck hava, deprem, haber ve plan \u00f6zeti"),
        ("daily_brief", "Personal daily digest settings"),
        ("gunlukplan", "G\u00fcnl\u00fck plan\u0131n\u0131 y\u00f6netir"),
        ("haftalikplan", "Haftal\u0131k plan\u0131n\u0131 y\u00f6netir"),
        ("alarm", "Döviz ve yakıt eşik alarmı kur"),
        ("ekonomi", "Grup coin bakiyesi ve işlemler"),
        ("bakiye", "Grup coin bakiyeni g\u00f6sterir"),
        ("gunluk", "Günlük coin ödülünü al"),
        ("duyuru", "Grup yöneticileri için planlı duyuru"),
        ("oku", "Yazıyı oku (TTS)"),
        ("seslendir", "Yaz\u0131y\u0131 seslendirir (TTS)"),
        ("q", "Mesajı alıntı görseline dönüştür"),
        ("qtop", "En yüksek puanlı quote'ları göster"),
        ("qrand", "Rastgele quote göster"),
        ("qtemplate", "Quote şablonlarını yönet"),
        ("sisecevir", "Şişe çevirmece 🍾"),
        ("d", "Doğruluk mu? (AI) 🕵️‍♂️"),
        ("c", "Cesaret mi? (AI) 🔥"),
        ("batak", "Batak kur 🃏"),
        ("blackjack", "Blackjack oyna"),
        ("anlat", "Kelime Anlatmaca"),
        ("quiz", "Quiz başlat 🎯"),
        ("adamasmaca", "Adam asmaca"),
        ("kelimeoyunu", "Kelime oyunu"),
        ("connect4", "Connect Four"),
        ("xox", "XOX oyunu"),
        ("gomoku", "Gomoku / beşli oyunu"),
        ("rusruleti", "Rus ruleti"),
        ("duello", "Düello başlat"),
        ("zar", "Zar at"),
        ("yazitura", "Yazı-Tura at"),
        ("slot", "Slot çevir"),
        ("clear", "Geçmişi temizle"),
        ("hafiza", "Kişisel AI hafıza onayı"),
        ("sehirler", "T\u00fcrkiye \u015fehir katalo\u011funu arar"),
    ]
    # Register canonical commands in Telegram menu. Telegram enforces a hard
    # limit of 100 commands per scope; aliases remain valid but are exposed by
    # /help and /menu instead of causing Bot_commands_too_much.
    from telegram import (
        BotCommand,
        BotCommandScopeAllChatAdministrators,
        BotCommandScopeAllPrivateChats,
        BotCommandScopeChat,
    )
    command_registry = _build_runtime_command_registry(commands)
    public_commands = [
        BotCommand(cmd, desc)
        for cmd, desc in _guardian_menu_rows(
            command_registry.menu_rows(CommandScope.PUBLIC, limit=None)
        )[:TELEGRAM_COMMAND_LIMIT]
    ]
    group_admin_commands = [
        BotCommand(cmd, desc)
        for cmd, desc in _guardian_menu_rows(
            command_registry.menu_rows(CommandScope.GROUP_ADMIN, limit=None),
            guardian_first=True,
        )[:TELEGRAM_COMMAND_LIMIT]
    ]
    owner_rows = command_registry.menu_rows(CommandScope.OWNER, limit=None)
    owner_required = set(OWNER_ONLY_COMMANDS) | {"arelmerkez"}
    owner_rows = [row for row in owner_rows if row[0] in owner_required] + [
        row for row in owner_rows if row[0] not in owner_required
    ]
    owner_commands = [BotCommand(cmd, desc) for cmd, desc in owner_rows[:TELEGRAM_COMMAND_LIMIT]]
    # Keep the default and private scopes clean for regular users. Telegram
    # applies the owner-specific scope only to the configured owner chat.
    await application.bot.set_my_commands(public_commands)
    await application.bot.set_my_commands(public_commands, scope=BotCommandScopeAllPrivateChats())
    await application.bot.set_my_commands(
        group_admin_commands,
        scope=BotCommandScopeAllChatAdministrators(),
    )
    owner_id = int(getattr(Config, "OWNER_ID", 0) or 0)
    if owner_id > 0:
        await application.bot.set_my_commands(
            owner_commands,
            scope=BotCommandScopeChat(chat_id=owner_id),
        )

    logger.info(
        "Bot commands registered successfully: public=%s group_admin=%s owner=%s",
        len(public_commands), len(group_admin_commands), len(owner_commands),
    )


def register_handlers(application, orchestrator):
    """Register Telegram surfaces against an explicitly injected orchestrator."""
    registered_command_names: set[str] = set()

    def add_handler(handler):
        """Add a Telegram handler while preventing duplicate command routing.

        The Arel catalogue is registered before the legacy surface below.  A
        compatibility alias therefore resolves to its canonical Arel callback,
        and the rolling legacy registration is retained only for commands that
        have not migrated yet.  Non-command handlers pass through unchanged.
        """

        if isinstance(handler, CommandHandler):
            names = sorted(
                str(name).strip().casefold()
                for name in handler.commands
                if str(name).strip().casefold() not in registered_command_names
            )
            if not names:
                return
            registered_command_names.update(names)
            handler = CommandHandler(names, handler.callback)
        application.add_handler(handler)

    def unavailable_arel_command_handler(command_name):
        """Return a safe callable for a catalogue row awaiting its service.

        A command must never crash the update dispatcher just because its
        provider is not enabled on this deployment.  The response is explicit
        and short; it also keeps the command callable while the Guardian
        migration is rolled out incrementally.
        """

        async def _handler(update, context):
            message = getattr(update, "effective_message", None)
            if message is not None and hasattr(message, "reply_text"):
                await message.reply_text(
                    f"/{command_name} bu kurulumda henüz etkin değil; komut kaydı alındı."
                )

        return _handler

    # Canonical Arel commands are registered first.  Hidden legacy aliases in
    # the same definition remain callable, but are never published by menu
    # registration.  Missing service methods receive a safe placeholder until
    # their feature wave is enabled; this is preferable to a dead command or a
    # dispatcher AttributeError.
    for definition in build_arel_command_registry().all_definitions():
        callback = getattr(orchestrator, definition.handler, None)
        # If a canonical Guardian migration row is not implemented yet, use
        # an explicit legacy/Arel handler only where its semantics are known.
        # This prevents aliases such as /ban from being claimed by the safe
        # placeholder before the existing /ban implementation is registered.
        if callback is None:
            fallback_name = AREL_HANDLER_FALLBACKS.get(definition.name)
            if fallback_name:
                callback = getattr(orchestrator, fallback_name, None)
        if callback is None:
            callback = unavailable_arel_command_handler(definition.name)
        add_handler(CommandHandler([definition.name, *definition.aliases], callback))

    # Register handlers
    add_handler(CommandHandler("start", orchestrator.handle_start_command))
    add_handler(CommandHandler("image", orchestrator.handle_image_command))
    add_handler(CommandHandler(["indir", "dl", "download"], orchestrator.handle_download_command))
    add_handler(CommandHandler("medya_cookie", orchestrator.handle_media_cookie_command))
    add_handler(CommandHandler("jobs", orchestrator.handle_media_job_command))
    add_handler(CommandHandler("metrics", orchestrator.handle_media_metrics_command))
    add_handler(CommandHandler("health", orchestrator.handle_health_command))
    add_handler(CommandHandler("providers", orchestrator.handle_providers_command))
    add_handler(CommandHandler("features", orchestrator.handle_features_command))
    add_handler(CommandHandler("cache", orchestrator.handle_cache_command))
    add_handler(CommandHandler("maintenance", orchestrator.handle_maintenance_command))
    add_handler(CommandHandler("audit", orchestrator.handle_audit_command))
    add_handler(CommandHandler("menu", orchestrator.handle_menu_command))
    add_handler(CommandHandler("clear", orchestrator.handle_clear_command))
    add_handler(CommandHandler(["belgeler", "documents"], orchestrator.handle_documents_command))
    add_handler(CommandHandler(["hafiza", "memory"], orchestrator.handle_memory_consent_command))
    add_handler(CommandHandler("help", orchestrator.handle_help_command))
    add_handler(CommandHandler(["leaderboard", "liderlik", "lb"], orchestrator.handle_leaderboard_command))
    add_handler(CommandHandler(["profile", "profil"], orchestrator.handle_profile_command))
    add_handler(CommandHandler(["basarimlar", "achievements"], orchestrator.handle_achievements_command))
    add_handler(CommandHandler(["sezon", "season"], orchestrator.handle_season_command))
    add_handler(CommandHandler(["ekonomi", "bakiye"], orchestrator.handle_economy_command))
    add_handler(CommandHandler(["gunluk", "daily"], orchestrator.handle_daily_reward_command))
    add_handler(CommandHandler("lang", orchestrator.handle_lang_command))
    add_handler(CommandHandler("digest", orchestrator.handle_digest_command))
    add_handler(CommandHandler("istatistik", orchestrator.handle_group_analytics_command))
    add_handler(CommandHandler(["moderasyon", "mod"], orchestrator.handle_moderation_command))
    add_handler(CommandHandler("sudohelp", orchestrator.handle_sudohelp_command))

    # Sudo Commands
    add_handler(CommandHandler("sudo", orchestrator.handle_sudo_command))
    add_handler(CommandHandler("unsudo", orchestrator.handle_unsudo_command))
    add_handler(CommandHandler("ban", orchestrator.handle_ban_command))
    add_handler(CommandHandler("unban", orchestrator.handle_unban_command))
    add_handler(CommandHandler("broadcast", orchestrator.handle_broadcast_command))
    add_handler(CommandHandler("broadcastpin", orchestrator.handle_broadcastpin_command))
    add_handler(CommandHandler("groups", orchestrator.handle_groups_command))
    add_handler(CommandHandler("sudolist", orchestrator.handle_sudolist_command))
    add_handler(CommandHandler("obs", orchestrator.toggle_observation))
    add_handler(CommandHandler("sohbet", orchestrator.handle_sohbet_command))
    add_handler(CommandHandler("ayar", orchestrator.handle_ayar_command))
    add_handler(CommandHandler(["persona", "uslup"], orchestrator.handle_personality_command))
    # Culture & Intelligence
    add_handler(CommandHandler("filmoneri", orchestrator.handle_culture_command))
    add_handler(CommandHandler("dizioneri", orchestrator.handle_culture_command))
    add_handler(CommandHandler("kitaponeri", orchestrator.handle_culture_command))
    add_handler(CommandHandler("sarkioneri", orchestrator.handle_culture_command))
    add_handler(CommandHandler("kultur", orchestrator.handle_culture_command))
    add_handler(CommandHandler("hikaye", orchestrator.handle_story_command))
    add_handler(CommandHandler(["soz", "sarki", "lyrics"], orchestrator.handle_lyrics_command))
    add_handler(CommandHandler(["ara", "search"], orchestrator.handle_search_command))
    add_handler(CommandHandler("cevir", orchestrator.handle_cevir_command))
    add_handler(CommandHandler("borsa", orchestrator.handle_finance_command))
    add_handler(CommandHandler("kripto", orchestrator.handle_finance_command))
    add_handler(CommandHandler("sticker", orchestrator.handle_sticker_command))
    add_handler(CommandHandler("st", orchestrator.handle_sticker_search))
    add_handler(CommandHandler("catalog", orchestrator.handle_catalog))
    add_handler(CommandHandler("packs", orchestrator.handle_packs_command))
    add_handler(CommandHandler("stdelete", orchestrator.handle_stdelete_command))
    add_handler(CommandHandler("stpack", orchestrator.handle_stpack_command))

    # Inline Query Handler for Global Sticker Search
    from telegram.ext import InlineQueryHandler
    application.add_handler(InlineQueryHandler(orchestrator.handle_inline_query))

    # General Message Handlers
    add_handler(CommandHandler(["remind", "hatirlat"], orchestrator.handle_remind_command))
    add_handler(CommandHandler("hatirlatmalar", orchestrator.handle_reminders_command))
    add_handler(CommandHandler(["bildirim_ayar", "bildirimayar"], orchestrator.handle_notification_settings_command))
    add_handler(CommandHandler(["takip", "bildirim"], orchestrator.handle_subscription_command))
    add_handler(CommandHandler(["takipler", "bildirimler"], orchestrator.handle_subscription_command))
    add_handler(CommandHandler("not", orchestrator.handle_note_command))
    add_handler(CommandHandler("notlar", orchestrator.handle_notes_command))
    add_handler(CommandHandler("gorev", orchestrator.handle_task_command))
    add_handler(CommandHandler("gorevler", orchestrator.handle_tasks_command))
    add_handler(CommandHandler(["plan", "gunlukplan", "haftalikplan"], orchestrator.handle_plan_command))
    add_handler(CommandHandler(["gunluk_ozet", "daily_brief"], orchestrator.handle_daily_brief_command))
    add_handler(CommandHandler("quiz", orchestrator.handle_quiz_command))
    add_handler(CommandHandler("q", orchestrator.handle_q_command))
    add_handler(CommandHandler("qtop", orchestrator.handle_qtop_command))
    add_handler(CommandHandler("qrand", orchestrator.handle_qrand_command))
    add_handler(CommandHandler("qd", orchestrator.handle_qd_command))
    add_handler(CommandHandler("qtemplate", orchestrator.handle_qtemplate_command))

    # Localized Game Commands
    add_handler(CommandHandler("sisecevir", orchestrator.handle_sisecevir_command))
    add_handler(CommandHandler("rusruleti", orchestrator.handle_rusruleti_command))
    add_handler(CommandHandler("duello", orchestrator.handle_duello_command))
    add_handler(CommandHandler("zar", orchestrator.handle_zar_command))
    add_handler(CommandHandler("yazitura", orchestrator.handle_yazitura_command))
    add_handler(CommandHandler("slot", orchestrator.handle_slot_command))
    add_handler(CommandHandler("adamasmaca", orchestrator.handle_hangman_command))
    add_handler(CommandHandler("kelimeoyunu", orchestrator.handle_wordgame_command))
    add_handler(CommandHandler("connect4", orchestrator.handle_connect4_command))
    add_handler(CommandHandler(["xox", "tictactoe"], orchestrator.handle_tictactoe_command))
    add_handler(CommandHandler(["gomoku", "besli"], orchestrator.handle_gomoku_command))
    add_handler(CommandHandler("batak", orchestrator.handle_batak_command))
    add_handler(CommandHandler("blackjack", orchestrator.handle_blackjack_command))
    add_handler(CommandHandler("anlat", orchestrator.handle_anlat_command))
    add_handler(CommandHandler("oyun", orchestrator.handle_oyun_command))
    add_handler(CommandHandler("iptal", orchestrator.handle_oyunbitir_command))
    add_handler(CommandHandler("bitir", orchestrator.handle_oyunbitir_command))
    add_handler(CommandHandler("d", orchestrator.handle_truth_dare_command))
    add_handler(CommandHandler("c", orchestrator.handle_truth_dare_command))

    add_handler(CommandHandler("test", orchestrator.handle_test_command))
    add_handler(CommandHandler("ping", orchestrator.handle_ping_command))
    add_handler(CommandHandler("haber", orchestrator.handle_haber_command))
    add_handler(CommandHandler("deprem", orchestrator.handle_deprem_command))
    add_handler(CommandHandler("ucak", orchestrator.handle_ucak_command))
    add_handler(CommandHandler("hava", orchestrator.handle_hava_command))
    add_handler(CommandHandler("hava_kalite", orchestrator.handle_air_quality_command))
    add_handler(CommandHandler(["sehir", "plaka", "telefon", "ilce", "sehirler"], orchestrator.handle_city_command))
    add_handler(CommandHandler("eczane", orchestrator.handle_eczane_command))
    add_handler(CommandHandler("akaryakit", orchestrator.handle_akaryakit_command))
    add_handler(CommandHandler(["doviz", "altin"], orchestrator.handle_doviz_command))
    add_handler(CommandHandler("bim", orchestrator.handle_bim_command))
    add_handler(CommandHandler("ezan", orchestrator.handle_ezan_command))
    add_handler(CommandHandler("alarm", orchestrator.handle_alert_command))
    add_handler(CommandHandler("duyuru", orchestrator.handle_announcement_command))
    add_handler(CommandHandler("oku", orchestrator.handle_tts_command))
    add_handler(CommandHandler("seslendir", orchestrator.handle_tts_command))
    add_handler(CommandHandler("qcolor", orchestrator.handle_qcolor_command))
    add_handler(CommandHandler("qemoji", orchestrator.handle_qemoji_command))
    add_handler(CommandHandler("qrate", orchestrator.handle_qrate_command))
    add_handler(CommandHandler("qtheme", orchestrator.handle_qtheme_command))

    # Group-scoped Guardian aliases are persisted at runtime and therefore
    # cannot be registered as static CommandHandler rows.  This catch-all is
    # deliberately placed after every concrete command handler; the Guardian
    # handler ignores unknown commands and preserves first-party collisions.
    application.add_handler(MessageHandler(filters.COMMAND, orchestrator.handle_guardian_command))

    # Register global callback query handler
    application.add_handler(CallbackQueryHandler(orchestrator.handle_callback_query))
    application.add_handler(ChatMemberHandler(orchestrator.handle_chat_member_update, ChatMemberHandler.CHAT_MEMBER))
    application.add_handler(MessageHandler(filters.StatusUpdate.ALL, orchestrator.handle_service_message))

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, orchestrator.handle_message))
    application.add_handler(MessageHandler(filters.ANIMATION, orchestrator.handle_animation))
    application.add_handler(MessageHandler(filters.CAPTION, orchestrator.handle_caption))
    application.add_handler(MessageHandler(filters.PHOTO, orchestrator.handle_photo))
    application.add_handler(MessageHandler(filters.Document.ALL, orchestrator.handle_document))
    application.add_handler(MessageHandler(filters.VOICE, orchestrator.handle_voice))
    passive_media = (filters.VIDEO | filters.AUDIO | filters.VIDEO_NOTE | filters.Sticker.ALL) & ~filters.CAPTION
    application.add_handler(MessageHandler(passive_media, orchestrator.handle_passive_attachment))

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
