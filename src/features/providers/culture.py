"""Provider-backed cultural recommendations without score fabrication."""

from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx

from ..contracts import ProviderHealth, ProviderRequest, ProviderResponse
from .transport import ProviderHttpTransport, ProviderTransportError
from src.security.tls import httpx_verify_kwargs


class CultureProvider:
    """Use stable public JSON APIs for books, series, films and music."""

    name = "culture_tool"
    capabilities = {"culture_recommendation", "books", "series", "movies", "music"}
    timeout_seconds = 7.0

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client
        self._owned_client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        if self._owned_client is None:
            self._owned_client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout_seconds, connect=3.0),
                headers={"Accept": "application/json", "User-Agent": "Arelbot/1.0"},
                follow_redirects=False,
                **httpx_verify_kwargs(),
            )
        return self._owned_client

    @staticmethod
    def _query(request: ProviderRequest, category: str) -> str:
        params = request.params
        query = str(params.get("specific_query") or params.get("genre") or params.get("mood") or "").strip()
        if not query:
            query = {
                "book": "popular books",
                "series": "popular series",
                "movie": "popular movies",
                "music": "popular music",
            }[category]
        return query[:120]

    @staticmethod
    def _category(request: ProviderRequest) -> str:
        category = str(request.params.get("category") or "").casefold().strip()
        if category not in {"book", "series", "movie", "music"}:
            raise ValueError("culture_category_invalid")
        return category

    async def fetch(self, request: ProviderRequest) -> ProviderResponse[dict[str, Any]]:
        category = self._category(request)
        query = self._query(request, category)
        encoded = quote(query, safe="")
        client = await self._get_client()
        if category == "book":
            url = f"https://openlibrary.org/search.json?q={encoded}&limit=5"
            source = "Open Library"
        elif category == "series":
            url = f"https://api.tvmaze.com/search/shows?q={encoded}"
            source = "TVmaze"
        else:
            entity = "movie" if category == "movie" else "song"
            url = f"https://itunes.apple.com/search?term={encoded}&entity={entity}&limit=5&country=tr"
            source = "Apple iTunes Search API"
        allowed_host = {
            "openlibrary.org" if category == "book" else
            "api.tvmaze.com" if category == "series" else
            "itunes.apple.com"
        }
        try:
            payload = await ProviderHttpTransport(client, allowed_hosts=allowed_host).get_json(
                url,
                timeout=min(max(request.timeout_seconds, 0.5), self.timeout_seconds),
                error_code="culture_invalid_json",
                # Apple serves valid JSON with text/javascript.  Keep this
                # exception scoped to the provider rather than weakening the
                # transport's global content-type policy.
                expected_content_types=(
                    "application/json",
                    "application/problem+json",
                    "text/json",
                    "text/javascript",
                ) if category in {"movie", "music"} else None,
            )
        except ProviderTransportError as exc:
            raise exc
        items = self._normalize_items(category, payload)
        if not items:
            raise RuntimeError("culture_schema_empty")
        lines = [f"<b>{html.escape(self._title(category, query))}</b>", ""]
        for index, item in enumerate(items, 1):
            lines.append(self._render_item(index, item))
        return ProviderResponse(
            data={"category": category, "query": query, "items": items, "content": "\n\n".join(lines)},
            source=source,
            source_url=url,
            fetched_at=datetime.now(timezone.utc),
            metadata={
                "provider_kind": "typed_json",
                "records": len(items),
                "scores_sourced_only": True,
            },
        )

    @staticmethod
    def _title(category: str, query: str) -> str:
        labels = {"book": "Kitap önerileri", "series": "Dizi önerileri", "movie": "Film önerileri", "music": "Müzik önerileri"}
        return f"{labels[category]} · {query}"

    @staticmethod
    def _normalize_items(category: str, payload: Any) -> list[dict[str, Any]]:
        if category == "book":
            docs = payload.get("docs", []) if isinstance(payload, dict) else []
            return [
                {
                    "title": item.get("title"),
                    "author": ", ".join((item.get("author_name") or [])[:2]),
                    "year": item.get("first_publish_year"),
                    "score": item.get("ratings_average"),
                    "url": f"https://openlibrary.org{item.get('key')}" if str(item.get("key", "")).startswith("/works/") else None,
                }
                for item in docs[:5]
                if item.get("title")
            ]
        if category == "series":
            rows = payload if isinstance(payload, list) else (payload.get("results", []) if isinstance(payload, dict) else [])
            return [
                {
                    "title": (item.get("show") or {}).get("name"),
                    "year": ((item.get("show") or {}).get("premiered") or "")[:4],
                    "genres": ", ".join(((item.get("show") or {}).get("genres") or [])[:3]),
                    "score": ((item.get("show") or {}).get("rating") or {}).get("average"),
                    "url": (item.get("show") or {}).get("url"),
                }
                for item in rows[:5]
                if (item.get("show") or {}).get("name")
            ]
        rows = payload.get("results", []) if isinstance(payload, dict) else []
        return [
            {
                "title": item.get("trackName"),
                "artist": item.get("artistName"),
                "album": item.get("collectionName"),
                "year": str(item.get("releaseDate") or "")[:4],
                "genres": item.get("primaryGenreName"),
                "url": item.get("trackViewUrl"),
            }
            for item in rows[:5]
            if item.get("trackName")
        ]

    @staticmethod
    def _render_item(index: int, item: dict[str, Any]) -> str:
        title = html.escape(str(item.get("title") or ""))
        details = []
        for label, key in (("Yazar", "author"), ("Sanatçı", "artist"), ("Albüm", "album"), ("Tür", "genres"), ("Yıl", "year")):
            if item.get(key):
                details.append(f"{label}: {html.escape(str(item[key]))}")
        if item.get("score") is not None:
            details.append(f"Kaynak puanı: {html.escape(str(item['score']))}")
        suffix = " · ".join(details)
        link = item.get("url")
        linked_title = f'<a href="{html.escape(str(link), quote=True)}">{title}</a>' if str(link).startswith("https://") else title
        return f"{index}. <b>{linked_title}</b>" + (f"\n{suffix}" if suffix else "")

    async def normalize(self, raw: Any) -> Any:
        return raw

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(name=self.name, healthy=True, detail="lazy_probe")

    async def close(self) -> None:
        if self._owned_client is not None:
            await self._owned_client.aclose()
            self._owned_client = None
