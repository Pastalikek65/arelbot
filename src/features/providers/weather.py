"""Typed wttr.in provider with schema validation and safe URL construction."""

from __future__ import annotations

import html
import re
import unicodedata
from typing import Any
from urllib.parse import quote

import httpx

from ..contracts import ProviderHealth, ProviderRequest, ProviderResponse
from .transport import ProviderHttpTransport, ProviderTransportError
from src.security.tls import httpx_verify_kwargs


class WeatherProvider:
    name = "get_weather"
    capabilities = {"weather", "current_weather"}
    endpoint = "https://wttr.in"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client
        self._owned_client: httpx.AsyncClient | None = None

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
