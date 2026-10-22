"""First-party typed public-data providers."""

from .earthquakes import EarthquakeProvider
from .flights import FlightSearchLinkProvider
from .culture import CultureProvider
from .story import StoryProvider
from .document_search import DocumentSearchProvider
from .public_data import (
    BimDealsProvider,
    CurrencyProvider,
    FuelProvider,
    PharmacyProvider,
    PrayerTimesProvider,
)
from .weather import WeatherProvider
from .rss_news import BreakingNewsRssProvider, RssNewsProvider
from .crypto import CryptoProvider
from .market import MarketProvider
from .air_quality import AirQualityProvider
from .web_reader import WebReaderProvider
from .web_search import TavilySearchProvider
from .lyrics import LyricsProvider

__all__ = [
    "EarthquakeProvider",
    "WeatherProvider",
    "PharmacyProvider",
    "FuelProvider",
    "CurrencyProvider",
    "BimDealsProvider",
    "PrayerTimesProvider",
    "FlightSearchLinkProvider",
    "CultureProvider",
    "StoryProvider",
    "DocumentSearchProvider",
    "RssNewsProvider",
    "BreakingNewsRssProvider",
    "CryptoProvider",
    "MarketProvider",
    "AirQualityProvider",
    "WebReaderProvider",
    "TavilySearchProvider",
    "LyricsProvider",
]
