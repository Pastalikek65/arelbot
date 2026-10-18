from __future__ import annotations

import httpx
import pytest

from src.features.contracts import ProviderRequest
from src.features.providers.earthquakes import EarthquakeProvider


@pytest.mark.asyncio
async def test_afad_provider_parses_fixture_table_and_escapes_text():
    fixture = """
    <html><body><table>
      <tr><th>Tarih</th><th>Enlem</th><th>Boylam</th><th>Derinlik</th><th>x</th><th>ML</th><th>Yer</th></tr>
      <tr><td>2026-10-04 10:00</td><td>39.1</td><td>27.2</td><td>7.4</td><td>x</td><td>4.2</td><td>A&amp;B yakınları</td></tr>
    </table></body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=fixture, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = EarthquakeProvider(client=client)
        result = await provider.fetch(ProviderRequest(operation="earthquakes"))

    assert result.source == "AFAD"
    assert result.data["earthquakes"][0]["magnitude"] == "4.2"
    assert "A&amp;B" in result.data["content"]


@pytest.mark.asyncio
async def test_afad_provider_rejects_empty_table():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<table><tr><td></td></tr></table>", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = EarthquakeProvider(client=client)
        with pytest.raises(RuntimeError, match="earthquake_schema_error"):
            await provider.fetch(ProviderRequest(operation="earthquakes"))


@pytest.mark.asyncio
async def test_afad_provider_prefers_event_json_service():
    payload = {
        "events": [
            {
                "eventID": "afad-1",
                "date": "2026-10-10T10:00:00Z",
                "latitude": 39.1,
                "longitude": 27.2,
                "depth": 7.4,
                "magnitude": 4.2,
                "location": "A&B yakınları",
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/apiv2/event/filter")
        return httpx.Response(200, json=payload, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await EarthquakeProvider(client=client).fetch(ProviderRequest(operation="earthquakes"))

    assert result.metadata["format"] == "json"
    assert result.data["earthquakes"][0]["event_id"] == "afad-1"
    assert "A&amp;B" in result.data["content"]
