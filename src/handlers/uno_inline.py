"""Thin Telegram entry points for the scoped UNO inline controller."""

from __future__ import annotations

from typing import Any


async def answer_uno_inline(service: Any, update: Any, context: Any) -> bool:
    return await service.answer_query(update, context)


async def chosen_uno_inline(service: Any, update: Any, context: Any) -> bool:
    return await service.chosen_result(update, context)


__all__ = ["answer_uno_inline", "chosen_uno_inline"]
