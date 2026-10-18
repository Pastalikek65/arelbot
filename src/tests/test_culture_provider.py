import httpx
import pytest

from src.features.contracts import ProviderRequest
from src.features.providers.culture import CultureProvider


def _client(payload):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_book_provider_keeps_only_sourced_score_and_escapes_html():
    client = _client({
        "docs": [{
            "title": "A < Book",
            "author_name": ["Author"],
            "first_publish_year": 2020,
            "ratings_average": 4.2,
            "key": "/works/OL1W",
        }]
    })
    provider = CultureProvider(client)
    result = await provider.fetch(ProviderRequest("culture_recommendation", {"category": "book", "genre": "fantasy"}))
    assert result.source == "Open Library"
    assert result.metadata["scores_sourced_only"] is True
    assert "A &lt; Book" in result.data["content"]
    assert "Kaynak puanı: 4.2" in result.data["content"]
    await client.aclose()


@pytest.mark.asyncio
async def test_series_provider_normalizes_rating_and_link():
    client = _client({"results": [{"show": {
        "name": "Example",
        "premiered": "2024-01-01",
        "genres": ["Drama"],
        "rating": {"average": 8.1},
        "url": "https://api.tvmaze.com/shows/1",
    }}]})
    provider = CultureProvider(client)
    result = await provider.fetch(ProviderRequest("culture_recommendation", {"category": "series", "specific_query": "example"}))
    assert result.source == "TVmaze"
    assert "Kaynak puanı: 8.1" in result.data["content"]
    assert 'href="https://api.tvmaze.com/shows/1"' in result.data["content"]
    await client.aclose()


@pytest.mark.asyncio
async def test_music_provider_does_not_invent_scores():
    client = _client({"resultCount": 1, "results": [{
        "trackName": "Song",
        "artistName": "Artist",
        "collectionName": "Album",
        "releaseDate": "2025-01-01T00:00:00Z",
        "primaryGenreName": "Rock",
        "trackViewUrl": "https://music.apple.com/tr/song/1",
    }]})
    provider = CultureProvider(client)
    result = await provider.fetch(ProviderRequest("culture_recommendation", {"category": "music", "mood": "energetic"}))
    assert "Kaynak puanı" not in result.data["content"]
    assert "Artist" in result.data["content"]
    await client.aclose()


@pytest.mark.asyncio
async def test_music_provider_accepts_apples_text_javascript_json_content_type():
    payload = b'{"resultCount":1,"results":[{"trackName":"Song","artistName":"Artist"}]}'

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=payload,
            headers={"content-type": "text/javascript; charset=utf-8"},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await CultureProvider(client).fetch(
            ProviderRequest("culture_recommendation", {"category": "music", "mood": "energetic"})
        )
    assert result.data["items"][0]["artist"] == "Artist"


@pytest.mark.asyncio
async def test_culture_provider_rejects_invalid_category_and_empty_query():
    provider = CultureProvider(_client({}))
    with pytest.raises(ValueError, match="category"):
        await provider.fetch(ProviderRequest("culture_recommendation", {"category": "podcast", "genre": "x"}))
    # An empty query uses a category-specific safe default for the command UI.
    empty_client = _client({"docs": [{"title": "Default"}]})
    empty = CultureProvider(empty_client)
    result = await empty.fetch(ProviderRequest("culture_recommendation", {"category": "book"}))
    assert result.data["query"] == "popular books"
    await empty_client.aclose()
