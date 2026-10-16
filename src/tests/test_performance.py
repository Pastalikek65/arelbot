"""
RedisCache, RateLimiter, Encryptor iÃ§in testler.
"""
import pytest
from src.database.redis_cache import RedisCache, MemoryBackend
from src.security.rate_limiter import RateLimiter, TokenBucket
from src.security.encryptor import Encryptor


class TestMemoryBackend:
    @pytest.mark.asyncio
    async def test_set_and_get(self):
        cache = MemoryBackend()
        await cache.set("test_key", "test_value")
        result = await cache.get("test_key")
        assert result == "test_value"

    @pytest.mark.asyncio
    async def test_get_nonexistent(self):
        cache = MemoryBackend()
        result = await cache.get("no_such_key")
        assert result is None

    @pytest.mark.asyncio
    async def test_ttl_expiry(self):
        import time
        cache = MemoryBackend()
        await cache.set("temp", "value", ttl=-1)
        result = await cache.get("temp")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete(self):
        cache = MemoryBackend()
        await cache.set("del_key", "value")
        await cache.delete("del_key")
        assert await cache.get("del_key") is None

    @pytest.mark.asyncio
    async def test_exists(self):
        cache = MemoryBackend()
        assert not await cache.exists("missing")
        await cache.set("present", "v")
        assert await cache.exists("present")

    @pytest.mark.asyncio
    async def test_clear(self):
        cache = MemoryBackend()
        await cache.set("a", "1")
        await cache.set("b", "2")
        await cache.clear()
        assert await cache.get("a") is None

    @pytest.mark.asyncio
    async def test_close(self):
        cache = MemoryBackend()
        await cache.set("x", "y")
        await cache.close()
        assert await cache.get("x") is None


class TestRedisCache:
    @pytest.mark.asyncio
    async def test_uses_memory_fallback(self):
        cache = RedisCache("redis://nonexistent:6379", required=False)
        await cache.initialize()
        assert not cache.is_redis_available
        assert cache.backend_name == "Memory"

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_required_redis_fails_fast_instead_of_using_memory(self):
        cache = RedisCache("redis://nonexistent:6379", required=True)
        with pytest.raises(RuntimeError, match="Redis is required"):
            await cache.initialize()

    async def test_get_or_set(self):
        cache = RedisCache(required=False)
        await cache.initialize()

        async def factory():
            return "factory_value"

        result = await cache.get_or_set(
            "test_factory",
            factory,
            ttl=60
        )
        assert result == "factory_value"

        cached = await cache.get("test_factory")
        assert cached == "factory_value"

    @pytest.mark.asyncio
    async def test_get_default(self):
        cache = RedisCache(required=False)
        await cache.initialize()
        result = await cache.get("no_key", default=42)
        assert result == 42

    @pytest.mark.asyncio
    async def test_close_and_reinit(self):
        # Keep this lifecycle test independent from a developer's local
        # Docker Redis instance; the fallback contract is what is under test.
        cache = RedisCache("redis://nonexistent:6379", required=False)
        await cache.initialize()
        await cache.close()
        await cache.initialize()
        assert cache.backend_name == "Memory"


class TestTokenBucket:
    def test_consume_allowed(self):
        tb = TokenBucket(tokens_per_second=10, bucket_size=10)
        assert tb.consume("user_1") is True

    def test_consume_exhausted(self):
        tb = TokenBucket(tokens_per_second=100, bucket_size=2)
        assert tb.consume("user_x") is True
        assert tb.consume("user_x") is True
        assert tb.consume("user_x") is False

    def test_wait_time_zero_when_available(self):
        tb = TokenBucket(tokens_per_second=10, bucket_size=10)
        assert tb.get_wait_time("user") == 0.0

    def test_wait_time_positive_when_exhausted(self):
        tb = TokenBucket(tokens_per_second=1.0, bucket_size=1)
        tb.consume("user")
        wait = tb.get_wait_time("user")
        assert wait > 0.0

    def test_reset(self):
        tb = TokenBucket(tokens_per_second=100, bucket_size=1)
        tb.consume("user")
        assert tb.consume("user") is False
        tb.reset("user")
        assert tb.consume("user") is True

    def test_clear(self):
        tb = TokenBucket(tokens_per_second=100, bucket_size=1)
        tb.consume("a")
        tb.consume("b")
        tb.clear()
        assert tb.consume("a") is True

    def test_independent_buckets(self):
        tb = TokenBucket(tokens_per_second=100, bucket_size=2)
        tb.consume("alice")
        tb.consume("alice")
        assert tb.consume("alice") is False  # Alice exhausted
        assert tb.consume("bob") is True     # Bob fresh


class TestRateLimiter:
    @pytest.mark.asyncio
    async def test_not_rate_limited_initially(self):
        limiter = RateLimiter()
        limited = await limiter.is_rate_limited(1, "private")
        assert limited is False

    @pytest.mark.asyncio
    async def test_rate_limit_after_many_requests(self):
        limiter = RateLimiter()
        limited = False
        for _ in range(20):
            limited = await limiter.is_rate_limited(2, "private")

        assert limited is True

    @pytest.mark.asyncio
    async def test_group_limit_stricter(self):
        limiter = RateLimiter()
        limited = False
        for _ in range(15):
            limited = await limiter.is_rate_limited(3, "supergroup")
        assert limited is True

    @pytest.mark.asyncio
    async def test_reset_user(self):
        limiter = RateLimiter()
        for _ in range(20):
            await limiter.is_rate_limited(4, "private")

        limiter.reset_user(4)
        limited = await limiter.is_rate_limited(4, "private")
        assert limited is False

    @pytest.mark.asyncio
    async def test_different_users_independent(self):
        limiter = RateLimiter()
        for _ in range(20):
            await limiter.is_rate_limited(5, "private")

        limited_b = await limiter.is_rate_limited(6, "private")
        assert limited_b is False

    @pytest.mark.asyncio
    async def test_get_wait_time(self):
        limiter = RateLimiter()
        wait = await limiter.get_wait_time(7)
        assert isinstance(wait, float)
        assert wait >= 0


class TestEncryptor:
    def test_encrypt_decrypt(self):
        encryptor = Encryptor(master_key="test-master-key-12345")
        if not encryptor.is_available:
            pytest.skip("Fernet not available")

        original = "my-super-secret-api-key"
        encrypted = encryptor.encrypt(original)
        assert encrypted != original
        assert encryptor.is_encrypted(encrypted)

        decrypted = encryptor.decrypt(encrypted)
        assert decrypted == original

    def test_none_input(self):
        encryptor = Encryptor(master_key="test-key")
        assert encryptor.encrypt("") == ""
        assert encryptor.decrypt("") == ""

    def test_encryptor_unavailable(self, monkeypatch):
        monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
        encryptor = Encryptor(master_key=None)
        assert encryptor.is_available is False
        assert encryptor.encrypt("test") == "test"
        assert encryptor.decrypt("test") == "test"

    def test_is_encrypted_check(self):
        encryptor = Encryptor(master_key="test-master")
        if not encryptor.is_available:
            pytest.skip("Fernet not available")
        assert encryptor.is_encrypted("gAAAAAabc123") is True
        assert encryptor.is_encrypted("plaintext") is False
