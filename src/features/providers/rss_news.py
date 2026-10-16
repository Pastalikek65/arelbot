"""Typed RSS provider used by the news digest surface.

RSS is deliberately parsed here instead of in a Telegram handler.  The
provider returns a small, stable schema so command, callback and AI callers
can share the same cache, timeout and health policy.
"""

from __future__ import annotations

import email.utils
import html
import ipaddress
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import urlsplit

import httpx

from ..contracts import ProviderHealth, ProviderRequest, ProviderResponse
from ..news_categories import normalize_news_category
from .transport import ProviderHttpTransport, ProviderTransportError
from src.security.tls import httpx_verify_kwargs


DEFAULT_FEEDS = (
    "https://www.trthaber.com/sondakika_articles.rss",
    "https://www.trthaber.com/manset_articles.rss",
)

def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].casefold()


def _child_text(node: ET.Element, *names: str) -> str:
    wanted = {name.casefold() for name in names}
    for child in list(node):
        if _local_name(child.tag) in wanted and child.text:
            return " ".join(child.text.split())
    return ""


def _child_values(node: ET.Element, *names: str) -> list[str]:
    wanted = {name.casefold() for name in names}
    return [
        " ".join((child.text or "").split())
        for child in list(node)
        if _local_name(child.tag) in wanted and (child.text or "").strip()
    ]


def _link(node: ET.Element) -> str:
    value = _child_text(node, "link")
    if value:
        return _safe_link(value)
    for child in list(node):
        if _local_name(child.tag) == "link":
            href = (child.attrib.get("href") or "").strip()
            if href:
                return _safe_link(href)
    return ""


def _safe_link(value: str) -> str:
    """Keep RSS links usable in Telegram HTML without accepting script URLs.

    RSS is external, untrusted input. Telegram link markup must only contain
    absolute HTTP(S) URLs without credentials; malformed or dangerous links
    are omitted while the article title remains available.
    """

    candidate = str(value or "").strip()
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return ""
    if parsed.username or parsed.password:
        return ""
    return candidate[:2000]


def _clean_description(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    return " ".join(html.unescape(value).split())[:500]


def _published_at(value: str) -> str | None:
    if not value:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


class RssNewsProvider:
    name = "rss_news_digest"
    capabilities = {"news", "breaking_news", "news_digest", "rss"}
    source_name = "RSS haber akÄ±ÅŸlarÄ±"

    def __init__(
        self,
        feeds: Iterable[str] | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.feeds = tuple(str(feed).strip() for feed in (feeds or DEFAULT_FEEDS) if str(feed).strip())
        if not self.feeds:
            raise ValueError("rss_feed_required")
        for feed in self.feeds:
            parsed = httpx.URL(feed)
            if parsed.scheme not in {"http", "https"} or not parsed.host:
                raise ValueError("rss_feed_must_be_http")
            host = parsed.host.casefold().rstrip(".")
            try:
                private = ipaddress.ip_address(host).is_private or ipaddress.ip_address(host).is_loopback
            except ValueError:
                private = host == "localhost" or host.endswith(".local") or host.endswith(".internal")
            if private or parsed.username or parsed.password:
                raise ValueError("rss_feed_public_required")
        self._client = client
        self._owned_client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        if self._owned_client is None:
            self._owned_client = httpx.AsyncClient(
                timeout=httpx.Timeout(8.0, connect=3.0),
                headers={"Accept": "application/rss+xml, application/atom+xml, text/xml", "User-Agent": "Arelbot/1.0"},
                follow_redirects=False,
                **httpx_verify_kwargs(),
            )
        return self._owned_client

    async def fetch(self, request: ProviderRequest) -> ProviderResponse[dict[str, Any]]:
        limit = int(request.params.get("limit", 8) or 8)
        if limit < 1 or limit > 30:
            raise ValueError("news_limit_out_of_range")
        raw_category = request.params.get("category")
        category = normalize_news_category(raw_category) if raw_category else None
        if raw_category and category is None:
            raise ValueError("news_category_invalid")
        items: list[dict[str, str]] = []
        seen: set[str] = set()
        client = await self._get_client()
        transport = ProviderHttpTransport(
            client,
            allowed_hosts={urlsplit(feed).hostname or "" for feed in self.feeds},
        )
        for feed in self.feeds:
            try:
                body = await transport.get_bytes(
                    feed,
                    timeout=min(max(float(request.timeout_seconds), 0.5), 8.0),
                    expected_content_types=(
                        "application/rss+xml",
                        "application/atom+xml",
                        "application/xml",
                        "text/xml",
                        "text/plain",
                    ),
                )
                root = ET.fromstring(body.body)
            except ProviderTransportError as exc:
                if exc.code == "provider_response_too_large":
                    raise RuntimeError("news_rss_response_too_large") from exc
                raise exc
            except ET.ParseError as exc:
                raise RuntimeError("news_rss_schema_error") from exc
            nodes = [node for node in root.iter() if _local_name(node.tag) in {"item", "entry"}]
            for node in nodes:
                title = _child_text(node, "title")
                if not title:
                    continue
                categories = [
                    normalized
                    for normalized in (
                        normalize_news_category(value)
                        for value in _child_values(node, "category")
                    )
                    if normalized
                ]
                if category and category not in categories:
                    continue
                key = title.casefold()
                if key in seen:
                    continue
                seen.add(key)
                item = {
                    "title": html.unescape(title),
                    "link": _link(node),
                    "description": _clean_description(_child_text(node, "description", "summary", "content")),
                }
                if categories:
                    item["categories"] = ",".join(dict.fromkeys(categories))
                published = _published_at(_child_text(node, "pubDate", "published", "updated", "date"))
                if published:
                    item["published_at"] = published
                items.append(item)
                if len(items) >= limit:
                    break
            if len(items) >= limit:
                break
        if not items:
            raise RuntimeError("news_rss_empty")
        heading = "ğŸ“° GÃ¼ncel Haber Ã–zeti" if not category else f"ğŸ“° Haberler Â· {category.title()}"
        lines = [f"<b>{heading}</b>", ""]
        for index, item in enumerate(items, 1):
            title = html.escape(item["title"])
            link = html.escape(item.get("link", ""), quote=True)
            lines.append(f'{index}. <a href="{link}">{title}</a>' if link else f"{index}. {title}")
        return ProviderResponse(
            data={"items": items, "category": category, "content": "\n".join(lines)},
            source=self.source_name,
            source_url=self.feeds[0],
            metadata={
                "provider_kind": "typed",
                "records": len(items),
                "feeds": len(self.feeds),
                "category": category,
            },
        )

    async def normalize(self, raw: Any) -> Any:
        return raw

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(
            name=self.name,
            healthy=bool(self.feeds),
            detail=f"configured_feeds={len(self.feeds)};lazy_network_check=true",
        )

    async def close(self) -> None:
        if self._owned_client is not None:
            await self._owned_client.aclose()
            self._owned_client = None


class BreakingNewsRssProvider(RssNewsProvider):
    """Compatibility-shaped RSS provider for the ``/haber`` feature.

    The original command returned a ``titles`` list from a source-specific
    HTML page.  Keeping that small shape lets existing Telegram and AI
    callers migrate to the governed RSS pipeline without a second scraper.
    The richer ``items`` list is retained for links, timestamps and future
    inline/pagination surfaces.
    """

    name = "get_breaking_news"
    source_name = "TRT Haber RSS akÄ±ÅŸlarÄ±"

    async def fetch(self, request: ProviderRequest) -> ProviderResponse[dict[str, Any]]:
        response = await super().fetch(request)
        items = response.data.get("items") if isinstance(response.data, dict) else []
        normalized_items = [item for item in items if isinstance(item, dict)]
        titles = [str(item.get("title") or "").strip() for item in normalized_items]
        titles = [title for title in titles if title]
        category = response.data.get("category") if isinstance(response.data, dict) else None
        heading = "ğŸ“° Son Dakika Haberleri" if not category else f"ğŸ“° Son Dakika Haberleri Â· {category.title()}"
        lines = [f"<b>{heading}</b>", ""]
        for index, item in enumerate(normalized_items[:10], 1):
            title = html.escape(str(item.get("title") or "").strip())
            link = html.escape(str(item.get("link") or "").strip(), quote=True)
            if not title:
                continue
            lines.append(f'{index}. <a href="{link}">{title}</a>' if link else f"{index}. {title}")
        return ProviderResponse(
            data={
                "titles": titles,
                "items": normalized_items,
                "content": "\n".join(lines),
            },
            source=self.source_name,
            source_url=response.source_url,
            fetched_at=response.fetched_at,
            metadata={**dict(response.metadata), "compatibility_shape": "breaking_news"},
        )
