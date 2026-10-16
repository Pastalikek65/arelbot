"""yt-dlp provider with bounded downloads and explicit cookie fallback."""

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from src.media.cookie_vault import CookieVault, CookieVaultError
from src.media.providers.base import MediaArtifact
from src.media.url_normalizer import InvalidMediaURL, ensure_public_host, platform_for_url

logger = logging.getLogger(__name__)


class _SilentYtDlpLogger:
    """Prevent upstream extractor text/URLs from reaching process stderr."""

    def debug(self, _message: str) -> None:
        return None

    def info(self, _message: str) -> None:
        return None

    def warning(self, _message: str) -> None:
        return None

    def error(self, _message: str) -> None:
        return None


class MediaDownloadError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# Public extractors sometimes return a login wall to a default Python client
# even when the same URL is available without an account in a normal browser.
# These headers only describe a normal public web request; they never provide
# credentials or bypass a platform access control.  Keep the policy local to
# the typed provider so legacy facades cannot create a second HTTP path.
_COMMON_PUBLIC_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
    ),
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
}
_PLATFORM_REFERERS = {
    "instagram": "https://www.instagram.com/",
    "tiktok": "https://www.tiktok.com/",
    "youtube": "https://www.youtube.com/",
    "x": "https://x.com/",
    "facebook": "https://www.facebook.com/",
    "reddit": "https://www.reddit.com/",
}


class YtDlpProvider:
    name = "yt-dlp"

    def __init__(self, download_dir: str, max_bytes: int, max_duration: int, timeout: int,
                 cookie_vault: CookieVault, cookie_enabled: bool = False,
                 aria2_enabled: bool = True):
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self.max_duration = max_duration
        self.timeout = timeout
        self.cookie_vault = cookie_vault
        self.cookie_enabled = cookie_enabled
        self.aria2_enabled = bool(aria2_enabled)

    async def probe(self, url: str) -> Dict[str, Any]:
        """Return lightweight metadata without downloading the media."""
        try:
            import yt_dlp
        except ImportError as exc:
            raise MediaDownloadError("provider_unavailable", "yt-dlp kurulmamÄ±ÅŸ") from exc
        try:
            await ensure_public_host(url)
            options = {
                "quiet": True,
                "no_warnings": True,
                "logger": _SilentYtDlpLogger(),
                "compat_opts": ["no-certifi"],
                "skip_download": True,
                "noplaylist": True,
                "socket_timeout": 5,
            }
            options.update(self._platform_options(url))
            try:
                info = await asyncio.wait_for(
                    asyncio.to_thread(self._probe_sync, yt_dlp, url, options),
                    min(self.timeout, 8),
                )
            except Exception as public_error:
                # Instagram can expose a public page while rejecting the
                # metadata request used by yt-dlp.  In that case the old
                # pipeline stopped during probe and never reached the
                # administrator cookie fallback used by download(). Retry
                # only a classified access failure and only when the owner
                # explicitly enabled the encrypted cookie vault.
                if not self._should_retry_with_cookie(url, public_error):
                    raise
                info = await self._probe_with_cookie(yt_dlp, url, options)
            return {
                "title": (info.get("title") or "")[:900],
                "duration": info.get("duration") or 0,
                "size": info.get("filesize") or info.get("filesize_approx"),
                "platform": platform_for_url(url),
            }
        except asyncio.TimeoutError as exc:
            raise MediaDownloadError("timeout", "Metadata sorgusu zaman aÅŸÄ±mÄ±na uÄŸradÄ±") from exc
        except InvalidMediaURL as exc:
            # Keep the shared SSRF/private-host policy observable at the
            # media boundary instead of hiding it behind ``probe_failed``.
            code = str(exc.args[0] if exc.args else "invalid_url")[:64]
            raise MediaDownloadError(code, "BaÄŸlantÄ± gÃ¼venli bir dÄ±ÅŸ hosta iÅŸaret etmiyor") from exc
        except MediaDownloadError:
            raise
        except Exception as exc:
            code, message = self._safe_source_error(exc, phase="probe")
            raise MediaDownloadError(code, message) from exc

    def _should_retry_with_cookie(self, url: str, error: Exception) -> bool:
        if not self.cookie_enabled:
            return False
        platform = platform_for_url(url)
        try:
            has_cookie = bool(self.cookie_vault.has(platform))
        except Exception:
            has_cookie = False
        if not has_cookie:
            return False
        code, _ = self._safe_source_error(error, phase="probe")
        return code == "source_access_restricted"

    async def _probe_with_cookie(self, yt_dlp: Any, url: str, options: Dict[str, Any]) -> Dict[str, Any]:
        platform = platform_for_url(url)
        probe_dir = Path(tempfile.mkdtemp(prefix="probe-", dir=self.download_dir))
        cookie_path = probe_dir / "admin.cookies.txt"
        try:
            try:
                self.cookie_vault.load_to_file(platform, str(cookie_path))
            except CookieVaultError as exc:
                raise MediaDownloadError("cookie_invalid", "YÃ¶netici cookie kasasÄ± aÃ§Ä±lamadÄ±") from exc
            cookie_options = dict(options)
            cookie_options["cookiefile"] = str(cookie_path)
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(self._probe_sync, yt_dlp, url, cookie_options),
                    min(self.timeout, 8),
                )
            except asyncio.TimeoutError as exc:
                raise MediaDownloadError("timeout", "Cookie ile metadata sorgusu zaman aÅŸÄ±mÄ±na uÄŸradÄ±") from exc
            except Exception as exc:
                code, message = self._safe_source_error(exc, phase="probe")
                raise MediaDownloadError(code, message) from exc
        finally:
            shutil.rmtree(probe_dir, ignore_errors=True)

    @staticmethod
    def _probe_sync(yt_dlp, url: str, options: Dict[str, Any]) -> Dict[str, Any]:
        with yt_dlp.YoutubeDL(options) as ydl:
            return ydl.extract_info(url, download=False) or {}

    async def health_check(self):
        from src.media.providers.base import ProviderHealth
        try:
            import yt_dlp  # noqa: F401
            return ProviderHealth(name=self.name, healthy=True)
        except ImportError:
            return ProviderHealth(name=self.name, healthy=False, reason="yt_dlp_missing")

    async def download(self, url: str, job_id: str) -> MediaArtifact:
        try:
            await ensure_public_host(url)
        except InvalidMediaURL as exc:
            raise MediaDownloadError(exc.args[0] if exc.args else "invalid_url", "BaÄŸlantÄ± gÃ¼venli bir dÄ±ÅŸ hosta iÅŸaret etmiyor") from exc
        platform = platform_for_url(url)
        job_dir = self.download_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        completed = False
        try:
            try:
                result = await asyncio.wait_for(asyncio.to_thread(self._download_sync, url, job_dir, None), self.timeout)
                completed = True
                return result
            except asyncio.TimeoutError as public_error:
                raise MediaDownloadError("timeout", "Ä°ndirme zaman aÅŸÄ±mÄ±na uÄŸradÄ±") from public_error
            except Exception as public_error:
                if isinstance(public_error, MediaDownloadError) and public_error.code in {
                    "file_too_large", "duration_limit", "non_public_host", "timeout"
                }:
                    raise public_error
                if not (self.cookie_enabled and self.cookie_vault.has(platform)):
                    if isinstance(public_error, MediaDownloadError):
                        raise public_error
                    code, message = self._safe_source_error(public_error, phase="download")
                    raise MediaDownloadError(code, message) from public_error
                cookie_path = job_dir / "admin.cookies.txt"
                try:
                    self.cookie_vault.load_to_file(platform, str(cookie_path))
                except CookieVaultError as exc:
                    raise MediaDownloadError("cookie_invalid", "YÃ¶netici cookie kasasÄ± aÃ§Ä±lamadÄ±") from exc
                try:
                    result = await asyncio.wait_for(
                        asyncio.to_thread(self._download_sync, url, job_dir, str(cookie_path)), self.timeout
                    )
                    result["used_cookie"] = True
                    completed = True
                    return result
                except Exception as cookie_error:
                    if isinstance(cookie_error, MediaDownloadError):
                        raise cookie_error
                    code, message = self._safe_source_error(cookie_error, phase="download")
                    raise MediaDownloadError(code, message) from cookie_error
        except asyncio.TimeoutError as exc:
            raise MediaDownloadError("timeout", "Ä°ndirme zaman aÅŸÄ±mÄ±na uÄŸradÄ±") from exc
        finally:
            try:
                cookie_path = job_dir / "admin.cookies.txt"
                if cookie_path.exists():
                    cookie_path.unlink()
            except OSError:
                logger.warning("Could not remove temporary cookie file for job %s", job_id)
            if not completed:
                # A failed provider attempt has no result for the service's
                # outer cleanup hook. Remove partial media here so retries do
                # not inherit untrusted or oversized artifacts.
                shutil.rmtree(job_dir, ignore_errors=True)

    def _download_sync(self, url: str, job_dir: Path, cookie_file: Optional[str]) -> Dict[str, Any]:
        try:
            import yt_dlp
        except ImportError as exc:
            raise MediaDownloadError("provider_unavailable", "yt-dlp kurulmamÄ±ÅŸ") from exc

        output = str(job_dir / "media.%(ext)s")

        def enforce_limits(data: Dict[str, Any]) -> None:
            downloaded = data.get("downloaded_bytes") or 0
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            if downloaded > self.max_bytes or total > self.max_bytes:
                raise MediaDownloadError("file_too_large", "Medya dosyasÄ± Telegram gÃ¶nderim sÄ±nÄ±rÄ±nÄ± aÅŸÄ±yor")

        options: Dict[str, Any] = {
            "format": "best[ext=mp4][height<=720][filesize<47000000]/best[height<=720][filesize<47000000]/best[filesize<47000000]/best[ext=mp4]/best",
            "outtmpl": output,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "logger": _SilentYtDlpLogger(),
            "compat_opts": ["no-certifi"],
            "ignoreerrors": False,
            "socket_timeout": 8,
            "retries": 1,
            "fragment_retries": 1,
            "concurrent_fragment_downloads": 4,
            "max_filesize": self.max_bytes,
            "progress_hooks": [enforce_limits],
            "restrictfilenames": True,
            "overwrites": False,
        }
        options.update(self._platform_options(url))
        aria2_path = shutil.which("aria2c") if self.aria2_enabled else None
        if aria2_path:
            # aria2c is used only for direct HTTP(S) media transfers. yt-dlp
            # still selects and validates the format; aria2c only accelerates
            # the byte transfer with bounded connections and no shell input.
            options["external_downloader"] = aria2_path
            options["external_downloader_args"] = {
                "default": [
                    "-x", "8", "-s", "8", "-k", "1M",
                    "--file-allocation=none", "--summary-interval=0",
                    "--console-log-level=warn", "--download-result=hide",
                    "--show-console-readout=false",
                ]
            }
        if cookie_file:
            options["cookiefile"] = cookie_file
        # YouTube's default web player can expose metadata while rejecting the
        # subsequent media request in hosted/VDS networks.  The Android player
        # remains a first-party yt-dlp client and is used only for YouTube
        # downloads; certificate verification and the public-host policy stay
        # unchanged.
        try:
            import yt_dlp
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=True)
        except MediaDownloadError:
            raise
        except Exception as exc:
            message = str(exc).lower()
            if "max file size" in message or "file_too_large" in message:
                raise MediaDownloadError("file_too_large", "Medya dosyasÄ± Telegram gÃ¶nderim sÄ±nÄ±rÄ±nÄ± aÅŸÄ±yor") from exc
            raise

        if not info:
            raise MediaDownloadError("empty_result", "Kaynak medya bulunamadÄ±")
        duration = info.get("duration")
        if self.max_duration and duration and duration > self.max_duration:
            raise MediaDownloadError("duration_limit", f"Video {self.max_duration} saniyeden uzun")
        paths = [path for path in job_dir.glob("media.*") if path.is_file() and path.name != "admin.cookies.txt"]
        if not paths:
            raise MediaDownloadError("empty_result", "Ä°ndirilen medya dosyasÄ± bulunamadÄ±")
        path = paths[0]
        size = path.stat().st_size
        if size > self.max_bytes:
            raise MediaDownloadError("file_too_large", "Medya dosyasÄ± Telegram gÃ¶nderim sÄ±nÄ±rÄ±nÄ± aÅŸÄ±yor")
        return {
            "path": str(path),
            "title": (info.get("title") or "Sosyal medya videosu")[:900],
            "duration": duration or 0,
            "size": size,
            "extension": path.suffix.lower(),
            "platform": platform_for_url(url),
        }

    @staticmethod
    def _platform_options(url: str) -> Dict[str, Any]:
        """Return bounded, credential-free yt-dlp options for one platform."""

        platform = platform_for_url(url)
        headers = dict(_COMMON_PUBLIC_HEADERS)
        referer = _PLATFORM_REFERERS.get(platform)
        if referer:
            headers["Referer"] = referer
        options: Dict[str, Any] = {"http_headers": headers}
        if platform == "youtube":
            options["extractor_args"] = {"youtube": {"player_client": ["android"]}}
        return options

    @staticmethod
    def _safe_source_error(exc: Exception, *, phase: str) -> tuple[str, str]:
        """Classify known upstream access failures without exposing their text."""

        text = str(exc).casefold()
        if any(marker in text for marker in (
            "sign in", "login required", "log in", "private", "restricted", "not available",
        )):
            return "source_access_restricted", "Kaynak herkese aÃ§Ä±k eriÅŸime izin vermiyor"
        if any(marker in text for marker in ("rate limit", "too many requests", "http error 429", "429")):
            return "source_rate_limited", "Kaynak geÃ§ici olarak Ã§ok fazla istek aldÄ±"
        return f"{phase}_failed", "Kaynak metadata bilgisi alÄ±namadÄ±" if phase == "probe" else "Bu baÄŸlantÄ± ÅŸu anda indirilemedi"

    @staticmethod
    def cleanup(result: Optional[Dict[str, Any]], job_id: str, download_dir: str) -> None:
        if result and result.get("path"):
            try:
                Path(result["path"]).unlink(missing_ok=True)
            except OSError:
                pass
        shutil.rmtree(Path(download_dir) / job_id, ignore_errors=True)
