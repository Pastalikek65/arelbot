from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.tiktok_downloader import TikTokDownloader
from src.video_downloader import VideoDownloader


@pytest.mark.asyncio
async def test_video_downloader_compatibility_facade_delegates_typed_provider(tmp_path: Path):
    downloader = VideoDownloader(str(tmp_path))
    job_ids: list[str] = []

    async def download(_url: str, job_id: str):
        job_ids.append(job_id)
        artifact = tmp_path / job_id / "media.mp4"
        artifact.parent.mkdir()
        artifact.write_bytes(b"fixture")
        return {
            "path": str(artifact),
            "title": "Fixture video",
            "duration": 3,
            "size": 7,
            "extension": ".mp4",
            "platform": "youtube",
        }

    downloader.provider = SimpleNamespace(download=download)

    result = await downloader.download("https://www.youtube.com/shorts/fixture")

    assert result is not None
    assert result["type"] == "video"
    assert result["caption"] == "Fixture video"
    assert result["duration"] == 3
    assert result["temp_id"] == job_ids[0]


@pytest.mark.asyncio
async def test_tiktok_facade_rejects_other_platform_before_provider_call(tmp_path: Path):
    downloader = TikTokDownloader(str(tmp_path))

    async def unexpected_download(*_args):  # pragma: no cover - failure guard
        raise AssertionError("provider must not run for another platform")

    downloader.provider = SimpleNamespace(download=unexpected_download)

    assert await downloader.download("https://www.youtube.com/shorts/fixture") is None


def test_legacy_facades_have_no_hardcoded_bypass_endpoints():
    for name in ("video_downloader.py", "tiktok_downloader.py"):
        content = (Path(__file__).parents[1] / name).read_text(encoding="utf-8").casefold()
        assert "api.cobalt" not in content
        assert "www.tikwm.com" not in content
