import httpx
import pytest

from src.features.contracts import ProviderRequest
from src.features.providers.culture import CultureProvider
from src.features.providers.transport import ProviderTransportError


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


def _film_entity(identifier="Q13417189", title="Yıldızlararası"):
    return {
        "id": identifier,
        "labels": {"tr": {"value": title}},
        "descriptions": {"tr": {"value": "2014 yapımı bilim kurgu filmi"}},
        "claims": {
            "P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q11424"}}}}],
            "P577": [{"mainsnak": {"datavalue": {"value": {"time": "+2014-11-07T00:00:00Z"}}}}],
        },
    }


@pytest.mark.asyncio
async def test_movie_provider_searches_wikidata_and_confirms_film_type():
    seen = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.headers["user-agent"].startswith("Arelbot/1.0")
        if request.url.params["action"] == "wbsearchentities":
            return httpx.Response(200, json={"search": [
                {"id": "Q1", "description": "interstellar medium"},
                {"id": "Q13417189", "description": "2014 film by Christopher Nolan"},
            ]}, request=request)
        return httpx.Response(200, json={"entities": {"Q13417189": _film_entity()}}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await CultureProvider(client).fetch(
            ProviderRequest("culture_recommendation", {"category": "movie", "specific_query": "Interstellar"})
        )
    assert len(seen) == 2
    assert result.source == "Wikidata"
    assert result.data["items"][0]["year"] == "2014"
    assert 'href="https://www.wikidata.org/wiki/Q13417189"' in result.data["content"]


@pytest.mark.asyncio
async def test_movie_genre_uses_bounded_pool_and_rejects_nonfilm_entity():
    seen = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        ids = request.url.params["ids"].split("|")
        entities = {identifier: _film_entity(identifier, f"Film {index}") for index, identifier in enumerate(ids)}
        entities[ids[0]]["claims"]["P31"][0]["mainsnak"]["datavalue"]["value"]["id"] = "Q5"
        return httpx.Response(200, json={"entities": entities}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await CultureProvider(client).fetch(
            ProviderRequest("culture_recommendation", {"category": "movie", "genre": "bilim kurgu"})
        )
    assert len(seen) == 1
    assert len(result.data["items"]) == 4
    assert all(item["url"].startswith("https://www.wikidata.org/wiki/Q") for item in result.data["items"])


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 503])
async def test_movie_provider_propagates_upstream_limit_and_failure(status):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": "upstream"}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderTransportError) as caught:
            await CultureProvider(client).fetch(
                ProviderRequest("culture_recommendation", {"category": "movie", "specific_query": "Interstellar"})
            )
    assert caught.value.status_code == status


@pytest.mark.asyncio
async def test_movie_provider_rejects_empty_or_invalid_schema():
    async with _client({"search": []}) as client:
        with pytest.raises(RuntimeError, match="culture_schema_empty"):
            await CultureProvider(client).fetch(
                ProviderRequest("culture_recommendation", {"category": "movie", "specific_query": "no such film"})
            )


def test_movie_release_year_ignores_deprecated_planned_dates_and_uses_first_release():
    entity = _film_entity()
    entity["claims"]["P577"] = [
        {"rank": "deprecated", "mainsnak": {"datavalue": {"value": {"time": "+2023-10-18T00:00:00Z"}}}},
        {"rank": "normal", "mainsnak": {"datavalue": {"value": {"time": "+2024-03-01T00:00:00Z"}}}},
        {"rank": "normal", "mainsnak": {"datavalue": {"value": {"time": "+2024-02-27T00:00:00Z"}}}},
    ]
    assert CultureProvider._release_year(entity) == "2024"
