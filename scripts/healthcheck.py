"""Container dependency health probe used by Docker Compose."""

from __future__ import annotations

import os
from urllib.parse import urlsplit
import socket
import shutil


def check_url(value: str, default_port: int, timeout: float = 1.0) -> bool:
    parsed = urlsplit(value)
    host = parsed.hostname or "localhost"
    port = parsed.port or default_port
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def check_mongo(value: str) -> bool:
    """Run a bounded Mongo ping when the driver is present."""
    client = None
    try:
        from pymongo import MongoClient

        client = MongoClient(value, serverSelectionTimeoutMS=700, connectTimeoutMS=500)
        client.admin.command("ping")
        return True
    except Exception:
        return False
    finally:
        if client is not None:
            client.close()


def check_redis(value: str) -> bool:
    """Run Redis PING without printing credentials from the URI."""
    client = None
    try:
        import redis

        client = redis.Redis.from_url(value, socket_connect_timeout=0.5, socket_timeout=0.5)
        result = client.ping()
        return bool(result)
    except Exception:
        return False
    finally:
        if client is not None:
            client.close()


def check_binary(name: str) -> bool:
    """Return whether a required runtime executable is available on PATH."""
    return bool(shutil.which(name))


def check_encryption_key(value: str | None, *, required: bool) -> bool:
    """Validate the cookie-vault key readiness without logging the secret."""
    return not required or bool(value and len(value) >= 32)


def main() -> int:
    role = os.getenv("APP_ROLE", "combined")
    mongo_ok = check_mongo(os.getenv("MONGODB_URI", "mongodb://localhost:27017"))
    redis_ok = check_redis(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    ffmpeg_ok = check_binary("ffmpeg")
    ffprobe_ok = check_binary("ffprobe")
    cookie_enabled = os.getenv("MEDIA_COOKIE_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
    encryption_ok = check_encryption_key(os.getenv("ENCRYPTION_KEY"), required=cookie_enabled)
    if role in {"bot", "worker"} and not (mongo_ok and redis_ok and ffmpeg_ok and ffprobe_ok and encryption_ok):
        print(
            f"unhealthy: mongo={mongo_ok} redis={redis_ok} "
            f"ffmpeg={ffmpeg_ok} ffprobe={ffprobe_ok} encryption={encryption_ok}"
        )
        return 1
    print(
        f"healthy: mongo={mongo_ok} redis={redis_ok} "
        f"ffmpeg={ffmpeg_ok} ffprobe={ffprobe_ok} encryption={encryption_ok}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
