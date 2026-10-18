"""Durable AI keyword tagging for newly registered stickers."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import time
from collections import defaultdict
from pathlib import Path

from config import Config
from src.observability import MetricsRegistry
from src.stickers.tag_queue import StickerTagJobQueue
from src.stickers.tag_store import StickerTagJobStore

logger = logging.getLogger(__name__)


class StickerTagJobError(RuntimeError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


class StickerTagService:
    def __init__(self, *, vision_service, database):
        self.vision_service = vision_service
        self.database = database
        required = Config.MONGODB_REQUIRED or Config.APP_ROLE in {"bot", "worker"}
        self.store = StickerTagJobStore(database, required=required)
        self.queue = StickerTagJobQueue(store=self.store)
        self.bot = None
        self.mongo_available = False
        self.redis_required = Config.APP_ROLE in {"bot", "worker"}
        self._limits = defaultdict(lambda: asyncio.Semaphore(2))
        self.metrics_registry = MetricsRegistry()

    async def start(self, bot):
        self.bot = bot
        self.mongo_available = bool(self.database and await self.database.ping())
        if not self.mongo_available and Config.APP_ROLE in {"bot", "worker"}:
            raise RuntimeError("MongoDB is required for sticker tagging")
        workers = 0 if Config.APP_ROLE == "bot" else Config.STICKER_TAG_QUEUE_WORKERS
        self.queue.processor = self._process_job
        await self.queue.start(Config.REDIS_URL, workers, self.redis_required)

    async def close(self):
        await self.queue.close()

    async def submit(self, *, chat_id: int, user_id: int, sticker_file_id: str, source_key: str | None = None):
        payload = {
            "job_type": "sticker_tag", "chat_id": int(chat_id), "user_id": int(user_id),
            "status_message_id": 0, "source_file_id": str(sticker_file_id),
            "source_key": str(source_key or sticker_file_id)[:256],
        }
        return await self.queue.enqueue(payload)

    async def get_metrics(self):
        metrics = await self.queue.metrics()
        metrics["service"] = "sticker_tagging"
        metrics.update(self.metrics_registry.snapshot())
        return metrics

    async def _process_job(self, payload):
        async with self._limits[payload["user_id"]]:
            await self._process_inner(payload)

    async def _process_inner(self, payload):
        path = None
        started = time.perf_counter()
        try:
            if self.bot is None or self.vision_service is None:
                raise StickerTagJobError("ai_provider_unavailable", "Sticker etiketleme servisi hazır değil.")
            telegram_file = await self.bot.get_file(payload["source_file_id"])
            os.makedirs(Config.STICKER_TAG_TEMP_DIR, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=Config.STICKER_TAG_TEMP_DIR, prefix="tag_", suffix=".webp", delete=False) as handle:
                path = handle.name
            await telegram_file.download_to_drive(path)
            prompt = "Bu sticker görselini incele. Arama için 5-10 Türkçe anahtar kelime döndür; yalnızca virgülle ayrılmış kelimeler yaz. Görselde olmayan şeyi uydurma."
            tags_raw = await asyncio.wait_for(
                self.vision_service.analyze_image(path, prompt, 0, payload["user_id"], "Arelbot", None),
                timeout=Config.STICKER_TAG_JOB_TIMEOUT_SECONDS,
            )
            tags = ", ".join(item.strip().casefold()[:48] for item in str(tags_raw or "").split(",") if item.strip())[:500]
            if not tags:
                raise StickerTagJobError("sticker_tags_empty", "Sticker etiketi üretilemedi.")
            await self.database.update_sticker_tags(payload["source_file_id"], tags)
            self.metrics_registry.inc("sticker_tag_jobs_completed")
            if self.mongo_available:
                try:
                    await self.database.add_audit_log(payload["user_id"], "sticker_tags_generated", job_id=payload["job_id"])
                except Exception:
                    logger.debug("Sticker tag audit failed", exc_info=True)
        except asyncio.TimeoutError as exc:
            self.metrics_registry.inc("sticker_tag_jobs_failed")
            raise StickerTagJobError("timeout", "Sticker etiketleme zaman aşımına uğradı.") from exc
        except StickerTagJobError:
            self.metrics_registry.inc("sticker_tag_jobs_failed")
            raise
        except Exception as exc:
            self.metrics_registry.inc("sticker_tag_jobs_failed")
            raise StickerTagJobError("sticker_tag_processing_error", "Sticker etiketlenemedi.") from exc
        finally:
            if path:
                Path(path).unlink(missing_ok=True)
            self.metrics_registry.observe("sticker_tag_job_seconds", time.perf_counter() - started)
