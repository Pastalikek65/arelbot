"""Typed, bounded search service for Telegram inline queries.

The Telegram adapter only turns these records into Bot API result objects.
Parsing, pagination bounds and repository calls remain in this service so the
inline surface cannot grow an unbounded database or provider path.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import asyncio
import ipaddress
from typing import Any
from urllib.parse import urlparse, urlsplit

from src.features.city_directory import CityDirectory
from src.features.culture_categories import normalize_culture_category
from src.utils import tr_lower


@dataclass(frozen=True)
class InlineArticle:
    result_id: str
    title: str
    description: str
    text: str


@dataclass(frozen=True)
class InlineSticker:
    result_id: str
    file_id: str


@dataclass(frozen=True)
class InlinePage:
    articles: tuple[InlineArticle, ...] = ()
    stickers: tuple[InlineSticker, ...] = ()
    next_offset: str = ""
    cache_time: int = 30


class InlineQueryService:
    MAX_QUERY_CHARS = 96
    MAX_PAGE_SIZE = 20
    MAX_OFFSET = 10_000

    def __init__(self, city_directory: CityDirectory, database: Any, feature_executor: Any | None = None) -> None:
        self.city_directory = city_directory
        self.database = database
        self.feature_executor = feature_executor

    @staticmethod
    def _result_id(prefix: str, value: str) -> str:
        digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:32]
        return f"{prefix}:{digest}"

    @classmethod
    def parse_offset(cls, value: str | None) -> int:
        try:
            offset = int(value or 0)
        except (TypeError, ValueError):
            return 0
        return max(0, min(offset, cls.MAX_OFFSET))

    @classmethod
    def normalize_query(cls, value: str | None) -> str:
        return " ".join(str(value or "").strip().split())[: cls.MAX_QUERY_CHARS]

    async def search(self, raw_query: str | None, raw_offset: str | None = None) -> InlinePage:
        query = self.normalize_query(raw_query)
        offset = self.parse_offset(raw_offset)
        lowered = tr_lower(query)

        city_prefixes = ("sehir ", "şehir ", "il ")
        for prefix in city_prefixes:
            if lowered.startswith(prefix):
                return self._city_page(query[len(prefix):].strip(), offset)

        if lowered.startswith("sticker ") or lowered.startswith("st:"):
            term = query.split(" ", 1)[1].strip() if " " in query else query[3:].strip()
            return await self._sticker_page(term, offset)

        if lowered.startswith("haber ") or lowered.startswith("news "):
            term = query.split(" ", 1)[1].strip()
            return await self._news_page(term, offset)

        if lowered.startswith(("ara ", "search ", "web ")):
            term = query.split(" ", 1)[1].strip()
            return await self._web_search_page(term, offset)

        if lowered.startswith(("soz ", "sarki ", "lyrics ")):
            term = query.split(" ", 1)[1].strip()
            return await self._lyrics_page(term, offset)

        if lowered in {"kultur", "kültür"} or lowered.startswith(("kultur ", "kültür ", "culture ")):
            term = query.split(" ", 1)[1].strip() if " " in query else ""
            return await self._culture_page(term, offset)

        if lowered == "kripto" or lowered.startswith(("kripto ", "crypto ")):
            term = query.split(" ", 1)[1].strip() if " " in query else "btc"
            return await self._finance_page("crypto", term, offset)

        if lowered == "borsa" or lowered.startswith(("borsa ", "market ")):
            term = query.split(" ", 1)[1].strip() if " " in query else "thy"
            return await self._finance_page("market", term, offset)

        if lowered.startswith("top:"):
            try:
                chat_id = int(query[4:].strip())
            except ValueError:
                return InlinePage(cache_time=15)
            return await self._quote_page(chat_id, offset)

        return InlinePage(cache_time=15)

    async def _execute_bounded(self, feature_key: str, params: dict[str, Any]) -> Any | None:
        if self.feature_executor is None:
            return None
        try:
            return await asyncio.wait_for(
                self.feature_executor.execute(feature_key, params),
                timeout=4.0,
            )
        except asyncio.TimeoutError:
            return None
        except Exception:
            return None

    @staticmethod
    def _safe_link(value: object) -> str:
        candidate = str(value or "").strip()
        try:
            parsed = urlsplit(candidate)
            port = parsed.port
        except ValueError:
            return ""
        if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
            return ""
        if parsed.username or parsed.password or port not in {None, 80, 443}:
            return ""
        host = (parsed.hostname or "").rstrip(".").casefold()
        if host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".internal")):
            return ""
        try:
            if not ipaddress.ip_address(host).is_global:
                return ""
        except ValueError:
            pass
        return candidate[:2048]

    async def _web_search_page(self, term: str, offset: int) -> InlinePage:
        result = await self._execute_bounded(
            "web_search",
            {"query": term, "topic": "general", "days": 3},
        )
        if not result or not result.ok or not isinstance(result.data, dict):
            return InlinePage(cache_time=10)
        rows: list[InlineArticle] = []
        for index, item in enumerate(result.data.get("results") or [], 1):
            if not isinstance(item, dict):
                continue
            title = " ".join(str(item.get("title") or "").split())[:160]
            snippet = " ".join(str(item.get("content") or "").split())[:400]
            link = self._safe_link(item.get("url"))
            if not title or not link:
                continue
            text = f"{title}\n{snippet}\nKaynak: {link}"[:1024]
            rows.append(
                InlineArticle(
                    result_id=self._result_id("web", link or f"{term}:{index}"),
                    title=title,
                    description=(snippet or "Kaynaklı web sonucu")[:256],
                    text=text,
                )
            )
        page = rows[offset : offset + self.MAX_PAGE_SIZE]
        return InlinePage(
            articles=tuple(page),
            next_offset=str(offset + self.MAX_PAGE_SIZE) if len(rows) > offset + self.MAX_PAGE_SIZE else "",
            cache_time=60,
        )

    async def _lyrics_page(self, term: str, offset: int) -> InlinePage:
        parts = [part.strip() for part in term.split(" - ", 1)]
        song = parts[0][:200]
        artist = parts[1][:200] if len(parts) == 2 else ""
        result = await self._execute_bounded("lyrics", {"song": song, "artist": artist})
        if not result or not result.ok or not isinstance(result.data, dict):
            return InlinePage(cache_time=10)
        title = " ".join(str(result.data.get("title") or f"{song} {artist}").split())[:160]
        excerpt = " ".join(str(result.data.get("excerpt") or "").split())[:500]
        link = self._safe_link(result.source_url)
        if not title or not excerpt or not link:
            return InlinePage(cache_time=10)
        article = InlineArticle(
            result_id=self._result_id("lyrics", link),
            title=title,
            description="Sınırlı kaynak alıntısı ve kaynak bağlantısı",
            text=f"{title}\n{excerpt}\nKaynak: {link}\nTam şarkı sözleri yeniden üretilmez.",
        )
        return InlinePage(articles=(article,) if offset == 0 else (), cache_time=300)

    async def _culture_page(self, term: str, offset: int) -> InlinePage:
        """Return bounded, sourced cultural recommendations for inline UX."""

        if not term:
            suggestions = (
                ("🎬 Film", "kultur film "),
                ("📺 Dizi", "kultur dizi "),
                ("📚 Kitap", "kultur kitap "),
                ("🎵 Müzik", "kultur müzik "),
            )
            articles = tuple(
                InlineArticle(
                    result_id=self._result_id("culture-help", query),
                    title=title,
                    description="Kategori ve konu yazarak öneri getir",
                    text=f"Inline aramada şu biçimi kullan: @{query}",
                )
                for title, query in suggestions
            )
            return InlinePage(articles=articles[offset : offset + self.MAX_PAGE_SIZE], cache_time=30)

        parts = term.split(maxsplit=1)
        category = normalize_culture_category(parts[0])
        if category is None:
            return InlinePage(cache_time=15)
        search_term = parts[1].strip() if len(parts) == 2 else ""
        result = await self._execute_bounded(
            "culture_recommendation",
            {"category": category, "specific_query": search_term, "genre": search_term},
        )
        if not result or not result.ok or not isinstance(result.data, dict):
            return InlinePage(cache_time=15)
        rows: list[InlineArticle] = []
        for index, item in enumerate(result.data.get("items") or [], 1):
            if not isinstance(item, dict):
                continue
            title = " ".join(str(item.get("title") or "").split())[:160]
            if not title:
                continue
            details = []
            for key in ("author", "artist", "album", "genres", "year", "score"):
                value = item.get(key)
                if value is not None and str(value).strip():
                    details.append(f"{key}: {str(value).strip()[:80]}")
            link = self._safe_link(item.get("url"))
            description = " · ".join(details)[:256] or "Kaynaklı kültür önerisi"
            text = f"{title}\n{description}"
            if link:
                text += f"\nKaynak: {link}"
            rows.append(
                InlineArticle(
                    result_id=self._result_id("culture", link or f"{category}:{title}:{index}"),
                    title=title,
                    description=description,
                    text=text[:1024],
                )
            )
        page = rows[offset : offset + self.MAX_PAGE_SIZE]
        return InlinePage(
            articles=tuple(page),
            next_offset=str(offset + self.MAX_PAGE_SIZE) if len(rows) > offset + self.MAX_PAGE_SIZE else "",
            cache_time=900,
        )

    async def _news_page(self, term: str, offset: int) -> InlinePage:
        if self.feature_executor is None:
            return InlinePage(cache_time=15)
        result = await self.feature_executor.execute("news_digest", {"limit": 30})
        if not result.ok or not isinstance(result.data, dict):
            return InlinePage(cache_time=15)
        normalized = tr_lower(term)
        items = []
        for item in result.data.get("items") or []:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            if not title or (normalized and normalized not in tr_lower(title)):
                continue
            link = str(item.get("link") or "").strip()
            parsed = urlparse(link)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                link = ""
            items.append((title, link, str(item.get("description") or "").strip()))
        page = items[offset : offset + self.MAX_PAGE_SIZE]
        articles = tuple(
            InlineArticle(
                result_id=self._result_id("news", link or title),
                title=title[:128],
                description=(description or "Kaynak bağlantısını açarak ayrıntıyı gör.")[:256],
                text=f"{title}\n{link}" if link else title,
            )
            for title, link, description in page
        )
        next_offset = str(offset + self.MAX_PAGE_SIZE) if len(items) > offset + self.MAX_PAGE_SIZE else ""
        return InlinePage(articles=articles, next_offset=next_offset, cache_time=120)

    async def _finance_page(self, kind: str, term: str, offset: int) -> InlinePage:
        if self.feature_executor is None or not term:
            return InlinePage(cache_time=15)
        params = (
            {"symbol": term, "vs_currency": "usd"}
            if kind == "crypto"
            else {"symbol": term}
        )
        key = "crypto" if kind == "crypto" else "market"
        result = await self.feature_executor.execute(key, params)
        if not result.ok or not isinstance(result.data, dict):
            return InlinePage(cache_time=15)
        source_url = str(getattr(result, "source_url", "") or "").strip()
        parsed = urlparse(source_url)
        safe_source = source_url if parsed.scheme in {"http", "https"} and parsed.netloc else ""
        rows: list[InlineArticle] = []
        if kind == "crypto":
            for row in result.data.get("coins") or []:
                if not isinstance(row, dict):
                    continue
                symbol = str(row.get("id") or "coin").strip()[:64]
                currency = str(row.get("currency") or "usd").upper()[:8]
                price = row.get("price")
                change = row.get("change_24h")
                if not isinstance(price, (int, float)):
                    continue
                change_text = f"{float(change):+.2f}%" if isinstance(change, (int, float)) else "veri yok"
                title = f"{symbol} • {float(price):,.4f} {currency}"
                description = f"24 saat: {change_text}"
                text = f"{title}\n{description}"
                if safe_source:
                    text += f"\nKaynak: {safe_source}"
                rows.append(InlineArticle(self._result_id("crypto", symbol + currency), title[:128], description[:256], text[:1024]))
        else:
            symbol = str(result.data.get("symbol") or term).strip()[:64]
            price = result.data.get("price")
            change = result.data.get("change_pct")
            if isinstance(price, (int, float)):
                change_text = f"{float(change):+.2f}%" if isinstance(change, (int, float)) else "veri yok"
                title = f"{symbol} • {float(price):,.4f}"
                description = f"7 günlük değişim: {change_text}"
                text = f"{title}\n{description}"
                if safe_source:
                    text += f"\nKaynak: {safe_source}"
                rows.append(InlineArticle(self._result_id("market", symbol), title[:128], description[:256], text[:1024]))
        page = rows[offset : offset + self.MAX_PAGE_SIZE]
        next_offset = str(offset + self.MAX_PAGE_SIZE) if len(rows) > offset + self.MAX_PAGE_SIZE else ""
        return InlinePage(articles=tuple(page), next_offset=next_offset, cache_time=30 if kind == "crypto" else 120)

    def _city_page(self, term: str, offset: int) -> InlinePage:
        # CityDirectory owns the complete local match set and returns a true
        # offset page; broad prefixes therefore do not truncate at page one.
        page, has_more = self.city_directory.search_page(
            term, offset=offset, limit=self.MAX_PAGE_SIZE
        )
        articles = tuple(
            InlineArticle(
                result_id=f"city:{city.plate}",
                title=f"{city.name} ({city.plate:02d})",
                description=(
                    f"Plaka {city.plate:02d} • Telefon {', '.join(map(str, city.phone))} • "
                    f"{city.region or '-'}"
                ),
                text=(
                    f"{city.name} | Plaka {city.plate:02d} | "
                    f"Telefon {', '.join(map(str, city.phone))}\n"
                    f"Bölge: {city.region or '-'} | İlçe sayısı: {len(city.districts)}"
                ),
            )
            for city in page
        )
        next_offset = str(offset + self.MAX_PAGE_SIZE) if has_more else ""
        return InlinePage(articles=articles, next_offset=next_offset, cache_time=3600)

    async def _quote_page(self, chat_id: int, offset: int) -> InlinePage:
        settings_getter = getattr(self.database, "get_chat_settings", None)
        if settings_getter:
            try:
                settings = await settings_getter(chat_id)
            except Exception:
                settings = {}
            if isinstance(settings, dict) and settings.get("q_enabled") is False:
                return InlinePage(cache_time=15)
        getter = getattr(self.database, "get_top_quotes", None)
        rows = await getter(chat_id, offset, self.MAX_PAGE_SIZE) if getter else []
        stickers = tuple(
            InlineSticker(
                result_id=f"quote:{chat_id}:{offset + index}",
                file_id=str(row["sticker_file_id"]),
            )
            for index, row in enumerate(rows)
            if row.get("sticker_file_id")
        )
        return InlinePage(
            stickers=stickers,
            next_offset=str(offset + self.MAX_PAGE_SIZE) if len(rows) == self.MAX_PAGE_SIZE else "",
            cache_time=30,
        )

    async def _sticker_page(self, term: str, offset: int) -> InlinePage:
        if not term:
            return InlinePage(cache_time=15)
        getter = getattr(self.database, "search_stickers", None)
        rows = await getter(term, offset=offset, limit=self.MAX_PAGE_SIZE) if getter else []
        stickers = tuple(
            InlineSticker(
                result_id=self._result_id("sticker", row.get("file_id", "")),
                file_id=str(row["file_id"]),
            )
            for row in rows
            if row.get("file_id")
        )
        return InlinePage(
            stickers=stickers,
            next_offset=str(offset + self.MAX_PAGE_SIZE) if len(rows) == self.MAX_PAGE_SIZE else "",
            cache_time=60,
        )
