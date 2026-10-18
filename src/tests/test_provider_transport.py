from __future__ import annotations

import httpx
import pytest

from src.features.providers.transport import (
    ProviderHttpTransport,
    ProviderTransportError,
    validate_provider_url,
)


@pytest.mark.asyncio
async def test_transport_reads_json_with_content_type_and_host_policy():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.example"
        return httpx.Response(
            200,
            json={"ok": True},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        payload = await ProviderHttpTransport(client, allowed_hosts={"api.example"}).get_json(
            "https://api.example/data",
            timeout=1,
        )

    assert payload == {"ok": True}


@pytest.mark.asyncio
async def test_transport_rejects_redirects_and_oversized_streams():
    calls = 0

    async def redirect_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://evil.example"}, request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(redirect_handler),
        follow_redirects=True,
    ) as client:
        with pytest.raises(ProviderTransportError, match="provider_redirect_rejected"):
            await ProviderHttpTransport(client, allowed_hosts={"api.example"}).get_bytes(
                "https://api.example/feed",
                timeout=1,
            )

    async def large_handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            content=b"x" * 32,
            headers={"content-type": "application/json"},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(large_handler)) as client:
        with pytest.raises(ProviderTransportError, match="provider_response_too_large"):
            await ProviderHttpTransport(
                client,
                allowed_hosts={"api.example"},
                max_response_bytes=16,
            ).get_bytes("https://api.example/data", timeout=1)
    assert calls == 1


@pytest.mark.asyncio
async def test_transport_maps_upstream_status_without_exposing_response_body():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"secret": "upstream-detail"}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderTransportError) as caught:
            await ProviderHttpTransport(client, allowed_hosts={"api.example"}).get_json(
                "https://api.example/data",
                timeout=1,
            )

    assert caught.value.code == "provider_upstream_http"
    assert caught.value.status_code == 429
    assert "upstream-detail" not in str(caught.value)


@pytest.mark.asyncio
async def test_transport_enforces_post_redirect_policy_and_bounded_json_response():
    async def redirect_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://evil.example"}, request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(redirect_handler),
        follow_redirects=True,
    ) as client:
        with pytest.raises(ProviderTransportError, match="provider_redirect_rejected"):
            await ProviderHttpTransport(client, allowed_hosts={"api.example"}).post_json(
                "https://api.example/query",
                json_body={"q": "safe"},
                timeout=1,
            )

    async def large_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"{" + (b" " * 32) + b"}",
            headers={"content-type": "application/json"},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(large_handler)) as client:
        with pytest.raises(ProviderTransportError, match="provider_response_too_large"):
            await ProviderHttpTransport(
                client,
                allowed_hosts={"api.example"},
                max_response_bytes=16,
            ).post_json(
                "https://api.example/query",
                json_body={"q": "safe"},
                timeout=1,
            )


def test_transport_rejects_private_credentials_and_unexpected_hosts():
    with pytest.raises(ProviderTransportError, match="provider_url_private_host"):
        validate_provider_url("https://127.0.0.1/internal")
    with pytest.raises(ProviderTransportError, match="provider_url_credentials_forbidden"):
        validate_provider_url("https://user:secret@api.example/data")
    with pytest.raises(ProviderTransportError, match="provider_host_not_allowed"):
        validate_provider_url("https://other.example/data", allowed_hosts={"api.example"})
