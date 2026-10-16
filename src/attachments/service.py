"""Durable worker-backed processing for image, voice and sticker inputs."""

from __future__ import annotations

import asyncio
import io
import logging
import os
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from config import Config
from src.attachments.job_queue import AttachmentJobQueue
from src.attachments.job_store import AttachmentJobStore
from src.observability import MetricsRegistry
from src.jobs.delivery_ledger import JobDeliveryLedger
from src.security.output_sanitizer import sanitize_html

logger = logging.getLogger(__name__)


class AttachmentJobError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class AttachmentProcessingService:
    ALLOWED_TYPES = {"vision", "voice", "sticker"}

    def __init__(self, *, vision_service=None, stt_service=None, tts_service=None,
                 sticker_render_service=None, database=None):
        self.vision_service = vision_service
        self.stt_service = stt_service
        self.tts_service = tts_service
        self.sticker_render_service = sticker_render_service
        self.database = database
        required = Config.MONGODB_REQUIRED or Config.APP_ROLE in {"bot", "worker"}
        self.job_store = AttachmentJobStore(database, required=required)
        self.queue = AttachmentJobQueue(store=self.job_store)
        self.bot = None
        self.mongo_available = False
        self.redis_required = getattr(Config, "ATTACHMENT_REDIS_REQUIRED", False) or Config.APP_ROLE in {"bot", "worker"}
        self._user_limits = defaultdict(lambda: asyncio.Semaphore(2))
        self._chat_limits = defaultdict(lambda: asyncio.Semaphore(2))
        self._request_times = defaultdict(list)
        self.metrics_registry = MetricsRegistry()
        self.delivery_ledger = JobDeliveryLedger(database, required=required)

    async def start(self, bot) -> None:
        self.bot = bot
        self.mongo_available = bool(self.database and await self.database.ping())
        if not self.mongo_available:
            self.job_store._disabled = True
            if Config.MONGODB_REQUIRED or Config.APP_ROLE in {"bot", "worker"}:
                raise RuntimeError("MongoDB is required for attachment processing")
        await self.delivery_ledger.ensure_indexes()
        workers = 0 if Config.APP_ROLE == "bot" else Config.ATTACHMENT_QUEUE_WORKERS
        # The bot process only enqueues durable jobs; the worker process must
        # still know which service method owns the payload.  Without this
        # binding the queue can acknowledge a job without running vision,
        # voice or sticker processing.
        self.queue.processor = self._process_job
        await self.queue.start(Config.REDIS_URL, workers, self.redis_required)

    async def close(self) -> None:
        await self.queue.close()

    @classmethod
    def validate(cls, job_type: str, file_size: int | None) -> str:
        kind = str(job_type or "").casefold().strip()
        if kind not in cls.ALLOWED_TYPES:
            raise AttachmentJobError("attachment_type_unsupported", "Bu medya tÃ¼rÃ¼ desteklenmiyor.")
        if file_size and int(file_size) > Config.ATTACHMENT_MAX_FILE_BYTES:
            raise AttachmentJobError("attachment_too_large", "Medya boyutu izin verilen sÄ±nÄ±rÄ± aÅŸÄ±yor.")
        return kind

    async def submit(self, *, job_type: str, chat_id: int, user_id: int, status_message_id: int,
                     source_file_id: str, source_key: str, filename: str, mime_type: str | None,
                     file_size: int | None, caption: str = "", reply_to_message_id: int | None = None,
                     user_name: str = "", username: str | None = None, sticker_kind: str = "static"):
        kind = self.validate(job_type, file_size)
        now = time.monotonic()
        recent = [x for x in self._request_times[user_id] if now - x < 60]
        if len(recent) >= 20:
            raise AttachmentJobError("attachment_rate_limited", "Medya kuyruÄŸuna Ã§ok hÄ±zlÄ± istek gÃ¶nderildi.")
        self._request_times[user_id] = recent + [now]
        payload = {
            "job_type": kind, "chat_id": int(chat_id), "user_id": int(user_id),
            "status_message_id": int(status_message_id), "source_file_id": str(source_file_id),
            "source_key": str(source_key)[:256], "filename": Path(str(filename or "attachment.bin")).name[:255],
            "mime_type": str(mime_type or "")[:128], "file_size": int(file_size or 0),
            "caption": str(caption or "")[:2000], "reply_to_message_id": reply_to_message_id,
            "user_name": str(user_name or "")[:120], "username": str(username or "")[:120],
            "sticker_kind": "video" if sticker_kind == "video" else "static",
        }
        return await self.queue.enqueue(payload)

    async def get_status(self, job_id: str):
        return await self.queue.get_job(job_id)

    async def get_metrics(self):
        metrics = await self.queue.metrics()
        metrics["service"] = "attachment_processing"
        metrics.update(self.metrics_registry.snapshot())
        return metrics

    async def _process_job(self, payload: dict[str, Any]) -> None:
        async with self._user_limits[payload["user_id"]], self._chat_limits[payload["chat_id"]]:
            await self._process_inner(payload)

    async def _process_inner(self, payload: dict[str, Any]) -> None:
        started = time.perf_counter()
        path = None
        job_id = payload["job_id"]
        kind = payload["job_type"]
        self.metrics_registry.inc("attachment_jobs_total")
        try:
            if self.bot is None:
                raise AttachmentJobError("telegram_unavailable", "Telegram istemcisi hazÄ±r deÄŸil.")
            await self._edit_status(payload, "ğŸ“¥ Medya indiriliyorâ€¦")
            telegram_file = await self.bot.get_file(payload["source_file_id"])
            os.makedirs(Config.ATTACHMENT_TEMP_DIR, exist_ok=True)
            suffix = Path(payload.get("filename") or ".bin").suffix[:10] or ".bin"
            with tempfile.NamedTemporaryFile(dir=Config.ATTACHMENT_TEMP_DIR, prefix="attachment_", suffix=suffix, delete=False) as handle:
                path = handle.name
            await telegram_file.download_to_drive(path)
            await self.queue._set_status(payload, "processing")
            if kind == "vision":
                await self._process_vision(payload, path)
            elif kind == "voice":
                await self._process_voice(payload, path)
            else:
                await self._process_sticker(payload, path)
            self.metrics_registry.inc("attachment_jobs_completed")
            if self.database and self.mongo_available:
                try:
                    await self.database.add_audit_log(payload["user_id"], "attachment_processed", job_id=job_id, attachment_type=kind)
                except Exception:
                    logger.debug("Attachment audit write failed", exc_info=True)
        except AttachmentJobError as exc:
            self.metrics_registry.inc("attachment_jobs_failed")
            await self._edit_status(payload, f"âŒ {exc} Ä°ÅŸ: <code>{job_id}</code>")
            raise
        except asyncio.TimeoutError as exc:
            self.metrics_registry.inc("attachment_jobs_failed")
            await self._edit_status(payload, f"â±ï¸ Medya iÅŸleme zaman aÅŸÄ±mÄ±na uÄŸradÄ±. Ä°ÅŸ: <code>{job_id}</code>")
            raise AttachmentJobError("timeout", "Medya iÅŸleme zaman aÅŸÄ±mÄ±na uÄŸradÄ±.") from exc
        except Exception as exc:
            self.metrics_registry.inc("attachment_jobs_failed")
            await self._edit_status(payload, f"âŒ Medya iÅŸlenemedi (ATTACHMENT_PROCESSING_ERROR). Ä°ÅŸ: <code>{job_id}</code>")
            raise AttachmentJobError("attachment_processing_error", "Medya iÅŸlenemedi.") from exc
        finally:
            if path:
                try:
                    Path(path).unlink(missing_ok=True)
                except OSError:
                    logger.warning("Attachment temp cleanup failed for %s", job_id)
            self.metrics_registry.observe("attachment_job_seconds", time.perf_counter() - started)

    async def _process_vision(self, payload, path):
        if self.vision_service is None:
            raise AttachmentJobError("ai_provider_unavailable", "GÃ¶rsel analiz servisi hazÄ±r deÄŸil.")
        await self._edit_status(payload, "ğŸ§  GÃ¶rsel analiz ediliyorâ€¦")
        result = await asyncio.wait_for(self.vision_service.analyze_image(
            path, payload.get("caption", ""), payload["chat_id"], payload["user_id"],
            payload.get("user_name", ""), payload.get("username") or None,
        ), timeout=Config.ATTACHMENT_JOB_TIMEOUT_SECONDS)
        text = sanitize_html(str(result or "GÃ¶rsel iÃ§in yanÄ±t Ã¼retilemedi."))
        await self._deliver(payload, "vision", lambda: self.bot.send_message(chat_id=payload["chat_id"], text=text, parse_mode="HTML", reply_to_message_id=payload.get("reply_to_message_id")))
        payload["output_type"] = "telegram_html"

    async def _process_voice(self, payload, path):
        if self.stt_service is None:
            raise AttachmentJobError("ai_provider_unavailable", "Ses Ã§Ã¶zÃ¼mleme servisi hazÄ±r deÄŸil.")
        await self._edit_status(payload, "ğŸ§ Ses Ã§Ã¶zÃ¼mleniyorâ€¦")
        response = await asyncio.wait_for(self.stt_service.transcribe_or_respond(
            path, payload["chat_id"], payload["user_id"], payload.get("user_name", ""), payload.get("username") or None,
        ), timeout=Config.ATTACHMENT_JOB_TIMEOUT_SECONDS)
        response = str(response or "")
        if not response:
            await self._edit_status(payload, "âœ… Ses iÅŸlendi; yanÄ±t Ã¼retilecek iÃ§erik bulunamadÄ±.")
            return
        if len(response) > 500 or self.tts_service is None:
            await self._deliver(payload, "voice_text", lambda: self.bot.send_message(chat_id=payload["chat_id"], text=sanitize_html(response), parse_mode="HTML", reply_to_message_id=payload.get("reply_to_message_id")))
            payload["output_type"] = "telegram_html"
            return
        output = None
        try:
            os.makedirs(Config.TTS_OUTPUT_DIR, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=Config.TTS_OUTPUT_DIR, prefix="reply_", suffix=".mp3", delete=False) as handle:
                output = handle.name
            output = await self.tts_service.synthesize(response, language="tr", output_path=output)
            with open(output, "rb") as voice_file:
                await self._deliver(payload, "voice", lambda: self.bot.send_voice(chat_id=payload["chat_id"], voice=voice_file, caption=response[:200], reply_to_message_id=payload.get("reply_to_message_id")))
            payload["output_type"] = "telegram_voice"
        finally:
            if output:
                Path(output).unlink(missing_ok=True)

    async def _process_sticker(self, payload, path):
        if self.sticker_render_service is None:
            raise AttachmentJobError("sticker_renderer_unavailable", "Sticker render servisi hazÄ±r deÄŸil.")
        await self._edit_status(payload, "âœ¨ Sticker hazÄ±rlanÄ±yorâ€¦")
        data = await asyncio.to_thread(Path(path).read_bytes)
        artifact = await asyncio.wait_for(self.sticker_render_service.render(data, kind=payload.get("sticker_kind", "static")), timeout=Config.ATTACHMENT_JOB_TIMEOUT_SECONDS)
        stream = io.BytesIO(artifact.content)
        stream.name = artifact.filename
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("â• Pakete ekle / yeni paket", callback_data=f"stk_select_pack_{payload.get('sticker_kind', 'static')}")]])
        await self._deliver(payload, "sticker", lambda: self.bot.send_sticker(chat_id=payload["chat_id"], sticker=stream, reply_markup=keyboard, reply_to_message_id=payload.get("reply_to_message_id")))
        payload["output_type"] = "telegram_sticker"

    async def _edit_status(self, payload, text: str) -> None:
        if self.bot is None:
            return
        try:
            await self.bot.edit_message_text(chat_id=payload["chat_id"], message_id=payload["status_message_id"], text=text, parse_mode="HTML")
        except Exception:
            logger.debug("Attachment status update failed", exc_info=True)

    async def _deliver(self, payload, output_type: str, sender) -> bool:
        key = f"attachment:{payload['job_id']}:{output_type}"
        if not await self.delivery_ledger.claim(key, job_id=payload["job_id"], chat_id=payload["chat_id"]):
            return False
        if not await self.delivery_ledger.begin_send(key):
            return False
        try:
            result = await sender()
            message_id = getattr(result, "message_id", None)
            await self.delivery_ledger.mark_sent(key, message_id)
            return True
        except Exception as exc:
            await self.delivery_ledger.mark_uncertain(key, type(exc).__name__)
            raise
