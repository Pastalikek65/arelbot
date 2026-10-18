from pathlib import Path

import pytest

from src.stickers.tag_queue import StickerTagJobQueue
from src.stickers.tag_service import StickerTagService


class Store:
    def __init__(self): self.rows = {}
    async def create(self, row): self.rows[row["job_id"]] = dict(row)
    async def update(self, job_id, **changes): self.rows.setdefault(job_id, {}).update(changes)
    async def get(self, job_id): return self.rows.get(job_id)
    async def delete(self, job_id): self.rows.pop(job_id, None)
    async def dead_letter(self, *args): pass


@pytest.mark.asyncio
async def test_sticker_tag_queue_deduplicates():
    queue = StickerTagJobQueue(store=Store())
    first, _ = await queue.enqueue({"chat_id": 1, "user_id": 2, "source_key": "sticker-1"})
    second, duplicate = await queue.enqueue({"chat_id": 1, "user_id": 2, "source_key": "sticker-1"})
    assert first == second
    assert duplicate


class FakeFile:
    async def download_to_drive(self, path): Path(path).write_bytes(b"sticker")


class FakeBot:
    async def get_file(self, file_id): return FakeFile()


class FakeVision:
    async def analyze_image(self, *args): return "kedi, komik, turuncu"


class FakeDatabase:
    async def ping(self): return False
    async def update_sticker_tags(self, file_id, tags): self.tags = (file_id, tags)


@pytest.mark.asyncio
async def test_sticker_tag_worker_cleans_spool_and_updates_tags(tmp_path, monkeypatch):
    from config import Config
    monkeypatch.setattr(Config, "STICKER_TAG_TEMP_DIR", str(tmp_path))
    db = FakeDatabase()
    service = StickerTagService(vision_service=FakeVision(), database=db)
    service.queue = StickerTagJobQueue(store=None)
    service.bot = FakeBot()
    payload = {"job_id": "tag-1", "user_id": 2, "source_file_id": "file-1"}
    await service._process_inner(payload)
    assert list(tmp_path.iterdir()) == []
    assert db.tags == ("file-1", "kedi, komik, turuncu")
