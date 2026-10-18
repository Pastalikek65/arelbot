"""Standalone media worker for the production Compose profile."""

from __future__ import annotations

import asyncio
import logging

from config import Config
from src.media import MediaDownloadService
from src.documents import DocumentIngestionService
from src.attachments import AttachmentProcessingService
from src.quotes.service import QuoteProcessingService
from src.quotes.render_service import QuoteRenderService
from src.speech import SpeechSynthesisService
from src.database import Database
from src.key_manager import KeyManager
from src.chat_engine import ChatEngine
from src.services.vision_service import VisionService
from src.services.stt_service import STTService
from src.services.tts_service import TTSService
from src.stickers.render_service import StickerRenderService
from src.stickers.tag_service import StickerTagService
from src.market_chart import MarketChartProcessingService
from src.services.market_chart_service import MarketChartService
from src.image_generation import ImageGenerationService
from src.image_router import ImageRouter
from src.telegram_request import build_bot
from src.observability import ObservabilityServer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("arel.worker")


async def run() -> None:
    Config.validate()
    service = MediaDownloadService()
    database = Database()
    chat_engine = ChatEngine(KeyManager())
    document_service = DocumentIngestionService(chat_engine.rag_engine, database)
    tts_service = TTSService(output_dir=Config.TTS_OUTPUT_DIR, max_concurrency=Config.TTS_MAX_CONCURRENCY,
                             timeout_seconds=Config.TTS_TIMEOUT_SECONDS, max_chars=Config.TTS_MAX_CHARS,
                             max_output_bytes=Config.TTS_MAX_OUTPUT_BYTES)
    sticker_render_service = StickerRenderService(temp_dir=Config.STICKER_TEMP_DIR,
                                                   max_concurrency=Config.STICKER_MAX_CONCURRENCY,
                                                   max_input_bytes=Config.STICKER_MAX_INPUT_BYTES,
                                                   timeout_seconds=Config.STICKER_RENDER_TIMEOUT_SECONDS)
    attachment_service = AttachmentProcessingService(
        vision_service=VisionService(chat_engine), stt_service=STTService(chat_engine),
        tts_service=tts_service, sticker_render_service=sticker_render_service, database=database,
    )
    quote_render_service = QuoteRenderService(output_dir=Config.QUOTE_OUTPUT_DIR,
                                               max_concurrency=Config.QUOTE_MAX_CONCURRENCY,
                                               timeout_seconds=Config.QUOTE_RENDER_TIMEOUT_SECONDS,
                                               max_messages=Config.QUOTE_MAX_MESSAGES,
                                               max_output_bytes=Config.QUOTE_MAX_OUTPUT_BYTES)
    quote_service = QuoteProcessingService(render_service=quote_render_service, database=database)
    speech_service = SpeechSynthesisService(tts_service=tts_service, database=database)
    sticker_tag_service = StickerTagService(vision_service=VisionService(chat_engine), database=database)
    market_chart_service = MarketChartService()
    market_chart_job_service = MarketChartProcessingService(renderer=market_chart_service, database=database)
    image_generation_service = ImageGenerationService(image_router=ImageRouter(KeyManager()), database=database)
    async with build_bot(Config.TELEGRAM_TOKEN) as bot:
        await service.start(bot)
        await document_service.start(bot)
        await attachment_service.start(bot)
        await quote_service.start(bot)
        await speech_service.start(bot)
        await sticker_tag_service.start(bot)
        await market_chart_job_service.start(bot)
        await image_generation_service.start(bot)
        observability = None
        if Config.OBSERVABILITY_ENABLED:
            async def worker_snapshot() -> dict:
                services = {
                    "media": service,
                    "documents": document_service,
                    "attachments": attachment_service,
                    "quotes": quote_service,
                    "speech": speech_service,
                    "sticker_tags": sticker_tag_service,
                    "market_charts": market_chart_job_service,
                    "images": image_generation_service,
                }
                result: dict[str, object] = {}
                for name, item in services.items():
                    try:
                        result[name] = await item.get_metrics()
                    except Exception:
                        result[name] = {"metrics_errors": 1}
                return result

            async def worker_health() -> dict:
                return {
                    "ok": bool(getattr(service.queue, "redis", None) or not Config.MEDIA_REDIS_REQUIRED),
                    "app_role": Config.APP_ROLE,
                    "app_env": Config.APP_ENV,
                    "redis": "healthy" if getattr(service.queue, "redis", None) else "unavailable",
                }

            observability = ObservabilityServer(
                host=Config.OBSERVABILITY_LISTEN,
                port=Config.OBSERVABILITY_WORKER_PORT,
                token=Config.OBSERVABILITY_TOKEN,
                snapshot_factory=worker_snapshot,
                health_factory=worker_health,
            )
            await observability.start()
        logger.info(
            "Arel media/document/attachment/quote/speech/sticker-tag/market-chart/image worker started"
        )
        try:
            await asyncio.Event().wait()
        finally:
            if observability is not None:
                await observability.close()
            await attachment_service.close()
            await quote_service.close()
            await speech_service.close()
            await sticker_tag_service.close()
            await market_chart_job_service.close()
            await image_generation_service.close()
            await document_service.close()
            await service.close()
            await tts_service.close()
            await chat_engine.close()


if __name__ == "__main__":
    asyncio.run(run())
