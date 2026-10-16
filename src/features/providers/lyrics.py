"""Typed lyrics search provider with source allowlisting and bounded excerpts."""

from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from ..contracts import ProviderHealth, ProviderRequest, ProviderResponse
from .web_reader import WebReaderProvider
from .web_search import TavilySearchProvider


class LyricsProvider:
    name = "lyrics_tool"
    capabilities = {"lyrics", "song_search", "source_excerpt"}
    timeout_seconds = 15.0
    allowed_hosts = frozenset({
        "genius.com",
        "www.genius.com",
        "musixmatch.com",
        "www.musixmatch.com",
        "lyrics.com",
        "www.lyrics.com",
        "azlyrics.com",
        "www.azlyrics.com",
        "songlyrics.com",
        "www.songlyrics.com",
    })

    def __init__(
        self,
        search_provider: TavilySearchProvider,
        reader_provider: WebReaderProvider,
    ) -> None:
        self.search_provider = search_provider
        self.reader_provider = reader_provider

    @classmethod
    def _allowed_url(cls, value: object) -> str | None:
        candidate = str(value or "").strip()
        parsed = urlsplit(candidate)
        if parsed.scheme != "https" or not parsed.hostname:
            return None
        host = parsed.hostname.casefold().rstrip(".")
        if host not in cls.allowed_hosts or parsed.username or parsed.password:
            return None
        return candidate[:2048]

    @staticmethod
    def _validate_params(params: dict[str, Any]) -> tuple[str, str]:
        song = str(params.get("song") or "").strip()
        artist = str(params.get("artist") or "").strip()
        if not song:
            raise ValueError("lyrics_song_required")
        if len(song) > 200 or len(artist) > 200:
            raise ValueError("lyrics_query_too_long")
        return song, artist

    async def fetch(self, request: ProviderRequest) -> ProviderResponse[dict[str, Any]]:
        song, artist = self._validate_params(dict(request.params))
        query = f"lyrics {song} {artist}".strip() + " site:genius.com OR site:musixmatch.com OR site:lyrics.com"
        search_response = await self.search_provider.fetch(
            ProviderRequest(
                operation="web_search",
                params={"query": query, "topic": "general"},
                timeout_seconds=min(request.timeout_seconds, self.timeout_seconds),
            )
        )
        results = search_response.data.get("results", []) if isinstance(search_response.data, dict) else []
        if not isinstance(results, list):
            raise RuntimeError("lyrics_search_schema_error")
        source_url = None
        for item in results[:10]:
            if not isinstance(item, dict):
                continue
            source_url = self._allowed_url(item.get("url"))
            if source_url:
                break
        if not source_url:
            raise ValueError("lyrics_source_not_found")

        page_response = await self.reader_provider.fetch(
            ProviderRequest(
                operation="web_reader",
                params={"url": source_url},
                timeout_seconds=min(request.timeout_seconds, self.timeout_seconds),
            )
        )
        page_data = page_response.data if isinstance(page_response.data, dict) else {}
        excerpt = " ".join(str(page_data.get("content") or "").split())[:2500]
        if not excerpt:
            raise RuntimeError("lyrics_source_empty")
        title = str(page_data.get("title") or f"{song} - {artist}".strip(" -"))[:200]
        content = (
            f"<b>ğŸµ ÅarkÄ± sÃ¶zÃ¼ kaynaÄŸÄ±</b>\n"
            f"<b>{html.escape(title)}</b>\n\n"
            f"{html.escape(excerpt)}\n\n"
            f'<a href="{html.escape(source_url, quote=True)}">KaynaÄŸÄ± aÃ§</a>\n'
            "<i>Kaynak metin telifli olabilir; Arelbot tam ÅŸarkÄ± sÃ¶zÃ¼ yerine sÄ±nÄ±rlÄ± kaynak Ã¶zeti gÃ¶sterir.</i>"
        )
        return ProviderResponse(
            data={
                "song": song,
                "artist": artist,
                "title": title,
                "excerpt": excerpt,
                "content": content,
            },
            source="Lyrics source search",
            source_url=source_url,
            fetched_at=datetime.now(timezone.utc),
            metadata={
                "provider_kind": "typed",
                "untrusted": True,
                "bounded_excerpt": True,
                "search_source": search_response.source,
            },
        )

    async def normalize(self, raw: Any) -> Any:
        return raw

    async def health_check(self) -> ProviderHealth:
        search_health = await self.search_provider.health_check()
        return ProviderHealth(
            name=self.name,
            healthy=bool(search_health.healthy),
            detail="search_provider=" + ("healthy" if search_health.healthy else "unhealthy"),
        )

    async def close(self) -> None:
        for provider in (self.search_provider, self.reader_provider):
            close = getattr(provider, "close", None)
            if close is not None:
                result = close()
                if hasattr(result, "__await__"):
                    await result
