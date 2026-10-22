"""Telegram document download and RAG ingestion service."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from config import Config
from src.database import Database
from src.documents.job_queue import DocumentJobQueue
from src.documents.job_store import DocumentJobStore
from src.observability import MetricsRegistry

logger = logging.getLogger(__name__)


class DocumentJobError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class DocumentIngestionService:
    ALLOWED_EXTENSIONS = {".pdf", ".txt", ".docx"}
    ALLOWED_MIMES = {
        "application/pdf",
        "text/plain",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }

    def __init__(self, rag_engine: Any, database: Database | None = None):
        self.rag_engine = rag_engine
        self.database = database or Database()
        required = Config.MONGODB_REQUIRED or Config.APP_ROLE in {"bot", "worker"}
        self.job_store = DocumentJobStore(self.database, required=required)
        self.queue = DocumentJobQueue(store=self.job_store)
        self.bot = None
        self.mongo_available = False
        self.redis_required = Config.DOCUMENT_REDIS_REQUIRED or Config.APP_ROLE in {"bot", "worker"}
        self._user_limits = defaultdict(lambda: asyncio.Semaphore(2))
        self._chat_limits = defaultdict(lambda: asyncio.Semaphore(2))
        self._request_times = defaultdict(list)
        self.metrics_registry = MetricsRegistry()

    @classmethod
    def validate_document(cls, filename: str, mime_type: str | None, file_size: int | None) -> str:
        suffix = Path(str(filename or "")).suffix.casefold()
        if suffix not in cls.ALLOWED_EXTENSIONS:
            raise DocumentJobError("document_type_unsupported", "Bu belge türü desteklenmiyor.")
        if mime_type and mime_type not in cls.ALLOWED_MIMES:
            raise DocumentJobError("document_mime_unsupported", "Bu belge türü desteklenmiyor.")
        if file_size and int(file_size) > Config.RAG_MAX_FILE_BYTES:
            raise DocumentJobError("document_too_large", "Dosya boyutu izin verilen belge sınırını aşıyor.")
        return suffix

    async def start(self, bot) -> None:
        self.bot = bot
        self.mongo_available = await self.database.ping()
        if not self.mongo_available:
            self.job_store._disabled = True
            if Config.MONGODB_REQUIRED or Config.APP_ROLE in {"bot", "worker"}:
                raise RuntimeError("MongoDB is required for the document ingestion service")
        workers = 0 if Config.APP_ROLE == "bot" else Config.DOCUMENT_QUEUE_WORKERS
        self.queue.processor = self._process_job
        await self.queue.start(Config.REDIS_URL, workers, self.redis_required)

    async def close(self) -> None:
        await self.queue.close()

    async def submit(
        self,
        *,
        chat_id: int,
        user_id: int,
        status_message_id: int,
        source_file_id: str,
        source_key: str,
        filename: str,
        mime_type: str | None,
        file_size: int | None,
    ) -> tuple[str, bool]:
        suffix = self.validate_document(filename, mime_type, file_size)
        now = time.monotonic()
        requests = self._request_times[user_id]
        self._request_times[user_id] = [item for item in requests if now - item < 60]
        if len(self._request_times[user_id]) >= 10:
            raise DocumentJobError("document_rate_limited", "Belge kuyruğuna çok hızlı istek gönderildi.")
        self._request_times[user_id].append(now)
        payload = {
            "chat_id": int(chat_id),
            "user_id": int(user_id),
            "status_message_id": int(status_message_id),
            "source_file_id": str(source_file_id),
            "source_key": str(source_key)[:256],
            "filename": Path(str(filename)).name[:255],
            "extension": suffix,
            "mime_type": mime_type,
            "file_size": int(file_size or 0),
        }
        return await self.queue.enqueue(payload)

    async def get_status(self, job_id: str):
        return await self.queue.get_job(job_id)

    async def get_metrics(self) -> dict[str, Any]:
        metrics = await self.queue.metrics()
        metrics["service"] = "document_ingest"
        metrics.update(self.metrics_registry.snapshot())
        return metrics

    async def _process_job(self, payload: dict[str, Any]) -> None:
        user_limit = self._user_limits[payload["user_id"]]
        chat_limit = self._chat_limits[payload["chat_id"]]
        async with user_limit, chat_limit:
            await self._process_job_inner(payload)

    async def _process_job_inner(self, payload: dict[str, Any]) -> None:
        job_id = payload["job_id"]
        chat_id = payload["chat_id"]
        status_id = payload["status_message_id"]
        path = None
        started = time.perf_counter()
        self.metrics_registry.inc("document_jobs_total")
        try:
            await self._edit_status(chat_id, status_id, "📥 Belge indiriliyor…")
            if self.bot is None:
                raise DocumentJobError("telegram_unavailable", "Telegram istemcisi hazır değil.")
            telegram_file = await self.bot.get_file(payload["source_file_id"])
            os.makedirs(Config.RAG_UPLOAD_DIR, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                dir=Config.RAG_UPLOAD_DIR,
                prefix="rag_",
                suffix=payload.get("extension", ".bin"),
                delete=False,
            ) as handle:
                path = handle.name
            await telegram_file.download_to_drive(path)
            await self.queue._set_status(payload, "ingesting")
            await self._edit_status(chat_id, status_id, "🧠 Belge okunuyor ve arama hafızasına ekleniyor…")
            result = await asyncio.wait_for(
                self.rag_engine.ingest_file(path, user_id=payload["user_id"], mime_type=payload.get("mime_type")),
                timeout=Config.DOCUMENT_JOB_TIMEOUT_SECONDS,
            )
            if "(DOCUMENT_INGESTED)" not in str(result):
                code = self._error_code(str(result))
                raise DocumentJobError(code, str(result))
            match = re.search(r";\s*(\d+)\s*parça", str(result), re.IGNORECASE)
            if match:
                payload["chunk_count"] = int(match.group(1))
            self.metrics_registry.inc("document_jobs_completed")
            if self.mongo_available:
                try:
                    await self.database.add_audit_log(
                        int(payload["user_id"]),
                        "document_ingested",
                        job_id=job_id,
                        chunk_count=payload.get("chunk_count", 0),
                    )
                except Exception:
                    logger.debug("Document ingestion audit write failed", exc_info=True)
            await self._edit_status(chat_id, status_id, f"✅ {result}")
        except DocumentJobError as exc:
            self.metrics_registry.inc("document_jobs_failed")
            await self._edit_status(chat_id, status_id, f"❌ {exc}\nİş: <code>{job_id}</code>")
            raise
        except asyncio.TimeoutError as exc:
            self.metrics_registry.inc("document_jobs_failed")
            await self._edit_status(chat_id, status_id, f"⏱️ Belge işleme zaman aşımına uğradı. İş: <code>{job_id}</code>")
            raise DocumentJobError("timeout", "Belge işleme zaman aşımına uğradı.") from exc
        except Exception as exc:
            self.metrics_registry.inc("document_jobs_failed")
            await self._edit_status(chat_id, status_id, f"❌ Belge işlenemedi (DOCUMENT_PROCESSING_ERROR). İş: <code>{job_id}</code>")
            raise DocumentJobError("telegram_download_failed", "Belge indirilemedi.") from exc
        finally:
            if path:
                try:
                    Path(path).unlink(missing_ok=True)
                except OSError:
                    logger.warning("Document temp cleanup failed for job %s", job_id)
            self.metrics_registry.observe("document_job_seconds", time.perf_counter() - started)

    @staticmethod
    def _error_code(result: str) -> str:
        match = re.search(r"\((DOCUMENT_[A-Z0-9_]+)\)", result)
        return match.group(1).casefold() if match else "document_processing_error"

    async def _edit_status(self, chat_id: int, message_id: int, text: str) -> None:
        if self.bot is None:
            return
        try:
            await self.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, parse_mode="HTML")
        except Exception:
            logger.debug("Document status update failed", exc_info=True)
