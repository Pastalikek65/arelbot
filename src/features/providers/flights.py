"""Safe flight-search link provider.

No upstream HTML is scraped and no live price is invented. The provider only
validates the requested route and builds fixed-host search URLs that users can
open in Google Flights or a general search engine.
"""

from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

from ..contracts import ProviderHealth, ProviderRequest, ProviderResponse


_LOCATION_RE = re.compile(r"^[\w][\w .()'’/-]{0,78}[\w)]$|^[\w]$", re.UNICODE)


def _location(value: Any, field_name: str) -> str:
    text = " ".join(str(value or "").split())
    if not text or len(text) > 80 or not _LOCATION_RE.fullmatch(text):
        raise ValueError(f"invalid_{field_name}")
    if not any(character.isalpha() for character in text):
        raise ValueError(f"invalid_{field_name}")
    return text


def _flight_date(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    for date_format in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text, date_format).date()
            return parsed.isoformat()
        except ValueError:
            continue
    raise ValueError("invalid_date")


class FlightSearchLinkProvider:
    """Generate deterministic route search links without network access."""

    name = "search_flights"
    capabilities = {"flight_search", "travel_search_links"}
    source_name = "Arelbot uçuş arama bağlantıları"

    @staticmethod
    def _build(request: ProviderRequest) -> ProviderResponse[dict[str, Any]]:
        origin = _location(request.params.get("origin"), "origin")
        destination = _location(request.params.get("destination"), "destination")
        normalized_date = _flight_date(request.params.get("date"))

        phrase = f"Flights from {origin} to {destination}"
        if normalized_date:
            phrase += f" on {normalized_date}"
        links = [
            {
                "provider": "Google Flights",
                "url": "https://www.google.com/travel/flights?" + urlencode(
                    {"q": phrase, "hl": "tr", "curr": "TRY"}
                ),
            },
            {
                "provider": "Google arama",
                "url": "https://www.google.com/search?" + urlencode(
                    {"q": f'"{origin}" "{destination}" uçak bileti {normalized_date or ""}'.strip(), "hl": "tr"}
                ),
            },
            {
                "provider": "Bing arama",
                "url": "https://www.bing.com/search?" + urlencode(
                    {"q": f'"{origin}" "{destination}" uçak bileti {normalized_date or ""}'.strip(), "cc": "TR"}
                ),
            },
        ]

        route = f"{html.escape(origin)} → {html.escape(destination)}"
        lines = [f"<b>✈️ {route} uçuş araması</b>", ""]
        if normalized_date:
            lines.extend((f"📅 Tarih: <b>{html.escape(normalized_date)}</b>", ""))
        lines.append("Güncel sefer ve fiyatları arama servislerinde görüntüleyebilirsiniz:")
        for item in links:
            safe_url = html.escape(item["url"], quote=True)
            lines.append(f'• <a href="{safe_url}">{html.escape(item["provider"])}</a>')
        lines.extend(("", "<i>Fiyat bilgisi bu botta doğrulanmaz; bağlantılar arama sayfalarını açar.</i>"))
        return ProviderResponse(
            data={
                "origin": origin,
                "destination": destination,
                "date": normalized_date,
                "links": links,
                "content": "\n".join(lines),
            },
            source=FlightSearchLinkProvider.source_name,
            source_url=None,
            metadata={"provider_kind": "local_link_builder", "live_prices": False},
        )

    async def fetch(self, request: ProviderRequest) -> ProviderResponse[dict[str, Any]]:
        return self._build(request)

    async def normalize(self, raw: Any) -> Any:
        return raw

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(name=self.name, healthy=True, detail="local_link_builder")
