"""
AI Service - Yapay zeka yanÄ±t Ã¼retimi

Chat engine'e delegate eder, ek retry/circuit breaker mantÄ±ÄŸÄ± ekler.
"""
import logging
import asyncio
from typing import Optional

logger = logging.getLogger(__name__)


class AIService:
    def __init__(self, chat_engine):
        self.chat_engine = chat_engine
        self._circuit_open = False
        self._failure_count = 0
        self._circuit_threshold = 5
        self._reset_timeout = 300

    async def get_response(
        self,
        chat_id: int,
        message_text: str,
        user_display_name: str = None,
        user_id: int = None,
        username: str = None,
        image_path: str = None,
        audio_path: str = None,
        context: str = None
    ) -> Optional[str]:
        if self._circuit_open:
            logger.warning("Circuit breaker is OPEN. AI service temporarily unavailable.")
            return None

        attempt = 0
        max_retries = 3

        while attempt < max_retries:
            try:
                response = await self.chat_engine.get_response(
                    chat_id=chat_id,
                    message_text=message_text,
                    user_display_name=user_display_name,
                    user_id=user_id,
                    username=username,
                    image_path=image_path,
                    audio_path=audio_path,
                    context=context
                )

                self._reset_circuit()
                return response

            except Exception as e:
                attempt += 1
                self._failure_count += 1
                error_str = str(e).lower()

                is_quota = any(x in error_str for x in ["quota", "429", "rate limit"])
                is_timeout = "timeout" in error_str or "timed out" in error_str

                if is_quota or is_timeout:
                    if attempt < max_retries:
                        wait_time = 2 ** attempt
                        logger.warning(f"AI request failed (attempt {attempt}). Waiting {wait_time}s...")
                        await asyncio.sleep(wait_time)
                        continue

                if self._failure_count >= self._circuit_threshold:
                    self._trip_circuit()
                    logger.error(f"Circuit breaker TRIPPED after {self._failure_count} failures")

                raise e

        return None

    def _trip_circuit(self):
        self._circuit_open = True
        logger.warning(f"Circuit breaker opened. Will reset after {self._reset_timeout}s")

    def _reset_circuit(self):
        if self._circuit_open:
            self._circuit_open = False
            self._failure_count = 0
            logger.info("Circuit breaker reset. AI service recovered.")

    async def health_check(self) -> bool:
        try:
            test_response = await self.chat_engine.get_response(
                chat_id=0,
                message_text="ping",
                user_id=0
            )
            return test_response is not None
        except:
            return False