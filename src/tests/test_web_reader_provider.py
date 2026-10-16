from __future__ import annotations

import httpx
import pytest

from src.features.contracts import ProviderRequest
from src.features.providers.web_reader import WebReaderProvider


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_web_reader_provider_extracts_bounded_untrusted_text():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text="<html><script>ignore()</script><body><h1>BaÅŸlÄ±k</h1><p>GÃ¼venilmeyen iÃ§erik</p></body></html>",
            request=request,
        )

    async with _client(handler) as client:
        provider = WebReaderProvider(client=client, resolve_host=False)
        response = await provider.fetch(
            ProviderRequest(operation="web_reader", params={"url": "https://example.com/page"})
        )

    assert response.source == "Public web page"
    assert response.source_url == "https://example.com/page"
    assert "ignore" not in response.data["content"]
    assert "GÃ¼venilmeyen iÃ§erik" in response.data["content"]
    assert response.metadata["untrusted"] is True


@pytest.mark.asyncio
async def test_web_reader_rejects_private_hosts_before_transport():
    called = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, text="unexpected", request=request)

    async with _client(handler) as client:
        provider = WebReaderProvider(client=client)
        with pytest.raises(ValueError, match="web_url_private_host"):
            await provider.fetch(
                ProviderRequest(operation="web_reader", params={"url": "http://127.0.0.1/internal"})
            )

    assert called is False


@pytest.mark.asyncio
async def test_web_reader_revalidates_redirect_targets():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"location": "http://127.0.0.1/metadata"},
            request=request,
        )

    async with _client(handler) as client:
        provider = WebReaderProvider(client=client, resolve_host=False)
        with pytest.raises(ValueError, match="web_url_private_host"):
            await provider.fetch(
                ProviderRequest(operation="web_reader", params={"url": "https://example.com/start"})
            )


@pytest.mark.asyncio
async def test_web_reader_rejects_non_text_content_and_oversized_body():
    async def binary_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/pdf"},
            content=b"%PDF-1.7",
            request=request,
        )

    async with _client(binary_handler) as client:
        provider = WebReaderProvider(client=client, resolve_host=False)
        with pytest.raises(ValueError, match="web_content_type_unsupported"):
            await provider.fetch(ProviderRequest(operation="web_reader", params={"url": "https://example.com/file"}))

    async def large_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            content=b"x" * 2048,
            request=request,
        )

    async with _client(large_handler) as client:
        provider = WebReaderProvider(client=client, resolve_host=False, max_body_bytes=16)
        with pytest.raises(ValueError, match="web_body_too_large"):
            await provider.fetch(ProviderRequest(operation="web_reader", params={"url": "https://example.com/large"}))
