"""Bounded, provider-independent text-to-speech orchestration."""

from __future__ import annotations

import asyncio
import html
import os
import re
import uuid
from pathlib import Path

from src.ai.tts_provider import EdgeTTSProvider, SpeechArtifact, SpeechProvider, SpeechSynthesisRequest


class TTSService:
    VOICE_MAP = {
        "tr": "tr-TR-AhmetNeural",
        "en": "en-US-EmmaNeural",
        "de": "de-DE-KillianNeural",
    }

    def __init__(
        self,
        provider: SpeechProvider | None = None,
        *,
        output_dir: str | os.PathLike[str] = "downloads",
        max_concurrency: int = 2,
        timeout_seconds: float = 30.0,
        max_chars: int = 1000,
        max_output_bytes: int = 12 * 1024 * 1024,
    ) -> None:
        self.provider = provider or EdgeTTSProvider()
        self.output_dir = Path(output_dir).expanduser().resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.max_chars = max(100, min(int(max_chars), 5000))
        self.max_output_bytes = max(1024, int(max_output_bytes))
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self._semaphore = asyncio.Semaphore(max(1, min(int(max_concurrency), 8)))

    def clean_text(self, text: str) -> str:
        value = html.unescape(str(text or ""))
        value = re.sub(r"<[^>]+>", "", value)
        value = re.sub(r"https?://\S+", "link", value)
        value = " ".join(value.split()).strip()
        return value[: self.max_chars]

    def _target_path(self, output_path: str | os.PathLike[str] | None) -> Path:
        target = (self.output_dir / f"tts_{uuid.uuid4().hex[:12]}.mp3") if output_path is None else Path(output_path).expanduser().resolve()
        try:
            target.relative_to(self.output_dir)
        except ValueError as exc:
            raise ValueError("tts_output_path_invalid") from exc
        if target.suffix.casefold() != ".mp3" or target.name.startswith("."):
            raise ValueError("tts_output_path_invalid")
        return target

    async def synthesize(
        self,
        text: str,
        language: str = "tr",
        output_dir: str | os.PathLike[str] | None = None,
        output_path: str | os.PathLike[str] | None = None,
    ) -> str:
        cleaned = self.clean_text(text)
        if not cleaned:
            raise ValueError("tts_text_required")
        if output_dir is not None and output_path is None:
            # Keep the legacy parameter while still applying the same root
            # safety rule. Callers should normally inject output_dir at init.
            requested_root = Path(output_dir).expanduser().resolve()
            if requested_root != self.output_dir:
                raise ValueError("tts_output_path_invalid")
        target = self._target_path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.part")
        normalized_language = str(language or "tr").casefold().split("-", 1)[0]
        if normalized_language not in self.VOICE_MAP:
            normalized_language = "tr"
        request = SpeechSynthesisRequest(
            text=cleaned,
            language=normalized_language,
            voice=self.VOICE_MAP[normalized_language],
            output_path=str(temporary),
        )
        try:
            async with self._semaphore:
                artifact = await asyncio.wait_for(
                    self.provider.synthesize(request), timeout=self.timeout_seconds
                )
                if not isinstance(artifact, SpeechArtifact):
                    raise RuntimeError("tts_invalid_artifact")
                artifact_path = Path(artifact.path).expanduser().resolve()
                if artifact_path != temporary.resolve():
                    raise RuntimeError("tts_provider_path_mismatch")
                if not temporary.exists() or temporary.stat().st_size <= 0:
                    raise RuntimeError("tts_empty_output")
                if temporary.stat().st_size > self.max_output_bytes:
                    raise RuntimeError("tts_output_too_large")
                os.replace(temporary, target)
            return str(target)
        finally:
            temporary.unlink(missing_ok=True)

    async def health_check(self) -> dict[str, object]:
        result = await self.provider.health_check()
        return {
            **dict(result or {}),
            "provider": getattr(self.provider, "name", "unknown"),
            "max_concurrency": self._semaphore._value,
        }

    async def close(self) -> None:
        close = getattr(self.provider, "close", None)
        if close is not None:
            result = close()
            if hasattr(result, "__await__"):
                await result


__all__ = ["TTSService", "SpeechArtifact", "SpeechSynthesisRequest"]

