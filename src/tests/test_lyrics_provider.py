import pytest

from src.features.contracts import ProviderRequest, ProviderResponse
from src.features.providers.lyrics import LyricsProvider


class FakeSearchProvider:
    name = "web_search"

    def __init__(self, results):
        self.results = results
        self.requests = []

    async def fetch(self, request):
        self.requests.append(request)
        return ProviderResponse(
            data={"results": self.results},
            source="Tavily Search",
            source_url="https://docs.tavily.com/",
        )


class FakeReaderProvider:
    name = "web_reader"

    def __init__(self, content):
        self.content = content
        self.requests = []

    async def fetch(self, request):
        self.requests.append(request)
        return ProviderResponse(
            data={"content": self.content, "title": "Fixture lyrics page", "url": request.params["url"]},
            source="Public web page",
            source_url=request.params["url"],
        )


@pytest.mark.asyncio
async def test_lyrics_provider_uses_allowed_source_and_bounded_excerpt():
    search = FakeSearchProvider([
        {"title": "Untrusted result", "url": "https://evil.example/redirect"},
        {"title": "Song page", "url": "https://genius.com/artist/song"},
    ])
    reader = FakeReaderProvider("Satır 1\n" + ("çok uzun " * 1000))
    provider = LyricsProvider(search, reader)

    response = await provider.fetch(
        ProviderRequest(operation="lyrics", params={"song": "Şarkı", "artist": "Sanatçı"})
    )

    assert search.requests[0].params["query"].startswith("lyrics Şarkı Sanatçı")
    assert reader.requests[0].params["url"] == "https://genius.com/artist/song"
    assert len(response.data["excerpt"]) <= 2500
    assert response.source_url == "https://genius.com/artist/song"
    assert "complete lyrics" not in response.data["content"].casefold()


@pytest.mark.asyncio
async def test_lyrics_provider_rejects_when_no_allowed_result_exists():
    provider = LyricsProvider(
        FakeSearchProvider([{"url": "https://example.com/not-lyrics"}]),
        FakeReaderProvider("never used"),
    )

    with pytest.raises(ValueError, match="lyrics_source_not_found"):
        await provider.fetch(ProviderRequest(operation="lyrics", params={"song": "Test"}))


@pytest.mark.asyncio
async def test_lyrics_provider_requires_song_before_search():
    search = FakeSearchProvider([])
    provider = LyricsProvider(search, FakeReaderProvider("unused"))

    with pytest.raises(ValueError, match="lyrics_song_required"):
        await provider.fetch(ProviderRequest(operation="lyrics", params={"song": "   "}))

