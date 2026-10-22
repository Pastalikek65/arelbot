"""Optional JSON-contract fallback provider.

Endpoints are configured, never hard-coded. A provider must return either
``{"url": "https://..."}`` or ``{"download_url": "https://..."}`` and may
include title/duration/size metadata. The returned URL is still checked for a
public host before the file is streamed to disk.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx

from src.media.providers.base import MediaArtifact, MediaMetadata, MediaRequest, ProviderHealth
from src.media.url_normalizer import ensure_public_host
from src.media.providers.ytdlp_provider import MediaDownloadError
from src.security.log_redaction import summarize_exception
from src.security.tls import httpx_verify_kwargs


class HttpFallbackProvider:
    # Configured fallback endpoints are intentionally generic. Their endpoint
    # contract is still subject to URL safety and response validation.
    platforms = frozenset({"*"})

    def __init__(self, name: str, endpoint: str, download_dir: str, max_bytes: int):
        self.name = name
        self.endpoint = endpoint.rstrip("/")
        self.download_dir = Path(download_dir)
        self.max_bytes = max_bytes

    async def _request(self, request: MediaRequest) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=request.timeout_seconds, follow_redirects=False, **httpx_verify_kwargs()) as client:
            response = await client.get(f"{self.endpoint}/probe", params={"url": request.url})
            if 300 <= response.status_code < 400:
                raise MediaDownloadError("redirect_not_allowed", "Fallback probe redirect döndürdü")
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise MediaDownloadError("fallback_invalid_response", "Fallback sağlayıcı geçersiz yanıt verdi")
            return payload

    async def probe(self, request: MediaRequest) -> MediaMetadata:
        payload = await self._request(request)
        return MediaMetadata(
            title=str(payload.get("title") or "")[:900],
            platform=request.platform,
            duration=float(payload.get("duration") or 0),
            size=int(payload.get("size") or 0) or None,
            media_type=str(payload.get("media_type") or "video"),
        )

    async def download(self, request: MediaRequest) -> MediaArtifact:
        metadata = await self._request(request)
        direct_url = metadata.get("download_url") or metadata.get("url")
        if not direct_url or not str(direct_url).startswith(("http://", "https://")):
            raise MediaDownloadError("fallback_missing_download_url", "Fallback sağlayıcı medya URL'si döndürmedi")
        await ensure_public_host(str(direct_url))
        job_dir = self.download_dir / request.job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        path = job_dir / "media.mp4"
        total = 0
        async with httpx.AsyncClient(timeout=request.timeout_seconds, follow_redirects=False, **httpx_verify_kwargs()) as client:
            async with client.stream("GET", str(direct_url)) as response:
                if 300 <= response.status_code < 400:
                    raise MediaDownloadError("redirect_not_allowed", "Medya kaynağı güvenli olmayan redirect döndürdü")
                response.raise_for_status()
                content_length = int(response.headers.get("content-length") or 0)
                if content_length > self.max_bytes:
                    raise MediaDownloadError("file_too_large", "Fallback medyası boyut sınırını aşıyor")
                with path.open("wb") as output:
                    async for chunk in response.aiter_bytes(128 * 1024):
                        total += len(chunk)
                        if total > self.max_bytes:
                            raise MediaDownloadError("file_too_large", "Fallback medyası boyut sınırını aşıyor")
                        output.write(chunk)
        return {
            "path": str(path),
            "title": str(metadata.get("title") or "Sosyal medya videosu")[:900],
            "duration": float(metadata.get("duration") or 0),
            "size": total,
            "extension": ".mp4",
            "platform": request.platform,
        }

    async def health_check(self) -> ProviderHealth:
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=3, **httpx_verify_kwargs()) as client:
                response = await client.get(f"{self.endpoint}/health")
                response.raise_for_status()
            return ProviderHealth(name=self.name, healthy=True, latency_ms=(time.perf_counter() - started) * 1000)
        except Exception as exc:
            return ProviderHealth(
                name=self.name,
                healthy=False,
                reason=summarize_exception(exc, label="media_fallback_health"),
            )
