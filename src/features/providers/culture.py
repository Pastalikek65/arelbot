"""Provider-backed cultural recommendations without score fabrication."""

from __future__ import annotations

import html
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx

from ..contracts import ProviderHealth, ProviderRequest, ProviderResponse
from .transport import ProviderHttpTransport
from src.security.tls import httpx_verify_kwargs


class CultureProvider:
    """Use stable public JSON APIs for books, series, films and music."""

    name = "culture_tool"
    capabilities = {"culture_recommendation", "books", "series", "movies", "music"}
    timeout_seconds = 7.0
    _WIKIDATA_API = "https://www.wikidata.org/w/api.php"
    _WIKIDATA_HEADERS = {"User-Agent": "Arelbot/1.0 (https://github.com/Pastalikek65/arelbot)"}
    # A small editorial starting pool keeps the no-argument command useful.
    # Names and release details still come from Wikidata on every cache miss.
    _MOVIE_POOLS = {
        "bilim kurgu": ("Q13417189", "Q83495", "Q109228991", "Q20382729", "Q21500755"),
        "aksiyon": ("Q1757288", "Q15732802", "Q163872", "Q109228991", "Q83495"),
        "komedi": ("Q3521099", "Q219315", "Q20856802"),
        "dram": ("Q61448040", "Q108839994", "Q15648198", "Q13417189"),
        "korku": ("Q25136235", "Q39070473"),
        "romantik": ("Q44578", "Q20856802", "Q659609"),
    }
    _MOVIE_GENRE_ALIASES = {
        "sci fi": "bilim kurgu", "science fiction": "bilim kurgu", "bilimkurgu": "bilim kurgu",
        "action": "aksiyon", "comedy": "komedi", "drama": "dram", "horror": "korku",
        "romance": "romantik", "ask": "romantik", "romantik komedi": "romantik",
    }

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
        if category == "movie":
            items = await self._fetch_movies(client, query)
            url = self._WIKIDATA_API
            source = "Wikidata"
        elif category == "book":
            url = f"https://openlibrary.org/search.json?q={encoded}&limit=5"
            source = "Open Library"
        elif category == "series":
            url = f"https://api.tvmaze.com/search/shows?q={encoded}"
            source = "TVmaze"
        else:
            url = f"https://itunes.apple.com/search?term={encoded}&entity=song&limit=5&country=tr"
            source = "Apple iTunes Search API"
        if category != "movie":
            allowed_host = {
                "openlibrary.org" if category == "book" else
                "api.tvmaze.com" if category == "series" else
                "itunes.apple.com"
            }
            payload = await ProviderHttpTransport(client, allowed_hosts=allowed_host).get_json(
                url,
                timeout=min(max(request.timeout_seconds, 0.5), self.timeout_seconds),
                error_code="culture_invalid_json",
                # Apple serves valid JSON with text/javascript. Keep this
                # exception scoped to music rather than global transport.
                expected_content_types=(
                    "application/json", "application/problem+json", "text/json", "text/javascript",
                ) if category == "music" else None,
            )
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
    def _fold_movie_query(query: str) -> str:
        folded = unicodedata.normalize("NFKD", query.casefold())
        return " ".join("".join(char for char in folded if not unicodedata.combining(char)).replace("ı", "i").split())

    @classmethod
    def _movie_pool(cls, query: str) -> tuple[str, ...] | None:
        folded = cls._fold_movie_query(query)
        if folded in {"popular movies", "populer filmler", "film", "filmler"}:
            pools = tuple(cls._MOVIE_POOLS.values())
            return tuple(dict.fromkeys(item for group in zip(*pools, strict=False) for item in group))
        genre = cls._MOVIE_GENRE_ALIASES.get(folded, folded)
        return cls._MOVIE_POOLS.get(genre)

    @classmethod
    def _movie_ids_for_day(cls, pool: tuple[str, ...]) -> tuple[str, ...]:
        # Rotating the starting point avoids a permanently identical top five.
        offset = datetime.now(timezone.utc).date().toordinal() % len(pool)
        rotated = pool[offset:] + pool[:offset]
        return rotated[:5]

    @staticmethod
    def _wikidata_value(entity: dict[str, Any], key: str) -> str | None:
        values = entity.get(key) or {}
        for language in ("tr", "en"):
            value = (values.get(language) or {}).get("value")
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _is_film(entity: dict[str, Any]) -> bool:
        for statement in (entity.get("claims") or {}).get("P31", []):
            value = ((statement.get("mainsnak") or {}).get("datavalue") or {}).get("value") or {}
            if isinstance(value, dict) and value.get("id") == "Q11424":
                return True
        return False

    @staticmethod
    def _release_year(entity: dict[str, Any]) -> str | None:
        years: list[str] = []
        for statement in (entity.get("claims") or {}).get("P577", []):
            if statement.get("rank") == "deprecated":
                continue
            value = ((statement.get("mainsnak") or {}).get("datavalue") or {}).get("value") or {}
            match = re.match(r"^[+-](\d{4})-", str(value.get("time") or "")) if isinstance(value, dict) else None
            if match:
                years.append(match.group(1))
        return min(years) if years else None

    async def _fetch_movies(self, client: httpx.AsyncClient, query: str) -> list[dict[str, Any]]:
        transport = ProviderHttpTransport(client, allowed_hosts={"www.wikidata.org"})
        pool = self._movie_pool(query)
        if pool is not None:
            ids = self._movie_ids_for_day(pool)
        else:
            search = await transport.get_json(
                self._WIKIDATA_API,
                params={"action": "wbsearchentities", "search": query, "language": "en", "format": "json", "limit": 20, "type": "item"},
                headers=self._WIKIDATA_HEADERS,
                timeout=self.timeout_seconds,
                error_code="culture_invalid_json",
            )
            rows = search.get("search", []) if isinstance(search, dict) else []
            ids = tuple(
                row["id"] for row in rows
                if isinstance(row, dict)
                and re.fullmatch(r"Q[1-9]\d*", str(row.get("id") or ""))
                and "film" in str(row.get("description") or "").casefold()
            )[:5]
        if not ids:
            return []
        payload = await transport.get_json(
            self._WIKIDATA_API,
            params={"action": "wbgetentities", "ids": "|".join(ids), "props": "labels|descriptions|claims", "languages": "tr|en", "format": "json"},
            headers=self._WIKIDATA_HEADERS,
            timeout=self.timeout_seconds,
            error_code="culture_invalid_json",
        )
        entities = payload.get("entities", {}) if isinstance(payload, dict) else {}
        items: list[dict[str, Any]] = []
        for identifier in ids:
            entity = entities.get(identifier, {}) if isinstance(entities, dict) else {}
            if not isinstance(entity, dict) or not self._is_film(entity):
                continue
            title = self._wikidata_value(entity, "labels")
            if not title:
                continue
            items.append({
                "title": title,
                "year": self._release_year(entity),
                "description": self._wikidata_value(entity, "descriptions"),
                "url": f"https://www.wikidata.org/wiki/{identifier}",
            })
        return items

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
        for label, key in (("Yazar", "author"), ("Sanatçı", "artist"), ("Albüm", "album"), ("Tür", "genres"), ("Yıl", "year"), ("Kaynak açıklaması", "description")):
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
