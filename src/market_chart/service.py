"""Durable market chart rendering and Telegram delivery."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections import defaultdict
from io import BytesIO

from config import Config
from src.market_chart.job_queue import MarketChartJobQueue
from src.market_chart.job_store import MarketChartJobStore
from src.observability import MetricsRegistry
from src.jobs.delivery_ledger import JobDeliveryLedger
from src.security.output_sanitizer import sanitize_html

logger = logging.getLogger(__name__)


class MarketChartJobError(RuntimeError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


class MarketChartProcessingService:
    def __init__(self, *, renderer, database):
        self.renderer = renderer
        self.database = database
        required = Config.MONGODB_REQUIRED or Config.APP_ROLE in {"bot", "worker"}
        self.store = MarketChartJobStore(database, required=required)
        self.queue = MarketChartJobQueue(store=self.store)
        self.bot = None
        self.mongo_available = False
        self.redis_required = Config.APP_ROLE in {"bot", "worker"}
        self._limits = defaultdict(lambda: asyncio.Semaphore(2))
        self.metrics_registry = MetricsRegistry()
        self.delivery_ledger = JobDeliveryLedger(database, required=required)

    async def start(self, bot):
        self.bot = bot
        self.mongo_available = bool(self.database and await self.database.ping())
        if not self.mongo_available and Config.APP_ROLE in {"bot", "worker"}:
            raise RuntimeError("MongoDB is required for market chart processing")
        await self.delivery_ledger.ensure_indexes()
        workers = 0 if Config.APP_ROLE == "bot" else Config.MARKET_CHART_QUEUE_WORKERS
        self.queue.processor = self._process_job
        await self.queue.start(Config.REDIS_URL, workers, self.redis_required)

    async def close(self):
        await self.queue.close()

    async def submit(
        self,
        *,
        chat_id: int,
        user_id: int,
        status_message_id: int,
        data: dict,
        caption: str,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
    ):
        rows = []
        for row in list(data.get("rows") or [])[:168]:
            if not isinstance(row, dict) or "close" not in row:
                continue
            rows.append({key: row[key] for key in ("timestamp", "close", "open", "high", "low") if key in row})
        if len(rows) < 2:
            raise MarketChartJobError("market_chart_rows_invalid", "Grafik iÃ§in yeterli piyasa verisi yok.")
        bounded_data = {"symbol": str(data.get("symbol") or "MARKET")[:24], "rows": rows}
        bounded_caption = str(caption or "")[:3500]
        source_key = hashlib.sha256(json.dumps(bounded_data, sort_keys=True, default=str).encode()).hexdigest()
        payload = {
            "job_type": "market_chart", "chat_id": int(chat_id), "user_id": int(user_id),
            "status_message_id": int(status_message_id), "source_key": source_key,
            "data": bounded_data, "caption": bounded_caption,
            "reply_to_message_id": reply_to_message_id,
            "message_thread_id": int(message_thread_id) if message_thread_id is not None else None,
        }
        return await self.queue.enqueue(payload)

    async def get_metrics(self):
        metrics = await self.queue.metrics()
        metrics["service"] = "market_chart"
        metrics.update(self.metrics_registry.snapshot())
        return metrics

    async def _process_job(self, payload):
        async with self._limits[payload["user_id"]]:
            await self._process_inner(payload)

    async def _process_inner(self, payload):
        started = time.perf_counter()
        try:
            if self.bot is None:
                raise MarketChartJobError("telegram_unavailable", "Telegram istemcisi hazÄ±r deÄŸil.")
            await self._edit(payload, "ğŸ“ˆ Piyasa grafiÄŸi hazÄ±rlanÄ±yorâ€¦")
            output = await asyncio.wait_for(self.renderer.render(payload["data"]), timeout=Config.MARKET_CHART_JOB_TIMEOUT_SECONDS)
            if output is None:
                await self._edit(payload, sanitize_html(payload["caption"]))
                payload["output_type"] = "telegram_html"
            else:
                output.seek(0)
                await self._deliver(payload, output)
                payload["output_type"] = "telegram_photo"
            self.metrics_registry.inc("market_chart_jobs_completed")
        except asyncio.TimeoutError as exc:
            self.metrics_registry.inc("market_chart_jobs_failed")
            await self._edit(payload, f"â±ï¸ Grafik zaman aÅŸÄ±mÄ±na uÄŸradÄ±. Ä°ÅŸ: <code>{payload['job_id']}</code>")
            raise MarketChartJobError("timeout", "Grafik oluÅŸturma zaman aÅŸÄ±mÄ±na uÄŸradÄ±.") from exc
        except MarketChartJobError:
            self.metrics_registry.inc("market_chart_jobs_failed")
            raise
        except Exception as exc:
            self.metrics_registry.inc("market_chart_jobs_failed")
            await self._edit(payload, f"âŒ Grafik oluÅŸturulamadÄ±. Ä°ÅŸ: <code>{payload['job_id']}</code>")
            raise MarketChartJobError("chart_processing_error", "Grafik oluÅŸturulamadÄ±.") from exc
        finally:
            self.metrics_registry.observe("market_chart_job_seconds", time.perf_counter() - started)

    async def _edit(self, payload, text):
        try:
            await self.bot.edit_message_text(chat_id=payload["chat_id"], message_id=payload["status_message_id"], text=text, parse_mode="HTML")
        except Exception:
            logger.debug("Market chart status update failed", exc_info=True)

    async def _deliver(self, payload, output):
        key = f"market_chart:{payload['job_id']}:photo"
        if not await self.delivery_ledger.claim(key, job_id=payload["job_id"], chat_id=payload["chat_id"]):
            return False
        if not await self.delivery_ledger.begin_send(key):
            return False
        try:
            result = await self.bot.send_photo(
                chat_id=payload["chat_id"], photo=output,
                caption=sanitize_html(payload["caption"]), parse_mode="HTML",
                reply_to_message_id=payload.get("reply_to_message_id"),
                message_thread_id=payload.get("message_thread_id"),
            )
            await self.delivery_ledger.mark_sent(key, getattr(result, "message_id", None))
            return True
        except Exception as exc:
            await self.delivery_ledger.mark_uncertain(key, type(exc).__name__)
            raise
