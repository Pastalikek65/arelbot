"""Typed, bounded daily digest assembly for personal Telegram delivery."""

from __future__ import annotations

import asyncio
import html
from typing import Any

from src.services.feature_presentation import format_feature_result


class DailyBriefService:
    """Compose one useful message without doing provider work in handlers."""

    def __init__(self, feature_executor: Any, productivity_service: Any, *, timeout_seconds: float = 8.0) -> None:
        self.feature_executor = feature_executor
        self.productivity_service = productivity_service
        self.timeout_seconds = float(timeout_seconds)

    async def _feature(self, key: str, params: dict[str, Any] | None = None) -> Any:
        try:
            return await asyncio.wait_for(
                self.feature_executor.execute(key, params or {}),
                timeout=self.timeout_seconds,
            )
        except Exception:
            return None

    async def build(self, user_id: int, *, city: str, timezone_name: str = "Europe/Istanbul") -> str:
        weather, earthquakes, news, summary = await asyncio.gather(
            self._feature("weather", {"city": city}),
            self._feature("earthquakes", {}),
            self._feature("breaking_news", {}),
            self.productivity_service.daily_summary(
                int(user_id), timezone_name=timezone_name, days=1, limit=5
            ),
        )
        lines = ["\U0001f4c5 <b>Arel'in gÃ¼nlÃ¼k Ã¶zeti</b>", f"ğŸ“ <b>{html.escape(str(city))}</b>", ""]
        sections = (
            ("ğŸŒ¤ Hava", weather, "Hava verisi ÅŸu an alÄ±namadÄ±."),
            ("ğŸŒ Deprem", earthquakes, "Deprem verisi ÅŸu an alÄ±namadÄ±."),
            ("ğŸ“° GÃ¼ndem", news, "Haber akÄ±ÅŸÄ± ÅŸu an alÄ±namadÄ±."),
        )
        for title, result, fallback in sections:
            lines.append(f"<b>{title}</b>")
            lines.append(format_feature_result(result, fallback) if result is not None else fallback)
            lines.append("")
        lines.append("<b>ğŸ“Œ KiÅŸisel plan</b>")
        if summary.get("overdue_count"):
            lines.append(f"ğŸ”´ Geciken hatÄ±rlatÄ±cÄ±: <b>{int(summary['overdue_count'])}</b>")
        if summary.get("upcoming_count"):
            lines.append(f"â° BugÃ¼nkÃ¼ yaklaÅŸan hatÄ±rlatÄ±cÄ±: <b>{int(summary['upcoming_count'])}</b>")
        if summary.get("open_task_count"):
            lines.append(f"âœ… AÃ§Ä±k gÃ¶rev: <b>{int(summary['open_task_count'])}</b>")
        for task in (summary.get("tasks") or [])[:5]:
            lines.append(f"â€¢ {html.escape(str(task.get('text') or 'GÃ¶rev'))[:180]}")
        if not any(summary.get(key) for key in ("overdue_count", "upcoming_count", "open_task_count")):
            lines.append("BugÃ¼n iÃ§in aÃ§Ä±k gÃ¶rev veya hatÄ±rlatÄ±cÄ± yok. Takvim sana kÃ¼smemiÅŸ; sadece boÅŸ bakÄ±yor.")
        return "\n".join(lines).strip()

