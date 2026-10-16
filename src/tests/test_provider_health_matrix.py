from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.features.city_directory import CityDirectory
from src.features.contracts import ProviderHealth
from src.features.providers import (
    AirQualityProvider,
    BimDealsProvider,
    CultureProvider,
    CurrencyProvider,
    DocumentSearchProvider,
    EarthquakeProvider,
    FlightSearchLinkProvider,
    FuelProvider,
    MarketProvider,
    NewsProvider,
    PharmacyProvider,
    PrayerTimesProvider,
    RssNewsProvider,
    StoryProvider,
    WeatherProvider,
)
from src.features.providers.crypto import CryptoProvider


@pytest.mark.asyncio
async def test_all_first_party_typed_providers_expose_lazy_health_contract():
    # Health checks are intentionally lazy and must not call live sites. This
    # matrix protects the registry surface as providers are added or replaced.
    providers = [
        PharmacyProvider(),
        FuelProvider(),
        CurrencyProvider(),
        NewsProvider(),
        BimDealsProvider(),
        PrayerTimesProvider(),
        WeatherProvider(),
        EarthquakeProvider(),
        AirQualityProvider(CityDirectory()),
        CryptoProvider(),
        MarketProvider(loader=lambda symbol: {"rows": []}),
        CultureProvider(),
        FlightSearchLinkProvider(),
        StoryProvider(),
        DocumentSearchProvider(SimpleNamespace(collection=None)),
        RssNewsProvider(feeds=("https://fixture.invalid/news.xml",)),
    ]

    for provider in providers:
        assert str(provider.name).strip()
        assert isinstance(provider.capabilities, set)
        assert callable(getattr(provider, "fetch", None))
        assert callable(getattr(provider, "normalize", None))
        assert callable(getattr(provider, "health_check", None))
        health = await provider.health_check()
        assert isinstance(health, ProviderHealth)
        assert health.name == provider.name
        assert isinstance(health.healthy, bool)
