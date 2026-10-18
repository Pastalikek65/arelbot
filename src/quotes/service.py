"""Worker-backed quote rendering and Telegram delivery."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import tempfile
from collections import defaultdict
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from config import Config
from src.observability import MetricsRegistry
from src.jobs.delivery_ledger import JobDeliveryLedger
from src.quotes.job_queue import QuoteJobQueue
from src.quotes.job_store import QuoteJobStore
from src.quotes.records import build_quote_record

logger = logging.getLogger(__name__)


class QuoteJobError(RuntimeError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


class QuoteProcessingService:
    def __init__(self, *, render_service, database):
        self.render_service = render_service
        self.database = database
        required = Config.MONGODB_REQUIRED or Config.APP_ROLE in {"bot", "worker"}
        self.store = QuoteJobStore(database, required=required)
        self.queue = QuoteJobQueue(store=self.store)
        self.bot = None
        self.mongo_available = False
        self.redis_required = Config.APP_ROLE in {"bot", "worker"}
        self._user_limits = defaultdict(lambda: asyncio.Semaphore(1))
        self._chat_limits = defaultdict(lambda: asyncio.Semaphore(1))
        self.metrics_registry = MetricsRegistry()
        self.delivery_ledger = JobDeliveryLedger(database, required=required)

    async def start(self, bot):
        self.bot = bot
        self.mongo_available = bool(self.database and await self.database.ping())
        if not self.mongo_available and Config.APP_ROLE in {"bot", "worker"}:
            raise RuntimeError("MongoDB is required for quote processing")
        await self.delivery_ledger.ensure_indexes()
        workers = 0 if Config.APP_ROLE == "bot" else Config.QUOTE_QUEUE_WORKERS
        self.queue.processor = self._process_job
        await self.queue.start(Config.REDIS_URL, workers, self.redis_required)

    async def close(self):
        await self.queue.close()

    async def submit(self, *, chat_id, user_id, status_message_id, messages, options, reply_to_message_id=None):
        bounded_messages = []
        for item in list(messages or [])[:10]:
            bounded_messages.append({
                "text": str(item.get("text") or "[Medya]")[:4000],
                "name": str(item.get("name") or "Anonim")[:128],
                "user_id": int(item.get("user_id") or 0),
                "message_id": item.get("message_id"),
                "entities": list(item.get("entities") or [])[:32],
            })
        if not bounded_messages:
            raise QuoteJobError("quote_empty", "Alıntılanacak mesaj bulunamadı.")
        safe_options = dict(options or {})
        source_key = hashlib.sha256(json.dumps({"messages": bounded_messages, "options": safe_options}, sort_keys=True, default=str).encode()).hexdigest()
        payload = {
            "job_type": "quote", "chat_id": int(chat_id), "user_id": int(user_id),
            "status_message_id": int(status_message_id), "source_key": source_key,
            "messages": bounded_messages, "options": safe_options,
            "reply_to_message_id": reply_to_message_id,
        }
        return await self.queue.enqueue(payload)

    async def get_status(self, job_id):
        return await self.queue.get_job(job_id)

    async def get_metrics(self):
        metrics = await self.queue.metrics()
        metrics["service"] = "quote_processing"
        metrics.update(self.metrics_registry.snapshot())
        return metrics

    async def _process_job(self, payload):
        async with self._user_limits[payload["user_id"]], self._chat_limits[payload["chat_id"]]:
            await self._process_inner(payload)

    async def _process_inner(self, payload):
        artifact_path = None
        avatar_paths = []
        try:
            if self.bot is None:
                raise QuoteJobError("telegram_unavailable", "Telegram istemcisi hazır değil.")
            await self._edit(payload, "🖼️ Alıntı görseli hazırlanıyor…")
            messages = payload["messages"]
            for item in messages:
                uid = int(item.get("user_id") or 0)
                if uid <= 0:
                    continue
                try:
                    photos = await self.bot.get_user_profile_photos(uid, limit=1)
                    if photos.total_count:
                        source = photos.photos[0][-1]
                        path = Path(Config.QUOTE_OUTPUT_DIR) / f"avatar_{uid}.jpg"
                        path.parent.mkdir(parents=True, exist_ok=True)
                        file = await self.bot.get_file(source.file_id)
                        await file.download_to_drive(str(path))
                        item["avatar_path"] = str(path)
                        avatar_paths.append(path)
                except Exception:
                    logger.debug("Quote avatar lookup failed", exc_info=True)
            artifact = await asyncio.wait_for(self.render_service.render(messages, payload["options"]), timeout=Config.QUOTE_JOB_TIMEOUT_SECONDS)
            artifact_path = str(artifact.path)
            options = payload["options"]
            if options.get("as_file") or options.get("is_png"):
                with open(artifact_path, "rb") as quote_file:
                    await self._deliver(payload, "document", lambda: self.bot.send_document(chat_id=payload["chat_id"], document=quote_file, filename=f"quote.{artifact.path.suffix.lstrip('.')}", reply_to_message_id=payload.get("reply_to_message_id")))
                payload["output_type"] = "telegram_document"
            else:
                keyboard = None
                if options.get("rate_enabled"):
                    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("👍", callback_data=f"qrate_up_{messages[0]['user_id']}"), InlineKeyboardButton("👎", callback_data=f"qrate_down_{messages[0]['user_id']}")]])
                with open(artifact_path, "rb") as quote_file:
                    result = await self._deliver(payload, "sticker", lambda: self.bot.send_sticker(chat_id=payload["chat_id"], sticker=quote_file, reply_markup=keyboard, reply_to_message_id=payload.get("reply_to_message_id")))
                payload["output_type"] = "telegram_sticker"
                payload["sticker_file_id"] = getattr(getattr(result, "sticker", None), "file_id", None)
            if self.database:
                record = build_quote_record(chat_id=payload["chat_id"], messages=messages, options=options, sticker_file_id=payload.get("sticker_file_id"))
                await self.database.save_quote(record)
            self.metrics_registry.inc("quote_jobs_completed")
        except QuoteJobError as exc:
            self.metrics_registry.inc("quote_jobs_failed")
            await self._edit(payload, f"❌ {exc} İş: <code>{payload['job_id']}</code>")
            raise
        except asyncio.TimeoutError as exc:
            self.metrics_registry.inc("quote_jobs_failed")
            await self._edit(payload, f"⏱️ Quote işlemi zaman aşımına uğradı. İş: <code>{payload['job_id']}</code>")
            raise QuoteJobError("timeout", "Alıntı oluşturma zaman aşımına uğradı.") from exc
        except Exception as exc:
            self.metrics_registry.inc("quote_jobs_failed")
            await self._edit(payload, f"❌ Alıntı oluşturulamadı. İş: <code>{payload['job_id']}</code>")
            raise QuoteJobError("quote_processing_error", "Alıntı oluşturulamadı.") from exc
        finally:
            if artifact_path:
                Path(artifact_path).unlink(missing_ok=True)
            for path in avatar_paths:
                path.unlink(missing_ok=True)

    async def _edit(self, payload, text):
        try:
            await self.bot.edit_message_text(chat_id=payload["chat_id"], message_id=payload["status_message_id"], text=text, parse_mode="HTML")
        except Exception:
            logger.debug("Quote status update failed", exc_info=True)

    async def _deliver(self, payload, output_type, sender):
        key = f"quote:{payload['job_id']}:{output_type}"
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
