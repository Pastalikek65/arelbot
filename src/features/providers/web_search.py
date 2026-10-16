"""Typed Tavily search provider.

Search output is external and untrusted. The provider owns transport, key
rotation, bounded input/output and provenance; callers never receive the API
key or a raw upstream error body.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Any

import httpx

from ..contracts import ProviderHealth, ProviderRequest, ProviderResponse
from .transport import ProviderHttpTransport, ProviderTransportError
from ...key_manager import KeyManager, NoKeysConfiguredError, ServiceCircuitOpenError, ServiceType
from src.security.tls import httpx_verify_kwargs


class TavilySearchProvider:
    name = "web_search"
    capabilities = {"web_search", "news_search", "current_web", "untrusted_context"}
    source_name = "Tavily Search"
    source_url = "https://docs.tavily.com/"
    endpoint = "https://api.tavily.com/search"
    fallback_endpoint = "https://api.duckduckgo.com/"
    timeout_seconds = 15.0
    max_query_length = 512

    def __init__(
        self,
        key_manager: KeyManager,
        *,
        client: httpx.AsyncClient | None = None,
        endpoint: str | None = None,
    ) -> None:
        self.key_manager = key_manager
        self.endpoint = (endpoint or self.endpoint).rstrip("/")
        if not self.endpoint.startswith("https://"):
            raise ValueError("web_search_endpoint_must_be_https")
        self._client = client
        self._owned_client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        if self._owned_client is None:
            self._owned_client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout_seconds, connect=4.0),
                headers={"Accept": "application/json", "User-Agent": "Arelbot/1.0"},
                follow_redirects=False,
                **httpx_verify_kwargs(),
            )
        return self._owned_client

    @staticmethod
    def _validate_params(params: dict[str, Any]) -> tuple[str, str, int | None]:
        query = str(params.get("query") or "").strip()
        if not query:
            raise ValueError("web_search_query_required")
        if len(query) > TavilySearchProvider.max_query_length:
            raise ValueError("web_search_query_too_long")
        topic = str(params.get("topic") or "general").strip().casefold()
        if topic not in {"general", "news"}:
            raise ValueError("web_search_topic_invalid")
        days: int | None = None
        if topic == "news":
            try:
                days = int(params.get("days", 3) or 3)
            except (TypeError, ValueError) as exc:
                raise ValueError("web_search_days_invalid") from exc
            if days < 1 or days > 30:
                raise ValueError("web_search_days_out_of_range")
        return query, topic, days

    @staticmethod
    def _format_content(answer: str | None, rows: list[dict[str, str]]) -> str:
        lines = ["<b>ğŸŒ Web arama sonuÃ§larÄ±</b>"]
        if answer:
            lines.extend([f"<b>Ã–zet:</b> {html.escape(answer[:1200])}", ""])
        for index, row in enumerate(rows, 1):
            title = html.escape(row["title"][:300])
            content = html.escape(row["content"][:500])
            url = html.escape(row["url"], quote=True)
            lines.append(f'{index}. <a href="{url}"><b>{title}</b></a> â€” {content}')
        return "\n".join(lines)

    async def fetch(self, request: ProviderRequest) -> ProviderResponse[dict[str, Any]]:
        query, topic, days = self._validate_params(dict(request.params))
        payload: dict[str, Any] = {
            "query": query,
            "search_depth": "advanced",
            "max_results": 7,
            "include_answer": True,
        }
        if topic == "news":
            payload.update({"topic": "news", "days": days})

        client = await self._get_client()
        # Arel remains useful on a fresh deployment even when the operator has
        # not provisioned a paid search key yet. DuckDuckGo's documented
        # Instant Answer JSON endpoint is a bounded, keyless fallback; Tavily
        # is still preferred whenever a configured key is available.
        last_error: str | None = None
        attempts = 0
        for _ in range(2):
            attempts += 1
            try:
                key = self.key_manager.get_key(ServiceType.TAVILY)
            except NoKeysConfiguredError:
                return await self._fetch_duckduckgo(client, request, query, topic, days)
            except ServiceCircuitOpenError as exc:
                raise RuntimeError("web_search_key_circuit_open") from exc
            except Exception as exc:
                raise RuntimeError("web_search_not_configured") from exc
            request_payload = {**payload, "api_key": key}
            try:
                body = await ProviderHttpTransport(
                    client,
                    allowed_hosts={httpx.URL(self.endpoint).host or ""},
                ).post_json(
                    self.endpoint,
                    json_body=request_payload,
                    timeout=min(max(float(request.timeout_seconds), 0.5), self.timeout_seconds),
                    error_code="web_search_invalid_json",
                )
                status = 200
            except ProviderTransportError as exc:
                if exc.code != "provider_upstream_http":
                    raise RuntimeError(exc.code) from exc
                status = exc.status_code or 500
                body = None
            if status == 200:
                if not isinstance(body, dict):
                    raise RuntimeError("web_search_schema_error")
                raw_rows = body.get("results")
                if not isinstance(raw_rows, list):
                    raise RuntimeError("web_search_schema_error")
                rows: list[dict[str, str]] = []
                for item in raw_rows[:7]:
                    if not isinstance(item, dict):
                        continue
                    title = str(item.get("title") or "").strip()
                    content = str(item.get("content") or "").strip()
                    url = str(item.get("url") or "").strip()
                    if title and content and url.startswith(("http://", "https://")):
                        rows.append({"title": title, "content": content, "url": url})
                if not rows:
                    raise RuntimeError("web_search_empty")
                answer = str(body.get("answer") or "").strip() or None
                data = {
                    "query": query,
                    "topic": topic,
                    "days": days,
                    "answer": answer,
                    "results": rows,
                    "content": self._format_content(answer, rows),
                }
                self.key_manager.report_success(ServiceType.TAVILY, key)
                return ProviderResponse(
                    data=data,
                    source=self.source_name,
                    source_url=self.source_url,
                    fetched_at=datetime.now(timezone.utc),
                    metadata={
                        "provider_kind": "typed",
                        "untrusted": True,
                        "records": len(rows),
                        "topic": topic,
                        "key_attempts": attempts,
                    },
                )
            if status in {401, 403, 429}:
                last_error = "quota" if status == 429 else str(status)
                self.key_manager.report_error(ServiceType.TAVILY, key, last_error)
                continue
            if status >= 500:
                last_error = "upstream_5xx"
                self.key_manager.report_error(ServiceType.TAVILY, key, last_error)
                continue
            raise RuntimeError("web_search_upstream_rejected")
        raise RuntimeError("web_search_quota_exhausted" if last_error in {"quota", "401", "403"} else "web_search_unavailable")

    async def _fetch_duckduckgo(
        self,
        client: httpx.AsyncClient,
        request: ProviderRequest,
        query: str,
        topic: str,
        days: int | None,
    ) -> ProviderResponse[dict[str, Any]]:
        payload = await ProviderHttpTransport(
            client,
            allowed_hosts={httpx.URL(self.fallback_endpoint).host or ""},
        ).get_json(
            self.fallback_endpoint,
            params={"q": query, "format": "json", "no_html": "1", "no_redirect": "1", "skip_disambig": "1"},
            timeout=min(max(float(request.timeout_seconds), 0.5), self.timeout_seconds),
            error_code="web_search_fallback_invalid_json",
            expected_content_types=("application/json", "application/problem+json", "text/json", "application/x-javascript"),
        )
        if not isinstance(payload, dict):
            raise RuntimeError("web_search_schema_error")
        rows: list[dict[str, str]] = []

        def collect(items: Any) -> None:
            if not isinstance(items, list):
                return
            for item in items:
                if not isinstance(item, dict):
                    continue
                url = str(item.get("FirstURL") or "").strip()
                text = str(item.get("Text") or "").strip()
                if url.startswith(("http://", "https://")) and text:
                    title = text.split(" - ", 1)[0][:300]
                    rows.append({"title": title or "Arama sonucu", "content": text[:500], "url": url})
                collect(item.get("Topics"))
                if len(rows) >= 7:
                    return

        abstract = str(payload.get("AbstractText") or "").strip()
        abstract_url = str(payload.get("AbstractURL") or "").strip()
        if abstract and abstract_url.startswith(("http://", "https://")):
            rows.append({"title": str(payload.get("Heading") or "Ã–zet")[:300], "content": abstract[:500], "url": abstract_url})
        collect(payload.get("RelatedTopics"))
        if not rows:
            raise RuntimeError("web_search_empty")
        answer = abstract or None
        return ProviderResponse(
            data={
                "query": query,
                "topic": topic,
                "days": days,
                "answer": answer,
                "results": rows[:7],
                "content": self._format_content(answer, rows[:7]),
            },
            source="DuckDuckGo Instant Answers",
            source_url="https://duckduckgo.com/",
            fetched_at=datetime.now(timezone.utc),
            metadata={"provider_kind": "typed", "untrusted": True, "records": len(rows[:7]), "fallback": "duckduckgo"},
        )

    async def normalize(self, raw: Any) -> Any:
        return raw

    async def health_check(self) -> ProviderHealth:
        configured = bool(getattr(self.key_manager, "keys", {}).get(ServiceType.TAVILY))
        return ProviderHealth(
            name=self.name,
            healthy=True,
            detail="configured" if configured else "duckduckgo_fallback",
        )

    async def close(self) -> None:
        if self._owned_client is not None:
            await self._owned_client.aclose()
            self._owned_client = None
