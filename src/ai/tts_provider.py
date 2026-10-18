"""Typed speech synthesis providers.

The Telegram layer only sees ``TTSService``.  Provider-specific SDK imports
and network behavior stay behind this small contract.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class SpeechSynthesisRequest:
    text: str
    language: str
    voice: str
    output_path: str


@dataclass(frozen=True)
class SpeechArtifact:
    path: str
    mime_type: str
    provider: str
    duration_seconds: float | None = None


class SpeechProvider(Protocol):
    name: str

    async def synthesize(self, request: SpeechSynthesisRequest) -> SpeechArtifact: ...

    async def health_check(self) -> dict[str, object]: ...


class EdgeTTSProvider:
    """Adapter for the optional edge-tts service."""

    name = "edge-tts"

    async def synthesize(self, request: SpeechSynthesisRequest) -> SpeechArtifact:
        import edge_tts

        target = Path(request.output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        communicator = edge_tts.Communicate(request.text, request.voice)
        await communicator.save(str(target))
        if not target.exists() or target.stat().st_size <= 0:
            raise RuntimeError("tts_empty_output")
        return SpeechArtifact(path=str(target), mime_type="audio/mpeg", provider=self.name)

    async def health_check(self) -> dict[str, object]:
        configured = importlib.util.find_spec("edge_tts") is not None
        return {"name": self.name, "healthy": configured, "detail": "installed" if configured else "missing_dependency"}

