from __future__ import annotations

import httpx
import pytest

from src.features.city_directory import CityDirectory
from src.features.contracts import ProviderRequest
from src.features.providers.air_quality import AirQualityProvider
from src.tools.air_quality_tool import AirQualityTool


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_air_quality_provider_resolves_turkish_city_and_normalizes_metrics():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/v1/air-quality")
        assert request.url.params["timezone"] == "Europe/Istanbul"
        assert "pm2_5" in request.url.params["current"]
        return httpx.Response(
            200,
            json={
                "current": {
                    "time": "2026-10-05T10:00",
                    "european_aqi": 24,
                    "us_aqi": 31,
                    "pm2_5": 8.4,
                    "pm10": 14.0,
                    "ozone": 62.2,
                    "nitrogen_dioxide": 12.1,
                    "grass_pollen": 0.4,
                },
                "current_units": {
                    "pm2_5": "Î¼g/mÂ³",
                    "pm10": "Î¼g/mÂ³",
                    "ozone": "Î¼g/mÂ³",
                    "nitrogen_dioxide": "Î¼g/mÂ³",
                    "grass_pollen": "grains/mÂ³",
                },
            },
            request=request,
        )

    async with _client(handler) as client:
        provider = AirQualityProvider(CityDirectory(), client=client)
        result = await provider.fetch(
            ProviderRequest(operation="air_quality", params={"city": "Ä°stanbul"})
        )

    assert result.data["city"] == "Ä°stanbul"
    assert result.data["aqi"] == 24.0
    assert result.data["aqi_label"] == "Orta"
    assert result.data["metrics"]["pm2_5"] == 8.4
    assert "Hava Kalitesi" in result.data["content"]
    assert result.metadata["coordinates_source"] == "local_city_snapshot"


@pytest.mark.asyncio
async def test_air_quality_provider_rejects_unknown_city_and_invalid_schema():
    async def empty_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"current": {"time": "now"}}, request=request)

    async with _client(empty_handler) as client:
        provider = AirQualityProvider(CityDirectory(), client=client)
        with pytest.raises(ValueError, match="air_quality_city_not_found"):
            await provider.fetch(ProviderRequest(operation="air_quality", params={"city": "Atlantis"}))
        with pytest.raises(RuntimeError, match="air_quality_schema_error"):
            await provider.fetch(
                ProviderRequest(
                    operation="air_quality",
                    params={"city": "Ä°stanbul"},
                )
            )


@pytest.mark.asyncio
async def test_air_quality_tool_returns_typed_result_or_safe_error():
    class Provider:
        async def fetch(self, request):
            from src.features.contracts import ProviderResponse

            return ProviderResponse(
                data={"content": "ğŸŒ¿ AQI"},
                source="fixture",
                source_url="https://fixture.invalid/aqi",
            )

    result = await AirQualityTool(Provider()).execute(city="Ankara")
    assert result["result"] == "ğŸŒ¿ AQI"
    assert result["metadata"] == {}
