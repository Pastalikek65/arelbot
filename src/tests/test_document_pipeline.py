from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from config import Config
from src.documents.job_queue import DocumentJobQueue
from src.documents.job_store import DocumentJobStore
from src.documents.service import DocumentIngestionService, DocumentJobError


@pytest.mark.asyncio
async def test_document_queue_deduplicates_and_recovers_inflight_job():
    store = DocumentJobStore(None)
    await store.create({
        "job_id": "recover-doc",
        "chat_id": -100,
        "user_id": 7,
        "source_key": "telegram-file-1",
        "status": "ingesting",
    })
    queue = DocumentJobQueue(store=store)
    completed = asyncio.Event()

    async def processor(payload):
        assert payload["job_id"] == "recover-doc"
        completed.set()

    queue.processor = processor
    await queue.start("redis://unavailable", workers=1, force_memory=True)
    try:
        await asyncio.wait_for(completed.wait(), timeout=2)
        first, duplicate = await queue.enqueue({
            "chat_id": -100,
            "user_id": 7,
            "source_key": "new-file",
            "source_file_id": "opaque",
        })
        second, duplicate_again = await queue.enqueue({
            "chat_id": -100,
            "user_id": 7,
            "source_key": "new-file",
            "source_file_id": "opaque",
        })
        assert duplicate is False
        assert duplicate_again is True
        assert first == second
        while (await store.get("recover-doc") or {}).get("status") != "completed":
            await asyncio.sleep(0.01)
    finally:
        await queue.close()


@pytest.mark.asyncio
async def test_document_queue_does_not_ack_without_processor():
    store = DocumentJobStore(None)
    queue = DocumentJobQueue(store=store)
    await queue.start("redis://unavailable", workers=1, force_memory=True)
    try:
        job_id, duplicate = await queue.enqueue({
            "chat_id": -100,
            "user_id": 7,
            "source_key": "missing-processor",
            "source_file_id": "opaque",
        })
        assert duplicate is False
        for _ in range(100):
            if (await queue.get_job(job_id) or {}).get("status") == "failed":
                break
            await asyncio.sleep(0.01)
        job = await queue.get_job(job_id)
        assert job["status"] == "failed"
        assert job["error_code"] == "queue_processor_unconfigured"
    finally:
        await queue.close()


@pytest.mark.asyncio
async def test_document_service_downloads_in_worker_and_removes_temp_file(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "RAG_UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(Config, "DOCUMENT_JOB_TIMEOUT_SECONDS", 5)

    class FakeDB:
        async def ping(self):
            return False

    class FakeRag:
        async def ingest_file(self, path, *, user_id, mime_type):
            assert Path(path).exists()
            assert user_id == 11
            return "memo.txt hafızaya alındı; 1 parça hazırlandı (DOCUMENT_INGESTED)."

    class FakeTelegramFile:
        async def download_to_drive(self, path):
            Path(path).write_text("Merhaba", encoding="utf-8")

    class FakeBot:
        def __init__(self):
            self.edits = []

        async def get_file(self, file_id):
            assert file_id == "opaque-file-id"
            return FakeTelegramFile()

        async def edit_message_text(self, **kwargs):
            self.edits.append(kwargs["text"])

    service = DocumentIngestionService(FakeRag(), FakeDB())
    service.bot = FakeBot()
    service.queue.processor = service._process_job
    service.queue.tasks = [asyncio.create_task(service.queue._worker(0))]
    try:
        job_id, duplicate = await service.submit(
            chat_id=-100,
            user_id=11,
            status_message_id=9,
            source_file_id="opaque-file-id",
            source_key="unique-file-id",
            filename="memo.txt",
            mime_type="text/plain",
            file_size=10,
        )
        assert duplicate is False
        for _ in range(100):
            if (await service.queue.get_job(job_id) or {}).get("status") == "completed":
                break
            await asyncio.sleep(0.01)
        assert (await service.queue.get_job(job_id))["status"] == "completed"
        assert not list(tmp_path.iterdir())
        assert any("hazırlandı" in value for value in service.bot.edits)
        metrics = await service.get_metrics()
        assert metrics["counters"]["document_jobs_completed"] == 1
        assert metrics["latency"]["document_job_seconds"]["count"] == 1
    finally:
        await service.queue.close()


def test_document_validation_rejects_unsafe_or_oversized_upload(monkeypatch):
    monkeypatch.setattr(Config, "RAG_MAX_FILE_BYTES", 10)
    with pytest.raises(DocumentJobError) as unsupported:
        DocumentIngestionService.validate_document("archive.exe", "application/octet-stream", 1)
    assert unsupported.value.code == "document_type_unsupported"
    with pytest.raises(DocumentJobError) as oversized:
        DocumentIngestionService.validate_document("memo.txt", "text/plain", 11)
    assert oversized.value.code == "document_too_large"
