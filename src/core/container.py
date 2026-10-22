"""
Dependency Injection Container — Tüm servislerin merkezi yönetimi.

Orchestrator'a gerek kalmadan tüm bileşenleri buradan erişilebilir yapar.
Global singleton'ları ortadan kaldırır, test edilebilirliği artırır.

Kullanım:
    container = Container()
    await container.initialize()
    chat_engine = container.chat_engine
"""
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class Container:
    """
    DI Container — Tüm servisleri başlatır, yönetir ve inject eder.

    Özellikler:
    - Lazy initialization: Servisler ilk erişimde başlatılır
    - Singleton yönetimi: Her servisten tek instance
    - Test mock'ları: Testlerde kolayca mock servis enjekte edilebilir
    - Graceful shutdown: Tüm servisleri düzgün şekilde kapatır
    """

    def __init__(self):
        self._initialized = False
        self._services: Dict[str, Any] = {}

    async def initialize(self):
        """Tüm servisleri başlatır."""
        if self._initialized:
            logger.warning("Container zaten başlatılmış, tekrar başlatılıyor...")
            await self.shutdown()

        logger.info("🚀 Container başlatılıyor...")

        # Bağımlılık sırasına göre servisleri başlat
        # (önce bağımsız olanlar, sonra bağımlı olanlar)

        # 1. Config
        from config import Config
        self._services["config"] = Config

        # 2. Veritabanı
        from src.database.redis_cache import RedisCache
        self._services["redis"] = RedisCache(
            required=(
                bool(getattr(Config, "REDIS_REQUIRED", False))
                or bool(getattr(Config, "MEDIA_REDIS_REQUIRED", False))
                or getattr(Config, "APP_ROLE", "combined") in {"bot", "worker"}
            )
        )
        await self._services["redis"].initialize()

        # 3. Güvenlik
        from src.security.rate_limiter import RateLimiter
        from src.security.encryptor import Encryptor
        self._services["rate_limiter"] = RateLimiter(self._services["redis"])
        self._services["encryptor"] = Encryptor()

        # 4. Error Handler
        from src.core.error_handler import ErrorHandler
        self._services["error_handler"] = ErrorHandler()

        # 5. Core servisler
        from src.key_manager import KeyManager
        self._services["key_manager"] = KeyManager()

        from src.mcp_manager import McpManager
        self._services["mcp_manager"] = McpManager(Config.MCP_SERVERS)

        from src.image_router import ImageRouter
        self._services["image_router"] = ImageRouter(self._services["key_manager"])

        from src.sudo_manager import SudoManager
        self._services["sudo_manager"] = SudoManager(Config.OWNER_ID)

        # 6. Chat Engine (diğer servislere bağımlı)
        from src.chat_engine import ChatEngine
        self._services["chat_engine"] = ChatEngine(
            key_manager=self._services["key_manager"],
            mcp_manager=self._services["mcp_manager"],
        )

        self._initialized = True
        logger.info("✅ Container başarıyla başlatıldı")

    async def shutdown(self):
        """Tüm servisleri düzgün şekilde kapatır."""
        logger.info("🛑 Container kapatılıyor...")

        if "redis" in self._services:
            await self._services["redis"].close()

        if "error_handler" in self._services:
            self._services["error_handler"].reset_stats()

        if "image_router" in self._services:
            await self._services["image_router"].close()

        self._services.clear()
        self._initialized = False
        logger.info("✅ Container kapatıldı")

    def get(self, service_name: str, default=None):
        """Bir servisi adıyla getirir."""
        return self._services.get(service_name, default)

    def register(self, name: str, instance: Any):
        """Test mock'ları için servis kaydetme."""
        self._services[name] = instance
        logger.debug(f"Test mock kaydedildi: {name}")

    @property
    def config(self):
        return self._services.get("config")

    @property
    def redis(self):
        return self._services.get("redis")

    @property
    def rate_limiter(self):
        return self._services.get("rate_limiter")

    @property
    def encryptor(self):
        return self._services.get("encryptor")

    @property
    def error_handler(self):
        return self._services.get("error_handler")

    @property
    def key_manager(self):
        return self._services.get("key_manager")

    @property
    def mcp_manager(self):
        return self._services.get("mcp_manager")

    @property
    def image_router(self):
        return self._services.get("image_router")

    @property
    def sudo_manager(self):
        return self._services.get("sudo_manager")

    @property
    def chat_engine(self):
        return self._services.get("chat_engine")

    @property
    def is_initialized(self) -> bool:
        return self._initialized
