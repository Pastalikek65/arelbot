"""
Retry ve Circuit Breaker yardÄ±mcÄ±larÄ±.

API saÄŸlayÄ±cÄ±larÄ±, RSS kaynaklarÄ± ve medya servisleri gibi dÄ±ÅŸ baÄŸÄ±mlÄ±lÄ±klarda
geÃ§ici hatalarÄ± kontrollÃ¼ ÅŸekilde yÃ¶netmek iÃ§in kullanÄ±lÄ±r.
"""
import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum
from functools import wraps
from typing import Any, Awaitable, Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    name: str
    failure_threshold: int = 5
    reset_timeout: float = 30.0

    def __post_init__(self):
        self.failure_count = 0
        self.last_failure_at: Optional[float] = None
        self.state = CircuitState.CLOSED

    def can_execute(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN and self.last_failure_at:
            if time.monotonic() - self.last_failure_at >= self.reset_timeout:
                self.state = CircuitState.HALF_OPEN
                logger.info("Circuit breaker half-open: %s", self.name)
                return True
        return self.state == CircuitState.HALF_OPEN

    def record_success(self):
        self.failure_count = 0
        self.last_failure_at = None
        if self.state != CircuitState.CLOSED:
            logger.info("Circuit breaker recovered: %s", self.name)
        self.state = CircuitState.CLOSED

    def record_failure(self):
        self.failure_count += 1
        self.last_failure_at = time.monotonic()
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logger.warning("Circuit breaker opened: %s", self.name)

    async def call(self, func: Callable[..., Awaitable[T]], *args: Any, **kwargs: Any) -> T:
        if not self.can_execute():
            raise RuntimeError(f"{self.name} servisi geÃ§ici olarak devre dÄ±ÅŸÄ±.")
        try:
            result = await func(*args, **kwargs)
            self.record_success()
            return result
        except Exception:
            self.record_failure()
            raise


class RetryHandler:
    @staticmethod
    def retry(
        attempts: int = 3,
        delay: float = 1.0,
        backoff: float = 2.0,
        exceptions: tuple[type[Exception], ...] = (Exception,),
    ):
        def decorator(func: Callable[..., Awaitable[T]]):
            @wraps(func)
            async def wrapper(*args: Any, **kwargs: Any) -> T:
                current_delay = delay
                last_error: Optional[Exception] = None
                for attempt in range(1, attempts + 1):
                    try:
                        return await func(*args, **kwargs)
                    except exceptions as exc:
                        last_error = exc
                        if attempt >= attempts:
                            break
                        logger.warning(
                            "Retry %s/%s for %s after error: %s",
                            attempt,
                            attempts,
                            getattr(func, "__name__", "callable"),
                            exc,
                        )
                        await asyncio.sleep(current_delay)
                        current_delay *= backoff
                assert last_error is not None
                raise last_error

            return wrapper

        return decorator