"""Compatibility tool surface backed exclusively by typed providers.

The original Spatula integration contained one-off HTTP scrapers in each
tool. These adapters keep the existing tool names and schemas while
delegating all network work to ``src.features.providers``. Provider runtime
policy (cache, rate limit, circuit breaker, source metadata and stale
handling) remains owned by the feature executor in production.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

from .base import BaseTool
from src.features.contracts import ProviderRequest
from src.features.providers.earthquakes import EarthquakeProvider
from src.features.providers.flights import FlightSearchLinkProvider
from src.features.providers.public_data import (
    BimDealsProvider,
    CurrencyProvider,
    FuelProvider,
    PharmacyProvider,
    PrayerTimesProvider,
)
from src.features.providers.rss_news import BreakingNewsRssProvider
from src.features.providers.story import StoryProvider
from src.features.providers.weather import WeatherProvider
from src.security.log_redaction import summarize_exception
from src.security.error_codes import normalize_error_code


logger = logging.getLogger(__name__)


class _TypedProviderTool(BaseTool):
    """Expose a typed provider through the legacy BaseTool schema."""

    def __init__(
        self,
        *,
        name: str,
        description: str,
        provider: Any,
        parameters: Mapping[str, Any],
    ) -> None:
        super().__init__(name=name, description=description)
        self.provider = provider
        self._parameters = dict(parameters)

    @property
    def parameters(self) -> dict[str, Any]:
        return dict(self._parameters)

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        response = await self.provider.fetch(
            ProviderRequest(operation=self.name, params=dict(kwargs))
        )
        value = response.data
        if isinstance(value, dict):
            return dict(value)
        return {"result": value}

    async def close(self) -> None:
        close = getattr(self.provider, "close", None)
        if callable(close):
            await close()


class PharmacyTool(_TypedProviderTool):
    def __init__(self, provider: Any | None = None):
        super().__init__(
            name="get_pharmacies",
            description="Türkiye'deki nöbetçi eczaneleri şehir ve ilçe bazlı sorgular.",
            provider=provider or PharmacyProvider(),
            parameters={
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "Şehir adı (ör. İstanbul)"},
                    "district": {"type": "string", "description": "İlçe adı (ör. Beşiktaş)"},
                },
                "required": ["city"],
            },
        )


class FuelTool(_TypedProviderTool):
    def __init__(self, provider: Any | None = None):
        super().__init__(
            name="get_fuel_prices",
            description="Türkiye'deki güncel akaryakıt fiyatlarını getirir.",
            provider=provider or FuelProvider(),
            parameters={
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "İsteğe bağlı şehir filtresi"},
                },
            },
        )


class CurrencyTool(_TypedProviderTool):
    def __init__(self, provider: Any | None = None):
        super().__init__(
            name="get_currency_rates",
            description="Güncel döviz ve altın kurlarını getirir.",
            provider=provider or CurrencyProvider(),
            parameters={"type": "object", "properties": {}},
        )


class EarthquakeTool(_TypedProviderTool):
    def __init__(self, provider: Any | None = None):
        super().__init__(
            name="get_recent_earthquakes",
            description="Türkiye'deki son depremleri listeler.",
            provider=provider or EarthquakeProvider(),
            parameters={"type": "object", "properties": {}},
        )


class BimDealsTool(_TypedProviderTool):
    def __init__(self, provider: Any | None = None):
        super().__init__(
            name="get_bim_deals",
            description="BİM marketin haftalık aktüel ürünlerini ve indirimlerini getirir.",
            provider=provider or BimDealsProvider(),
            parameters={"type": "object", "properties": {}},
        )


class NewsTool(_TypedProviderTool):
    def __init__(self, provider: Any | None = None):
        super().__init__(
            name="get_breaking_news",
            description="Türkiye'den son dakika haber başlıklarını getirir.",
            # The feature manifest declares TRT Haber RSS as the breaking news
            # source, so the default adapter uses the same RSS provider.
            provider=provider or BreakingNewsRssProvider(),
            parameters={"type": "object", "properties": {}},
        )


class WeatherTool(_TypedProviderTool):
    def __init__(self, provider: Any | None = None):
        super().__init__(
            name="get_weather",
            description="Belirtilen şehrin güncel hava durumu ve tahminini getirir.",
            provider=provider or WeatherProvider(),
            parameters={
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "Şehir adı (ör. Ankara)"},
                    "days": {"type": "integer", "minimum": 1, "maximum": 7},
                },
                "required": ["city"],
            },
        )


class PrayerTimesTool(_TypedProviderTool):
    def __init__(self, provider: Any | None = None):
        super().__init__(
            name="get_prayer_times",
            description="Belirtilen şehrin günlük namaz vakitlerini getirir.",
            provider=provider or PrayerTimesProvider(),
            parameters={
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "Şehir adı (ör. İstanbul)"},
                },
                "required": ["city"],
            },
        )


class FlightTool(BaseTool):
    def __init__(self, provider: FlightSearchLinkProvider | None = None):
        super().__init__(
            name="search_flights",
            description="Belirtilen rotada güvenli uçuş arama bağlantıları oluşturur.",
        )
        self.provider = provider or FlightSearchLinkProvider()

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "origin": {"type": "string", "description": "Kalkış noktası veya havalimanı kodu"},
                "destination": {"type": "string", "description": "Varış noktası veya havalimanı kodu"},
                "date": {"type": "string", "description": "GG/AA/YYYY veya ISO tarih (opsiyonel)"},
            },
            "required": ["origin", "destination"],
        }

    async def execute(
        self,
        origin: str,
        destination: str,
        date: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        try:
            response = await self.provider.fetch(
                ProviderRequest(
                    operation="flight_search",
                    params={"origin": origin, "destination": destination, "date": date},
                )
            )
            return {"result": response.data["content"], "links": response.data["links"]}
        except ValueError as exc:
            code = normalize_error_code(exc, "INVALID_ROUTE").lower()
            message = "Kalkış, varış veya tarih bilgisini kontrol edin."
            return {
                "error": code if code in {"invalid_origin", "invalid_destination", "invalid_date"} else "invalid_route",
                "content": message,
            }


class StoryTool(BaseTool):
    def __init__(self, provider: StoryProvider | None = None):
        super().__init__(
            name="generate_story",
            description="Belirtilen konuda güvenli ve kısa bir hikâye üretir.",
        )
        self.provider = provider or StoryProvider()

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Hikâye konusu"},
                "style": {"type": "string", "description": "masal, gerilim, komik veya duygusal"},
            },
            "required": ["topic"],
        }

    async def execute(self, topic: str, style: str = "masal", **kwargs: Any) -> dict[str, Any]:
        try:
            response = await self.provider.fetch(
                ProviderRequest(operation="story", params={"topic": topic, "style": style})
            )
            return {"result": response.data["content"], "metadata": dict(response.metadata)}
        except ValueError as exc:
            code = normalize_error_code(exc, "STORY_INVALID_REQUEST")
            return {"error": "story_request_rejected", "error_code": code}
        except Exception as exc:
            logger.error(
                "Typed story provider failed: %s",
                summarize_exception(exc, label="story_provider"),
            )
            return {"error": "story_provider_unavailable", "error_code": "story_provider_unavailable"}
