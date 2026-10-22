"""Typed Open-Meteo forecast with a bounded wttr.in fallback."""

from __future__ import annotations

import html
import math
import re
import unicodedata
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from ..contracts import ProviderHealth, ProviderRequest, ProviderResponse
from ..city_directory import CityDirectory
from .transport import ProviderHttpTransport, ProviderTransportError
from src.security.tls import httpx_verify_kwargs


class WeatherProvider:
    name = "get_weather"
    capabilities = {"weather", "current_weather"}
    endpoint = "https://wttr.in"

    forecast_endpoint = "https://api.open-meteo.com/v1/forecast"

    def __init__(self, client: httpx.AsyncClient | None = None, *, city_directory: CityDirectory | None = None) -> None:
        self._client = client
        self._owned_client: httpx.AsyncClient | None = None
        self.city_directory = city_directory

    @staticmethod
    def _number(value: Any, *, minimum: float, maximum: float) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("weather_schema_error") from exc
        if not math.isfinite(number) or not minimum <= number <= maximum:
            raise RuntimeError("weather_schema_error")
        return f"{number:g}"

    @staticmethod
    def _condition(code: Any) -> str:
        try:
            value = int(code)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("weather_schema_error") from exc
        if value == 0:
            return "Açık"
        if value in {1, 2}:
            return "Az bulutlu" if value == 1 else "Parçalı bulutlu"
        if value == 3:
            return "Bulutlu"
        if value in {45, 48}:
            return "Sisli"
        if 51 <= value <= 57:
            return "Çisenti"
        if 61 <= value <= 67 or 80 <= value <= 82:
            return "Yağmurlu"
        if 71 <= value <= 77 or 85 <= value <= 86:
            return "Karlı"
        if 95 <= value <= 99:
            return "Gök gürültülü"
        return "Değişken"

    async def _fetch_open_meteo(self, city: str, latitude: float, longitude: float, days: int, timeout: float) -> ProviderResponse[dict[str, Any]]:
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise ValueError("weather_coordinates_invalid")
        query = {
            "latitude": f"{latitude:.5f}",
            "longitude": f"{longitude:.5f}",
            "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,uv_index_max,wind_speed_10m_max",
            "timezone": "Europe/Istanbul",
            "forecast_days": str(days),
        }
        url = f"{self.forecast_endpoint}?{urlencode(query)}"
        payload = await ProviderHttpTransport(
            await self._get_client(), allowed_hosts={"api.open-meteo.com"}
        ).get_json(url, timeout=timeout, error_code="weather_invalid_json")
        current = payload.get("current")
        daily = payload.get("daily")
        if not isinstance(current, dict) or not isinstance(daily, dict) or not isinstance(current.get("time"), str):
            raise RuntimeError("weather_schema_error")
        temperature = self._number(current.get("temperature_2m"), minimum=-100, maximum=70)
        humidity = self._number(current.get("relative_humidity_2m"), minimum=0, maximum=100)
        description = self._condition(current.get("weather_code"))
        dates = daily.get("time")
        maxima = daily.get("temperature_2m_max")
        minima = daily.get("temperature_2m_min")
        if not all(isinstance(items, list) and len(items) >= days for items in (dates, maxima, minima)):
            raise RuntimeError("weather_schema_error")
        forecast: list[dict[str, str]] = []
        for index in range(days):
            date = dates[index]
            if not isinstance(date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                raise RuntimeError("weather_schema_error")
            row = {
                "date": date,
                "max_c": self._number(maxima[index], minimum=-100, maximum=70),
                "min_c": self._number(minima[index], minimum=-100, maximum=70),
            }
            for source, key, upper in (
                ("precipitation_probability_max", "rain_chance", 100),
                ("uv_index_max", "uv_index", 30),
                ("wind_speed_10m_max", "wind_kmph", 400),
            ):
                values = daily.get(source)
                if isinstance(values, list) and len(values) > index and values[index] is not None:
                    row[key] = self._number(values[index], minimum=0, maximum=upper)
            forecast.append(row)
        safe_city = html.escape(city)
        lines = [
            f"<b>🌤 {safe_city} Hava Durumu</b>",
            f"🌡 Sıcaklık: <b>{temperature}°C</b>",
            f"📝 Durum: <b>{description}</b>",
            f"💧 Nem: %{humidity}",
            f"<b>📅 {days} günlük tahmin</b>",
        ]
        for row in forecast:
            extras = []
            if row.get("rain_chance"):
                extras.append(f"Yağış %{row['rain_chance']}")
            if row.get("wind_kmph"):
                extras.append(f"Rüzgar {row['wind_kmph']} km/sa")
            if row.get("uv_index"):
                extras.append(f"UV {row['uv_index']}")
            suffix = f" · {' · '.join(extras)}" if extras else ""
            lines.append(f"• <b>{row['date']}</b>: {row['min_c']}–{row['max_c']}°C{suffix}")
        data = {"city": city, "temperature_c": temperature, "humidity": humidity, "description": description,
                "forecast": forecast, "observed_at": current["time"][:32], "content": "\n".join(lines)}
        return ProviderResponse(data=data, source="Open-Meteo", source_url="https://open-meteo.com/",
                                metadata={"provider_kind": "typed", "platform": "open_meteo", "coordinates_source": "local_city_snapshot"})

    @staticmethod
    def _city_slug(value: str) -> str:
        value = value.translate(str.maketrans({
            "ç": "c", "Ç": "c", "ğ": "g", "Ğ": "g", "ı": "i", "İ": "i",
            "ö": "o", "Ö": "o", "ş": "s", "Ş": "s", "ü": "u", "Ü": "u",
        }))
        value = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
        value = re.sub(r"[^a-zA-Z0-9 -]", "", value).strip()
        value = re.sub(r"\s+", "+", value)
        if not value:
            raise ValueError("city_required")
        return quote(value, safe="+")[:80]

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        if self._owned_client is None:
            self._owned_client = httpx.AsyncClient(
                timeout=httpx.Timeout(8.0, connect=3.0),
                headers={"Accept": "application/json", "User-Agent": "Arelbot/1.0"},
                follow_redirects=False,
                **httpx_verify_kwargs(),
            )
        return self._owned_client

    async def fetch(self, request: ProviderRequest) -> ProviderResponse[dict[str, Any]]:
        city = str(request.params.get("city") or "").strip()
        try:
            forecast_days = int(request.params.get("days", 3) or 3)
        except (TypeError, ValueError) as exc:
            raise ValueError("weather_days_out_of_range") from exc
        if forecast_days < 1 or forecast_days > 7:
            raise ValueError("weather_days_out_of_range")
        if self.city_directory is not None:
            record = self.city_directory.by_name(city) or self.city_directory.by_plate(city)
            if record is not None and record.latitude is not None and record.longitude is not None:
                try:
                    return await self._fetch_open_meteo(
                        record.name, float(record.latitude), float(record.longitude), forecast_days,
                        min(max(request.timeout_seconds / 2, 0.5), 5.0),
                    )
                except (ProviderTransportError, httpx.RequestError, RuntimeError):
                    # Keep the established source available during a forecast
                    # outage or malformed upstream response.
                    pass
        slug = self._city_slug(city)
        url = f"{self.endpoint}/{slug}?format=j1&lang=tr"
        client = await self._get_client()
        try:
            payload = await ProviderHttpTransport(
                client,
                allowed_hosts={"wttr.in"},
            ).get_json(
                url,
                timeout=min(max(request.timeout_seconds, 0.5), 8.0),
                error_code="weather_invalid_json",
                # wttr.in currently serves its JSON endpoint with a text
                # content type. The host is fixed and the payload is still
                # parsed as JSON, so accepting these two types does not
                # weaken the redirect/SSRF policy.
                expected_content_types=("application/json", "text/plain", "text/html"),
            )
        except ProviderTransportError as exc:
            raise exc
        current = payload.get("current_condition")
        if not isinstance(current, list) or not current or not isinstance(current[0], dict):
            raise RuntimeError("weather_schema_error")
        current = current[0]
        temperature = current.get("temp_C")
        humidity = current.get("humidity")
        description = current.get("lang_tr") or current.get("weatherDesc")
        if isinstance(description, list):
            description = description[0] if description else {}
        if isinstance(description, dict):
            description = description.get("value")
        if temperature is None or humidity is None or not description:
            raise RuntimeError("weather_schema_error")
        safe_city = html.escape(city)
        safe_description = html.escape(str(description))
        content = (
            f"<b>🌤 {safe_city} Hava Durumu</b>\n\n"
            f"🌡 Sıcaklık: <b>{html.escape(str(temperature))}°C</b>\n"
            f"📝 Durum: <b>{safe_description}</b>\n"
            f"💧 Nem: %{html.escape(str(humidity))}"
        )
        forecast: list[dict[str, str]] = []
        raw_forecast = payload.get("weather")
        if isinstance(raw_forecast, list):
            for item in raw_forecast[:forecast_days]:
                if not isinstance(item, dict):
                    continue
                date = str(item.get("date") or "").strip()[:32]
                maximum = str(item.get("maxtempC") or "").strip()[:16]
                minimum = str(item.get("mintempC") or "").strip()[:16]
                uv_index = str(item.get("uvIndex") or "").strip()[:16]
                if not date or not maximum or not minimum:
                    continue
                row = {
                    "date": date,
                    "max_c": maximum,
                    "min_c": minimum,
                    "uv_index": uv_index,
                }
                hourly = item.get("hourly")
                if isinstance(hourly, list) and hourly:
                    rain_values = [
                        int(str(hour.get("chanceofrain") or 0))
                        for hour in hourly
                        if isinstance(hour, dict) and str(hour.get("chanceofrain") or "0").isdigit()
                    ]
                    wind_values = [
                        int(str(hour.get("windspeedKmph") or 0))
                        for hour in hourly
                        if isinstance(hour, dict) and str(hour.get("windspeedKmph") or "0").isdigit()
                    ]
                    if rain_values:
                        row["rain_chance"] = str(max(0, min(100, max(rain_values))))
                    if wind_values:
                        row["wind_kmph"] = str(max(wind_values))
                forecast.append(row)
        if forecast:
            content += f"\n\n<b>📅 {len(forecast)} günlük tahmin</b>"
            for row in forecast:
                extra = []
                if row.get("uv_index"):
                    extra.append(f"UV {row['uv_index']}")
                if row.get("rain_chance"):
                    extra.append(f"Yağış %{row['rain_chance']}")
                if row.get("wind_kmph"):
                    extra.append(f"Rüzgar {row['wind_kmph']} km/sa")
                suffix = f" · {' · '.join(extra)}" if extra else ""
                content += (
                    f"\n• <b>{html.escape(row['date'])}</b>: "
                    f"{html.escape(row['min_c'])}–{html.escape(row['max_c'])}°C"
                    f"{suffix}"
                )
        data = {
            "city": city,
            "temperature_c": str(temperature),
            "humidity": str(humidity),
            "description": str(description),
            "forecast": forecast,
            "content": content,
        }
        return ProviderResponse(
            data=data,
            source="wttr.in",
            source_url=url,
            metadata={"provider_kind": "typed", "platform": "wttr"},
        )

    async def normalize(self, raw: Any) -> Any:
        return raw

    async def health_check(self) -> ProviderHealth:
        # Health checks are deliberately lazy.  A full upstream request on
        # every /health call would amplify outages and waste provider quota.
        return ProviderHealth(name=self.name, healthy=True, detail="lazy_probe")

    async def close(self) -> None:
        if self._owned_client is not None:
            await self._owned_client.aclose()
            self._owned_client = None
