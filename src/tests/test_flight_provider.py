from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

from src.features.contracts import ProviderRequest
from src.features.providers.flights import FlightSearchLinkProvider
from src.security.output_sanitizer import sanitize_html
from src.tools.spatula_tool import FlightTool


@pytest.mark.asyncio
async def test_flight_provider_builds_route_and_date_search_links_without_network():
    provider = FlightSearchLinkProvider()
    response = await provider.fetch(
        ProviderRequest(
            operation="flight_search",
            params={"origin": "Ä°stanbul", "destination": "Ankara", "date": "05/11/2026"},
        )
    )

    assert response.data["origin"] == "Ä°stanbul"
    assert response.data["destination"] == "Ankara"
    assert response.data["date"] == "2026-11-05"
    assert response.metadata["live_prices"] is False
    assert len(response.data["links"]) == 3
    for link in response.data["links"]:
        parsed = urlsplit(link["url"])
        assert parsed.scheme == "https"
        assert parsed.netloc in {"www.google.com", "www.bing.com"}
        query = parse_qs(parsed.query)
        assert "Ä°stanbul" in query["q"][0]
        assert "Ankara" in query["q"][0]
        assert "2026-11-05" in query["q"][0]

    safe_html = sanitize_html(response.data["content"])
    assert safe_html.count("<a href=") == 3
    assert "fiyat bilgisi" in safe_html.casefold()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("origin", "destination", "date", "error"),
    [
        ("", "Ankara", None, "invalid_origin"),
        ("Istanbul", "<script>alert(1)</script>", None, "invalid_destination"),
        ("IST", "ESB", "31/02/2027", "invalid_date"),
        ("IST", "ESB", "2027-13-01", "invalid_date"),
    ],
)
async def test_flight_provider_rejects_invalid_input(origin, destination, date, error):
    with pytest.raises(ValueError, match=error):
        await FlightSearchLinkProvider().fetch(
            ProviderRequest(
                operation="flight_search",
                params={"origin": origin, "destination": destination, "date": date},
            )
        )


@pytest.mark.asyncio
async def test_ai_flight_tool_uses_link_provider_and_does_not_scrape_live_results():
    result = await FlightTool().execute("Ä°stanbul", "Ä°zmir", "2026-11-05")

    assert "Google Flights" in result["result"]
    assert len(result["links"]) == 3
    assert "price" not in result["result"].casefold()
