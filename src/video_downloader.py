"""Backward-compatible facade for the typed media provider.

The Telegram application uses :mod:`src.media.service` directly.  A few
external scripts historically imported ``VideoDownloader`` from this module,
so the import is kept as a deliberately small compatibility boundary.  It
must not grow a second downloader implementation: provider selection,
SSRF/redirect policy, cookies, size limits and cleanup all belong to the
typed media provider stack.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

from config import Config
from src.media.cookie_vault import CookieVault
from src.media.providers.ytdlp_provider import MediaDownloadError, YtDlpProvider
from src.security.log_redaction import summarize_exception

logger = logging.getLogger(__name__)


class VideoDownloader:
    """Compatibility adapter delegating every download to ``YtDlpProvider``.

    New code should submit work to ``MediaDownloadService`` so queue,
    deduplication and Telegram ``file_id`` caching remain available.  This
    adapter exists only for old integrations that need the former
    ``download() -> dict | None`` shape.
    """

    def __init__(self, download_dir: str = "downloads") -> None:
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.provider = YtDlpProvider(
            str(self.download_dir),
            Config.MEDIA_MAX_FILE_BYTES,
            Config.MEDIA_MAX_VIDEO_DURATION_SECONDS,
            Config.MEDIA_JOB_TIMEOUT_SECONDS,
            CookieVault(Config.MEDIA_COOKIE_STORAGE_DIR, Config.ENCRYPTION_KEY),
            Config.MEDIA_COOKIE_ENABLED,
            Config.MEDIA_ARIA2_ENABLED,
        )

    async def download(self, url: str) -> dict[str, Any] | None:
        """Return the historical result mapping while preserving typed policy."""

        job_id = uuid.uuid4().hex
        try:
            artifact = await self.provider.download(url, job_id)
        except MediaDownloadError as exc:
            logger.warning("Legacy media facade rejected download code=%s", exc.code)
            self.cleanup(None, job_id)
            return None
        except Exception as exc:  # pragma: no cover - defensive compatibility boundary
            logger.warning("Legacy media facade failed: %s", summarize_exception(exc, label="legacy_media"))
            self.cleanup(None, job_id)
            return None

        extension = str(artifact.get("extension") or "").lower()
        media_type = "video" if extension in {".mp4", ".mov", ".mkv", ".webm", ".m4v"} else "image"
        return {
            "type": media_type,
            "path": artifact["path"],
            "caption": artifact.get("title") or "Sosyal medya medyasÄ±",
            "author": "Unknown",
            "duration": artifact.get("duration", 0),
            "temp_id": job_id,
        }

    def cleanup(self, path: str | None, temp_id: str | None = None) -> None:
        """Keep the old cleanup signature while using provider-owned cleanup."""

        if temp_id:
            self.provider.cleanup(None, temp_id, str(self.download_dir))
            return
        if path:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                logger.debug("Legacy media cleanup could not remove artifact")


__all__ = ["VideoDownloader"]
