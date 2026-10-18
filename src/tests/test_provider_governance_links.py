import asyncio
from types import SimpleNamespace

import httpx

from scripts.provider_governance import check_manifest_links


def _registry():
    class Registry:
        def definitions(self):
            return (
                SimpleNamespace(
                    key="weather",
                    source_url="https://weather.example/source",
                    source_terms_url="https://weather.example/terms",
                ),
                SimpleNamespace(
                    key="invalid",
                    source_url=None,
                    source_terms_url="https://user:secret@example.test/terms",
                ),
            )

    return Registry()


def test_manifest_link_check_is_bounded_and_keeps_approval_separate():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/terms" and request.method == "HEAD":
            return httpx.Response(405, request=request)
        return httpx.Response(200, request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    try:
        report = asyncio.run(check_manifest_links(_registry(), client=client, concurrency=2))
    finally:
        asyncio.run(client.aclose())

    assert report["approval_unchanged"] is True
    assert report["link_count"] == 3
    assert report["passed"] == 2
    assert report["failed"] == 1
    assert {row["outcome"] for row in report["results"]} == {"ok", "invalid_url"}
    assert all("url" not in row for row in report["results"])


def test_manifest_link_check_marks_redirect_without_following_it():
    class Registry:
        def definitions(self):
            return (
                SimpleNamespace(
                    key="redirected",
                    source_url="https://example.test/source",
                    source_terms_url=None,
                ),
            )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://other.example/terms"}, request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    try:
        report = asyncio.run(check_manifest_links(Registry(), client=client))
    finally:
        asyncio.run(client.aclose())

    assert report["ok"] is False
    assert report["results"][0]["outcome"] == "redirect"
    assert report["results"][0]["source_host"] == "example.test"


def test_manifest_link_check_retries_head_redirect_with_bounded_get():
    class Registry:
        def definitions(self):
            return (
                SimpleNamespace(
                    key="head_redirect",
                    source_url="https://example.test/source",
                    source_terms_url=None,
                ),
            )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(302, headers={"Location": "/canonical"}, request=request)
        assert request.method == "GET"
        assert request.headers.get("Range") == "bytes=0-2047"
        return httpx.Response(200, text="bounded", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    try:
        report = asyncio.run(check_manifest_links(Registry(), client=client))
    finally:
        asyncio.run(client.aclose())

    assert report["ok"] is True
    assert report["passed"] == 1
    assert report["results"][0]["outcome"] == "ok"
