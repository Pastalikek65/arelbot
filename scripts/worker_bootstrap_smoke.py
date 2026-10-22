"""Start and stop both production worker queues without consuming a job."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Config
from src.database import Database
from src.documents import DocumentIngestionService
from src.attachments import AttachmentProcessingService
from src.key_manager import KeyManager
from src.media import MediaDownloadService
from src.rag_engine import RAGEngine
from src.services.vision_service import VisionService
from src.services.stt_service import STTService
from src.services.tts_service import TTSService
from src.stickers.render_service import StickerRenderService
from src.chat_engine import ChatEngine
from src.quotes.service import QuoteProcessingService
from src.quotes.render_service import QuoteRenderService
from src.speech import SpeechSynthesisService
from src.stickers.tag_service import StickerTagService
from src.market_chart import MarketChartProcessingService
from src.services.market_chart_service import MarketChartService


class ProbeBot:
    """Queue bootstrap only needs a bot object; no Telegram call is made."""


async def main() -> None:
    Config.validate()
    database = Database()
    chat_engine = ChatEngine(KeyManager())
    rag = chat_engine.rag_engine
    media = MediaDownloadService()
    documents = DocumentIngestionService(rag, database)
    tts = TTSService(output_dir=Config.TTS_OUTPUT_DIR)
    stickers = StickerRenderService(temp_dir=Config.STICKER_TEMP_DIR)
    attachments = AttachmentProcessingService(
        vision_service=VisionService(chat_engine), stt_service=STTService(chat_engine),
        tts_service=tts, sticker_render_service=stickers, database=database,
    )
    quote_render = QuoteRenderService(output_dir=Config.QUOTE_OUTPUT_DIR)
    quotes = QuoteProcessingService(render_service=quote_render, database=database)
    speech = SpeechSynthesisService(tts_service=tts, database=database)
    sticker_tags = StickerTagService(vision_service=VisionService(chat_engine), database=database)
    chart_renderer = MarketChartService()
    charts = MarketChartProcessingService(renderer=chart_renderer, database=database)
    bot = ProbeBot()
    try:
        await media.start(bot)
        await documents.start(bot)
        await attachments.start(bot)
        await quotes.start(bot)
        await speech.start(bot)
        await sticker_tags.start(bot)
        await charts.start(bot)
        print("worker_bootstrap=ok")
    finally:
        await attachments.close()
        await quotes.close()
        await speech.close()
        await sticker_tags.close()
        await charts.close()
        await documents.close()
        await media.close()
        await tts.close()
        await chat_engine.close()
        await database.close_async()


if __name__ == "__main__":
    asyncio.run(main())
