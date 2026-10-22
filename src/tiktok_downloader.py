"""Backward-compatible TikTok facade over the shared typed media provider."""

from __future__ import annotations

from typing import Any

from src.media.url_normalizer import InvalidMediaURL, platform_for_url
from src.video_downloader import VideoDownloader


class TikTokDownloader(VideoDownloader):
    """Preserve the old class name without a TikTok-specific HTTP bypass."""

    async def download(self, url: str) -> dict[str, Any] | None:
        try:
            if platform_for_url(url) != "tiktok":
                return None
        except InvalidMediaURL:
            return None
        return await super().download(url)


__all__ = ["TikTokDownloader"]
