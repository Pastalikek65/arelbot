"""AI compatibility wrapper for the typed web-search provider."""

from __future__ import annotations

from typing import Any, Dict

from .base import BaseTool
from ..features.contracts import ProviderRequest
from ..features.providers.web_search import TavilySearchProvider
from ..key_manager import KeyManager
from ..security.log_redaction import summarize_exception, summarize_private_text
from ..security.error_codes import normalize_error_code
import logging

logger = logging.getLogger(__name__)


class WebSearchTool(BaseTool):
    def __init__(self, provider_or_key_manager: TavilySearchProvider | KeyManager):
        super().__init__(
            name="web_search",
            description=(
                "Real-time web search for current information. Results are "
                "external, untrusted context and include source links."
            ),
        )
        self.provider = (
            provider_or_key_manager
            if isinstance(provider_or_key_manager, TavilySearchProvider)
            else TavilySearchProvider(provider_or_key_manager)
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Current, specific search query.",
                },
                "topic": {
                    "type": "string",
                    "enum": ["general", "news"],
                    "description": "Use news only for recent headlines.",
                },
                "days": {
                    "type": "integer",
                    "description": "For news searches, look back this many days (1-30).",
                },
            },
            "required": ["query"],
        }

    async def execute(self, query: str, topic: str = "general", days: int = 3) -> Dict[str, Any]:
        try:
            logger.info(
                "WebSearch execution query=%s topic=%s days=%s",
                summarize_private_text(query, label="query"),
                topic,
                days,
            )
            response = await self.provider.fetch(
                ProviderRequest(
                    operation="web_search",
                    params={"query": query, "topic": topic, "days": days},
                    timeout_seconds=self.provider.timeout_seconds,
                )
            )
            data = dict(response.data)
            return {
                "result": data.get("content", ""),
                "data": data,
                "source": response.source,
                "source_url": response.source_url,
                "fetched_at": response.fetched_at.isoformat(),
                "metadata": dict(response.metadata),
            }
        except ValueError as exc:
            code = normalize_error_code(exc, "WEB_SEARCH_INVALID_REQUEST")
            return {"error": "web_search_request_rejected", "error_code": code}
        except Exception as exc:
            logger.error("WebSearchTool failed: %s", summarize_exception(exc, label="web_search"))
            return {"error": "web_search_failed", "error_code": "WEB_SEARCH_UNAVAILABLE"}

    async def search(self, query: str, topic: str = "general", days: int = 3) -> str:
        """Compatibility method for the legacy /ara surface.

        The call still uses the typed provider; callers receive only the
        bounded rendered context, never a raw upstream response.
        """

        result = await self.execute(query=query, topic=topic, days=days)
        return str(result.get("result") or "")
