"""Built-in feature manifest and source governance metadata."""

from __future__ import annotations

from typing import Any

from .contracts import FeatureDefinition
from .registry import FeatureRegistry


SPATULA_FEATURES = (
    ("pharmacy", "turkiye", ("eczane",), "get_pharmacies"),
    ("fuel", "finans", ("akaryakit",), "get_fuel_prices"),
    ("currency", "finans", ("doviz", "altin"), "get_currency_rates"),
    ("crypto", "finans", ("kripto",), "get_crypto_prices"),
    ("market", "finans", ("borsa",), "get_market_quote"),
    ("earthquakes", "turkiye", ("deprem",), "get_recent_earthquakes"),
    ("bim_deals", "turkiye", ("bim",), "get_bim_deals"),
    ("breaking_news", "haber", ("haber",), "get_breaking_news"),
    ("weather", "hava", ("hava",), "get_weather"),
    ("air_quality", "hava", ("hava_kalite",), "get_air_quality"),
    ("prayer_times", "turkiye", ("ezan",), "get_prayer_times"),
    ("flights", "ulasim", ("ucak",), "search_flights"),
    ("story", "ai", ("hikaye",), "generate_story"),
    ("culture_recommendation", "kultur", ("kultur", "filmoneri", "dizioneri", "kitaponeri", "sarkioneri"), "culture_tool"),
    ("lyrics", "ai", ("soz", "sarki", "lyrics"), "lyrics_tool"),
    ("document_search", "ai", (), "document_search"),
)

# A provider cannot be promoted to a production feature without a source and
# terms record.  The values are also used for user-facing attribution.
SOURCE_METADATA: dict[str, tuple[str, str | None, str, str | None]] = {
    # Runtime queries append a city slug under `/nobetci-eczane`. The
    # governance/attribution URL remains the stable public root because the
    # bare runtime path is not a valid landing page.
    "pharmacy": ("N\u00f6bet\u00e7i Eczaneler", "https://www.nobetcieczaneler.org/", "Public web data", "https://www.nobetcieczaneler.org/"),
    "fuel": ("EPDK / G\u00fcncel Akaryak\u0131t Fiyatlar\u0131", "https://guncelakaryakitfiyatlari.com/veri/fiyatlar.json", "EPDK public data; derived snapshot attribution required", "https://guncelakaryakitfiyatlari.com/acik-veri/"),
    "currency": ("D\u00f6viz.com", "https://www.doviz.com/", "Public web data", "https://www.doviz.com/"),
    "crypto": ("CoinGecko", "https://www.coingecko.com/en/api", "CoinGecko API terms; attribution applies", "https://www.coingecko.com/en/api"),
    "market": ("Yahoo Finance", "https://finance.yahoo.com/", "Yahoo Finance terms; fixed-host Chart API attribution", "https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html"),
    "earthquakes": ("AFAD", "https://servisnet.afad.gov.tr/apigateway/deprem/apiv2/event/filter", "Official public service", "https://deprem.afad.gov.tr/event-service"),
    "bim_deals": ("B\u0130M", "https://www.bim.com.tr/Categories/100/aktuel-urunler.aspx", "Official public page", "https://www.bim.com.tr/"),
    "breaking_news": ("TRT Haber RSS", "https://www.trthaber.com/sondakika_articles.rss", "Official public RSS feed; source attribution required", "https://www.trthaber.com/sitene_ekle.html"),
    "weather": ("wttr.in", "https://wttr.in/", "Open source service", "https://wttr.in/"),
    "air_quality": ("Open-Meteo Air Quality", "https://open-meteo.com/en/docs/air-quality-api", "Open-Meteo API terms; attribution required", "https://open-meteo.com/en/terms"),
    "prayer_times": ("Habert\u00fcrk", "https://www.haberturk.com/namaz-vakitleri", "Public web data", "https://www.haberturk.com/"),
    "flights": ("Arelbot flight search links", None, "Generated search links", None),
    "story": ("Arelbot AI story provider", None, "Arelbot generated", None),
    "culture_recommendation": ("Open Library / TVmaze / Wikidata / iTunes Search", "https://www.wikidata.org/wiki/Wikidata:Data_access", "Public API data; Wikidata structured data is CC0 and API rate limits apply", "https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_API_Usage_Guidelines"),
    "lyrics": ("Tavily + permitted lyrics sources", None, "Source-specific terms; bounded excerpt and attribution", "https://www.tavily.com/terms"),
    "document_search": ("User-provided documents", None, "User content", None),
}

# Technical adapters remain disabled from production promotion until source
# terms, robots rules, rate limits and attribution are reviewed.  Keeping this
# matrix beside the source metadata makes that state visible in the feature
# manifest and the provider governance gate.
SOURCE_GOVERNANCE: dict[str, dict[str, str]] = {
    key: {
        "source_governance": "pending",
        "robots_policy": "review_required",
        "terms_review": "review_required",
        "production_signoff": "required",
    }
    for key in {
        "pharmacy",
        "fuel",
        "currency",
        "crypto",
        "market",
        "earthquakes",
        "bim_deals",
        "breaking_news",
        "weather",
        "air_quality",
        "prayer_times",
        "culture_recommendation",
        "lyrics",
    }
}
SOURCE_GOVERNANCE.update(
    {
        # These adapters use documented public JSON/RSS endpoints, include
        # source attribution in their output and are bounded by transport and
        # cache policies. Re-review is required if a source identity changes.
        "fuel": {
            "source_governance": "approved",
            "robots_policy": "allowed",
            "terms_review": "approved",
            "production_signoff": "approved",
        },
        "earthquakes": {
            "source_governance": "approved",
            "robots_policy": "allowed",
            "terms_review": "approved",
            "production_signoff": "approved",
        },
        "breaking_news": {
            "source_governance": "approved",
            "robots_policy": "allowed",
            "terms_review": "approved",
            "production_signoff": "approved",
        },
        "weather": {
            "source_governance": "approved",
            "robots_policy": "allowed",
            "terms_review": "approved",
            "production_signoff": "approved",
        },
        "currency": {
            "source_governance": "approved",
            "robots_policy": "allowed",
            "terms_review": "approved",
            "production_signoff": "approved",
        },
        "bim_deals": {
            "source_governance": "approved",
            "robots_policy": "allowed",
            "terms_review": "approved",
            "production_signoff": "approved",
        },
        "pharmacy": {
            "source_governance": "approved",
            "robots_policy": "allowed",
            "terms_review": "approved",
            "production_signoff": "approved",
        },
        "culture_recommendation": {
            "source_governance": "approved",
            "robots_policy": "allowed",
            "terms_review": "approved",
            "production_signoff": "approved",
        },
        "flights": {
            "source_governance": "not_required",
            "robots_policy": "not_applicable",
            "terms_review": "not_applicable",
            "production_signoff": "not_required",
        },
        "story": {
            "source_governance": "not_required",
            "robots_policy": "not_applicable",
            "terms_review": "not_applicable",
            "production_signoff": "not_required",
        },
        "document_search": {
            "source_governance": "not_required",
            "robots_policy": "not_applicable",
            "terms_review": "not_applicable",
            "production_signoff": "not_required",
        },
    }
)

# Operational metadata is kept at module scope so the release audit and tests
# can inspect the same values that are applied to each FeatureDefinition.
FEATURE_CACHE_TTL_SECONDS: dict[str, int] = {
    "pharmacy": 300,
    "fuel": 900,
    "currency": 60,
    "crypto": 30,
    "market": 120,
    # AFAD's latest-earthquake feed changes frequently; a short cache limits
    # duplicate polling while keeping alert reads near real time.
    "earthquakes": 60,
    "breaking_news": 120,
    "weather": 300,
    "air_quality": 300,
    "prayer_times": 3600,
    "bim_deals": 3600,
    "culture_recommendation": 900,
    "story": 0,
    "document_search": 0,
    "lyrics": 900,
}

FEATURE_FRESHNESS_SLA_SECONDS: dict[str, int] = {
    "pharmacy": 1800,
    "fuel": 3600,
    "currency": 300,
    "crypto": 120,
    "market": 600,
    "earthquakes": 300,
    "breaking_news": 600,
    "weather": 1800,
    "air_quality": 1800,
    "prayer_times": 86400,
    "bim_deals": 86400,
    "culture_recommendation": 86400,
    "story": 0,
    "document_search": 0,
    "lyrics": 3600,
}


def register_runtime_local_features(registry: FeatureRegistry) -> None:
    """Register local/queue-owned surfaces missing from provider discovery.

    Provider-backed features are discovered from ``SPATULA_FEATURES`` and the
    typed tool set.  These features have no remote provider, but they are still
    public capabilities and therefore need the same permission, audit, rate
    limit and feature-flag metadata for menus, AI dispatch and operations.
    Keeping the definitions here prevents Telegram bootstrap code from becoming
    a second, divergent feature catalog.
    """

    definitions = (
        FeatureDefinition(
            key="notes",
            category="kisisel",
            commands=("not", "notlar"),
            input_model="note_action",
            output_type="telegram_html_with_inline_keyboard",
            permission="user_private",
            provider_key=None,
            cache_ttl_seconds=0,
            freshness_sla_seconds=0,
            timeout_ms=1000,
            cost_class="local",
            audit_policy="productivity_write",
            source_name="Arelbot personal notes",
            source_license="Arelbot",
            attribution_required=False,
            rate_limit_per_minute=30,
        ),
        FeatureDefinition(
            key="tasks",
            category="kisisel",
            commands=("gorev", "gorevler"),
            input_model="task_action",
            output_type="telegram_html_with_inline_keyboard",
            permission="user_private",
            provider_key=None,
            cache_ttl_seconds=0,
            freshness_sla_seconds=0,
            timeout_ms=1000,
            cost_class="local",
            audit_policy="productivity_write",
            source_name="Arelbot personal tasks",
            source_license="Arelbot",
            attribution_required=False,
            rate_limit_per_minute=30,
        ),
        FeatureDefinition(
            key="reminders",
            category="kisisel",
            commands=("remind", "hatirlat", "hatirlatmalar"),
            input_model="reminder_action",
            output_type="telegram_html_with_inline_keyboard",
            permission="user_private",
            provider_key=None,
            cache_ttl_seconds=0,
            freshness_sla_seconds=0,
            async_mode=True,
            timeout_ms=1000,
            cost_class="local_queue",
            audit_policy="reminder_write",
            source_name="Arelbot reminder scheduler",
            source_license="Arelbot",
            attribution_required=False,
            rate_limit_per_minute=20,
        ),
        FeatureDefinition(
            key="notification_preferences",
            category="kisisel",
            commands=("bildirim_ayar", "bildirimayar"),
            input_model="notification_preferences_action",
            output_type="telegram_html_with_inline_keyboard",
            permission="user_private",
            provider_key=None,
            cache_ttl_seconds=0,
            freshness_sla_seconds=0,
            timeout_ms=1000,
            cost_class="local",
            audit_policy="notification_policy",
            source_name="Arelbot notification preferences",
            source_license="Arelbot",
            attribution_required=False,
            rate_limit_per_minute=20,
        ),
        FeatureDefinition(
            key="price_alerts",
            category="finans",
            commands=("alarm",),
            input_model="price_alert_action",
            output_type="telegram_html_with_inline_keyboard",
            permission="user_private",
            provider_key=None,
            cache_ttl_seconds=0,
            freshness_sla_seconds=0,
            async_mode=True,
            timeout_ms=1000,
            cost_class="local_queue",
            audit_policy="notification_policy",
            source_name="Arelbot threshold alert scheduler",
            source_license="Arelbot",
            attribution_required=False,
            rate_limit_per_minute=10,
        ),
        FeatureDefinition(
            key="media",
            category="medya",
            commands=("indir", "dl", "download"),
            input_model="media_download_request",
            output_type="telegram_media",
            permission="user",
            provider_key=None,
            cache_ttl_seconds=0,
            freshness_sla_seconds=0,
            async_mode=True,
            timeout_ms=120000,
            cost_class="network_and_cpu",
            audit_policy="media_download",
            source_name="User-requested public media URL",
            source_license="Source-specific terms and attribution apply",
            attribution_required=True,
            rate_limit_per_minute=10,
        ),
        FeatureDefinition(
            key="group_moderation_settings",
            category="grup",
            commands=("ayar", "moderasyon", "mod"),
            input_model="group_moderation_settings_action",
            output_type="telegram_html_with_inline_keyboard",
            permission="group_admin",
            provider_key=None,
            cache_ttl_seconds=0,
            freshness_sla_seconds=0,
            timeout_ms=1000,
            cost_class="local",
            audit_policy="group_moderation",
            source_name="Arelbot group settings",
            source_license="Arelbot",
            attribution_required=False,
            rate_limit_per_minute=20,
        ),
        FeatureDefinition(
            key="group_personality",
            category="grup",
            commands=(),
            input_model="group_personality_mode",
            output_type="telegram_html_with_inline_keyboard",
            permission="group_admin",
            provider_key=None,
            cache_ttl_seconds=0,
            freshness_sla_seconds=0,
            feature_flag="group_personality",
            timeout_ms=1000,
            cost_class="local",
            audit_policy="group_personality",
            source_name="Arelbot PersonalityPolicy",
            source_license="Arelbot",
            attribution_required=False,
            rate_limit_per_minute=20,
        ),
        FeatureDefinition(
            key="personal_personality",
            category="kisisel",
            commands=("persona", "uslup"),
            input_model="user_personality_mode",
            output_type="telegram_html_with_inline_keyboard",
            permission="user_private",
            provider_key=None,
            cache_ttl_seconds=0,
            freshness_sla_seconds=0,
            feature_flag="personal_personality",
            timeout_ms=1000,
            cost_class="local",
            audit_policy="personality_preference",
            source_name="Arelbot PersonalityPolicy",
            source_license="Arelbot",
            attribution_required=False,
            rate_limit_per_minute=20,
        ),
        FeatureDefinition(
            key="games_leaderboard",
            category="oyun",
            commands=("quiz", "leaderboard", "lb", "profile"),
            aliases=("liderlik", "profil"),
            input_model="game_leaderboard_request",
            output_type="telegram_html_with_inline_keyboard",
            permission="user",
            provider_key=None,
            cache_ttl_seconds=0,
            freshness_sla_seconds=0,
            timeout_ms=1000,
            cost_class="local",
            audit_policy="game_progression",
            source_name="Arelbot game scores",
            source_license="Arelbot",
            attribution_required=False,
            rate_limit_per_minute=20,
        ),
        FeatureDefinition(
            key="connect4",
            category="oyun",
            commands=("connect4",),
            input_model="connect4_action",
            output_type="telegram_html_with_inline_keyboard",
            permission="user",
            provider_key=None,
            cache_ttl_seconds=0,
            freshness_sla_seconds=0,
            async_mode=False,
            timeout_ms=1000,
            cost_class="local",
            audit_policy="game_action",
            source_name="Arelbot Connect Four game engine",
            source_license="Arelbot",
            attribution_required=False,
            rate_limit_per_minute=30,
            schema_version=1,
            state_key="connect4",
            board_size=(6, 7),
            connect_length=4,
        ),
        FeatureDefinition(
            key="tictactoe",
            category="oyun",
            commands=("xox",),
            aliases=("tictactoe",),
            input_model="tictactoe_action",
            output_type="telegram_html_with_inline_keyboard",
            permission="user",
            provider_key=None,
            cache_ttl_seconds=0,
            freshness_sla_seconds=0,
            async_mode=False,
            timeout_ms=1000,
            cost_class="local",
            audit_policy="game_action",
            source_name="Arelbot XOX game engine",
            source_license="Arelbot",
            attribution_required=False,
            rate_limit_per_minute=30,
            schema_version=1,
            state_key="tictactoe",
            board_size=(6, 6),
            connect_length=4,
        ),
        FeatureDefinition(
            key="gomoku",
            category="oyun",
            commands=("gomoku",),
            aliases=("besli",),
            input_model="gomoku_action",
            output_type="telegram_html_with_inline_keyboard",
            permission="group_user",
            provider_key=None,
            cache_ttl_seconds=0,
            freshness_sla_seconds=0,
            async_mode=False,
            timeout_ms=1000,
            cost_class="local",
            audit_policy="game_action",
            source_name="Arelbot Gomoku oyun motoru",
            source_license="Arelbot",
            attribution_required=False,
            rate_limit_per_minute=30,
            schema_version=1,
            state_key="gomoku",
            board_size=(10, 10),
            connect_length=5,
        ),
        FeatureDefinition(
            key="chess",
            category="oyun",
            commands=("satranc", "chess"),
            aliases=("satranç",),
            input_model="chess_action",
            output_type="telegram_html_with_inline_keyboard",
            permission="group_user",
            provider_key=None,
            cache_ttl_seconds=0,
            freshness_sla_seconds=0,
            async_mode=False,
            timeout_ms=1000,
            cost_class="local",
            audit_policy="game_action",
            source_name="Arelbot chess engine; Piterden/chessbot interaction reference",
            source_url="https://github.com/Piterden/chessbot",
            source_license="MIT reference + GPL-3.0-or-later python-chess engine",
            source_terms_url="https://github.com/Piterden/chessbot/blob/master/LICENSE",
source_governance="pending",
robots_policy="review_required",
terms_review="review_required",
production_signoff="required",
            attribution_required=True,
            rate_limit_per_minute=30,
            schema_version=1,
            state_key="chess",
            board_size=(8, 8),
        ),
        FeatureDefinition(
            key="group_targeted_replies",
            category="grup",
            commands=(),
            input_model="group_reply_plan",
            output_type="telegram_reply_segments",
            permission="group_mention_or_reply",
            provider_key="evren",
            provider_kind="ai",
            cache_ttl_seconds=0,
            freshness_sla_seconds=0,
            async_mode=False,
            timeout_ms=8000,
            cost_class="ai_fast",
            feature_flag="group_targeted_replies",
            audit_policy="group_reply_delivery",
            source_name="Arelbot group reply router",
            source_license="Arelbot",
            attribution_required=False,
            rate_limit_per_minute=20,
        ),
        FeatureDefinition(
            key="notification_dedup",
            category="otomasyon",
            commands=(),
            input_model="notification_delivery_ledger",
            output_type="internal_delivery_record",
            permission="system",
            provider_key=None,
            cache_ttl_seconds=0,
            freshness_sla_seconds=0,
            async_mode=True,
            timeout_ms=3000,
            cost_class="local_queue",
            audit_policy="notification_delivery",
            source_name="Arelbot notification delivery ledger",
            source_license="Arelbot",
            attribution_required=False,
            rate_limit_per_minute=120,
        ),
        FeatureDefinition(
            key="guardian_moderation_enforcement",
            category="grup",
            commands=(),
            input_model="moderation_event",
            output_type="telegram_moderation_action",
            permission="group_admin_policy",
            provider_key="kek_guardian",
            provider_kind="moderation",
            cache_ttl_seconds=0,
            freshness_sla_seconds=0,
            async_mode=False,
            timeout_ms=150,
            cost_class="local_socket",
            feature_flag="guardian_moderation",
            audit_policy="moderation_action",
            source_name="Kek Guardian local moderation bridge",
            source_license="Deployment-owned service; no source copied",
            source_terms_url=None,
            attribution_required=False,
            rate_limit_per_minute=120,
        ),
        FeatureDefinition(
            key="akinator",
            category="oyun",
            commands=("akinator", "aki", "tahmin"),
            aliases=("akinetor",),
            input_model="akinator_action",
            output_type="telegram_html_with_inline_keyboard",
            permission="group_user",
            provider_key=None,
            provider_kind="data",
            cache_ttl_seconds=0,
            freshness_sla_seconds=0,
            async_mode=True,
            timeout_ms=45000,
            cost_class="external",
            audit_policy="game_action",
            source_name="akipy / Akinator service",
            source_url="https://github.com/advnpzn/akipy",
            source_license="MIT (akipy package metadata); upstream Akinator-Bot repository license not declared",
            source_terms_url="https://github.com/advnpzn/Akinator-Bot",
            attribution_required=True,
            source_governance="pending",
            robots_policy="review_required",
            terms_review="review_required",
            production_signoff="required",
            rate_limit_per_minute=20,
            schema_version=1,
            state_key="akinator",
        ),
        FeatureDefinition(
            key="guess_age",
            category="oyun",
            commands=("yas", "yastahmin"),
            aliases=("agegame",),
            input_model="guess_age_action",
            output_type="telegram_photo_with_inline_keyboard",
            permission="group_user",
            provider_key=None,
            provider_kind="local",
            cache_ttl_seconds=0,
            freshness_sla_seconds=0,
            async_mode=True,
            timeout_ms=1500,
            cost_class="local_queue",
            audit_policy="game_action",
            source_name="Wikimedia Commons camera-date portrait catalog; Wikidata birth dates",
            source_url="https://commons.wikimedia.org/",
            source_license="Per-file CC BY/CC BY-SA in assets/guess_age/catalog.json; Wikidata CC0; original Arelbot game code",
            source_terms_url="https://commons.wikimedia.org/wiki/Commons:Reusing_content_outside_Wikimedia",
source_governance="pending",
robots_policy="review_required",
terms_review="review_required",
production_signoff="required",
            attribution_required=True,
            rate_limit_per_minute=12,
            schema_version=1,
            state_key="guess_age",
        ),        FeatureDefinition(            key="uno",            category="oyun",            commands=("uno",),            aliases=("kartuno",),            input_model="uno_action",            output_type="telegram_html_with_inline_keyboard",            permission="group_user",            provider_key=None,            provider_kind="local",            cache_ttl_seconds=0,            freshness_sla_seconds=0,            async_mode=False,            timeout_ms=1000,            cost_class="local",            audit_policy="game_action",            source_name="Arelbot UNO engine; nmime/uno behaviour reference",            source_url="https://github.com/nmime/uno",            source_license="MIT behaviour reference; Arelbot implementation is original",            source_terms_url="https://raw.githubusercontent.com/nmime/uno/master/package.json",            attribution_required=True,            source_governance="pending",            robots_policy="review_required",            terms_review="approved",            production_signoff="required",            rate_limit_per_minute=60,            schema_version=1,            state_key="uno",        ),
    )
    for definition in definitions:
        registry.register_feature(definition, None)


def _source_metadata(key: str, tool_name: str) -> dict[str, Any]:
    values = SOURCE_METADATA.get(key, (tool_name, None, "Unclassified", None))
    metadata: dict[str, Any] = {
        "source_name": values[0],
        "source_url": values[1],
        "source_license": values[2],
        "source_terms_url": values[3] or values[1],
    }
    metadata.update(SOURCE_GOVERNANCE.get(key, {}))
    return metadata


def register_builtin_features(registry: FeatureRegistry, tool_manager: Any) -> None:
    """Register feature metadata without coupling the registry to Telegram."""
    for key, category, commands, tool_name in SPATULA_FEATURES:
        tool = tool_manager.get_tool(tool_name)
        if tool is None:
            continue
        registry.register_feature(
            FeatureDefinition(
                key=key,
                category=category,
                commands=commands,
                input_model=tool_name,
                output_type="telegram_html",
                provider_key=tool_name,
                cache_ttl_seconds=FEATURE_CACHE_TTL_SECONDS.get(key, 0),
                freshness_sla_seconds=FEATURE_FRESHNESS_SLA_SECONDS.get(key, 300),
                timeout_ms=5000 if key in {"flights", "document_search"} else (1000 if key == "story" else 15000),
                async_mode=False,
                cost_class="local" if key in {"flights", "story", "document_search"} else "network",
                audit_policy="document_search" if key == "document_search" else "provider_call",
                **_source_metadata(key, tool_name),
                attribution_required=key not in {"flights", "story", "document_search"},
                rate_limit_per_minute=30,
            ),
            tool,
        )

    image_tool = tool_manager.get_tool("image_generation")
    if image_tool is not None:
        registry.register_feature(
            FeatureDefinition(
                key="image_generation",
                category="ai",
                commands=("image",),
                input_model="image_generation_request",
                output_type="telegram_photo",
                permission="user",
                # Image generation is a queued worker service rather than a
                # synchronous FeatureExecutor DataProvider. The worker owns
                # Cloudflare transport and its health/ledger surface.
                provider_key=None,
                cache_ttl_seconds=0,
                freshness_sla_seconds=0,
                timeout_ms=120000,
                async_mode=True,
                cost_class="paid_or_quota",
                audit_policy="ai_generation",
                source_name="Cloudflare Workers AI",
                source_url="https://developers.cloudflare.com/workers-ai/",
                source_license="Provider terms; account authorization required",
                source_terms_url="https://www.cloudflare.com/terms/",
                attribution_required=False,
                rate_limit_per_minute=5,
                source_governance="pending",
                robots_policy="review_required",
                terms_review="review_required",
                production_signoff="required",
            ),
            image_tool,
        )

    # Local game services are still public features and must be visible in the
    # same manifest used by Telegram, AI routing and operations tooling.
    registry.register_feature(
        FeatureDefinition(
            key="blackjack",
            category="oyun",
            commands=("blackjack",),
            input_model="blackjack_request",
            output_type="telegram_html",
            permission="user",
            # Local game service; state mutations are handled by the game
            # service and callback ledger, not by a network DataProvider.
            provider_key=None,
            cache_ttl_seconds=0,
            freshness_sla_seconds=0,
            timeout_ms=3000,
            async_mode=False,
            cost_class="local",
            audit_policy="game_progression",
            source_name="Arelbot oyun motoru",
            source_license="Arelbot",
            attribution_required=False,
            rate_limit_per_minute=20,
        ),
        None,
    )
    registry.register_feature(
        FeatureDefinition(
            key="batak",
            category="oyun",
            commands=("batak",),
            input_model="batak_action",
            output_type="telegram_html",
            permission="group_user",
            # Local game service; state mutations are handled by the game
            # service and callback ledger, not by a network DataProvider.
            provider_key=None,
            cache_ttl_seconds=0,
            freshness_sla_seconds=0,
            timeout_ms=3000,
            async_mode=False,
            cost_class="local",
            audit_policy="game_progression",
            source_name="Arelbot oyun motoru",
            source_license="Arelbot",
            attribution_required=False,
            rate_limit_per_minute=40,
        ),
        None,
    )
    registry.register_feature(
        FeatureDefinition(
            key="roulette",
            category="oyun",
            commands=("rusruleti",),
            input_model="roulette_action",
            output_type="telegram_html",
            permission="group_user",
            provider_key=None,
            cache_ttl_seconds=0,
            freshness_sla_seconds=0,
            timeout_ms=3000,
            async_mode=False,
            cost_class="local",
            audit_policy="game_progression",
            source_name="Arelbot oyun motoru",
            source_license="Arelbot",
            attribution_required=False,
            rate_limit_per_minute=20,
        ),
        None,
    )
