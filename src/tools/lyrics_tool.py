"""AI compatibility wrapper for the typed lyrics provider."""

from __future__ import annotations

from typing import Any, Dict

from .base import BaseTool
from ..features.contracts import ProviderRequest
from ..features.providers.lyrics import LyricsProvider
from ..key_manager import KeyManager
from ..security.error_codes import normalize_error_code
from ..security.log_redaction import summarize_exception, summarize_private_text
import logging

logger = logging.getLogger(__name__)


class LyricsTool(BaseTool):
    def __init__(self, provider_or_key_manager: LyricsProvider | KeyManager):
        super().__init__(
            name="lyrics_tool",
            description="Searches a permitted lyrics source and returns a bounded excerpt with attribution.",
        )
        if isinstance(provider_or_key_manager, LyricsProvider):
            self.provider = provider_or_key_manager
        else:
            from ..features.providers.web_reader import WebReaderProvider
            from ..features.providers.web_search import TavilySearchProvider
            self.provider = LyricsProvider(
                TavilySearchProvider(provider_or_key_manager),
                WebReaderProvider(),
            )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "song": {"type": "string", "description": "Song title."},
                "artist": {"type": "string", "description": "Artist name (optional)."},
            },
            "required": ["song"],
        }

    async def execute(self, song: str, artist: str = "") -> Dict[str, Any]:
        try:
            logger.info(
                "Lyrics search song=%s artist=%s",
                summarize_private_text(song, label="song"),
                summarize_private_text(artist, label="artist"),
            )
            response = await self.provider.fetch(
                ProviderRequest(
                    operation="lyrics",
                    params={"song": song, "artist": artist},
                    timeout_seconds=self.provider.timeout_seconds,
                )
            )
            payload = dict(response.data)
            return {
                "result": payload.get("content", ""),
                "data": payload,
                "source": response.source,
                "source_url": response.source_url,
                "metadata": dict(response.metadata),
            }
        except ValueError as exc:
            return {
                "error": "lyrics_request_rejected",
                "error_code": normalize_error_code(exc, "LYRICS_INVALID_REQUEST"),
            }
        except Exception as exc:
            logger.error("LyricsTool failed: %s", summarize_exception(exc, label="lyrics"))
            return {"error": "lyrics_source_unavailable", "error_code": "LYRICS_UNAVAILABLE"}
