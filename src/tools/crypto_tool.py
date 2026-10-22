"""AI allowlisted adapter for the typed crypto provider."""

from __future__ import annotations

from typing import Any, Dict

from .base import BaseTool
from src.features.contracts import ProviderRequest
from src.features.providers.crypto import CryptoProvider
from src.security.error_codes import normalize_error_code


class CryptoTool(BaseTool):
    def __init__(self, provider: CryptoProvider | None = None) -> None:
        super().__init__(
            name="get_crypto_prices",
            description="Kripto varlıkların güncel fiyat ve 24 saat değişimini getirir.",
        )
        self.provider = provider or CryptoProvider()

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "CoinGecko kimliği veya kısa sembol; örnek btc, eth, bitcoin"},
                "vs_currency": {"type": "string", "enum": ["usd", "try", "eur", "gbp"]},
            },
            "required": ["symbol"],
        }

    async def execute(self, symbol: str, vs_currency: str = "usd", **_: Any) -> Dict[str, Any]:
        try:
            response = await self.provider.fetch(
                ProviderRequest(operation="crypto", params={"symbol": symbol, "vs_currency": vs_currency})
            )
            return {"result": response.data["content"], "data": response.data, "metadata": dict(response.metadata)}
        except ValueError as exc:
            return {
                "error": "crypto_request_rejected",
                "error_code": normalize_error_code(exc, "CRYPTO_INVALID_REQUEST"),
            }
        except Exception:
            return {"error": "crypto_provider_unavailable", "error_code": "crypto_provider_unavailable"}
