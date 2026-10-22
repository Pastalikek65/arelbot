"""Typed market quote provider backed by a bounded Yahoo chart API adapter."""

from __future__ import annotations

import asyncio
import html
import inspect
import math
from datetime import datetime, timezone
from typing import Any, Callable, Mapping
from urllib.parse import quote

import httpx

from ..contracts import ProviderHealth, ProviderRequest, ProviderResponse
from .transport import ProviderHttpTransport, ProviderTransportError
from src.security.tls import httpx_verify_kwargs


_ALIASES = {
    "thy": "THYAO.IS", "thyao": "THYAO.IS", "thy.is": "THYAO.IS",
    "aselsan": "ASELS.IS", "asels": "ASELS.IS", "koc": "KCHOL.IS", "sasa": "SASA.IS",
    "eregli": "EREGL.IS", "garanti": "GARAN.IS", "isbank": "ISCTR.IS", "tupras": "TUPRS.IS",
}


class MarketProvider:
    """Fetch market data without blocking the Telegram event loop.

    A loader remains injectable for replay/contract tests. Production uses the
    fixed-host Yahoo chart endpoint rather than the fragile yfinance crumb flow.
    """

    name = "get_market_quote"
    capabilities = {"market", "stocks", "quotes"}
    api_base_url = "https://query1.finance.yahoo.com/v8/finance/chart"
    source_base_url = "https://finance.yahoo.com/quote"

    def __init__(
        self,
        loader: Callable[[str], Any] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.loader = loader
        self._client = client
        self._owned_client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        if self._owned_client is None:
            self._owned_client = httpx.AsyncClient(
                timeout=httpx.Timeout(8.0, connect=3.0),
                follow_redirects=False,
                headers={"Accept": "application/json", "User-Agent": "Arelbot/1.0"},
                **httpx_verify_kwargs(),
            )
        return self._owned_client

    @staticmethod
    def _normalise_symbol(value: object) -> str:
        raw = str(value or "").strip().lower()
        if not raw or len(raw) > 32:
            raise ValueError("market_symbol_invalid")
        if raw in _ALIASES:
            return _ALIASES[raw]
        if not all(char.isalnum() or char in {".", "-", "^", "="} for char in raw):
            raise ValueError("market_symbol_invalid")
        return raw.upper()

    async def _load_http(self, symbol: str, timeout_seconds: float) -> Mapping[str, Any]:
        client = await self._get_client()
        endpoint = f"{self.api_base_url}/{quote(symbol, safe='.^-=')}"
        try:
            body = await ProviderHttpTransport(
                client,
                allowed_hosts={"query1.finance.yahoo.com"},
            ).get_json(
                endpoint,
                params={"range": "7d", "interval": "1h", "events": "history"},
                timeout=min(max(timeout_seconds, 0.5), 10.0),
                error_code="market_invalid_json",
            )
        except httpx.HTTPStatusError as exc:
            raise RuntimeError("market_upstream_http") from exc
        except ProviderTransportError as exc:
            if exc.code == "market_invalid_json":
                raise RuntimeError(exc.code) from exc
            raise exc
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeError("market_upstream_unavailable") from exc
        if not isinstance(body, Mapping):
            raise RuntimeError("market_invalid_json")
        return body

    async def _load(self, symbol: str, timeout_seconds: float) -> Mapping[str, Any]:
        if self.loader is not None:
            value = self.loader(symbol)
            if inspect.isawaitable(value):
                value = await value
            if not isinstance(value, Mapping):
                raise RuntimeError("market_schema_error")
            return value
        return await self._load_http(symbol, timeout_seconds)

    @staticmethod
    def _rows_from_chart(body: Mapping[str, Any]) -> list[dict[str, Any]]:
        try:
            chart = body["chart"]
            result = chart["result"][0]
            timestamps = result["timestamp"]
            quote_data = result["indicators"]["quote"][0]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("market_schema_error") from exc
        if not isinstance(timestamps, list) or not isinstance(quote_data, Mapping):
            raise RuntimeError("market_schema_error")
        closes = quote_data.get("close")
        highs = quote_data.get("high")
        lows = quote_data.get("low")
        if not all(isinstance(values, list) for values in (closes, highs, lows)):
            raise RuntimeError("market_schema_error")
        rows: list[dict[str, Any]] = []
        for timestamp, close, high, low in zip(timestamps, closes, highs, lows):
            try:
                close_value = float(close)
                high_value = float(high if high is not None else close_value)
                low_value = float(low if low is not None else close_value)
                timestamp_value = int(timestamp)
            except (TypeError, ValueError, OverflowError):
                continue
            if not all(math.isfinite(value) for value in (close_value, high_value, low_value)):
                continue
            rows.append({
                "time": datetime.fromtimestamp(timestamp_value, timezone.utc).isoformat(),
                "close": close_value,
                "high": high_value,
                "low": low_value,
            })
        return rows[-168:]

    async def fetch(self, request: ProviderRequest) -> ProviderResponse[dict[str, Any]]:
        symbol = self._normalise_symbol(request.params.get("symbol"))
        timeout_seconds = min(max(request.timeout_seconds, 0.5), 12.0)
        raw = await asyncio.wait_for(self._load(symbol, timeout_seconds), timeout=timeout_seconds)
        if "chart" in raw:
            raw = {"rows": self._rows_from_chart(raw)}
        raw_rows = raw.get("rows")
        if not isinstance(raw_rows, list):
            raise RuntimeError("market_schema_error")
        rows = [row for row in raw_rows if isinstance(row, Mapping)]
        if not rows:
            raise RuntimeError("market_no_data")
        try:
            closes = [float(row["close"]) for row in rows]
            highs = [float(row["high"]) for row in rows]
            lows = [float(row["low"]) for row in rows]
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("market_schema_error") from exc
        if not all(math.isfinite(value) for value in closes + highs + lows):
            raise RuntimeError("market_schema_error")
        current = closes[-1]
        previous = closes[0]
        change = current - previous
        change_pct = (change / previous * 100.0) if previous else 0.0
        content = (
            f"<b>\U0001f4ca Piyasa Analizi: {html.escape(symbol)}</b>\n\n"
            f"\U0001f4b0 <b>Fiyat:</b> <code>{current:,.4f}</code>\n"
            f"\U0001f4c8 <b>7g Degisim:</b> <code>{change:+,.4f} ({change_pct:+.2f}%)</code>\n"
            f"\U0001f51d <b>Zirve:</b> <code>{max(highs):,.4f}</code>\n"
            f"\U0001f53b <b>Dip:</b> <code>{min(lows):,.4f}</code>\n\n"
            "<i>Veri Yahoo Finance uzerinden alinir; yatirim tavsiyesi degildir.</i>"
        )
        return ProviderResponse(
            data={
                "symbol": symbol,
                "price": current,
                "change": change,
                "change_pct": change_pct,
                "high": max(highs),
                "low": min(lows),
                "rows": rows,
                "content": content,
            },
            source="Yahoo Finance (Chart API)",
            source_url=f"{self.source_base_url}/{symbol}",
            fetched_at=datetime.now(timezone.utc),
            metadata={"provider_kind": "typed", "records": len(rows), "symbol": symbol},
        )

    async def normalize(self, raw: Any) -> Any:
        return raw

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(name=self.name, healthy=True, detail="lazy_probe")

    async def close(self) -> None:
        if self._owned_client is not None:
            await self._owned_client.aclose()
            self._owned_client = None
