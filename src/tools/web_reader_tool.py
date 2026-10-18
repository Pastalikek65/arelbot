"""AI-facing wrapper for the typed public web reader provider."""

from __future__ import annotations

from typing import Any, Dict

from .base import BaseTool
from src.features.contracts import ProviderRequest
from src.features.providers.web_reader import WebReaderProvider
from src.security.error_codes import normalize_error_code


class WebReaderTool(BaseTool):
    """Keep legacy tool discovery while routing execution through a provider."""

    def __init__(self, provider: WebReaderProvider | None = None):
        super().__init__(
            name="web_reader",
            description="Read a bounded public HTTP(S) page as untrusted context; redirects and private hosts are rejected.",
        )
        self.provider = provider or WebReaderProvider()

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Public HTTPS or HTTP URL; credentials, private hosts and unsafe redirects are rejected.",
                }
            },
            "required": ["url"],
        }

    async def execute(self, url: str, **_: Any) -> Dict[str, Any]:
        try:
            response = await self.provider.fetch(
                ProviderRequest(operation="web_reader", params={"url": str(url or "")})
            )
            payload = dict(response.data)
            payload.update(
                {
                    "source": response.source,
                    "source_url": response.source_url,
                    "metadata": dict(response.metadata),
                }
            )
            return {"result": payload.get("content", ""), **payload}
        except ValueError as exc:
            code = normalize_error_code(exc, "WEB_READER_INVALID_REQUEST")
            return {"error": "web_reader_request_rejected", "error_code": code}
        except Exception:
            return {"error": "web_reader_source_unavailable", "error_code": "WEB_READER_UNAVAILABLE"}
