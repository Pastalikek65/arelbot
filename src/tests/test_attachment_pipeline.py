from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from config import Config
from src.attachments.job_queue import AttachmentJobQueue
from src.attachments.service import AttachmentJobError, AttachmentProcessingService
from src.observability import MetricsRegistry


class FakeStore:
    def __init__(self):
        self.rows = {}
        self.dead = []

    async def create(self, row):
        self.rows[row["job_id"]] = dict(row)

    async def update(self, job_id, **changes):
        self.rows.setdefault(job_id, {"job_id": job_id}).update(changes)

    async def get(self, job_id):
        return dict(self.rows[job_id]) if job_id in self.rows else None

    async def delete(self, job_id):
        self.rows.pop(job_id, None)

    async def dead_letter(self, job_id, code, payload):
        self.dead.append((job_id, code))


@pytest.mark.asyncio
async def test_attachment_queue_deduplicates_same_source():
    store = FakeStore()
    queue = AttachmentJobQueue(store=store)
    first = {"chat_id": 1, "user_id": 2, "source_key": "file-1"}
    job_id, duplicate = await queue.enqueue(first)
    second = {"chat_id": 1, "user_id": 2, "source_key": "file-1"}
    same, duplicate = await queue.enqueue(second)
    assert job_id == same
    assert duplicate is True
    assert await queue.get_job(job_id)


class FakeTelegramFile:
    async def download_to_drive(self, path):
        Path(path).write_bytes(b"fixture")


class FakeBot:
    def __init__(self):
        self.edits = []
        self.sent = []

    async def get_file(self, file_id):
        return FakeTelegramFile()

    async def edit_message_text(self, **kwargs):
        self.edits.append(kwargs)

    async def send_message(self, **kwargs):
        self.sent.append(("message", kwargs))


class FakeVision:
    async def analyze_image(self, *args):
        assert Path(args[0]).exists()
        return "<script>alert(1)</script>hazır"


@pytest.mark.asyncio
async def test_attachment_worker_cleans_temp_and_sends_sanitized_vision(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "ATTACHMENT_TEMP_DIR", str(tmp_path))
    service = AttachmentProcessingService(vision_service=FakeVision(), database=None)
    service.queue = AttachmentJobQueue(store=None)
    service.bot = FakeBot()
    service.metrics_registry = MetricsRegistry()
    payload = {
        "job_id": "job-1", "job_type": "vision", "chat_id": 10, "user_id": 20,
        "status_message_id": 30, "source_file_id": "f", "source_key": "k",
        "filename": "x.jpg", "caption": "anla", "user_name": "Ada", "username": "ada",
    }
    await service._process_inner(payload)
    assert list(tmp_path.iterdir()) == []
    assert service.bot.sent[0][0] == "message"
    assert "<script>" not in service.bot.sent[0][1]["text"]
    assert service.metrics_registry.snapshot()["counters"]["attachment_jobs_completed"] == 1


def test_attachment_validation_rejects_oversized_input(monkeypatch):
    monkeypatch.setattr(Config, "ATTACHMENT_MAX_FILE_BYTES", 10)
    with pytest.raises(AttachmentJobError) as error:
        AttachmentProcessingService.validate("voice", 11)
    assert error.value.code == "attachment_too_large"
