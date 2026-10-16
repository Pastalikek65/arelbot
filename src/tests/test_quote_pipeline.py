from pathlib import Path
from types import SimpleNamespace

import pytest

from src.quotes.job_queue import QuoteJobQueue
from src.quotes.service import QuoteProcessingService


class Store:
    def __init__(self): self.rows = {}
    async def create(self, row): self.rows[row["job_id"]] = dict(row)
    async def update(self, job_id, **changes): self.rows.setdefault(job_id, {}).update(changes)
    async def get(self, job_id): return self.rows.get(job_id)
    async def delete(self, job_id): self.rows.pop(job_id, None)


@pytest.mark.asyncio
async def test_quote_queue_deduplicates_payload():
    queue = QuoteJobQueue(store=Store())
    payload = {"chat_id": 1, "user_id": 2, "source_key": "same", "messages": []}
    first, _ = await queue.enqueue(payload)
    second, duplicate = await queue.enqueue({"chat_id": 1, "user_id": 2, "source_key": "same", "messages": []})
    assert first == second
    assert duplicate


class Render:
    async def render(self, messages, options):
        path = Path("temp_quote_test.png")
        path.write_bytes(b"png")
        return SimpleNamespace(path=path)


@pytest.mark.asyncio
async def test_quote_service_enqueues_without_rendering_in_submit(monkeypatch):
    service = QuoteProcessingService(render_service=Render(), database=None)
    service.queue = QuoteJobQueue(store=None)
    job_id, duplicate = await service.submit(
        chat_id=1, user_id=2, status_message_id=3,
        messages=[{"text": "merhaba", "name": "A", "user_id": 2}],
        options={"as_file": True},
    )
    assert job_id and not duplicate
    assert service.queue.local_queue.qsize() == 1
