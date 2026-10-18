"""Bounded AI usage, latency and configurable cost telemetry."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class UsageTracker:
    """Keep a fast process snapshot and optionally persist aggregate events.

    Only provider/task/usage numbers are stored. Prompts, completions, URLs,
    keys and user content are intentionally outside this contract.
    ``rates`` uses USD per one million input/output tokens.
    """

    def __init__(self, database: Any | None = None, rates: dict[str, Any] | None = None) -> None:
        self.database = database
        self.rates = dict(rates or {})
        self._lock = asyncio.Lock()
        self._providers: dict[str, dict[str, Any]] = defaultdict(lambda: {
            "requests": 0, "successes": 0, "failures": 0,
            "total_latency_ms": 0.0, "input_tokens": 0, "output_tokens": 0,
            "cost_usd": 0.0, "last_success_at": None,
        })

    def _cost(self, provider: str, input_tokens: int, output_tokens: int) -> float:
        config = self.rates.get(provider) or self.rates.get("*") or {}
        if not isinstance(config, dict):
            return 0.0
        input_rate = config.get("input", config.get("input_usd_per_1m", 0))
        output_rate = config.get("output", config.get("output_usd_per_1m", 0))
        try:
            return max(0.0, (int(input_tokens) * float(input_rate) + int(output_tokens) * float(output_rate)) / 1_000_000)
        except (TypeError, ValueError):
            return 0.0

    async def record(
        self,
        provider: str,
        *,
        success: bool,
        latency_ms: float,
        input_tokens: int = 0,
        output_tokens: int = 0,
        task: str = "chat",
    ) -> None:
        input_tokens = max(0, int(input_tokens))
        output_tokens = max(0, int(output_tokens))
        latency_ms = max(0.0, float(latency_ms))
        cost_usd = self._cost(str(provider), input_tokens, output_tokens)
        async with self._lock:
            item = self._providers[str(provider)]
            item["requests"] += 1
            item["successes" if success else "failures"] += 1
            item["total_latency_ms"] += latency_ms
            item["input_tokens"] += input_tokens
            item["output_tokens"] += output_tokens
            item["cost_usd"] += cost_usd
            if success:
                item["last_success_at"] = time.time()

        persist = getattr(self.database, "record_ai_usage", None)
        available = getattr(self.database, "available", True)
        if persist is not None and available is not False:
            try:
                await asyncio.wait_for(
                    persist({
                        "provider": str(provider)[:80],
                        "task": str(task)[:80],
                        "success": bool(success),
                        "latency_ms": round(latency_ms, 3),
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "cost_usd": round(cost_usd, 10),
                        "created_at": datetime.now(timezone.utc),
                    }),
                    timeout=0.75,
                )
            except Exception:
                logger.debug("AI usage persistence failed", exc_info=True)

    async def snapshot(self) -> dict[str, dict[str, Any]]:
        async with self._lock:
            result = {}
            for name, item in self._providers.items():
                copy = dict(item)
                copy["avg_latency_ms"] = (
                    copy["total_latency_ms"] / copy["requests"] if copy["requests"] else 0.0
                )
                result[name] = copy
            return result
