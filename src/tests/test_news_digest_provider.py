from __future__ import annotations

import httpx
import pytest

from src.features.contracts import FeatureResult, ProviderRequest
from src.features.providers.rss_news import BreakingNewsRssProvider, RssNewsProvider
from src.services.news_digest_service import NewsDigestService


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_rss_provider_normalizes_items_and_preserves_source_metadata():
    xml = """
    <rss><channel>
      <title>Arel test feed</title>
      <item><title>Ä°lk baÅŸlÄ±k &amp; ayrÄ±ntÄ±</title><link>https://news.example/1</link>
        <description>Ä°lk aÃ§Ä±klama</description><pubDate>Mon, 05 Oct 2026 08:00:00 GMT</pubDate></item>
      <item><title>Ä°lk baÅŸlÄ±k &amp; ayrÄ±ntÄ±</title><link>https://news.example/duplicate</link></item>
      <item><title>Ä°kinci baÅŸlÄ±k</title><link>https://news.example/2</link></item>
    </channel></rss>
    """

    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://feed.example/news.xml"
        return httpx.Response(200, text=xml, request=request)

    async with _client(handler) as client:
        provider = RssNewsProvider(
            feeds=["https://feed.example/news.xml"],
            client=client,
        )
        response = await provider.fetch(ProviderRequest(operation="news_digest", params={"limit": 5}))

    assert [item["title"] for item in response.data["items"]] == [
        "Ä°lk baÅŸlÄ±k & ayrÄ±ntÄ±",
        "Ä°kinci baÅŸlÄ±k",
    ]
    assert response.data["items"][0]["published_at"]
    assert response.source == "RSS haber akÄ±ÅŸlarÄ±"
    assert response.source_url == "https://feed.example/news.xml"
    assert response.metadata["provider_kind"] == "typed"
    assert response.metadata["records"] == 2
    assert "&lt;" not in response.data["content"]


@pytest.mark.asyncio
async def test_breaking_news_rss_keeps_legacy_titles_shape_and_rich_items():
    xml = """
    <rss><channel>
      <item><title>Ä°lk son dakika</title><link>https://news.example/1</link></item>
      <item><title>Ä°kinci son dakika</title><link>https://news.example/2</link></item>
    </channel></rss>
    """

    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://feed.example/news.xml"
        return httpx.Response(200, text=xml, request=request)

    async with _client(handler) as client:
        provider = BreakingNewsRssProvider(
            feeds=["https://feed.example/news.xml"],
            client=client,
        )
        response = await provider.fetch(
            ProviderRequest(operation="breaking_news", params={"limit": 5})
        )

    assert response.source == "TRT Haber RSS akÄ±ÅŸlarÄ±"
    assert response.data["titles"] == ["Ä°lk son dakika", "Ä°kinci son dakika"]
    assert response.data["items"][0]["link"] == "https://news.example/1"
    assert response.metadata["compatibility_shape"] == "breaking_news"


@pytest.mark.asyncio
async def test_breaking_news_rss_labels_category_in_telegram_content():
    xml = """
    <rss><channel>
      <item><title>Spor son dakika</title><category>spor</category><link>https://news.example/s</link></item>
    </channel></rss>
    """

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=xml, request=request)

    async with _client(handler) as client:
        provider = BreakingNewsRssProvider(feeds=["https://feed.example/news.xml"], client=client)
        response = await provider.fetch(
            ProviderRequest(operation="breaking_news", params={"category": "spor"})
        )

    assert "Haberleri Â· Spor" in response.data["content"]


@pytest.mark.asyncio
async def test_rss_provider_filters_turkish_category_and_preserves_category_metadata():
    xml = """
    <rss><channel>
      <item><title>Spor baÅŸlÄ±ÄŸÄ±</title><category> Spor </category><link>https://news.example/s</link></item>
      <item><title>Ekonomi baÅŸlÄ±ÄŸÄ±</title><category>ekonomi</category><link>https://news.example/e</link></item>
    </channel></rss>
    """

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=xml, request=request)

    async with _client(handler) as client:
        provider = RssNewsProvider(feeds=["https://feed.example/news.xml"], client=client)
        response = await provider.fetch(
            ProviderRequest(operation="news_digest", params={"category": "SPOR", "limit": 5})
        )

    assert response.data["category"] == "spor"
    assert [item["title"] for item in response.data["items"]] == ["Spor baÅŸlÄ±ÄŸÄ±"]
    assert response.data["items"][0]["categories"] == "spor"
    assert response.metadata["category"] == "spor"


@pytest.mark.asyncio
async def test_rss_provider_rejects_unknown_category_before_fetch():
    provider = RssNewsProvider(feeds=["https://feed.example/news.xml"], client=_client("unused"))
    with pytest.raises(ValueError, match="news_category_invalid"):
        await provider.fetch(ProviderRequest(operation="news_digest", params={"category": "magazin"}))


@pytest.mark.asyncio
async def test_rss_provider_drops_unsafe_article_links_but_keeps_titles():
    xml = """
    <rss><channel>
      <item><title>GÃ¼venli baÅŸlÄ±k</title><link>javascript:alert(1)</link></item>
      <item><title>Kimlik bilgili baÄŸlantÄ±</title><link>https://user:secret@news.example/2</link></item>
      <item><title>Normal baÅŸlÄ±k</title><link>https://news.example/3</link></item>
    </channel></rss>
    """

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=xml, request=request)

    async with _client(handler) as client:
        provider = RssNewsProvider(feeds=["https://feed.example/news.xml"], client=client)
        response = await provider.fetch(ProviderRequest(operation="news_digest", params={"limit": 5}))

    items = response.data["items"]
    assert [item["title"] for item in items] == [
        "GÃ¼venli baÅŸlÄ±k", "Kimlik bilgili baÄŸlantÄ±", "Normal baÅŸlÄ±k"
    ]
    assert items[0]["link"] == ""
    assert items[1]["link"] == ""
    assert items[2]["link"] == "https://news.example/3"
    assert "javascript:" not in response.data["content"]
    assert "secret" not in response.data["content"]


@pytest.mark.asyncio
async def test_news_digest_service_consumes_feature_executor_result_without_http():
    class FakeExecutor:
        def __init__(self):
            self.calls = []

        async def execute(self, feature_key, params):
            self.calls.append((feature_key, params))
            return FeatureResult.success(
                {"items": [{"title": "Yerel baÅŸlÄ±k", "link": "https://news.example/item"}]},
                display_blocks=("<b>Yerel</b>",),
            )

    executor = FakeExecutor()
    service = NewsDigestService(feature_executor=executor)

    items = await service.fetch_items(limit=3)

    assert items == [{"title": "Yerel baÅŸlÄ±k", "link": "https://news.example/item"}]
    assert executor.calls == [("news_digest", {"limit": 3})]


def test_rss_provider_rejects_private_or_credentialed_feed_configuration():
    with pytest.raises(ValueError, match="rss_feed_public_required"):
        RssNewsProvider(feeds=["http://127.0.0.1/internal.xml"])

    with pytest.raises(ValueError, match="rss_feed_public_required"):
        RssNewsProvider(feeds=["https://user:password@feed.example/news.xml"])
