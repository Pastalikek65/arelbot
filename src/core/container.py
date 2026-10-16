"""
Dependency Injection Container â€” TÃ¼m servislerin merkezi yÃ¶netimi.

Orchestrator'a gerek kalmadan tÃ¼m bileÅŸenleri buradan eriÅŸilebilir yapar.
Global singleton'larÄ± ortadan kaldÄ±rÄ±r, test edilebilirliÄŸi artÄ±rÄ±r.

KullanÄ±m:
    container = Container()
    await container.initialize()
    chat_engine = container.chat_engine
"""
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class Container:
    """
    DI Container â€” TÃ¼m servisleri baÅŸlatÄ±r, yÃ¶netir ve inject eder.

    Ã–zellikler:
    - Lazy initialization: Servisler ilk eriÅŸimde baÅŸlatÄ±lÄ±r
    - Singleton yÃ¶netimi: Her servisten tek instance
    - Test mock'larÄ±: Testlerde kolayca mock servis enjekte edilebilir
    - Graceful shutdown: TÃ¼m servisleri dÃ¼zgÃ¼n ÅŸekilde kapatÄ±r
    """

    def __init__(self):
        self._initialized = False
        self._services: Dict[str, Any] = {}

    async def initialize(self):
        """TÃ¼m servisleri baÅŸlatÄ±r."""
        if self._initialized:
            logger.warning("Container zaten baÅŸlatÄ±lmÄ±ÅŸ, tekrar baÅŸlatÄ±lÄ±yor...")
            await self.shutdown()

        logger.info("ğŸš€ Container baÅŸlatÄ±lÄ±yor...")

        # BaÄŸÄ±mlÄ±lÄ±k sÄ±rasÄ±na gÃ¶re servisleri baÅŸlat
        # (Ã¶nce baÄŸÄ±msÄ±z olanlar, sonra baÄŸÄ±mlÄ± olanlar)

        # 1. Config
        from config import Config
        self._services["config"] = Config

        # 2. VeritabanÄ±
        from src.database.redis_cache import RedisCache
        self._services["redis"] = RedisCache(
            required=(
                bool(getattr(Config, "REDIS_REQUIRED", False))
                or bool(getattr(Config, "MEDIA_REDIS_REQUIRED", False))
                or getattr(Config, "APP_ROLE", "combined") in {"bot", "worker"}
            )
        )
        await self._services["redis"].initialize()

        # 3. GÃ¼venlik
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

        # 6. Chat Engine (diÄŸer servislere baÄŸÄ±mlÄ±)
        from src.chat_engine import ChatEngine
        self._services["chat_engine"] = ChatEngine(
            key_manager=self._services["key_manager"],
            mcp_manager=self._services["mcp_manager"],
        )

        self._initialized = True
        logger.info("âœ… Container baÅŸarÄ±yla baÅŸlatÄ±ldÄ±")

    async def shutdown(self):
        """TÃ¼m servisleri dÃ¼zgÃ¼n ÅŸekilde kapatÄ±r."""
        logger.info("ğŸ›‘ Container kapatÄ±lÄ±yor...")

        if "redis" in self._services:
            await self._services["redis"].close()

        if "error_handler" in self._services:
            self._services["error_handler"].reset_stats()

        if "image_router" in self._services:
            await self._services["image_router"].close()

        self._services.clear()
        self._initialized = False
        logger.info("âœ… Container kapatÄ±ldÄ±")

    def get(self, service_name: str, default=None):
        """Bir servisi adÄ±yla getirir."""
        return self._services.get(service_name, default)

    def register(self, name: str, instance: Any):
        """Test mock'larÄ± iÃ§in servis kaydetme."""
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
