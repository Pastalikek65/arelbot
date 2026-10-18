from __future__ import annotations

import pytest

from config import Config
from src.attachments.service import AttachmentProcessingService
from src.documents.service import DocumentIngestionService
from src.market_chart.service import MarketChartProcessingService
from src.quotes.service import QuoteProcessingService
from src.speech.service import SpeechSynthesisService
from src.stickers.tag_service import StickerTagService


class OfflineDatabase:
    async def ping(self):
        return False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "factory",
    [
        lambda: AttachmentProcessingService(database=None),
        lambda: DocumentIngestionService(object(), OfflineDatabase()),
        lambda: QuoteProcessingService(render_service=object(), database=None),
        lambda: SpeechSynthesisService(tts_service=object(), database=None),
        lambda: MarketChartProcessingService(renderer=object(), database=None),
        lambda: StickerTagService(vision_service=object(), database=None),
    ],
)
async def test_worker_services_bind_processor_before_start(factory, monkeypatch):
    """A started worker must never acknowledge jobs without processing them."""

    monkeypatch.setattr(Config, "APP_ROLE", "test")
    monkeypatch.setattr(Config, "MONGODB_REQUIRED", False)
    service = factory()
    calls = []

    async def fake_start(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(service.queue, "start", fake_start)
    await service.start(object())

    assert service.queue.processor == service._process_job
    assert calls
