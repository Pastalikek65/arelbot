"""Stable provider interfaces used by routing and future vision/STT adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class AIProviderRequestError(ValueError):
    """A deterministic caller-input failure that must not trip provider health."""


@dataclass(frozen=True)
class CompletionRequest:
    messages: list[dict[str, Any]]
    model: str | None = None
    task: str = "chat"
    tools: list[dict[str, Any]] = field(default_factory=list)
    max_tokens: int | None = None
    # DeepSeek-compatible providers may otherwise enable an expensive
    # reasoning pass by default. ``none`` selects the fast answer-only path.
    reasoning_effort: str | None = None
    thinking_enabled: bool | None = None
    response_format: dict[str, Any] | None = None


@dataclass
class CompletionResponse:
    content: str
    provider: str
    model: str
    usage: dict[str, int] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VisionRequest:
    """Bounded multimodal request accepted by provider adapters."""

    prompt: str
    image_path: str | None = None
    image_bytes: bytes | None = None
    mime_type: str = "image/jpeg"
    model: str | None = None
    max_tokens: int = 1200


@dataclass(frozen=True)
class AudioInput:
    """Audio payload for transcription providers.

    Exactly one of ``audio_path`` and ``audio_bytes`` should be supplied.  A
    provider may choose a local path or in-memory bytes, but must not log the
    contents.
    """

    audio_path: str | None = None
    audio_bytes: bytes | None = None
    filename: str = "audio.ogg"
    mime_type: str = "audio/ogg"
    language: str | None = "tr"
    model: str | None = None


@dataclass(frozen=True)
class Transcription:
    text: str
    provider: str
    model: str
    language: str | None = None
    usage: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class EmbeddingResponse:
    vectors: list[list[float]]
    provider: str
    model: str


class AIProvider(Protocol):
    name: str

    async def complete(self, request: CompletionRequest) -> CompletionResponse: ...
    async def vision(self, request: VisionRequest) -> CompletionResponse: ...
    async def transcribe(self, audio: AudioInput) -> Transcription: ...
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
    async def health_check(self) -> dict[str, Any]: ...
