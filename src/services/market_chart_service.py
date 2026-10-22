"""Bounded market chart rendering outside Telegram handlers."""

from __future__ import annotations

import asyncio
import io
from typing import Any, Mapping


class MarketChartError(RuntimeError):
    """Stable chart rendering error code."""


class MarketChartService:
    def __init__(self, *, max_concurrency: int = 2, timeout_seconds: float = 8.0, max_output_bytes: int = 4 * 1024 * 1024) -> None:
        self.timeout_seconds = max(0.5, float(timeout_seconds))
        self.max_output_bytes = max(64 * 1024, min(int(max_output_bytes), 8 * 1024 * 1024))
        self._semaphore = asyncio.Semaphore(max(1, min(int(max_concurrency), 4)))

    @staticmethod
    def _render_sync(rows: list[Mapping[str, Any]], symbol: str) -> io.BytesIO | None:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except ImportError:
            return None
        values = [float(row["close"]) for row in rows]
        figure, axis = plt.subplots(figsize=(10, 5), dpi=100)
        try:
            color = "#23d18b" if values[-1] >= values[0] else "#ff5f56"
            axis.plot(range(len(values)), values, color=color, linewidth=2)
            axis.fill_between(range(len(values)), values, color=color, alpha=0.12)
            axis.set_title(f"{symbol} · 7 günlük piyasa hareketi")
            axis.grid(True, alpha=0.15)
            figure.tight_layout()
            output = io.BytesIO()
            figure.savefig(output, format="png")
            output.seek(0)
            return output
        finally:
            plt.close(figure)

    async def render(self, data: Mapping[str, Any] | None) -> io.BytesIO | None:
        if not isinstance(data, Mapping):
            raise MarketChartError("market_data_missing")
        rows = [row for row in data.get("rows", []) if isinstance(row, Mapping)]
        if len(rows) < 2 or len(rows) > 168:
            raise MarketChartError("market_chart_rows_invalid")
        if any("close" not in row for row in rows):
            raise MarketChartError("market_chart_schema_error")
        async with self._semaphore:
            task = asyncio.create_task(asyncio.to_thread(self._render_sync, rows, str(data.get("symbol") or "MARKET")))
            try:
                output = await asyncio.wait_for(task, timeout=self.timeout_seconds)
            except asyncio.TimeoutError as exc:
                raise MarketChartError("market_chart_timeout") from exc
        if output is None:
            return None
        if output.getbuffer().nbytes <= 0:
            raise MarketChartError("market_chart_empty")
        if output.getbuffer().nbytes > self.max_output_bytes:
            raise MarketChartError("market_chart_too_large")
        return output

    async def health_check(self) -> dict[str, Any]:
        try:
            import matplotlib  # noqa: F401
            available = True
        except ImportError:
            available = False
        return {"healthy": available, "max_concurrency": self._semaphore._value, "max_output_bytes": self.max_output_bytes}
