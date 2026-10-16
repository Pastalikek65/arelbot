import os
import json
import logging
import re
from typing import List
from pathlib import Path
from urllib.parse import urlparse
from dotenv import load_dotenv
from src.security.log_redaction import RedactionFilter

# Load environment variables
load_dotenv()

# Logger setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
for _handler in logging.getLogger().handlers:
    _handler.addFilter(RedactionFilter())
logger = logging.getLogger(__name__)

class Config:
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
    TELEGRAM_MODE = os.getenv("TELEGRAM_MODE", "polling").lower()
    WEBHOOK_URL = os.getenv("WEBHOOK_URL")
    WEBHOOK_LISTEN = os.getenv("WEBHOOK_LISTEN", "0.0.0.0")
    WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8443"))
    WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "telegram/webhook")
    WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
    # Optional loopback-only health/metrics endpoint. It is disabled by
    # default so a normal polling development process opens no extra port.
    OBSERVABILITY_ENABLED = os.getenv("OBSERVABILITY_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
    OBSERVABILITY_LISTEN = os.getenv("OBSERVABILITY_LISTEN", "127.0.0.1")
    OBSERVABILITY_PORT = int(os.getenv("OBSERVABILITY_PORT", "9108"))
    OBSERVABILITY_WORKER_PORT = int(os.getenv("OBSERVABILITY_WORKER_PORT", "9109"))
    OBSERVABILITY_TOKEN = os.getenv("OBSERVABILITY_TOKEN")
    OBSERVABILITY_TOKEN_FILE = os.getenv("OBSERVABILITY_TOKEN_FILE")
    API_ID = os.getenv("API_ID")
    API_HASH = os.getenv("API_HASH")
    BOT_PERSONA_ENABLED = os.getenv("BOT_PERSONA_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
    # Arel is playful by default; sensitive and operational turns are still
    # forced to a serious tone by PersonalityPolicy.
    BOT_SARCASM_LEVEL = max(0, min(3, int(os.getenv("BOT_SARCASM_LEVEL", "2"))))
    AI_FAST_MAX_TOKENS = max(128, min(2048, int(os.getenv("AI_FAST_MAX_TOKENS", "192"))))
    AI_TOOL_MAX_TOKENS = max(192, min(4096, int(os.getenv("AI_TOOL_MAX_TOKENS", "384"))))
    AI_FAST_TIMEOUT_SECONDS = max(2, min(15, int(os.getenv("AI_FAST_TIMEOUT_SECONDS", "8"))))
    AI_PROVIDER_RETRY_ATTEMPTS = max(0, min(2, int(os.getenv("AI_PROVIDER_RETRY_ATTEMPTS", "1"))))
    AI_PROVIDER_RETRY_BACKOFF_MS = max(25, min(1000, int(os.getenv("AI_PROVIDER_RETRY_BACKOFF_MS", "150"))))
    AI_REASONING_EFFORT = os.getenv("AI_REASONING_EFFORT", "none").strip().casefold()
    if AI_REASONING_EFFORT not in {"none", "low", "high", "max"}:
        AI_REASONING_EFFORT = "none"
    AI_THINKING_ENABLED = os.getenv("AI_THINKING_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
    GROUP_TARGETED_REPLIES_ENABLED = os.getenv("GROUP_TARGETED_REPLIES_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
    GROUP_TARGETED_REPLIES_SHADOW = os.getenv("GROUP_TARGETED_REPLIES_SHADOW", "false").lower() in {"1", "true", "yes", "on"}
    GROUP_REPLY_WINDOW_SECONDS = max(30, min(900, int(os.getenv("GROUP_REPLY_WINDOW_SECONDS", "300"))))
    GROUP_REPLY_MAX_CANDIDATES = max(1, min(8, int(os.getenv("GROUP_REPLY_MAX_CANDIDATES", "8"))))
    GROUP_REPLY_MAX_SEGMENTS = max(1, min(3, int(os.getenv("GROUP_REPLY_MAX_SEGMENTS", "3"))))
    
    # Groq Settings
    GROQ_KEYS: List[str] = [k.strip() for k in os.getenv("GROQ_KEYS", "").split(",") if k.strip()]
    GROQ_URL = os.getenv("GROQ_URL", "https://api.groq.com/openai/v1").rstrip("/")
    GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
    
    # Gemini Settings
    GEMINI_KEYS: List[str] = [k.strip() for k in os.getenv("GEMINI_KEYS", "").split(",") if k.strip()]
    GEMINI_URL = os.getenv("GEMINI_URL", "https://generativelanguage.googleapis.com/v1beta/openai").rstrip("/")
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
    
    # Cerebras Settings
    CEREBRAS_KEYS: List[str] = [k.strip() for k in os.getenv("CEREBRAS_KEYS", "").split(",") if k.strip()]
    CEREBRAS_URL = os.getenv("CEREBRAS_URL", "https://api.cerebras.ai/v1").rstrip("/")
    CEREBRAS_MODEL = os.getenv("CEREBRAS_MODEL", "qwen-3-235b-a22b-instruct-2507")
    
    # Tavily Settings
    TAVILY_KEYS: List[str] = [k.strip() for k in os.getenv("TAVILY_KEYS", "").split(",") if k.strip()]
    
    # Cloudflare accounts format: account_id:token, account_id:token
    CLOUDFLARE_ACCOUNTS: List[str] = [k.strip() for k in os.getenv("CLOUDFLARE_ACCOUNTS", "").split(",") if k.strip()]
    
    # For backward compatibility and single entry fallback
    _cf_token = os.getenv("CLOUDFLARE_API_TOKEN")
    _cf_id = os.getenv("CLOUDFLARE_ACCOUNT_ID")
    if _cf_id and _cf_token:
        pair = f"{_cf_id}:{_cf_token}"
        if pair not in CLOUDFLARE_ACCOUNTS:
            CLOUDFLARE_ACCOUNTS.append(pair)

    # Model Names
    # Prioritized Model List (Deprecated but kept for structure)
    GROQ_MODELS = [
        "qwen/qwen3-32b",
        "qwen-2.5-32b",
        "llama-3.3-70b-versatile"
    ]
    CLOUDFLARE_IMAGE_MODEL = "@cf/black-forest-labs/flux-2-klein-4b"

    # Logging (Unified Channel with Topics)
    LOG_CHANNEL_ID = -1003669897190
    DM_LOG_THREAD_ID = 1
    GROUP_LOG_THREAD_ID = 3
    # Normal input/output events stay out of the operational Telegram topic.
    # Error records remain enabled through the dedicated error boundary.
    LOG_CHANNEL_ERRORS_ONLY = os.getenv("LOG_CHANNEL_ERRORS_ONLY", "false").lower() in {"1", "true", "yes", "on"}
    
    # Sudo (Owner ID from environment)
    # Keep the historical development fallback for local compatibility, but
    # remember whether production received an explicit value.  A real
    # operator may legitimately have the same numeric ID as that old fallback.
    OWNER_ID_DEFAULT = 5544654560
    OWNER_ID_CONFIGURED = bool(os.getenv("OWNER_ID", "").strip())
    OWNER_ID = int(os.getenv("OWNER_ID", str(OWNER_ID_DEFAULT)))

    MONGODB_URI = os.getenv("MONGODB_URI")
    MONGODB_REQUIRED = os.getenv("MONGODB_REQUIRED", "false").lower() in {"1", "true", "yes", "on"}
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    # Shared Redis consumers fail fast in production; local polling/tests may opt into memory.
    REDIS_REQUIRED = os.getenv("REDIS_REQUIRED", "false").lower() in {"1", "true", "yes", "on"}
    MEDIA_REDIS_REQUIRED = os.getenv("MEDIA_REDIS_REQUIRED", "false").lower() in {"1", "true", "yes", "on"}
    MEDIA_DOWNLOAD_DIR = os.getenv("MEDIA_DOWNLOAD_DIR", "downloads")
    MEDIA_MAX_FILE_BYTES = int(os.getenv("MEDIA_MAX_FILE_BYTES", str(45 * 1024 * 1024)))
    MEDIA_MAX_VIDEO_DURATION_SECONDS = int(os.getenv("MAX_VIDEO_DURATION_SECONDS", "0"))
    MEDIA_QUEUE_WORKERS = max(1, min(10, int(os.getenv("MEDIA_QUEUE_WORKERS", "4"))))
    # The 10-second value is a completion SLO, not a cancellation deadline.
    # Slow public providers must remain observable and recoverable instead of
    # being cut off exactly when the SLO is breached.
    MEDIA_JOB_TIMEOUT_SECONDS = max(15, min(300, int(os.getenv("MEDIA_JOB_TIMEOUT_SECONDS", "60"))))
    MEDIA_COMPLETION_SLO_SECONDS = max(1, min(120, int(os.getenv("MEDIA_COMPLETION_SLO_SECONDS", "10"))))
    # aria2c is an optional multi-connection downloader. It is used only when
    # the executable is present; Windows development therefore keeps the
    # yt-dlp path without requiring a local binary, while the Docker image can
    # use the faster path automatically.
    MEDIA_ARIA2_ENABLED = os.getenv("MEDIA_ARIA2_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
    MEDIA_COOKIE_ENABLED = os.getenv("MEDIA_COOKIE_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
    MEDIA_COOKIE_STORAGE_DIR = os.getenv("MEDIA_COOKIE_STORAGE_DIR", "data/cookies")
    _media_fallback_raw = os.getenv("MEDIA_FALLBACK_URLS", os.getenv("MEDIA_PROVIDER_FALLBACK_URLS", ""))
    MEDIA_FALLBACK_URLS: List[str] = [u.strip().rstrip("/") for u in _media_fallback_raw.split(",") if u.strip()]
    MEDIA_PROVIDER_FALLBACK_URLS = MEDIA_FALLBACK_URLS
    CITY_DATA_PATH = os.getenv(
        "CITY_DATA_PATH",
        str(Path(__file__).resolve().parent / "src" / "data" / "turkish_cities.json"),
    )
    FEATURE_REGISTRY_ENABLED = os.getenv("FEATURE_REGISTRY_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
    # Optional source kill-switch configuration. Explicit false values stop a
    # feature before any provider/network call is started.
    try:
        _feature_flags_raw = json.loads(os.getenv("FEATURE_FLAGS", "{}"))
        FEATURE_FLAGS = {
            str(name).strip().casefold(): value
            for name, value in _feature_flags_raw.items()
            if str(name).strip() and isinstance(value, bool)
        } if isinstance(_feature_flags_raw, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        FEATURE_FLAGS = {}
    DISABLED_FEATURES = {
        item.strip().casefold()
        for item in os.getenv("DISABLED_FEATURES", "").split(",")
        if item.strip()
    }
    # Optional validated staging-core profile. It is consumed only when
    # APP_ENV=staging; production cannot silently inherit a staging manifest.
    STAGING_PROFILE_PATH = os.getenv("STAGING_PROFILE_PATH", "").strip()
    # Typed providers are the production route. Legacy tool adapters can be
    # enabled deliberately during a migration window, but are never silent.
    LEGACY_PROVIDER_FALLBACK_ENABLED = os.getenv("LEGACY_PROVIDER_FALLBACK_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
    _DEFAULT_AI_TOOL_ALLOWLIST = {
        "web_search", "group_analytics", "memory_tool", "web_reader", "document_search",
        "quiz_tool", "games_tool", "culture_tool", "lyrics_tool", "image_generation",
        "get_pharmacies", "get_fuel_prices", "get_currency_rates", "get_crypto_prices", "get_market_quote", "get_recent_earthquakes",
        "get_bim_deals", "get_breaking_news", "get_weather", "get_air_quality", "get_prayer_times",
        "search_flights", "generate_story", "turkiye_city_lookup",
    }
    _configured_ai_tools = {item.strip() for item in os.getenv("AI_TOOL_ALLOWLIST", "").split(",") if item.strip()}
    AI_TOOL_ALLOWLIST = _configured_ai_tools or _DEFAULT_AI_TOOL_ALLOWLIST
    PRODUCTIVITY_MAX_NOTES = max(10, int(os.getenv("PRODUCTIVITY_MAX_NOTES", "200")))
    PRODUCTIVITY_MAX_TASKS = max(10, int(os.getenv("PRODUCTIVITY_MAX_TASKS", "200")))
    RAG_MAX_FILE_BYTES = int(os.getenv("RAG_MAX_FILE_BYTES", str(20 * 1024 * 1024)))
    RAG_UPLOAD_DIR = os.getenv("RAG_UPLOAD_DIR", "downloads")
    RAG_USE_PROVIDER_EMBEDDINGS = os.getenv("RAG_USE_PROVIDER_EMBEDDINGS", "false").strip().lower() in {"1", "true", "yes", "on"}
    DOCUMENT_QUEUE_WORKERS = max(1, min(10, int(os.getenv("DOCUMENT_QUEUE_WORKERS", "2"))))
    DOCUMENT_JOB_TIMEOUT_SECONDS = max(15, int(os.getenv("DOCUMENT_JOB_TIMEOUT_SECONDS", "120")))
    DOCUMENT_REDIS_REQUIRED = os.getenv("DOCUMENT_REDIS_REQUIRED", "false").lower() in {"1", "true", "yes", "on"}
    ATTACHMENT_QUEUE_WORKERS = max(1, min(10, int(os.getenv("ATTACHMENT_QUEUE_WORKERS", "2"))))
    ATTACHMENT_JOB_TIMEOUT_SECONDS = max(15, int(os.getenv("ATTACHMENT_JOB_TIMEOUT_SECONDS", "120")))
    ATTACHMENT_TEMP_DIR = os.getenv("ATTACHMENT_TEMP_DIR", "temp/attachments")
    ATTACHMENT_MAX_FILE_BYTES = max(1024, min(50 * 1024 * 1024, int(os.getenv("ATTACHMENT_MAX_FILE_BYTES", str(20 * 1024 * 1024)))))
    ATTACHMENT_REDIS_REQUIRED = os.getenv("ATTACHMENT_REDIS_REQUIRED", "false").lower() in {"1", "true", "yes", "on"}
    QUOTE_QUEUE_WORKERS = max(1, min(8, int(os.getenv("QUOTE_QUEUE_WORKERS", "2"))))
    QUOTE_JOB_TIMEOUT_SECONDS = max(15, int(os.getenv("QUOTE_JOB_TIMEOUT_SECONDS", "120")))
    SPEECH_QUEUE_WORKERS = max(1, min(8, int(os.getenv("SPEECH_QUEUE_WORKERS", "2"))))
    SPEECH_JOB_TIMEOUT_SECONDS = max(15, int(os.getenv("SPEECH_JOB_TIMEOUT_SECONDS", "120")))
    STICKER_TAG_QUEUE_WORKERS = max(1, min(8, int(os.getenv("STICKER_TAG_QUEUE_WORKERS", "2"))))
    STICKER_TAG_JOB_TIMEOUT_SECONDS = max(15, int(os.getenv("STICKER_TAG_JOB_TIMEOUT_SECONDS", "120")))
    STICKER_TAG_TEMP_DIR = os.getenv("STICKER_TAG_TEMP_DIR", "temp/sticker_tags")
    MARKET_CHART_QUEUE_WORKERS = max(1, min(8, int(os.getenv("MARKET_CHART_QUEUE_WORKERS", "2"))))
    MARKET_CHART_JOB_TIMEOUT_SECONDS = max(15, int(os.getenv("MARKET_CHART_JOB_TIMEOUT_SECONDS", "30")))
    _news_feeds_raw = os.getenv("NEWS_RSS_FEEDS", "")
    NEWS_RSS_FEEDS: List[str] = [item.strip() for item in _news_feeds_raw.split(",") if item.strip()]
    COINGECKO_API_URL = os.getenv("COINGECKO_API_URL", "https://api.coingecko.com/api/v3").rstrip("/")
    COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY") or None
    COINGECKO_API_PLAN = os.getenv("COINGECKO_API_PLAN", "demo").strip().lower()
    ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
    APP_ROLE = os.getenv("APP_ROLE", "combined").strip().lower()
    # Explicit environment tier prevents development defaults from silently
    # being used by a production deployment.
    APP_ENV = os.getenv("APP_ENV", os.getenv("ENVIRONMENT", "development")).strip().lower()
    REQUIRE_PROVIDER_APPROVAL = os.getenv(
        "REQUIRE_PROVIDER_APPROVAL",
        "true" if APP_ENV == "production" else "false",
    ).strip().lower() in {"1", "true", "yes", "on"}
    # Optional, operator-reviewed source approval evidence.  An empty value
    # deliberately means "no external approvals"; production then remains
    # fail-closed for every pending provider.
    PROVIDER_APPROVAL_MANIFEST = os.getenv("PROVIDER_APPROVAL_MANIFEST", "").strip()
    FREEMODEL_URL = os.getenv("FREEMODEL_URL", "https://api.freemodel.dev/v1").rstrip("/")
    FREEMODEL_KEY = os.getenv("FREEMODEL_KEY")
    FREEMODEL_MODEL = os.getenv("FREEMODEL_MODEL", "gpt-5.3-codex")
    # Optional task-specific models for multimodal adapters. When unset, the
    # provider's chat model is used and the upstream decides availability.
    AI_VISION_MODEL = os.getenv("AI_VISION_MODEL") or None
    AI_TRANSCRIPTION_MODEL = os.getenv("AI_TRANSCRIPTION_MODEL") or None
    AI_EMBEDDING_MODEL = os.getenv("AI_EMBEDDING_MODEL") or None
    try:
        AI_COST_RATES = json.loads(os.getenv("AI_COST_RATES", "{}"))
        if not isinstance(AI_COST_RATES, dict):
            AI_COST_RATES = {}
    except (TypeError, ValueError):
        AI_COST_RATES = {}
    try:
        # NVIDIA NIM is preferred when its key is configured, with EVREN as a
        # compatible failover. ChatEngine filters this allowlist against
        # providers that actually have a key, so existing EVREN-only
        # deployments keep working without a route change.
        _default_ai_routes = json.dumps(
            {
                "chat": ["nvidia", "evren"],
                "fast": ["nvidia", "evren"],
                "analysis": ["nvidia", "evren"],
                "summary": ["nvidia", "evren"],
                "sticker_tagging": ["nvidia", "evren"],
            }
        )
        AI_PROVIDER_ROUTES = json.loads(os.getenv("AI_PROVIDER_ROUTES", _default_ai_routes))
        if not isinstance(AI_PROVIDER_ROUTES, dict):
            AI_PROVIDER_ROUTES = {}
        AI_PROVIDER_ROUTES = {
            str(task).strip().casefold(): [str(name).strip().casefold() for name in names if str(name).strip()]
            for task, names in AI_PROVIDER_ROUTES.items()
            if isinstance(names, (list, tuple)) and names
        }
    except (TypeError, ValueError):
        AI_PROVIDER_ROUTES = {}
    TTS_OUTPUT_DIR = os.getenv("TTS_OUTPUT_DIR", "downloads")
    TTS_MAX_CONCURRENCY = max(1, min(8, int(os.getenv("TTS_MAX_CONCURRENCY", "2"))))
    TTS_TIMEOUT_SECONDS = max(1, min(120, int(os.getenv("TTS_TIMEOUT_SECONDS", "30"))))
    TTS_MAX_CHARS = max(100, min(5000, int(os.getenv("TTS_MAX_CHARS", "1000"))))
    TTS_MAX_OUTPUT_BYTES = max(1024, min(50 * 1024 * 1024, int(os.getenv("TTS_MAX_OUTPUT_BYTES", str(12 * 1024 * 1024)))))
    STICKER_TEMP_DIR = os.getenv("STICKER_TEMP_DIR", "temp/stickers")
    STICKER_MAX_CONCURRENCY = max(1, min(8, int(os.getenv("STICKER_MAX_CONCURRENCY", "2"))))
    STICKER_MAX_INPUT_BYTES = max(1024, min(100 * 1024 * 1024, int(os.getenv("STICKER_MAX_INPUT_BYTES", str(20 * 1024 * 1024)))))
    STICKER_RENDER_TIMEOUT_SECONDS = max(1, min(120, int(os.getenv("STICKER_RENDER_TIMEOUT_SECONDS", "20"))))
    QUOTE_OUTPUT_DIR = os.getenv("QUOTE_OUTPUT_DIR", "downloads/quotes")
    QUOTE_MAX_CONCURRENCY = max(1, min(8, int(os.getenv("QUOTE_MAX_CONCURRENCY", "2"))))
    QUOTE_RENDER_TIMEOUT_SECONDS = max(1, min(120, int(os.getenv("QUOTE_RENDER_TIMEOUT_SECONDS", "15"))))
    QUOTE_MAX_MESSAGES = max(1, min(10, int(os.getenv("QUOTE_MAX_MESSAGES", "10"))))
    QUOTE_MAX_OUTPUT_BYTES = max(1024, min(12 * 1024 * 1024, int(os.getenv("QUOTE_MAX_OUTPUT_BYTES", str(8 * 1024 * 1024)))))
    IMAGE_QUEUE_WORKERS = max(1, min(8, int(os.getenv("IMAGE_QUEUE_WORKERS", "2"))))
    IMAGE_JOB_TIMEOUT_SECONDS = max(15, min(300, int(os.getenv("IMAGE_JOB_TIMEOUT_SECONDS", "120"))))
    IMAGE_MAX_PROMPT_CHARS = max(100, min(4000, int(os.getenv("IMAGE_MAX_PROMPT_CHARS", "1200"))))
    IMAGE_MAX_OUTPUT_BYTES = max(1024, min(20 * 1024 * 1024, int(os.getenv("IMAGE_MAX_OUTPUT_BYTES", str(12 * 1024 * 1024)))))
    IMAGE_RATE_LIMIT_PER_MINUTE = max(1, min(30, int(os.getenv("IMAGE_RATE_LIMIT_PER_MINUTE", "5"))))
    IMAGE_REDIS_REQUIRED = os.getenv("IMAGE_REDIS_REQUIRED", "false").lower() in {"1", "true", "yes", "on"}

    # Legacy fallback provider settings
    NINEROUTER_URL = os.getenv("NINEROUTER_URL", "https://rdi4qg6.9router.com/v1")
    NINEROUTER_KEY = os.getenv("NINEROUTER_KEY")
    NINEROUTER_MODEL = os.getenv("NINEROUTER_MODEL", "areltest")
    
    # IAMHC (DeepSeek) Settings
    IAMHC_URL = os.getenv("IAMHC_URL", "https://api.iamhc.cn/v1").rstrip("/")
    IAMHC_KEY = os.getenv("IAMHC_KEY")
    IAMHC_MODEL = os.getenv("IAMHC_MODEL", "DeepSeek-V4-Flash")
    # EVREN exposes the OpenAI-compatible endpoint used by production AI.
    EVREN_URL = os.getenv("EVREN_URL", "https://evren-llmapi.ssyz.org.tr/v1").rstrip("/")
    EVREN_KEY = os.getenv("EVREN_KEY") or os.getenv("EVREN_LLM_API_KEY")
    EVREN_MODEL = os.getenv("EVREN_MODEL", "deepseek-v4.1-flash")
    # EVREN's Mimo model accepts image input and returns text analysis. It is
    # intentionally separate from the image-generation provider setting.
    EVREN_VISION_MODEL = os.getenv("EVREN_VISION_MODEL", "mimo-v2.6-pro")
    # NVIDIA NIM OpenAI-compatible settings. Keep this provider opt-in: it is
    # only eligible for routing when NVIDIA_API_KEY is present. The default
    # route includes NVIDIA first and EVREN as the compatible failover.
    NVIDIA_URL = os.getenv("NVIDIA_URL", "https://integrate.api.nvidia.com/v1").rstrip("/")
    NVIDIA_KEY = os.getenv("NVIDIA_API_KEY") or os.getenv("NVIDIA_KEY")
    NVIDIA_MODEL = os.getenv("NVIDIA_MODEL", "deepseek-ai/deepseek-v4.1-flash")
    NVIDIA_VISION_MODEL = os.getenv("NVIDIA_VISION_MODEL") or None
    # Legacy compatibility values are intentionally not populated from EVREN.
    # This prevents an EVREN key from being mislabeled as Clean APIs in logs,
    # metrics, health output, or provider routing.
    CLEANAPIS_URL = os.getenv("CLEANAPIS_URL", "https://cleanapis.com/v1").rstrip("/")
    CLEANAPIS_KEY = os.getenv("CLEANAPIS_KEY")
    CLEANAPIS_MODEL = os.getenv("CLEANAPIS_MODEL", "deepseek-v4.1-flash")
    
    # MCP (Model Context Protocol) Settings
    MCP_SERVERS = os.getenv("MCP_SERVERS", "{}")

    @classmethod
    def validate(cls):
        missing = []
        if not cls.TELEGRAM_TOKEN:
            missing.append("TELEGRAM_TOKEN")
        
        has_ai_provider_key = bool(
            cls.FREEMODEL_KEY or cls.GROQ_KEYS or cls.GEMINI_KEYS or cls.CEREBRAS_KEYS
            or cls.IAMHC_KEY or cls.CLEANAPIS_KEY or cls.EVREN_KEY or cls.NINEROUTER_KEY
            or cls.NVIDIA_KEY
        )
        if not has_ai_provider_key and cls.APP_ROLE != "worker":
            missing.append("at least one AI provider key")
        elif cls.RAG_USE_PROVIDER_EMBEDDINGS and not has_ai_provider_key:
            missing.append("at least one AI provider key for RAG embeddings")
        
        if missing:
            raise ValueError(f"Missing required configuration: {', '.join(missing)}")
        
        if cls.MEDIA_MAX_FILE_BYTES < 1024 * 1024 or cls.MEDIA_MAX_FILE_BYTES > 45 * 1024 * 1024:
            raise ValueError("MEDIA_MAX_FILE_BYTES must be between 1 MiB and 45 MiB")
        if cls.RAG_MAX_FILE_BYTES < 1024 * 1024 or cls.RAG_MAX_FILE_BYTES > 100 * 1024 * 1024:
            raise ValueError("RAG_MAX_FILE_BYTES must be between 1 MiB and 100 MiB")
        if cls.MEDIA_COOKIE_ENABLED and (not cls.ENCRYPTION_KEY or len(cls.ENCRYPTION_KEY) < 32):
            raise ValueError("ENCRYPTION_KEY must contain at least 32 characters when MEDIA_COOKIE_ENABLED=true")
        if not Path(cls.CITY_DATA_PATH).exists():
            raise ValueError(f"CITY_DATA_PATH does not exist: {cls.CITY_DATA_PATH}")
        tls_bundle = os.getenv("SSL_CERT_FILE", "").strip()
        if tls_bundle and not Path(tls_bundle).is_file():
            raise ValueError("SSL_CERT_FILE must point to a readable certificate bundle")

        if cls.TELEGRAM_MODE not in {"polling", "webhook"}:
            raise ValueError("TELEGRAM_MODE must be 'polling' or 'webhook'")
        if not 1 <= cls.WEBHOOK_PORT <= 65535:
            raise ValueError("WEBHOOK_PORT must be between 1 and 65535")
        if not 1 <= cls.OBSERVABILITY_PORT <= 65535 or not 1 <= cls.OBSERVABILITY_WORKER_PORT <= 65535:
            raise ValueError("OBSERVABILITY ports must be between 1 and 65535")
        if cls.OBSERVABILITY_ENABLED:
            cls.load_observability_token()
            listen = cls.OBSERVABILITY_LISTEN.strip().lower()
            local_bind = listen in {"localhost", "127.0.0.1", "::1", "[::1]"}
            if not local_bind and not cls.OBSERVABILITY_TOKEN:
                raise ValueError("OBSERVABILITY_TOKEN is required for a non-loopback observability bind")
            if cls.OBSERVABILITY_TOKEN and not re.fullmatch(r"[A-Za-z0-9_\-]{16,256}", cls.OBSERVABILITY_TOKEN):
                raise ValueError("OBSERVABILITY_TOKEN must contain 16-256 URL-safe characters")
        if cls.TELEGRAM_MODE == "webhook":
            if not cls.WEBHOOK_URL:
                raise ValueError("WEBHOOK_URL is required when TELEGRAM_MODE=webhook")
            parsed_webhook = urlparse(cls.WEBHOOK_URL)
            if (
                parsed_webhook.scheme != "https"
                or not parsed_webhook.hostname
                or parsed_webhook.username
                or parsed_webhook.password
                or parsed_webhook.query
                or parsed_webhook.fragment
            ):
                raise ValueError("WEBHOOK_URL must be a credential-free HTTPS URL")
            if not cls.WEBHOOK_SECRET:
                raise ValueError("WEBHOOK_SECRET is required when TELEGRAM_MODE=webhook")
            if not re.fullmatch(r"[A-Za-z0-9_-]{16,256}", cls.WEBHOOK_SECRET):
                raise ValueError("WEBHOOK_SECRET must contain 16-256 URL-safe characters")
            if not re.fullmatch(r"[A-Za-z0-9/_-]{1,128}", cls.WEBHOOK_PATH.strip("/")):
                raise ValueError("WEBHOOK_PATH contains unsafe characters")
        if cls.APP_ROLE not in {"combined", "bot", "worker"}:
            raise ValueError("APP_ROLE must be one of: combined, bot, worker")
        if cls.APP_ENV not in {"development", "test", "staging", "production"}:
            raise ValueError("APP_ENV must be one of: development, test, staging, production")
        if cls.STAGING_PROFILE_PATH and cls.APP_ENV != "staging":
            raise ValueError("STAGING_PROFILE_PATH requires APP_ENV=staging")
        if cls.MEDIA_REDIS_REQUIRED and not cls.REDIS_URL:
            raise ValueError("REDIS_URL is required when MEDIA_REDIS_REQUIRED=true")
        if cls.REDIS_REQUIRED and not cls.REDIS_URL:
            raise ValueError("REDIS_URL is required when REDIS_REQUIRED=true")
        if (cls.MONGODB_REQUIRED or cls.APP_ROLE in {"bot", "worker"}) and not cls.MONGODB_URI:
            raise ValueError("MONGODB_URI is required for production bot/worker roles")

        owner_configured = bool(getattr(cls, "OWNER_ID_CONFIGURED", False)) or cls.OWNER_ID != cls.OWNER_ID_DEFAULT
        if cls.APP_ENV == "production" and not owner_configured:
            raise ValueError("OWNER_ID must be explicitly configured when APP_ENV=production")
        if cls.OWNER_ID == cls.OWNER_ID_DEFAULT and not owner_configured:
            logger.warning("\\u26a0\\ufe0f OWNER_ID is using default value. Set OWNER_ID in .env for security.")

    @classmethod
    def load_observability_token(cls) -> str | None:
        """Load a mounted token file without ever logging its contents."""
        if cls.OBSERVABILITY_TOKEN:
            return cls.OBSERVABILITY_TOKEN
        path = str(cls.OBSERVABILITY_TOKEN_FILE or "").strip()
        if not path:
            return None
        try:
            value = Path(path).read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            return None
        cls.OBSERVABILITY_TOKEN = value or None
        return cls.OBSERVABILITY_TOKEN


# Canonical validated settings name; Config remains backwards compatible.
Settings = Config

