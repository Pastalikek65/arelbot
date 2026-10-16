"""AFAD recent-earthquakes provider with a JSON-first, bounded fallback."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import html
from typing import Any

import httpx
from bs4 import BeautifulSoup

from ..contracts import ProviderHealth, ProviderRequest, ProviderResponse
from .transport import ProviderHttpTransport, ProviderTransportError
from src.security.tls import httpx_verify_kwargs


class EarthquakeProvider:
    name = "get_recent_earthquakes"
    capabilities = {"earthquakes", "seismic_activity"}
    # AFAD's public hostname redirects to the service gateway.  The gateway
    # is the documented JSON endpoint and avoids an extra redirect hop in the
    # bounded provider transport.
    api_url = "https://servisnet.afad.gov.tr/apigateway/deprem/apiv2/event/filter"
    html_url = "https://deprem.afad.gov.tr/last-earthquakes.html"
    source_url = api_url

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client
        self._owned_client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        if self._owned_client is None:
            self._owned_client = httpx.AsyncClient(
                timeout=httpx.Timeout(8.0, connect=3.0),
                headers={"Accept": "application/json, text/html", "User-Agent": "Arelbot/1.0"},
                follow_redirects=False,
                **httpx_verify_kwargs(),
            )
        return self._owned_client

    @staticmethod
    def _string(value: Any, default: str = "") -> str:
        if value is None:
            return default
        if isinstance(value, float):
            return f"{value:g}"
        return " ".join(str(value).split()) or default

    @classmethod
    def _normalize_json_rows(cls, payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            candidates = payload
        elif isinstance(payload, dict):
            candidates = None
            for key in ("events", "event", "data", "result", "items"):
                value = payload.get(key)
                if isinstance(value, list):
                    candidates = value
                    break
                if isinstance(value, dict):
                    candidates = [value]
                    break
            if candidates is None and any(key in payload for key in ("location", "magnitude", "mag", "eventID")):
                candidates = [payload]
            candidates = candidates or []
        else:
            candidates = []

        rows: list[dict[str, Any]] = []
        for item in candidates:
            if not isinstance(item, dict):
                continue
            location = cls._string(
                item.get("location") or item.get("lokasyon") or item.get("place") or item.get("title"),
                "Konum bilgisi yok",
            )
            date = cls._string(
                item.get("date") or item.get("eventDate") or item.get("datetime") or item.get("time"),
                "Zaman bilgisi yok",
            )
            magnitude = cls._string(
                item.get("magnitude") or item.get("mag") or item.get("ml") or item.get("mw"),
                "-",
            )
            depth = cls._string(
                item.get("depth") or item.get("depth_km") or item.get("depthKm"),
                "-",
            )
            rows.append(
                {
                    "event_id": cls._string(item.get("eventID") or item.get("eventId") or item.get("id")),
                    "date": date,
                    "latitude": cls._string(item.get("latitude") or item.get("lat")),
                    "longitude": cls._string(item.get("longitude") or item.get("lon")),
                    "depth_km": depth,
                    "magnitude": magnitude,
                    "location": location,
                    "raw": item,
                }
            )
            if len(rows) >= 10:
                break
        return rows

    @staticmethod
    def _cells(row) -> list[str]:
        return [" ".join(cell.get_text(" ", strip=True).split()) for cell in row.find_all(["td", "th"])]

    @classmethod
    def _normalize_html_rows(cls, body: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(body, "html.parser")
        table = soup.find("table")
        if table is None:
            return []
        rows: list[dict[str, Any]] = []
        for row in table.find_all("tr"):
            cells = cls._cells(row)
            if len(cells) < 5 or all(not value for value in cells):
                continue
            header = " ".join(cells).casefold()
            if "tarih" in header or "enlem" in header or "boylam" in header:
                continue
            rows.append(
                {
                    "event_id": "",
                    "date": cells[0],
                    "latitude": cells[1] if len(cells) > 1 else "",
                    "longitude": cells[2] if len(cells) > 2 else "",
                    "depth_km": cells[3] if len(cells) > 3 else "",
                    "magnitude": cells[5] if len(cells) > 5 else cells[4],
                    "location": cells[6] if len(cells) > 6 else cells[-1],
                    "raw": cells,
                }
            )
            if len(rows) >= 10:
                break
        return rows

    @staticmethod
    def _render(rows: list[dict[str, Any]]) -> str:
        lines = ["<b>ğŸ“Š Son Depremler (AFAD)</b>", ""]
        for item in rows:
            lines.append(
                f"ğŸ“ {html.escape(str(item['location']))} | <b>{html.escape(str(item['magnitude']))}</b>\n"
                f"ğŸ•’ {html.escape(str(item['date']))} Â· Derinlik: {html.escape(str(item['depth_km']))} km"
            )
        return "\n\n".join(lines)

    async def fetch(self, request: ProviderRequest) -> ProviderResponse[dict[str, Any]]:
        client = await self._get_client()
        transport = ProviderHttpTransport(
            client,
            allowed_hosts={"servisnet.afad.gov.tr", "deprem.afad.gov.tr"},
        )
        now = datetime.now(timezone.utc)
        start = request.params.get("start") or (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
        end = request.params.get("end") or now.strftime("%Y-%m-%dT%H:%M:%S")
        params = {
            "start": str(start),
            "end": str(end),
            "limit": min(max(int(request.params.get("limit", 10) or 10), 1), 20),
            "orderby": "timedesc",
            "format": "json",
        }
        rows: list[dict[str, Any]] = []
        source_url = self.api_url
        try:
            payload = await transport.get_json(
                self.api_url,
                params=params,
                timeout=min(max(request.timeout_seconds, 0.5), 8.0),
                error_code="earthquake_json_invalid",
            )
            rows = self._normalize_json_rows(payload)
            if not rows:
                raise RuntimeError("earthquake_schema_error")
        except ProviderTransportError as exc:
            if exc.code not in {
                "provider_content_type_invalid",
                "earthquake_json_invalid",
                "provider_upstream_http",
                "provider_response_too_large",
            }:
                raise
        except RuntimeError:
            pass

        if not rows:
            body = await transport.get_text(
                self.html_url,
                timeout=min(max(request.timeout_seconds, 0.5), 8.0),
                expected_content_types=("text/html", "application/xhtml+xml", "text/plain"),
            )
            rows = self._normalize_html_rows(body)
            source_url = self.html_url
        if not rows:
            raise RuntimeError("earthquake_schema_error")
        return ProviderResponse(
            data={"earthquakes": rows, "content": self._render(rows)},
            source="AFAD",
            source_url=source_url,
            metadata={
                "provider_kind": "typed",
                "records": len(rows),
                "format": "json" if source_url == self.api_url else "html",
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
