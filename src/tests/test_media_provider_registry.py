from __future__ import annotations

from types import SimpleNamespace
from typing import get_type_hints

import pytest

from src.media.providers.base import MediaArtifact, MediaMetadata, MediaRequest, ProviderHealth, validate_media_artifact
from src.media.providers.registry import ProviderRegistry, YtDlpAdapter


class _Provider:
    def __init__(self, name: str, platforms: frozenset[str] | None = None, healthy: bool = True):
        self.name = name
        if platforms is not None:
            self.platforms = platforms
        self.healthy = healthy

    async def probe(self, request: MediaRequest) -> MediaMetadata:
        return MediaMetadata(title=self.name, platform=request.platform)

    async def download(self, request: MediaRequest):
        return {
            "path": "fixture",
            "title": self.name,
            "duration": 1.0,
            "size": 7,
            "extension": ".mp4",
            "platform": request.platform,
        }

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(name=self.name, healthy=self.healthy, reason="fixture_down" if not self.healthy else None)


class _BrokenHealthProvider(_Provider):
    async def health_check(self) -> ProviderHealth:
        raise RuntimeError("private endpoint https://example.test/health?token=secret")


class _FailingProvider(_Provider):
    async def probe(self, request: MediaRequest) -> MediaMetadata:
        raise RuntimeError("fixture provider down")


class _InvalidArtifactProvider(_Provider):
    async def download(self, request: MediaRequest):
        return {
            "path": "fixture",
            "title": self.name,
            "duration": 1.0,
            "size": 7,
            "extension": "../escape",
            "platform": request.platform,
        }


def test_registry_prefers_platform_specific_provider_but_keeps_wildcard_fallback():
    registry = ProviderRegistry([
        _Provider("fallback", frozenset({"*"})),
        _Provider("youtube", frozenset({"youtube"})),
        _Provider("unknown-contract"),
    ])

    assert [provider.name for provider in registry.ordered("youtube")] == [
        "youtube", "fallback", "unknown-contract"
    ]


def test_media_artifact_contract_keeps_delivery_fields_explicit():
    annotations = get_type_hints(MediaArtifact)
    required = {"path", "title", "duration", "size", "extension", "platform"}
    assert set(annotations) == required | {"used_cookie"}
    # ``total=False`` plus Required/NotRequired annotations is the portable
    # Python 3.11 shape; get_type_hints intentionally unwraps those markers.
    assert MediaArtifact.__total__ is False
    assert annotations["used_cookie"] is bool


def test_media_artifact_runtime_validation_rejects_unsafe_shapes():
    valid = validate_media_artifact({
        "path": "C:/spool/job/media.mp4",
        "title": "Fixture",
        "duration": 2.0,
        "size": 16,
        "extension": ".mp4",
        "platform": "youtube",
    })
    assert valid["extension"] == ".mp4"

    with pytest.raises(ValueError, match="media_artifact_extension_invalid"):
        validate_media_artifact({**valid, "extension": "../secret"})
    with pytest.raises(ValueError, match="media_artifact_numeric_invalid"):
        validate_media_artifact({**valid, "size": -1})


@pytest.mark.parametrize("platform", ["instagram", "x", "twitter"])
def test_registry_prefers_instagram_and_x_specific_adapters(platform: str):
    specific_platform = "instagram" if platform == "instagram" else "x"
    registry = ProviderRegistry([
        _Provider("fallback", frozenset({"*"})),
        _Provider(specific_platform, frozenset({specific_platform})),
    ])

    assert [provider.name for provider in registry.ordered(platform)] == [specific_platform, "fallback"]


@pytest.mark.asyncio
async def test_platform_specific_failure_opens_circuit_and_falls_back_to_wildcard():
    registry = ProviderRegistry(
        [_FailingProvider("instagram", frozenset({"instagram"})), _Provider("fallback", frozenset({"*"}))],
        failure_threshold=1,
        cooldown=60,
    )

    metadata = await registry.probe(MediaRequest("https://www.instagram.com/reel/fixture", "job-1", "instagram"))

    assert metadata.title == "fallback"
    health = {row.name: row for row in await registry.health()}
    assert health["instagram"].healthy is False
    assert health["instagram"].reason == "circuit_open"


@pytest.mark.asyncio
async def test_registry_rejects_invalid_artifact_and_falls_back():
    registry = ProviderRegistry(
        [_InvalidArtifactProvider("instagram", frozenset({"instagram"})), _Provider("fallback", frozenset({"*"}))],
        failure_threshold=1,
        cooldown=60,
    )

    artifact, provider_name = await registry.download(
        MediaRequest("https://www.instagram.com/reel/fixture", "job-artifact", "instagram")
    )

    assert provider_name == "fallback"
    assert artifact["extension"] == ".mp4"


@pytest.mark.asyncio
async def test_ytdlp_adapter_delegates_real_health_status():
    adapter = YtDlpAdapter(SimpleNamespace(health_check=lambda: _health(False)))

    health = await adapter.health_check()

    assert health.name == "yt-dlp"
    assert health.healthy is False
    assert health.reason == "fixture_down"


@pytest.mark.asyncio
async def test_registry_health_summarizes_provider_exceptions():
    health = (await ProviderRegistry([_BrokenHealthProvider("broken")]).health())[0]

    assert health.healthy is False
    assert health.reason == "media_provider_health type=RuntimeError"
    assert "https://example.test" not in str(health)
    assert "token=secret" not in str(health)


async def _health(value: bool) -> ProviderHealth:
    return ProviderHealth(name="legacy", healthy=value, reason="fixture_down" if not value else None)
