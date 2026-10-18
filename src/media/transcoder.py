"""Small, optional FFmpeg conversion step for Telegram-friendly video delivery."""

import asyncio
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict


class MediaTranscodeError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class MediaTranscoder:
    def __init__(self, max_bytes: int, *, timeout_seconds: float = 60.0):
        self.max_bytes = max_bytes
        self.timeout_seconds = max(0.01, min(300.0, float(timeout_seconds)))

    @staticmethod
    async def _stop_process(process) -> None:
        if process.returncode is not None:
            return
        try:
            process.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(process.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()

    async def prepare(self, result: Dict[str, Any]) -> Dict[str, Any]:
        source = Path(result["path"])
        # Providers enforce the limit while downloading, but the final
        # artifact is the last trust boundary before Telegram.  Fallback
        # providers and remuxers may report a stale size, so always check the
        # bytes on disk before allowing a direct MP4 upload.
        try:
            actual_size = source.stat().st_size
        except OSError as exc:
            raise MediaTranscodeError("artifact_missing", "Medya dosyası hazırlanamadı") from exc
        declared_size = int(result.get("size") or 0)
        if actual_size > self.max_bytes or declared_size > self.max_bytes:
            raise MediaTranscodeError("file_too_large", "Medya dosyası Telegram gönderim sınırını aşıyor")
        if source.suffix.lower() == ".mp4":
            checked = dict(result)
            checked["size"] = actual_size
            return checked
        if not shutil.which("ffmpeg"):
            raise MediaTranscodeError("ffmpeg_unavailable", "Bu video biçimi için FFmpeg gerekli")
        target = source.with_suffix(".telegram.mp4")
        command = [
            "ffmpeg", "-y", "-i", str(source), "-c:v", "libx264", "-preset", "ultrafast",
            "-crf", "28", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(target),
        ]
        process = None
        keep_output = False
        try:
            process = await asyncio.create_subprocess_exec(
                *command, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            returncode = await asyncio.wait_for(process.wait(), timeout=self.timeout_seconds)
            if returncode != 0:
                raise MediaTranscodeError("transcode_failed", "Video Telegram biçimine dönüştürülemedi")
            if target.exists() and target.stat().st_size <= self.max_bytes:
                source.unlink(missing_ok=True)
                result = dict(result)
                result.update(path=str(target), size=target.stat().st_size, extension=".mp4")
                keep_output = True
                return result
            raise MediaTranscodeError("transcode_failed", "Dönüştürülen video gönderim sınırlarını karşılamıyor")
        except asyncio.TimeoutError as exc:
            if process is not None:
                await self._stop_process(process)
            raise MediaTranscodeError("transcode_timeout", "Video dönüştürme zaman sınırını aştı") from exc
        except asyncio.CancelledError:
            if process is not None:
                await self._stop_process(process)
            raise
        except OSError as exc:
            raise MediaTranscodeError("transcode_failed", "Video dönüştürme işlemi başlatılamadı") from exc
        finally:
            if not keep_output:
                target.unlink(missing_ok=True)
