"""Small, optional FFmpeg conversion step for Telegram-friendly video delivery."""

import asyncio
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict


class MediaTranscodeError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class MediaTranscoder:
    def __init__(self, max_bytes: int):
        self.max_bytes = max_bytes

    async def prepare(self, result: Dict[str, Any]) -> Dict[str, Any]:
        source = Path(result["path"])
        # Providers enforce the limit while downloading, but the final
        # artifact is the last trust boundary before Telegram.  Fallback
        # providers and remuxers may report a stale size, so always check the
        # bytes on disk before allowing a direct MP4 upload.
        try:
            actual_size = source.stat().st_size
        except OSError as exc:
            raise MediaTranscodeError("artifact_missing", "Medya dosyasÄ± hazÄ±rlanamadÄ±") from exc
        declared_size = int(result.get("size") or 0)
        if actual_size > self.max_bytes or declared_size > self.max_bytes:
            raise MediaTranscodeError("file_too_large", "Medya dosyasÄ± Telegram gÃ¶nderim sÄ±nÄ±rÄ±nÄ± aÅŸÄ±yor")
        if source.suffix.lower() == ".mp4":
            checked = dict(result)
            checked["size"] = actual_size
            return checked
        if not shutil.which("ffmpeg") and source.suffix.lower() in {".webm", ".mkv", ".mov", ".avi", ".flv"}:
            raise MediaTranscodeError("ffmpeg_unavailable", "Bu video biÃ§imi iÃ§in FFmpeg gerekli")
        target = source.with_suffix(".telegram.mp4")
        command = [
            "ffmpeg", "-y", "-i", str(source), "-c:v", "libx264", "-preset", "ultrafast",
            "-crf", "28", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(target),
        ]
        try:
            await asyncio.to_thread(subprocess.run, command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if target.exists() and target.stat().st_size <= self.max_bytes:
                source.unlink(missing_ok=True)
                result = dict(result)
                result.update(path=str(target), size=target.stat().st_size, extension=".mp4")
                return result
        except (OSError, subprocess.CalledProcessError):
            pass
        target.unlink(missing_ok=True)
        return result
