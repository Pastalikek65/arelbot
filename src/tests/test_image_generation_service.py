from __future__ import annotations

from pathlib import Path

import pytest

from config import Config
from src.image_generation.service import ImageGenerationJobError, ImageGenerationService
from src.image_generation.job_store import ImageJobStore


class FakeDatabase:
    async def ping(self):
        return False


class FakeRouter:
    def __init__(self, path: Path):
        self.path = path

    async def generate_image(self, prompt: str, *, max_output_bytes: int):
        self.path.write_bytes(b"fake-image")
        return str(self.path)


class FakeMessage:
    message_id = 900


class FakeBot:
    def __init__(self):
        self.sent = []
        self.edits = []

    async def edit_message_text(self, **kwargs):
        self.edits.append(kwargs)

    async def send_photo(self, **kwargs):
        self.sent.append(kwargs)
        return FakeMessage()


@pytest.mark.asyncio
async def test_image_submit_deduplicates_and_bounds_prompt(monkeypatch):
    monkeypatch.setattr(Config, "APP_ROLE", "combined")
    monkeypatch.setattr(Config, "IMAGE_RATE_LIMIT_PER_MINUTE", 5)
    service = ImageGenerationService(image_router=FakeRouter(Path("unused.png")), database=FakeDatabase())
    await service.queue.start("redis://127.0.0.1:6399/0", workers=0, force_memory=True)
    first, duplicate = await service.submit(chat_id=1, user_id=2, status_message_id=3, prompt="istanbul")
    second, duplicate_again = await service.submit(chat_id=1, user_id=2, status_message_id=3, prompt="istanbul")
    assert first == second
    assert duplicate is False
    assert duplicate_again is True
    with pytest.raises(ImageGenerationJobError) as error:
        await service.submit(chat_id=1, user_id=2, status_message_id=3, prompt=" ")
    assert error.value.code == "image_prompt_invalid"
    await service.close()


@pytest.mark.asyncio
async def test_image_worker_delivers_and_cleans_temp_file(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "APP_ROLE", "combined")
    output = tmp_path / "generated.png"
    service = ImageGenerationService(image_router=FakeRouter(output), database=FakeDatabase())
    bot = FakeBot()
    service.bot = bot
    payload = {
        "job_id": "image-test",
        "chat_id": 10,
        "user_id": 20,
        "status_message_id": 30,
        "prompt": "test",
        "message_thread_id": None,
        "reply_to_message_id": None,
    }
    await service._process_inner(payload)
    assert len(bot.sent) == 1
    assert payload["output_type"] == "telegram_photo"
    assert not output.exists()
    assert service.metrics_registry.counters["image_jobs_completed"] == 1


@pytest.mark.asyncio
async def test_image_store_recovers_processing_jobs_from_memory():
    store = ImageJobStore(None)
    await store.create({"job_id": "recover-me", "status": "processing", "created_at": 1})
    rows = await store.recoverable()
    assert [row["job_id"] for row in rows] == ["recover-me"]
