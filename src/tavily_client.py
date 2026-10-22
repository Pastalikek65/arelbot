"""Deprecated compatibility facade for the typed Tavily provider.

Transport intentionally lives in ``src.features.providers.web_search``. This
module remains only for integrations that imported ``TavilyClient`` directly.
"""

from __future__ import annotations

from typing import Any

from src.features.contracts import ProviderRequest
from src.features.providers.web_search import TavilySearchProvider


class _StaticKeyManager:
    def __init__(self, key: str | None):
        self.key = key
        self.keys = {}

    def get_key(self, _service: Any) -> str:
        if not self.key:
            raise RuntimeError("web_search_not_configured")
        return self.key

    def report_success(self, _service: Any, _key: str) -> None:
        return None

    def report_error(self, _service: Any, _key: str, _message: str) -> None:
        return None


class TavilyClient:
    """Backward-compatible facade; new code should inject the provider."""

    def __init__(self, api_key: str | None = None, *, provider: TavilySearchProvider | None = None):
        self.provider = provider or TavilySearchProvider(_StaticKeyManager(api_key))

    async def search(
        self,
        query: str,
        search_depth: str = "basic",
        max_results: int = 5,
        api_key: str | None = None,
        **kwargs: Any,
    ) -> str:
        response = await self.provider.fetch(
            ProviderRequest(
                operation="web_search",
                params={"query": query, "topic": kwargs.get("topic", "general"), "days": kwargs.get("days", 3)},
                timeout_seconds=self.provider.timeout_seconds,
            )
        )
        return str(response.data.get("content", ""))
