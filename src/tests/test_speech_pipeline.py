import pytest

from src.speech.job_queue import SpeechJobQueue
from src.speech.service import SpeechSynthesisService


class Store:
    def __init__(self): self.rows = {}
    async def create(self, row): self.rows[row["job_id"]] = dict(row)
    async def update(self, job_id, **changes): self.rows.setdefault(job_id, {}).update(changes)
    async def get(self, job_id): return self.rows.get(job_id)
    async def delete(self, job_id): self.rows.pop(job_id, None)


@pytest.mark.asyncio
async def test_speech_submit_is_durable_and_deduplicated():
    service = SpeechSynthesisService(tts_service=object(), database=None)
    service.queue = SpeechJobQueue(store=Store())
    args = dict(chat_id=1, user_id=2, status_message_id=3, text="Merhaba", language="tr")
    first, _ = await service.submit(**args)
    second, duplicate = await service.submit(**args)
    assert first == second
    assert duplicate
    assert service.queue.local_queue.qsize() == 1
