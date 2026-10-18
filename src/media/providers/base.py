"""Provider contracts shared by platform extractors and fallbacks."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from collections.abc import Mapping
from typing import Any, NotRequired, Protocol, Required, TypedDict, cast


@dataclass(frozen=True)
class MediaRequest:
    url: str
    job_id: str
    platform: str
    timeout_seconds: float = 15.0


@dataclass
class MediaMetadata:
    title: str = ""
    platform: str = "unknown"
    duration: float = 0.0
    size: int | None = None
    media_type: str = "video"
    extra: dict[str, Any] = field(default_factory=dict)


class MediaArtifact(TypedDict, total=False):
    """Validated hand-off produced by a media provider.

    The artifact remains a mapping for compatibility with the existing
    transcoder and delivery code, while the provider boundary documents the
    fields every implementation must return. ``used_cookie`` is optional
    because public downloads deliberately omit it.
    """

    path: Required[str]
    title: Required[str]
    duration: Required[float]
    size: Required[int]
    extension: Required[str]
    platform: Required[str]
    used_cookie: NotRequired[bool]


def validate_media_artifact(value: Mapping[str, Any]) -> MediaArtifact:
    """Validate provider output before it reaches transcoding or Telegram."""

    if not isinstance(value, Mapping):
        raise ValueError("media_artifact_invalid")
    required_strings = ("path", "title", "extension", "platform")
    if any(not isinstance(value.get(key), str) or not str(value.get(key)).strip() for key in required_strings):
        raise ValueError("media_artifact_invalid")
    extension = str(value["extension"]).lower()
    if not extension.startswith(".") or len(extension) > 12 or any(char in extension for char in "\\/\x00"):
        raise ValueError("media_artifact_extension_invalid")
    for key in ("duration", "size"):
        candidate = value.get(key)
        if isinstance(candidate, bool) or not isinstance(candidate, (int, float)):
            raise ValueError("media_artifact_numeric_invalid")
        if not math.isfinite(float(candidate)) or float(candidate) < 0:
            raise ValueError("media_artifact_numeric_invalid")
    if "used_cookie" in value and not isinstance(value["used_cookie"], bool):
        raise ValueError("media_artifact_cookie_flag_invalid")
    if len(str(value["title"])) > 900 or len(str(value["platform"])) > 32:
        raise ValueError("media_artifact_text_too_long")
    return cast(MediaArtifact, dict(value))


@dataclass
class ProviderHealth:
    name: str
    healthy: bool
    latency_ms: float | None = None
    error_rate: float = 0.0
    last_success_at: float | None = None
    reason: str | None = None


class MediaProvider(Protocol):
    name: str
    # Providers may narrow this set to the platforms they explicitly support.
    # Missing/empty values are treated as a wildcard by ProviderRegistry so
    # existing third-party adapters remain compatible with the contract.
    platforms: frozenset[str]

    async def probe(self, request: MediaRequest) -> MediaMetadata: ...
    async def download(self, request: MediaRequest) -> MediaArtifact: ...
    async def health_check(self) -> ProviderHealth: ...
