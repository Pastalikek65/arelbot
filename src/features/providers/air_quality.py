"""Typed Open-Meteo air-quality provider for Turkish city queries."""

from __future__ import annotations

import html
from typing import Any
from urllib.parse import urlencode

import httpx

from ..city_directory import CityDirectory
from ..contracts import ProviderHealth, ProviderRequest, ProviderResponse
from .transport import ProviderHttpTransport, ProviderTransportError
from src.security.tls import httpx_verify_kwargs


class AirQualityProvider:
    """Fetch bounded current AQI, particulate and pollen values.

    Coordinates come from the signed local city snapshot.  Callers cannot
    inject an arbitrary host or location URL; the only network destination is
    the configured Open-Meteo endpoint.
    """

    name = "get_air_quality"
    capabilities = {"air_quality", "aqi", "pollen"}
    endpoint = "https://air-quality-api.open-meteo.com/v1/air-quality"
    current_variables = (
        "european_aqi",
        "us_aqi",
        "pm10",
        "pm2_5",
        "carbon_monoxide",
        "nitrogen_dioxide",
        "ozone",
        "alder_pollen",
        "birch_pollen",
        "grass_pollen",
        "mugwort_pollen",
        "olive_pollen",
        "ragweed_pollen",
    )

    def __init__(
        self,
        city_directory: CityDirectory | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.city_directory = city_directory
        self._client = client
        self._owned_client: httpx.AsyncClient | None = None

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

    def _coordinates(self, params: dict[str, Any]) -> tuple[str, float, float]:
        city = str(params.get("city") or "").strip()
        if self.city_directory is not None and city:
            matches = self.city_directory.resolve(city)
            if matches:
                record = matches[0]
                if record.latitude is not None and record.longitude is not None:
                    return record.name, float(record.latitude), float(record.longitude)
        try:
            latitude = float(params["latitude"])
            longitude = float(params["longitude"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("air_quality_city_not_found") from exc
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ValueError("air_quality_coordinates_invalid")
        return city[:80] or "Konum", latitude, longitude

    @staticmethod
    def _number(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return round(float(value), 2)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _aqi_label(value: float | None) -> str:
        if value is None:
            return "Bilinmiyor"
        if value <= 20:
            return "Ä°yi"
        if value <= 40:
            return "Orta"
        if value <= 60:
            return "Hassas gruplar iÃ§in riskli"
        if value <= 80:
            return "SaÄŸlÄ±ksÄ±z"
        if value <= 100:
            return "Ã‡ok saÄŸlÄ±ksÄ±z"
        return "Tehlikeli"

    async def fetch(self, request: ProviderRequest) -> ProviderResponse[dict[str, Any]]:
        city, latitude, longitude = self._coordinates(dict(request.params))
        query = {
            "latitude": f"{latitude:.5f}",
            "longitude": f"{longitude:.5f}",
            "current": ",".join(self.current_variables),
            "timezone": "Europe/Istanbul",
        }
        url = f"{self.endpoint}?{urlencode(query)}"
        client = await self._get_client()
        try:
            payload = await ProviderHttpTransport(
                client,
                allowed_hosts={"air-quality-api.open-meteo.com"},
            ).get_json(
                url,
                timeout=min(max(request.timeout_seconds, 0.5), 8.0),
                error_code="air_quality_invalid_json",
            )
        except ProviderTransportError as exc:
            raise exc
        current = payload.get("current")
        if not isinstance(current, dict) or "time" not in current:
            raise RuntimeError("air_quality_schema_error")

        metrics = {
            key: self._number(current.get(key))
            for key in self.current_variables
            if current.get(key) is not None
        }
        if not any(key in metrics for key in ("european_aqi", "us_aqi", "pm2_5", "pm10")):
            raise RuntimeError("air_quality_schema_error")
        european_aqi = metrics.get("european_aqi")
        label = self._aqi_label(european_aqi)
        units = payload.get("current_units") if isinstance(payload.get("current_units"), dict) else {}

        def value_line(key: str, label_text: str) -> str | None:
            value = metrics.get(key)
            if value is None:
                return None
            unit = html.escape(str(units.get(key) or ("Âµg/mÂ³" if key not in {"european_aqi", "us_aqi"} else "")))
            return f"{label_text}: <b>{html.escape(str(value))} {unit}</b>"

        lines = [
            f"<b>ğŸŒ¿ {html.escape(city)} Hava Kalitesi</b>",
            f"ğŸ“Š Avrupa AQI: <b>{html.escape(str(european_aqi if european_aqi is not None else metrics.get('us_aqi', 'â€”')))}</b> Â· {html.escape(label)}",
        ]
        for key, label_text in (("pm2_5", "PM2.5"), ("pm10", "PM10"), ("ozone", "Ozon"), ("nitrogen_dioxide", "Azot dioksit")):
            line = value_line(key, label_text)
            if line:
                lines.append(line)

        pollen = {
            key: value
            for key, value in metrics.items()
            if key.endswith("_pollen") and value is not None
        }
        if pollen:
            lines.append("ğŸŒ¾ <b>Polen</b>: " + ", ".join(
                f"{html.escape(key.removesuffix('_pollen'))} {html.escape(str(value))}"
                for key, value in pollen.items()
            ))

        data = {
            "city": city,
            "latitude": latitude,
            "longitude": longitude,
            "observed_at": str(current.get("time"))[:64],
            "aqi": european_aqi,
            "aqi_label": label,
            "metrics": metrics,
            "content": "\n".join(lines),
        }
        return ProviderResponse(
            data=data,
            source="Open-Meteo Air Quality",
            source_url=url,
            metadata={
                "provider_kind": "typed",
                "platform": "open_meteo_air_quality",
                "coordinates_source": "local_city_snapshot" if self.city_directory else "validated_request",
            },
        )

    async def normalize(self, raw: Any) -> Any:
        return raw

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(name=self.name, healthy=True, detail="lazy_probe")

    async def close(self) -> None:
        if self._owned_client is not None:
            await self._owned_client.aclose()
            self._owned_client = None
