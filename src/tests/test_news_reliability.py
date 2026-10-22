import asyncio
import httpx
import pytest

from src.features.contracts import ProviderRequest
from src.features.providers.rss_news import BreakingNewsRssProvider
from src.features.runtime import ProviderRuntime


FEED = b'''<rss><channel>
<item><title>Economy fixture</title><link>https://www.trthaber.com/haber/ekonomi/example-1.html</link></item>
<item><title>World fixture</title><link>https://www.trthaber.com/haber/dunya/example-2.html</link></item>
</channel></rss>'''


@pytest.mark.asyncio
async def test_current_news_does_not_require_absent_rss_category_tags():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=FEED, headers={"content-type": "application/rss+xml"}))) as client:
        provider = BreakingNewsRssProvider(["https://www.trthaber.com/fixture.rss"], client=client)
        result = await provider.fetch(ProviderRequest(operation="news", params={"category": "guncel"}))
    assert len(result.data["items"]) == 2


@pytest.mark.asyncio
async def test_specific_news_category_uses_article_path_when_rss_omits_tags():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=FEED, headers={"content-type": "application/rss+xml"}))) as client:
        provider = BreakingNewsRssProvider(["https://www.trthaber.com/fixture.rss"], client=client)
        result = await provider.fetch(ProviderRequest(operation="news", params={"category": "ekonomi"}))
    assert result.data["titles"] == ["Economy fixture"]


@pytest.mark.asyncio
async def test_one_broken_feed_cannot_hide_valid_backup_feed():
    def transport(request):
        return httpx.Response(503) if request.url.path == "/broken.rss" else httpx.Response(200, content=FEED, headers={"content-type": "application/rss+xml"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        provider = BreakingNewsRssProvider(["https://www.trthaber.com/broken.rss", "https://www.trthaber.com/good.rss"], client=client)
        result = await provider.fetch(ProviderRequest(operation="news"))
    assert len(result.data["items"]) == 2
    assert result.source_url == "https://www.trthaber.com/good.rss"


@pytest.mark.asyncio
async def test_stalled_primary_leaves_runtime_budget_for_backup():
    async def transport(request):
        if request.url.path == "/stalled.rss":
            await asyncio.sleep(5)
        return httpx.Response(200, content=FEED, headers={"content-type": "application/rss+xml"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        provider = BreakingNewsRssProvider(["https://www.trthaber.com/stalled.rss", "https://www.trthaber.com/good.rss"], client=client)
        runtime = ProviderRuntime(provider, attempts=1)
        result = await runtime.fetch(ProviderRequest(operation="news", timeout_seconds=0.6))
    assert len(result.data["items"]) == 2
    assert result.source_url == "https://www.trthaber.com/good.rss"
