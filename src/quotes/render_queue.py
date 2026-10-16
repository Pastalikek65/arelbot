"""Bounded off-thread renderer for quote/sticker jobs."""

from __future__ import annotations

import asyncio
from typing import Any


class QuoteRenderQueue:
    def __init__(self, workers: int = 2):
        self._semaphore = asyncio.Semaphore(max(1, workers))

    async def render(self, manager: Any, messages: list[dict], output_path: str, **options) -> str:
        async with self._semaphore:
            return await asyncio.to_thread(manager.generate_quote, messages, output_path, **options)

    async def close(self) -> None:
        return None
