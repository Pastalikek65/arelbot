"""AI allowlisted adapter for typed market quotes."""

from __future__ import annotations

from typing import Any, Dict

from .base import BaseTool
from src.features.contracts import ProviderRequest
from src.features.providers.market import MarketProvider
from src.security.error_codes import normalize_error_code


class MarketTool(BaseTool):
    def __init__(self, provider: MarketProvider | None = None) -> None:
        super().__init__(
            name="get_market_quote",
            description="Hisse senedi sembolü için güncel piyasa özeti ve 7 günlük değişim verir.",
        )
        self.provider = provider or MarketProvider()

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {"symbol": {"type": "string", "description": "Yahoo Finance sembolü; örnek THYAO.IS veya thy"}},
            "required": ["symbol"],
        }

    async def execute(self, symbol: str, **_: Any) -> Dict[str, Any]:
        try:
            response = await self.provider.fetch(ProviderRequest(operation="market", params={"symbol": symbol}))
            return {"result": response.data["content"], "data": response.data, "metadata": dict(response.metadata)}
        except ValueError as exc:
            return {
                "error": "market_request_rejected",
                "error_code": normalize_error_code(exc, "MARKET_INVALID_REQUEST"),
            }
        except Exception:
            return {"error": "market_provider_unavailable", "error_code": "market_provider_unavailable"}
