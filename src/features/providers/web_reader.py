"""Bounded typed provider for reading public web pages as untrusted context."""

from __future__ import annotations

import html
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from ..contracts import ProviderHealth, ProviderRequest, ProviderResponse
from src.security.public_url import validate_public_url
from src.security.tls import httpx_verify_kwargs


class WebReaderProvider:
    name = "web_reader"
    capabilities = {"web_reader", "public_web", "untrusted_context"}
    timeout_seconds = 15.0
    max_redirects = 3
    allowed_content_types = frozenset({"text/html", "application/xhtml+xml", "text/plain", "application/json", "application/xml", "text/xml"})

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        resolve_host: bool = True,
        max_body_bytes: int = 1_000_000,
    ) -> None:
        self._client = client
        self._owned_client: httpx.AsyncClient | None = None
        self.resolve_host = bool(resolve_host)
        self.max_body_bytes = max(1024, min(int(max_body_bytes), 5_000_000))

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        if self._owned_client is None:
            self._owned_client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout_seconds, connect=4.0),
                headers={"Accept": "text/html,application/xhtml+xml,text/plain,application/json", "User-Agent": "Arelbot/1.0"},
                follow_redirects=False,
                **httpx_verify_kwargs(),
            )
        return self._owned_client

    @staticmethod
    def _content_type(response: httpx.Response) -> str:
        return str(response.headers.get("content-type", "")).split(";", 1)[0].strip().casefold()

    async def _read_body(self, response: httpx.Response) -> bytes:
        content_length = response.headers.get("content-length")
        try:
            if content_length is not None and int(content_length) > self.max_body_bytes:
                raise ValueError("web_body_too_large")
        except ValueError as exc:
            if str(exc) == "web_body_too_large":
                raise
            raise ValueError("web_content_length_invalid") from exc
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes(64 * 1024):
            total += len(chunk)
            if total > self.max_body_bytes:
                raise ValueError("web_body_too_large")
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _extract_text(body: bytes, content_type: str) -> tuple[str, str | None]:
        text = body.decode("utf-8", errors="replace")
        title: str | None = None
        if content_type in {"text/html", "application/xhtml+xml"}:
            soup = BeautifulSoup(text, "html.parser")
            for node in soup(("script", "style", "noscript", "template")):
                node.decompose()
            title_node = soup.find("title")
            title = " ".join(title_node.get_text(" ", strip=True).split())[:300] if title_node else None
            text = soup.get_text("\n", strip=True)
        lines = [" ".join(line.split()) for line in text.splitlines()]
        return "\n".join(line for line in lines if line)[:20_000], title

    async def fetch(self, request: ProviderRequest) -> ProviderResponse[dict[str, object]]:
        raw_url = str(request.params.get("url") or "").strip()
        if len(raw_url) > 2048:
            raise ValueError("web_url_too_long")
        current = await validate_public_url(raw_url, resolve_host=self.resolve_host)
        client = await self._get_client()
        for redirect_count in range(self.max_redirects + 1):
            async with client.stream(
                "GET",
                current,
                timeout=min(max(float(request.timeout_seconds), 0.5), self.timeout_seconds),
            ) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = str(response.headers.get("location") or "").strip()
                    if not location or redirect_count >= self.max_redirects:
                        raise ValueError("web_redirect_limit")
                    current = await validate_public_url(
                        urljoin(current, location),
                        resolve_host=self.resolve_host,
                    )
                    continue
                response.raise_for_status()
                content_type = self._content_type(response)
                if content_type and content_type not in self.allowed_content_types:
                    raise ValueError("web_content_type_unsupported")
                body = await self._read_body(response)
                content, title = self._extract_text(body, content_type or "text/plain")
                if not content:
                    raise ValueError("web_content_empty")
                return ProviderResponse(
                    data={
                        "content": content,
                        "title": title,
                        "content_type": content_type or "text/plain",
                        "url": current,
                    },
                    source="Public web page",
                    source_url=current,
                    fetched_at=datetime.now(timezone.utc),
                    metadata={
                        "provider_kind": "typed_public_web",
                        "untrusted": True,
                        "bytes": len(body),
                        "redirects": redirect_count,
                    },
                )
        raise ValueError("web_redirect_limit")

    async def normalize(self, raw: object) -> object:
        return raw

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(name=self.name, healthy=True, detail="lazy_probe")

    async def close(self) -> None:
        if self._owned_client is not None:
            await self._owned_client.aclose()
            self._owned_client = None
