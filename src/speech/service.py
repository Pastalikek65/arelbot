from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import tempfile
from collections import defaultdict
from pathlib import Path

from config import Config
from src.observability import MetricsRegistry
from src.jobs.delivery_ledger import JobDeliveryLedger
from src.speech.job_queue import SpeechJobQueue
from src.speech.job_store import SpeechJobStore

logger = logging.getLogger(__name__)


class SpeechJobError(RuntimeError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


class SpeechSynthesisService:
    def __init__(self, *, tts_service, database):
        self.tts_service = tts_service
        self.database = database
        required = Config.MONGODB_REQUIRED or Config.APP_ROLE in {"bot", "worker"}
        self.store = SpeechJobStore(database, required=required)
        self.queue = SpeechJobQueue(store=self.store)
        self.bot = None
        self.mongo_available = False
        self.redis_required = Config.APP_ROLE in {"bot", "worker"}
        self._user_limits = defaultdict(lambda: asyncio.Semaphore(2))
        self.metrics_registry = MetricsRegistry()
        self.delivery_ledger = JobDeliveryLedger(database, required=required)

    async def start(self, bot):
        self.bot = bot
        self.mongo_available = bool(self.database and await self.database.ping())
        if not self.mongo_available and Config.APP_ROLE in {"bot", "worker"}:
            raise RuntimeError("MongoDB is required for speech processing")
        await self.delivery_ledger.ensure_indexes()
        workers = 0 if Config.APP_ROLE == "bot" else Config.SPEECH_QUEUE_WORKERS
        self.queue.processor = self._process_job
        await self.queue.start(Config.REDIS_URL, workers, self.redis_required)

    async def close(self):
        await self.queue.close()

    async def submit(self, *, chat_id, user_id, status_message_id, text, language="tr", reply_to_message_id=None):
        bounded = str(text or "").strip()[:Config.TTS_MAX_CHARS]
        if not bounded:
            raise SpeechJobError("speech_empty", "Seslendirilecek metin bulunamadı.")
        source_key = hashlib.sha256(f"{chat_id}:{user_id}:{language}:{bounded}".encode("utf-8")).hexdigest()
        payload = {
            "job_type": "speech", "chat_id": int(chat_id), "user_id": int(user_id),
            "status_message_id": int(status_message_id), "source_key": source_key,
            "text": bounded, "language": str(language or "tr")[:12],
            "reply_to_message_id": reply_to_message_id,
        }
        return await self.queue.enqueue(payload)

    async def get_metrics(self):
        metrics = await self.queue.metrics()
        metrics["service"] = "speech_synthesis"
        metrics.update(self.metrics_registry.snapshot())
        return metrics

    async def _process_job(self, payload):
        async with self._user_limits[payload["user_id"]]:
            path = None
            try:
                if self.bot is None or self.tts_service is None:
                    raise SpeechJobError("tts_unavailable", "Seslendirme servisi hazır değil.")
                await self._edit(payload, "🎙️ Metin seslendiriliyor…")
                os.makedirs(Config.TTS_OUTPUT_DIR, exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=Config.TTS_OUTPUT_DIR, prefix="speech_", suffix=".mp3", delete=False) as handle:
                    path = handle.name
                path = await asyncio.wait_for(self.tts_service.synthesize(payload["text"], language=payload["language"], output_path=path), timeout=Config.SPEECH_JOB_TIMEOUT_SECONDS)
                with open(path, "rb") as voice_file:
                    await self._deliver(payload, lambda: self.bot.send_voice(chat_id=payload["chat_id"], voice=voice_file, caption="🗣️ Seslendirildi.", reply_to_message_id=payload.get("reply_to_message_id")))
                payload["output_type"] = "telegram_voice"
                self.metrics_registry.inc("speech_jobs_completed")
            except asyncio.TimeoutError as exc:
                await self._edit(payload, f"⏱️ Seslendirme zaman aşımına uğradı. İş: <code>{payload['job_id']}</code>")
                self.metrics_registry.inc("speech_jobs_failed")
                raise SpeechJobError("timeout", "Seslendirme zaman aşımına uğradı.") from exc
            except SpeechJobError as exc:
                await self._edit(payload, f"❌ {exc} İş: <code>{payload['job_id']}</code>")
                self.metrics_registry.inc("speech_jobs_failed")
                raise
            except Exception as exc:
                await self._edit(payload, f"❌ Seslendirme başarısız. İş: <code>{payload['job_id']}</code>")
                self.metrics_registry.inc("speech_jobs_failed")
                raise SpeechJobError("speech_processing_error", "Seslendirme başarısız.") from exc
            finally:
                if path:
                    Path(path).unlink(missing_ok=True)

    async def _edit(self, payload, text):
        try:
            await self.bot.edit_message_text(chat_id=payload["chat_id"], message_id=payload["status_message_id"], text=text, parse_mode="HTML")
        except Exception:
            logger.debug("Speech status update failed", exc_info=True)

    async def _deliver(self, payload, sender):
        key = f"speech:{payload['job_id']}:voice"
        if not await self.delivery_ledger.claim(key, job_id=payload["job_id"], chat_id=payload["chat_id"]):
            return False
        if not await self.delivery_ledger.begin_send(key):
            return False
        try:
            result = await sender()
            await self.delivery_ledger.mark_sent(key, getattr(result, "message_id", None))
            return True
        except Exception as exc:
            await self.delivery_ledger.mark_uncertain(key, type(exc).__name__)
            raise
