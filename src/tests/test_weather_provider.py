from __future__ import annotations

import httpx
import pytest

from src.features.contracts import ProviderRequest
from src.features.providers.weather import WeatherProvider
from src.features.city_directory import CityDirectory


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


@pytest.mark.asyncio
async def test_turkish_city_uses_open_meteo_structured_forecast():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.open-meteo.com"
        assert request.url.params["forecast_days"] == "3"
        assert request.url.params["timezone"] == "Europe/Istanbul"
        assert "temperature_2m" in request.url.params["current"]
        return httpx.Response(200, json={
            "current": {"time": "2026-10-19T10:00", "temperature_2m": 18.5,
                        "relative_humidity_2m": 62, "weather_code": 61},
            "daily": {"time": ["2026-10-19", "2026-10-20", "2026-10-21"],
                      "temperature_2m_max": [20, 19, 17],
                      "temperature_2m_min": [12, 11, 9],
                      "precipitation_probability_max": [70, 20, 10],
                      "uv_index_max": [3, 4, 3],
                      "wind_speed_10m_max": [25, 18, 12]},
        }, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = WeatherProvider(client=client, city_directory=CityDirectory())
        response = await provider.fetch(ProviderRequest(operation="weather", params={"city": "İstanbul", "days": 3}))

    assert response.source == "Open-Meteo"
    assert response.data["temperature_c"] == "18.5"
    assert response.data["description"] == "Yağmurlu"
    assert len(response.data["forecast"]) == 3
    assert "Yağış %70" in response.data["content"]
    assert response.source_url == "https://open-meteo.com/"


@pytest.mark.asyncio
async def test_open_meteo_rejects_incomplete_forecast():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "current": {"time": "2026-10-19T10:00", "temperature_2m": 18,
                        "relative_humidity_2m": 60, "weather_code": 0},
            "daily": {"time": ["2026-10-19"], "temperature_2m_max": [20], "temperature_2m_min": [12]},
        }, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = WeatherProvider(client=client, city_directory=CityDirectory())
        with pytest.raises(RuntimeError, match="weather_schema_error"):
            await provider._fetch_open_meteo("İstanbul", 41.0, 29.0, 3, 2.0)


@pytest.mark.asyncio
async def test_open_meteo_failure_falls_back_to_wttr():
    hosts = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        if request.url.host == "api.open-meteo.com":
            return httpx.Response(503, request=request)
        return httpx.Response(200, json={
            "current_condition": [{"temp_C": "19", "humidity": "50", "lang_tr": [{"value": "Açık"}]}],
        }, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = WeatherProvider(client=client, city_directory=CityDirectory())
        result = await provider.fetch(ProviderRequest(operation="weather", params={"city": "İstanbul"}))

    assert hosts == ["api.open-meteo.com", "wttr.in"]
    assert result.source == "wttr.in"


@pytest.mark.asyncio
async def test_open_meteo_connection_timeout_falls_back_to_wttr():
    hosts = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        if request.url.host == "api.open-meteo.com":
            raise httpx.ConnectTimeout("temporary upstream timeout", request=request)
        return httpx.Response(200, json={
            "current_condition": [{"temp_C": "19", "humidity": "50", "lang_tr": [{"value": "Açık"}]}],
        }, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = WeatherProvider(client=client, city_directory=CityDirectory())
        result = await provider.fetch(ProviderRequest(operation="weather", params={"city": "İstanbul"}))

    assert hosts == ["api.open-meteo.com", "wttr.in"]
    assert result.source == "wttr.in"
