"""Bounded, atomic quote rendering outside Telegram handlers."""

from __future__ import annotations

import asyncio
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .manager import QuoteManager
from .options import QuoteOptionsError, normalize_quote_options


class QuoteRenderError(RuntimeError):
    """Stable error codes for quote rendering failures."""


@dataclass(frozen=True)
class QuoteArtifact:
    path: Path
    size: int
    media_type: str


class QuoteRenderService:
    MAX_MESSAGES = 10
    MAX_OUTPUT_BYTES = 8 * 1024 * 1024

    def __init__(
        self,
        *,
        output_dir: str | os.PathLike[str] = "downloads/quotes",
        manager_factory: Callable[[], Any] = QuoteManager,
        max_concurrency: int = 2,
        timeout_seconds: float = 8.0,
        max_messages: int = MAX_MESSAGES,
        max_output_bytes: int = MAX_OUTPUT_BYTES,
    ) -> None:
        self.output_dir = Path(output_dir).expanduser().resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.manager_factory = manager_factory
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self.max_messages = max(1, min(int(max_messages), self.MAX_MESSAGES))
        self.max_output_bytes = max(1, min(int(max_output_bytes), self.MAX_OUTPUT_BYTES))
        self._semaphore = asyncio.Semaphore(max(1, int(max_concurrency)))

    async def render(self, messages: list[dict[str, Any]], options: dict[str, Any] | None = None) -> QuoteArtifact:
        values = list(messages or [])
        if not values or len(values) > self.max_messages:
            raise QuoteRenderError("message_limit")
        try:
            options = normalize_quote_options(options)
        except QuoteOptionsError as exc:
            raise QuoteRenderError(str(exc)) from exc
        extension = "png" if bool(options.get("is_png")) else "webp"
        media_type = "image/png" if extension == "png" else "image/webp"
        token = uuid.uuid4().hex
        job_dir = self.output_dir / f".quote_{token}"
        job_dir.mkdir(parents=True, exist_ok=False)
        partial = job_dir / f"quote_{token}.{extension}.part"
        final = self.output_dir / f"quote_{token}.{extension}"
        async with self._semaphore:
            def render_sync() -> Any:
                return self.manager_factory().generate_quote(values, str(partial), **options)

            worker = asyncio.create_task(
                asyncio.to_thread(render_sync)
            )
            try:
                await asyncio.wait_for(worker, timeout=self.timeout_seconds)
            except asyncio.TimeoutError as exc:
                worker.add_done_callback(self._cleanup_task_output(partial, job_dir))
                self._cleanup_job(job_dir)
                raise QuoteRenderError("render_timeout") from exc
            except Exception as exc:
                self._cleanup_job(job_dir)
                raise QuoteRenderError("render_failed") from exc
            if not partial.is_file():
                self._cleanup_job(job_dir)
                raise QuoteRenderError("render_output_missing")
            size = partial.stat().st_size
            if size <= 0:
                self._cleanup_job(job_dir)
                raise QuoteRenderError("render_output_empty")
            if size > self.max_output_bytes:
                self._cleanup_job(job_dir)
                raise QuoteRenderError("output_too_large")
            os.replace(partial, final)
            self._cleanup_job(job_dir)
            return QuoteArtifact(path=final, size=size, media_type=media_type)

    @staticmethod
    def _unlink(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            return

    @classmethod
    def _cleanup_job(cls, path: Path) -> None:
        try:
            shutil.rmtree(path, ignore_errors=True)
        except OSError:
            return

    @classmethod
    def _cleanup_task_output(cls, path: Path, job_dir: Path):
        def cleanup(_task: asyncio.Task) -> None:
            cls._unlink(path)
            cls._cleanup_job(job_dir)

        return cleanup

    async def health_check(self) -> dict[str, Any]:
        return {
            "healthy": self.output_dir.is_dir(),
            "output_dir": str(self.output_dir),
            "max_concurrency": self._semaphore._value,
            "max_output_bytes": self.max_output_bytes,
        }

    async def close(self) -> None:
        return None
