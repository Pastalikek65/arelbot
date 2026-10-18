import httpx
import pytest
import json
from unittest.mock import Mock

from src.features.contracts import ProviderRequest
from src.features.providers.web_search import TavilySearchProvider
from src.tools.web_search_tool import WebSearchTool
from src.key_manager import NoKeysConfiguredError


def _key_manager(key: str = "test-tavily-key"):
    manager = Mock()
    manager.get_key.return_value = key
    manager.report_error.return_value = True
    return manager


def _empty_key_manager():
    manager = Mock()
    manager.get_key.side_effect = NoKeysConfiguredError("missing")
    manager.keys = {}
    return manager


@pytest.mark.asyncio
async def test_tavily_provider_normalizes_results_without_leaking_key():
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["api_key"] == "test-tavily-key"
        assert payload["query"] == "İstanbul hava durumu"
        return httpx.Response(
            200,
            json={
                "answer": "Kısa yanıt",
                "results": [
                    {
                        "title": "<Başlık>",
                        "content": "Güvenilmeyen <script>alert(1)</script> içerik",
                        "url": "https://example.com/result",
                    }
                ],
            },
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = TavilySearchProvider(_key_manager(), client=client)
        response = await provider.fetch(
            ProviderRequest(
                operation="web_search",
                params={"query": "İstanbul hava durumu", "topic": "general"},
            )
        )

    assert response.source == "Tavily Search"
    assert response.metadata["untrusted"] is True
    assert response.data["results"][0]["title"] == "<Başlık>"
    assert "&lt;script&gt;" in response.data["content"]
    assert "test-tavily-key" not in str(response.data)


@pytest.mark.asyncio
async def test_tavily_provider_marks_quota_key_and_rotates_once():
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, json={"error": "quota"}, request=request)
        return httpx.Response(200, json={"results": [{"title": "OK", "content": "ok", "url": "https://example.com"}]}, request=request)

    manager = _key_manager()
    manager.get_key.side_effect = ["first-key", "second-key"]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = TavilySearchProvider(manager, client=client)
        response = await provider.fetch(ProviderRequest(operation="web_search", params={"query": "test"}))

    assert calls == 2
    assert response.data["results"][0]["title"] == "OK"
    manager.report_error.assert_called_once()


@pytest.mark.asyncio
async def test_tavily_provider_rejects_invalid_input_before_transport():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("transport must not be called")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = TavilySearchProvider(_key_manager(), client=client)
        with pytest.raises(ValueError, match="web_search_query_required"):
            await provider.fetch(ProviderRequest(operation="web_search", params={"query": "   "}))


@pytest.mark.asyncio
async def test_web_search_tool_is_a_typed_provider_wrapper():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [{"title": "Sonuç", "content": "İçerik", "url": "https://example.com"}]}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = TavilySearchProvider(_key_manager(), client=client)
        tool = WebSearchTool(provider)
        result = await tool.execute(query="test")

    assert result["source"] == "Tavily Search"
    assert "Sonuç" in result["result"]

@pytest.mark.asyncio
async def test_search_uses_bounded_duckduckgo_fallback_without_tavily_key():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.duckduckgo.com"
        assert request.url.params["q"] == "Python"
        return httpx.Response(
            200,
            json={
                "Heading": "Python",
                "AbstractText": "Programlama dili",
                "AbstractURL": "https://www.python.org/",
                "RelatedTopics": [],
            },
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = TavilySearchProvider(_empty_key_manager(), client=client)
        response = await provider.fetch(ProviderRequest(operation="web_search", params={"query": "Python"}))

    assert response.source == "DuckDuckGo Instant Answers"
    assert response.metadata["fallback"] == "duckduckgo"
    assert response.data["results"][0]["url"] == "https://www.python.org/"
