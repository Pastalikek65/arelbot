"""AI allowlisted adapter for the typed air-quality provider."""

from __future__ import annotations

from typing import Any, Dict

from .base import BaseTool
from src.features.contracts import ProviderRequest
from src.features.providers.air_quality import AirQualityProvider
from src.security.error_codes import normalize_error_code


class AirQualityTool(BaseTool):
    def __init__(self, provider: AirQualityProvider | None = None) -> None:
        super().__init__(
            name="get_air_quality",
            description="Bir TÃ¼rk ÅŸehrinin gÃ¼ncel hava kalitesi, AQI, partikÃ¼l ve polen deÄŸerlerini getirir.",
        )
        self.provider = provider or AirQualityProvider()

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "Ä°l adÄ± veya yerel ÅŸehir kaydÄ±ndaki plaka."},
            },
            "required": ["city"],
        }

    async def execute(self, city: str, **_: Any) -> Dict[str, Any]:
        try:
            response = await self.provider.fetch(
                ProviderRequest(operation="air_quality", params={"city": city})
            )
            return {"result": response.data["content"], "data": response.data, "metadata": dict(response.metadata)}
        except ValueError as exc:
            return {
                "error": "air_quality_request_rejected",
                "error_code": normalize_error_code(exc, "AIR_QUALITY_INVALID_REQUEST"),
            }
        except Exception:
            return {"error": "air_quality_provider_unavailable", "error_code": "air_quality_provider_unavailable"}
