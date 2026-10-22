"""Bounded static/video sticker rendering outside Telegram handlers."""

from __future__ import annotations

import asyncio
import io
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class StickerRenderError(ValueError):
    """Safe, user-facing sticker rendering error code."""


@dataclass(frozen=True)
class StickerArtifact:
    content: bytes
    filename: str
    format: str
    width: int
    height: int
    duration_seconds: float | None = None


class StickerRenderService:
    def __init__(
        self,
        *,
        temp_dir: str | os.PathLike[str] = "temp/stickers",
        max_concurrency: int = 2,
        max_input_bytes: int = 20 * 1024 * 1024,
        max_static_output_bytes: int = 512 * 1024,
        max_video_output_bytes: int = 256 * 1024,
        timeout_seconds: float = 20.0,
        ffmpeg_binary: str = "ffmpeg",
    ) -> None:
        self.temp_dir = Path(temp_dir).expanduser().resolve()
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.max_input_bytes = max(1, int(max_input_bytes))
        self.max_static_output_bytes = max(1024, int(max_static_output_bytes))
        self.max_video_output_bytes = max(1024, int(max_video_output_bytes))
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.ffmpeg_binary = ffmpeg_binary
        self._semaphore = asyncio.Semaphore(max(1, min(int(max_concurrency), 8)))

    async def render(self, content: bytes | bytearray, *, kind: str) -> StickerArtifact:
        raw = bytes(content or b"")
        if not raw:
            raise StickerRenderError("sticker_input_required")
        if len(raw) > self.max_input_bytes:
            raise StickerRenderError("sticker_input_too_large")
        kind = str(kind or "").casefold().strip()
        if kind not in {"static", "video"}:
            raise StickerRenderError("sticker_kind_invalid")
        async with self._semaphore:
            if kind == "static":
                return await asyncio.to_thread(self._render_static, raw)
            return await self._render_video(raw)

    def _render_static(self, content: bytes) -> StickerArtifact:
        try:
            from PIL import Image, ImageOps

            with Image.open(io.BytesIO(content)) as source:
                source = ImageOps.exif_transpose(source).convert("RGBA")
                source.thumbnail((512, 512), Image.Resampling.LANCZOS)
                canvas = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
                canvas.alpha_composite(
                    source,
                    ((canvas.width - source.width) // 2, (canvas.height - source.height) // 2),
                )
                output = io.BytesIO()
                quality = 92
                while quality >= 45:
                    output.seek(0)
                    output.truncate(0)
                    canvas.save(output, format="WEBP", method=6, quality=quality)
                    if output.tell() <= self.max_static_output_bytes:
                        return StickerArtifact(
                            content=output.getvalue(),
                            filename="sticker.webp",
                            format="webp",
                            width=canvas.width,
                            height=canvas.height,
                        )
                    quality -= 10
        except StickerRenderError:
            raise
        except Exception as exc:
            raise StickerRenderError("sticker_image_invalid") from exc
        raise StickerRenderError("sticker_output_too_large")

    async def _render_video(self, content: bytes) -> StickerArtifact:
        if shutil.which(self.ffmpeg_binary) is None:
            raise StickerRenderError("ffmpeg_unavailable")
        token = uuid.uuid4().hex
        input_path = self.temp_dir / f"{token}.input"
        output_path = self.temp_dir / f"{token}.webm"
        input_path.write_bytes(content)
        try:
            command = [
                self.ffmpeg_binary,
                "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(input_path),
                "-t", "2.9",
                "-an",
                "-vf", "scale=512:512:force_original_aspect_ratio=decrease,pad=512:512:(ow-iw)/2:(oh-ih)/2:color=black@0,format=yuva420p",
                "-r", "30",
                "-c:v", "libvpx-vp9",
                "-b:v", "256k",
                "-deadline", "realtime",
                "-cpu-used", "8",
                str(output_path),
            ]
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=self.timeout_seconds)
            if process.returncode != 0 or not output_path.exists():
                raise StickerRenderError("sticker_video_conversion_failed")
            if output_path.stat().st_size > self.max_video_output_bytes:
                raise StickerRenderError("sticker_video_output_too_large")
            return StickerArtifact(
                content=output_path.read_bytes(),
                filename="sticker.webm",
                format="webm",
                width=512,
                height=512,
                duration_seconds=2.9,
            )
        except asyncio.TimeoutError as exc:
            raise StickerRenderError("sticker_video_timeout") from exc
        finally:
            input_path.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)

    async def health_check(self) -> dict[str, object]:
        return {
            "name": "sticker_renderer",
            "healthy": shutil.which(self.ffmpeg_binary) is not None,
            "ffmpeg": bool(shutil.which(self.ffmpeg_binary)),
            "max_concurrency": self._semaphore._value,
        }
