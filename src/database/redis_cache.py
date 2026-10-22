"""Redis cache with an explicit local/test fallback contract.

Memory fallback is allowed only for local/test usage. Production bot/worker
roles fail fast when REDIS_REQUIRED is enabled; they never silently degrade.
"""
import json
import inspect
import logging
import time
from typing import Optional, Any, Dict
from src.security.log_redaction import summarize_exception

logger = logging.getLogger(__name__)


class MemoryBackend:
    """Redis yoksa kullanılacak basit in-memory cache."""

    def __init__(self):
        self._store: Dict[str, tuple] = {}

    async def get(self, key: str) -> Optional[str]:
        if key in self._store:
            value, expiry = self._store[key]
            if expiry is None or time.time() < expiry:
                return value
            del self._store[key]
        return None

    async def set(self, key: str, value: str, ttl: Optional[int] = None):
        expiry = (time.time() + ttl) if ttl else None
        self._store[key] = (value, expiry)

    async def delete(self, key: str):
        self._store.pop(key, None)

    async def exists(self, key: str) -> bool:
        return key in self._store

    async def clear(self):
        self._store.clear()

    async def close(self):
        self._store.clear()


class RedisBackend:
    """Gerçek Redis bağlantısı."""

    def __init__(self, redis_url: str = "redis://localhost:6379/0"):
        self._redis_url = redis_url
        self._redis = None

    async def get(self, key: str) -> Optional[str]:
        try:
            value = await self._redis.get(key)
            # ``decode_responses=True`` returns ``str`` while older clients
            # and test doubles may return ``bytes``.  Handle both without
            # turning a successful cache hit into a false miss.
            if not value:
                return None
            return value.decode() if isinstance(value, bytes) else value
        except Exception as e:
            logger.warning("Redis get error: %s", summarize_exception(e, label="redis_get"))
            return None

    async def set(self, key: str, value: str, ttl: Optional[int] = None):
        try:
            if ttl:
                await self._redis.set(key, value, ex=ttl)
            else:
                await self._redis.set(key, value)
        except Exception as e:
            logger.warning("Redis set error: %s", summarize_exception(e, label="redis_set"))

    async def delete(self, key: str):
        try:
            await self._redis.delete(key)
        except Exception as e:
            logger.warning("Redis delete error: %s", summarize_exception(e, label="redis_delete"))

    async def exists(self, key: str) -> bool:
        try:
            return await self._redis.exists(key) > 0
        except Exception as e:
            logger.warning("Redis exists error: %s", summarize_exception(e, label="redis_exists"))
            return False

    async def clear(self):
        try:
            await self._redis.flushdb()
        except Exception as e:
            logger.warning("Redis clear error: %s", summarize_exception(e, label="redis_clear"))

    async def close(self):
        if self._redis:
            close = getattr(self._redis, "aclose", None)
            if close is None:
                close = getattr(self._redis, "close", None)
            if close is not None:
                result = close()
                if inspect.isawaitable(result):
                    await result
            self._redis = None

    async def initialize(self):
        try:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(
                self._redis_url,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2
            )
            await self._redis.ping()
            logger.info("✅ Redis bağlantısı başarılı")
            return True
        except Exception as e:
            logger.warning("Redis bağlantısı başarısız: %s", summarize_exception(e, label="redis_init"))
            return False


class RedisCache:
    """
    Birleşik Cache Sistemi — Redis var mı diye kontrol eder,
    yoksa MemoryBackend kullanır.

    The application injects one instance through ``Container``.  A process-global
    singleton would make worker/test lifecycles share mutable cache state, so
    each owner explicitly creates and closes its instance.
    """

    def __init__(self, redis_url: str = None, required: bool | None = None):
        self._redis_url = redis_url or "redis://localhost:6379/0"
        if required is None:
            try:
                from config import Config
                required = bool(
                    getattr(Config, "REDIS_REQUIRED", False)
                    or getattr(Config, "MEDIA_REDIS_REQUIRED", False)
                    or getattr(Config, "APP_ROLE", "combined") in {"bot", "worker"}
                )
            except Exception:
                required = False
        self.required = bool(required)
        self._backend = None
        self._redis_available = False
        self._initialized = False

    async def initialize(self):
        if self._initialized:
            return

        # Önce Redis'i dene
        redis_backend = RedisBackend(self._redis_url)
        redis_ok = await redis_backend.initialize()

        if redis_ok:
            self._backend = redis_backend
            self._redis_available = True
            logger.info("⚡ RedisCache: Redis backend aktif")
        else:
            if self.required:
                raise RuntimeError("Redis is required for this application role but is unavailable")
            self._backend = MemoryBackend()
            self._redis_available = False
            logger.info("🧠 RedisCache: Memory backend aktif (Redis yok)")

        self._initialized = True

    @property
    def is_redis_available(self) -> bool:
        return self._redis_available

    @property
    def backend_name(self) -> str:
        return "Redis" if self._redis_available else "Memory"

    async def get(self, key: str, default: Any = None) -> Any:
        raw = await self._backend.get(key)
        if raw is None:
            return default
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw

    async def set(self, key: str, value: Any, ttl: Optional[int] = None):
        serialized = json.dumps(value, default=str)
        await self._backend.set(key, serialized, ttl=ttl)

    async def delete(self, key: str):
        await self._backend.delete(key)

    async def exists(self, key: str) -> bool:
        return await self._backend.exists(key)

    async def get_or_set(self, key: str, factory, ttl: int = 300) -> Any:
        cached = await self.get(key)
        if cached is not None:
            return cached
        value = await factory()
        await self.set(key, value, ttl=ttl)
        return value

    async def clear(self):
        await self._backend.clear()

    async def close(self):
        await self._backend.close()
        self._initialized = False
        logger.info("RedisCache kapatıldı.")
