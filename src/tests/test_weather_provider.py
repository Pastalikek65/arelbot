from __future__ import annotations

import httpx
import pytest

from src.features.contracts import ProviderRequest
from src.features.providers.weather import WeatherProvider


@pytest.mark.asyncio
async def test_weather_provider_normalizes_valid_payload_and_escapes_display():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "wttr.in"
        assert request.url.path == "/istanbul"
        return httpx.Response(
            200,
            json={
                "current_condition": [
                    {"temp_C": "22", "humidity": "47", "lang_tr": [{"value": "Açık & güneşli"}]}
                ]
            },
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = WeatherProvider(client=client)
        response = await provider.fetch(
            ProviderRequest(operation="weather", params={"city": "İstanbul"}, timeout_seconds=2)
        )

    assert response.source == "wttr.in"
    assert response.data["temperature_c"] == "22"
    assert "Açık &amp; güneşli" in response.data["content"]


@pytest.mark.asyncio
async def test_weather_provider_rejects_invalid_schema():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"current_condition": []}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = WeatherProvider(client=client)
        with pytest.raises(RuntimeError, match="weather_schema_error"):
            await provider.fetch(ProviderRequest(operation="weather", params={"city": "Ankara"}))


@pytest.mark.asyncio
async def test_weather_provider_exposes_bounded_daily_forecast():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "current_condition": [
                    {"temp_C": "22", "humidity": "47", "lang_tr": [{"value": "Açık"}]}
                ],
                "weather": [
                    {"date": "2026-10-05", "maxtempC": "25", "mintempC": "15", "uvIndex": "4"},
                    {"date": "2026-10-06", "maxtempC": "24", "mintempC": "14", "uvIndex": "3"},
                    {"date": "2026-10-07", "maxtempC": "20", "mintempC": "12", "uvIndex": "2"},
                    {"date": "2026-10-08", "maxtempC": "21", "mintempC": "11", "uvIndex": "2"},
                ],
            },
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = WeatherProvider(client=client)
        response = await provider.fetch(
            ProviderRequest(operation="weather", params={"city": "Istanbul", "days": 3}, timeout_seconds=2)
        )

    assert len(response.data["forecast"]) == 3
    assert response.data["forecast"][0] == {
        "date": "2026-10-05",
        "max_c": "25",
        "min_c": "15",
        "uv_index": "4",
    }
    assert "3 günlük tahmin" in response.data["content"]
    assert "UV 4" in response.data["content"]


@pytest.mark.asyncio
async def test_weather_provider_rejects_forecast_days_out_of_range():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500, request=request))) as client:
        provider = WeatherProvider(client=client)
        with pytest.raises(ValueError, match="weather_days_out_of_range"):
            await provider.fetch(ProviderRequest(operation="weather", params={"city": "Ankara", "days": 8}))


def test_weather_provider_transliterates_turkish_city_names():
    assert WeatherProvider._city_slug("Şırnak") == "sirnak"
    assert WeatherProvider._city_slug("İstanbul") == "istanbul"
