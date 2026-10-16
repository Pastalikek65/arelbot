"""Queue, worker and Telegram delivery boundary for AI image generation."""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from config import Config
from src.image_generation.job_queue import ImageJobQueue
from src.image_generation.job_store import ImageJobStore
from src.jobs.delivery_ledger import JobDeliveryLedger
from src.observability import MetricsRegistry
from src.security.output_sanitizer import sanitize_html
from src.security.error_codes import public_error_message


class ImageGenerationJobError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class ImageGenerationService:
    def __init__(self, *, image_router: Any, database: Any):
        self.image_router = image_router
        self.database = database
        required = Config.MONGODB_REQUIRED or Config.APP_ROLE in {"bot", "worker"}
        self.store = ImageJobStore(database, required=required)
        self.queue = ImageJobQueue(store=self.store)
        self.delivery_ledger = JobDeliveryLedger(database, required=required)
        self.bot = None
        self.mongo_available = False
        self.redis_required = getattr(Config, "IMAGE_REDIS_REQUIRED", False) or Config.APP_ROLE in {"bot", "worker"}
        self._user_limits = defaultdict(lambda: asyncio.Semaphore(1))
        self._chat_limits = defaultdict(lambda: asyncio.Semaphore(1))
        self._request_times = defaultdict(list)
        self.metrics_registry = MetricsRegistry()

    async def start(self, bot) -> None:
        self.bot = bot
        self.mongo_available = bool(self.database and await self.database.ping())
        if not self.mongo_available:
            self.store._disabled = True
            if Config.MONGODB_REQUIRED or Config.APP_ROLE in {"bot", "worker"}:
                raise RuntimeError("MongoDB is required for image generation")
        await self.delivery_ledger.ensure_indexes()
        self.queue.processor = self._process_job
        workers = 0 if Config.APP_ROLE == "bot" else Config.IMAGE_QUEUE_WORKERS
        await self.queue.start(Config.REDIS_URL, workers, self.redis_required)

    async def close(self) -> None:
        await self.queue.close()

    @staticmethod
    def normalize_prompt(prompt: str) -> str:
        value = " ".join(str(prompt or "").replace("\x00", " ").split())
        if not value:
            raise ImageGenerationJobError("image_prompt_invalid", "GÃ¶rsel aÃ§Ä±klamasÄ± boÅŸ olamaz.")
        if len(value) > Config.IMAGE_MAX_PROMPT_CHARS:
            raise ImageGenerationJobError("image_prompt_invalid", f"GÃ¶rsel aÃ§Ä±klamasÄ± en fazla {Config.IMAGE_MAX_PROMPT_CHARS} karakter olabilir.")
        return value

    async def submit(self, *, chat_id: int, user_id: int, status_message_id: int, prompt: str,
                     reply_to_message_id: int | None = None, message_thread_id: int | None = None) -> tuple[str, bool]:
        prompt = self.normalize_prompt(prompt)
        now = time.monotonic()
        recent = [item for item in self._request_times[int(user_id)] if now - item < 60]
        if len(recent) >= Config.IMAGE_RATE_LIMIT_PER_MINUTE:
            raise ImageGenerationJobError("image_rate_limited", "GÃ¶rsel kuyruÄŸuna Ã§ok hÄ±zlÄ± istek gÃ¶nderildi.")
        self._request_times[int(user_id)] = recent + [now]
        source_key = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        payload = {
            "job_type": "image_generation", "chat_id": int(chat_id), "user_id": int(user_id),
            "status_message_id": int(status_message_id), "source_key": source_key,
            "prompt": prompt, "reply_to_message_id": reply_to_message_id,
            "message_thread_id": int(message_thread_id) if message_thread_id is not None else None,
        }
        return await self.queue.enqueue(payload)

    async def get_status(self, job_id: str):
        return await self.queue.get_job(job_id)

    async def get_metrics(self):
        metrics = await self.queue.metrics()
        metrics["service"] = "image_generation"
        metrics.update(self.metrics_registry.snapshot())
        return metrics

    async def _process_job(self, payload: dict[str, Any]) -> None:
        async with self._user_limits[payload["user_id"]], self._chat_limits[payload["chat_id"]]:
            await self._process_inner(payload)

    async def _process_inner(self, payload: dict[str, Any]) -> None:
        started = time.perf_counter()
        output = None
        self.metrics_registry.inc("image_jobs_total")
        try:
            if self.bot is None:
                raise ImageGenerationJobError("telegram_unavailable", "Telegram istemcisi hazÄ±r deÄŸil.")
            await self._edit(payload, "ğŸ¨ GÃ¶rsel hazÄ±rlanÄ±yorâ€¦")
            await self.queue._set_status(payload, "processing")
            output = await asyncio.wait_for(
                self.image_router.generate_image(payload["prompt"], max_output_bytes=Config.IMAGE_MAX_OUTPUT_BYTES),
                timeout=Config.IMAGE_JOB_TIMEOUT_SECONDS,
            )
            if not output:
                raise ImageGenerationJobError("image_provider_unavailable", "GÃ¶rsel saÄŸlayÄ±cÄ±sÄ± boÅŸ Ã§Ä±ktÄ± dÃ¶ndÃ¼rdÃ¼.")
            output_path = Path(str(output))
            if output_path.is_file():
                with output_path.open("rb") as image_file:
                    await self._deliver(payload, lambda: self.bot.send_photo(
                        chat_id=payload["chat_id"], photo=image_file, caption="ğŸ¨ Ä°ÅŸte gÃ¶rseliniz.",
                        reply_to_message_id=payload.get("reply_to_message_id"),
                        message_thread_id=payload.get("message_thread_id"),
                    ))
            else:
                await self._deliver(payload, lambda: self.bot.send_photo(
                    chat_id=payload["chat_id"], photo=str(output), caption="ğŸ¨ Ä°ÅŸte gÃ¶rseliniz.",
                    reply_to_message_id=payload.get("reply_to_message_id"),
                    message_thread_id=payload.get("message_thread_id"),
                ))
            payload["output_type"] = "telegram_photo"
            self.metrics_registry.inc("image_jobs_completed")
            if self.mongo_available and self.database:
                audit = getattr(self.database, "add_audit_log", None)
                if audit:
                    await audit(payload["user_id"], "image_generated", job_id=payload["job_id"])
            await self._edit(payload, "âœ… GÃ¶rsel gÃ¶nderildi.")
        except ImageGenerationJobError as exc:
            self.metrics_registry.inc("image_jobs_failed")
            message = public_error_message(
                exc,
                "GÃ¶rsel Ã¼retilemedi.",
                messages={
                    "IMAGE_PROMPT_INVALID": "GÃ¶rsel aÃ§Ä±klamasÄ± geÃ§ersiz.",
                    "IMAGE_RATE_LIMITED": "GÃ¶rsel kuyruÄŸuna Ã§ok hÄ±zlÄ± istek gÃ¶nderildi.",
                    "IMAGE_POLICY_REJECTED": "Bu gÃ¶rsel isteÄŸi gÃ¼venlik politikasÄ± nedeniyle iÅŸlenemiyor.",
                    "IMAGE_OUTPUT_TOO_LARGE": "Ãœretilen gÃ¶rsel Telegram boyut sÄ±nÄ±rÄ±nÄ± aÅŸÄ±yor.",
                    "IMAGE_PROVIDER_UNAVAILABLE": "GÃ¶rsel saÄŸlayÄ±cÄ±sÄ± ÅŸu an yanÄ±t vermiyor.",
                    "TELEGRAM_UNAVAILABLE": "Telegram istemcisi ÅŸu an hazÄ±r deÄŸil.",
                },
            )
            await self._edit(payload, f"âŒ {sanitize_html(message)} Ä°ÅŸ: <code>{payload['job_id']}</code>")
            raise
        except asyncio.TimeoutError as exc:
            self.metrics_registry.inc("image_jobs_failed")
            await self._edit(payload, f"â±ï¸ GÃ¶rsel Ã¼retimi zaman aÅŸÄ±mÄ±na uÄŸradÄ±. Ä°ÅŸ: <code>{payload['job_id']}</code>")
            raise ImageGenerationJobError("timeout", "GÃ¶rsel Ã¼retimi zaman aÅŸÄ±mÄ±na uÄŸradÄ±.") from exc
        except Exception as exc:
            self.metrics_registry.inc("image_jobs_failed")
            detail = str(exc)
            if "NSFW_REJECTED" in detail:
                code, message = "image_policy_rejected", "Bu gÃ¶rsel isteÄŸini gÃ¼venlik politikasÄ± nedeniyle iÅŸleyemiyorum."
            elif "IMAGE_OUTPUT_TOO_LARGE" in detail:
                code, message = "image_output_too_large", "Ãœretilen gÃ¶rsel Telegram boyut sÄ±nÄ±rÄ±nÄ± aÅŸÄ±yor."
            else:
                code, message = "image_generation_error", "GÃ¶rsel Ã¼retilemedi."
            await self._edit(payload, f"âŒ {message} Ä°ÅŸ: <code>{payload['job_id']}</code>")
            raise ImageGenerationJobError(code, message) from exc
        finally:
            if output and Path(str(output)).is_file():
                try:
                    Path(str(output)).unlink(missing_ok=True)
                except OSError:
                    pass
            self.metrics_registry.observe("image_job_seconds", time.perf_counter() - started)

    async def _edit(self, payload: dict[str, Any], text: str) -> None:
        try:
            await self.bot.edit_message_text(chat_id=payload["chat_id"], message_id=payload["status_message_id"], text=text, parse_mode="HTML")
        except Exception:
            pass

    async def _deliver(self, payload: dict[str, Any], sender) -> Any:
        key = f"image:{payload['job_id']}:photo"
        if not await self.delivery_ledger.claim(key, job_id=payload["job_id"], chat_id=payload["chat_id"]):
            return None
        if not await self.delivery_ledger.begin_send(key):
            return None
        try:
            result = await sender()
            await self.delivery_ledger.mark_sent(key, getattr(result, "message_id", None))
            return result
        except Exception as exc:
            await self.delivery_ledger.mark_uncertain(key, type(exc).__name__)
            raise
