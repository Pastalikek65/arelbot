from __future__ import annotations

import pytest

from src.media.url_normalizer import (
    InvalidMediaURL,
    extract_media_url,
    normalize_media_url,
    platform_for_url,
)


@pytest.mark.parametrize(
    ("url", "platform"),
    [
        ("https://www.instagram.com/reel/ABC123/", "instagram"),
        ("https://instagr.am/p/ABC123", "instagram"),
        ("https://www.tiktok.com/@arel/video/123", "tiktok"),
        ("https://m.youtube.com/shorts/ABC123", "youtube"),
        ("https://youtu.be/ABC123", "youtube"),
        ("https://twitter.com/arel/status/123", "x"),
        ("https://www.x.com/arel/status/123", "x"),
        ("https://www.reddit.com/r/test/comments/abc/title", "reddit"),
    ],
)
def test_public_platform_matrix_normalizes_and_routes(url: str, platform: str):
    normalized = normalize_media_url(url)

    assert normalized.startswith("https://")
    assert platform_for_url(normalized) == platform


def test_extract_media_url_ignores_unsupported_and_keeps_instagram_url():
    text = "ÅŸuna bak http://localhost/private sonra https://www.instagram.com/reel/ABC123/?utm_source=x."

    assert extract_media_url(text) == "https://www.instagram.com/reel/ABC123/?utm_source=x"


@pytest.mark.parametrize(
    "url",
    [
        "ftp://www.instagram.com/reel/abc",
        "https://user:password@www.instagram.com/reel/abc",
        "https://www.instagram.com:8443/reel/abc",
        "https://example.com/video.mp4",
        "https://127.0.0.1/video.mp4",
    ],
)
def test_media_url_policy_rejects_unsafe_or_unsupported_sources(url: str):
    with pytest.raises(InvalidMediaURL):
        normalize_media_url(url)
