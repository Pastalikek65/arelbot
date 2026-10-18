"""
Rate Limiter — Token Bucket algoritması ile kullanıcı bazlı hız sınırlama.

Her kullanıcı/sohbet için ayrı bir bucket tutar.
Redis varsa dağıtık rate limiting, yoksa in-memory.

Kullanım:
    limiter = RateLimiter(redis_cache)
    if await limiter.is_rate_limited(user_id):
        return "Yavaşla!"
"""
import time
import logging
from typing import Dict, Tuple, Optional

logger = logging.getLogger(__name__)


class TokenBucket:
    """
    Token Bucket algoritması implementasyonu.

    - Her saniye `tokens_per_second` token eklenir
    - Maksimum `bucket_size` kadar token birikebilir
    - Her istek 1 token tüketir
    """

    def __init__(self, tokens_per_second: float = 1.0, bucket_size: int = 10):
        self.tokens_per_second = tokens_per_second
        self.bucket_size = bucket_size
        self._tokens: Dict[str, Tuple[float, float]] = {}
        # key -> (tokens, last_refill_time)

    def _refill(self, key: str):
        now = time.time()
        tokens, last_time = self._tokens.get(key, (self.bucket_size, now))
        elapsed = now - last_time
        new_tokens = min(self.bucket_size, tokens + elapsed * self.tokens_per_second)
        self._tokens[key] = (new_tokens, now)
        return new_tokens

    def consume(self, key: str) -> bool:
        """1 token tüketir. False dönerse rate limit aşıldı."""
        tokens = self._refill(key)
        if tokens >= 1.0:
            self._tokens[key] = (tokens - 1.0, self._tokens[key][1])
            return True
        return False

    def get_wait_time(self, key: str) -> float:
        """Rate limit aşımında beklenecek süre (saniye)."""
        tokens = self._refill(key)
        if tokens >= 1.0:
            return 0.0
        return (1.0 - tokens) / self.tokens_per_second

    def reset(self, key: str):
        self._tokens.pop(key, None)

    def clear(self):
        self._tokens.clear()


class RateLimiter:
    """
    Birleşik Rate Limiter.

    Kullanım:
        limiter = RateLimiter(redis_cache)
        if await limiter.is_rate_limited(user_id):
            await message.reply_text("Çok hızlısın, yavaşla! ⏳")
            return
    """

    def __init__(self, redis_cache=None):
        self._redis = redis_cache
        self._memory_bucket = TokenBucket(
            tokens_per_second=1.0,
            bucket_size=10
        )

        self._memory_group = TokenBucket(
            tokens_per_second=0.5,
            bucket_size=5
        )

        self._memory_global = TokenBucket(
            tokens_per_second=50.0,
            bucket_size=100
        )

        self._limits = {
            "private": TokenBucket(2.0, 20),
            "supergroup": TokenBucket(1.0, 10),
            "group": TokenBucket(1.0, 10),
        }

    async def is_rate_limited(
        self,
        user_id: int,
        chat_type: str = "private",
        key: Optional[str] = None
    ) -> bool:
        """
        Kullanıcının rate limit aşıp aşmadığını kontrol eder.

        Returns:
            True = rate limit aşıldı, False = devam edebilir
        """
        uid = str(user_id)
        cache_key = f"ratelimit:{uid}"

        if self._redis and self._redis.is_redis_available:
            return await self._redis_rate_check(cache_key, uid, chat_type)
        else:
            return self._memory_rate_check(uid, chat_type)

    async def _redis_rate_check(self, cache_key: str, uid: str, chat_type: str) -> bool:
        counter = await self._redis.get(cache_key, default=0)
        if counter >= self._limits.get(chat_type, self._limits["private"]).bucket_size:
            return True
        await self._redis.set(cache_key, counter + 1, ttl=1)
        return False

    def _memory_rate_check(self, uid: str, chat_type: str) -> bool:
        limit = self._limits.get(chat_type, self._limits["private"])

        if not limit.consume(uid):
            wait = limit.get_wait_time(uid)
            logger.debug("Rate limit reached: wait_seconds=%.1f", wait)
            return True

        if not self._memory_bucket.consume(uid):
            return True

        return False

    async def get_wait_time(self, user_id: int) -> float:
        uid = str(user_id)
        limit = self._limits.get("private")

        if self._redis and self._redis.is_redis_available:
            return 0.5
        else:
            return limit.get_wait_time(uid)

    def reset_user(self, user_id: int):
        uid = str(user_id)
        self._memory_bucket.reset(uid)
        for limiter in self._limits.values():
            limiter.reset(uid)

    async def clear_all(self):
        self._memory_bucket.clear()
        self._memory_group.clear()
        self._memory_global.clear()
        for limiter in self._limits.values():
            limiter.clear()
        if self._redis and self._redis.is_redis_available:
            await self._redis.clear()
        logger.info("RateLimiter sıfırlandı.")
