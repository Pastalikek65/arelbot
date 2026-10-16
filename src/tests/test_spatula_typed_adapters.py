from __future__ import annotations

from pathlib import Path

import pytest

from src.features.contracts import ProviderResponse
from src.tools.spatula_tool import (
    BimDealsTool,
    CurrencyTool,
    EarthquakeTool,
    FuelTool,
    NewsTool,
    PharmacyTool,
    PrayerTimesTool,
    WeatherTool,
)


class _FixtureProvider:
    name = "fixture_provider"

    def __init__(self):
        self.calls = []

    async def fetch(self, request):
        self.calls.append(request)
        return ProviderResponse(
            data={"content": "fixture"},
            source="Fixture provider",
            source_url="https://fixture.invalid/source",
        )


@pytest.mark.asyncio
async def test_legacy_public_data_tools_forward_to_typed_provider_without_scraping():
    cases = [
        (PharmacyTool, {"city": "Ä°stanbul", "district": "KadÄ±kÃ¶y"}),
        (FuelTool, {"city": "Ankara"}),
        (CurrencyTool, {}),
        (EarthquakeTool, {}),
        (BimDealsTool, {}),
        (NewsTool, {}),
        (WeatherTool, {"city": "Ä°zmir", "days": 3}),
        (PrayerTimesTool, {"city": "Bursa"}),
    ]
    for tool_type, params in cases:
        provider = _FixtureProvider()
        result = await tool_type(provider=provider).execute(**params)
        assert result == {"content": "fixture"}
        assert len(provider.calls) == 1
        request = provider.calls[0]
        assert request.operation == tool_type(provider=_FixtureProvider()).name
        assert dict(request.params) == params


def test_legacy_spatula_module_has_no_direct_network_or_html_scraper_imports():
    source = Path("src/tools/spatula_tool.py").read_text(encoding="utf-8")
    assert "import httpx" not in source
    assert "from bs4" not in source
    assert "AsyncClient" not in source
    assert "BeautifulSoup" not in source
