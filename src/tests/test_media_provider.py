from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.media.providers.ytdlp_provider import MediaDownloadError, YtDlpProvider
from src.media.url_normalizer import InvalidMediaURL


class _FixtureYoutubeDL:
    last_options = None

    def __init__(self, options):
        self.options = options
        type(self).last_options = options

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def extract_info(self, url, download=False):
        if not download:
            return {"title": "Fixture video", "duration": 12}
        for hook in self.options["progress_hooks"]:
            hook({"downloaded_bytes": 32, "total_bytes": 32})
        output = Path(self.options["outtmpl"].replace("%(ext)s", "mp4"))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"fixture-media")
        return {"title": "Fixture video", "duration": 12}


class _TooLargeYoutubeDL(_FixtureYoutubeDL):
    def extract_info(self, url, download=False):
        if not download:
            return super().extract_info(url, download=False)
        for hook in self.options["progress_hooks"]:
            hook({"downloaded_bytes": 101, "total_bytes": 101})
        return {"title": "Too large", "duration": 12}


async def _allow_fixture_public_host(_url: str) -> None:
    """Keep extractor unit fixtures offline; URL policy has separate tests."""
    return None


@pytest.mark.asyncio
async def test_ytdlp_fixture_download_returns_bounded_artifact(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=_FixtureYoutubeDL))
    monkeypatch.setattr("src.media.providers.ytdlp_provider.ensure_public_host", _allow_fixture_public_host)
    provider = YtDlpProvider(str(tmp_path), max_bytes=100, max_duration=0, timeout=3, cookie_vault=object())

    result = await provider.download("https://www.youtube.com/watch?v=fixture", "job-ok")

    assert result["extension"] == ".mp4"
    assert "no-certifi" in _FixtureYoutubeDL.last_options["compat_opts"]
    assert _FixtureYoutubeDL.last_options["logger"].__class__.__name__ == "_SilentYtDlpLogger"
    assert _FixtureYoutubeDL.last_options["extractor_args"] == {
        "youtube": {"player_client": ["android"]}
    }
    assert result["size"] == len(b"fixture-media")
    assert Path(result["path"]).is_file()
    provider.cleanup(result, "job-ok", str(tmp_path))
    assert not (tmp_path / "job-ok").exists()


@pytest.mark.asyncio
async def test_ytdlp_uses_aria2_only_when_the_optional_binary_exists(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=_FixtureYoutubeDL))
    monkeypatch.setattr("src.media.providers.ytdlp_provider.ensure_public_host", _allow_fixture_public_host)
    monkeypatch.setattr(
        "src.media.providers.ytdlp_provider.shutil.which",
        lambda name: "aria2c.exe" if name == "aria2c" else None,
    )
    provider = YtDlpProvider(
        str(tmp_path),
        max_bytes=100,
        max_duration=0,
        timeout=3,
        cookie_vault=object(),
        aria2_enabled=True,
    )

    result = await provider.download("https://www.youtube.com/watch?v=fixture", "job-aria2")

    assert _FixtureYoutubeDL.last_options["external_downloader"] == "aria2c.exe"
    assert _FixtureYoutubeDL.last_options["external_downloader_args"]["default"][:4] == ["-x", "8", "-s", "8"]
    provider.cleanup(result, "job-aria2", str(tmp_path))


@pytest.mark.asyncio
async def test_ytdlp_fixture_rejects_over_limit_before_telegram_send(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=_TooLargeYoutubeDL))
    monkeypatch.setattr("src.media.providers.ytdlp_provider.ensure_public_host", _allow_fixture_public_host)
    provider = YtDlpProvider(str(tmp_path), max_bytes=100, max_duration=0, timeout=3, cookie_vault=object())

    with pytest.raises(MediaDownloadError) as error:
        await provider.download("https://www.youtube.com/watch?v=fixture", "job-too-large")

    assert error.value.code == "file_too_large"
    assert not (tmp_path / "job-too-large" / "media.mp4").exists()
    assert not (tmp_path / "job-too-large").exists()


@pytest.mark.asyncio
async def test_ytdlp_probe_preserves_public_host_policy_error(tmp_path, monkeypatch):
    provider = YtDlpProvider(
        str(tmp_path),
        max_bytes=45 * 1024 * 1024,
        max_duration=0,
        timeout=5,
        cookie_vault=SimpleNamespace(),
        cookie_enabled=False,
    )

    async def reject(_url):
        raise InvalidMediaURL("non_public_host")

    monkeypatch.setattr("src.media.providers.ytdlp_provider.ensure_public_host", reject)

    with pytest.raises(MediaDownloadError, match="gÃ¼venli") as raised:
        await provider.probe("https://www.youtube.com/watch?v=fixture")

    assert raised.value.code == "non_public_host"


@pytest.mark.asyncio
async def test_ytdlp_probe_uses_platform_http_policy_for_public_instagram(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=_FixtureYoutubeDL))
    monkeypatch.setattr("src.media.providers.ytdlp_provider.ensure_public_host", _allow_fixture_public_host)
    provider = YtDlpProvider(str(tmp_path), max_bytes=100, max_duration=0, timeout=3, cookie_vault=object())

    await provider.probe("https://www.instagram.com/reel/fixture/")

    options = _FixtureYoutubeDL.last_options
    assert options["http_headers"]["User-Agent"].startswith("Mozilla/5.0")
    assert options["http_headers"]["Accept-Language"].startswith("tr-TR")
    assert options["http_headers"]["Referer"] == "https://www.instagram.com/"


@pytest.mark.asyncio
async def test_ytdlp_maps_public_source_login_failures_to_safe_error_code(tmp_path, monkeypatch):
    class _LoginRequiredYoutubeDL(_FixtureYoutubeDL):
        def extract_info(self, url, download=False):
            raise RuntimeError("Sign in to confirm you are not a bot")

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=_LoginRequiredYoutubeDL))
    monkeypatch.setattr("src.media.providers.ytdlp_provider.ensure_public_host", _allow_fixture_public_host)
    provider = YtDlpProvider(str(tmp_path), max_bytes=100, max_duration=0, timeout=3, cookie_vault=object())

    with pytest.raises(MediaDownloadError) as raised:
        await provider.probe("https://www.instagram.com/reel/fixture/")

    assert raised.value.code == "source_access_restricted"
    assert "Sign in" not in str(raised.value)


@pytest.mark.asyncio
async def test_ytdlp_probe_retries_instagram_with_enabled_admin_cookie(tmp_path, monkeypatch):
    class _CookieFallbackYoutubeDL(_FixtureYoutubeDL):
        def extract_info(self, url, download=False):
            if "cookiefile" not in self.options:
                raise RuntimeError("Requested content is not available, rate-limit reached or login required")
            assert Path(self.options["cookiefile"]).read_text(encoding="utf-8").startswith("# Netscape")
            return {"title": "Authenticated reel", "duration": 8}

    class _FixtureVault:
        def has(self, platform):
            return platform == "instagram"

        def load_to_file(self, platform, target_path):
            Path(target_path).write_text(
                "# Netscape HTTP Cookie File\n.instagram.com\tTRUE\t/\tTRUE\t0\tsession\tfixture\n",
                encoding="utf-8",
            )

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=_CookieFallbackYoutubeDL))
    monkeypatch.setattr("src.media.providers.ytdlp_provider.ensure_public_host", _allow_fixture_public_host)
    provider = YtDlpProvider(
        str(tmp_path), max_bytes=100, max_duration=0, timeout=3,
        cookie_vault=_FixtureVault(), cookie_enabled=True,
    )

    result = await provider.probe("https://www.instagram.com/reel/fixture/")

    assert result["title"] == "Authenticated reel"
    assert _CookieFallbackYoutubeDL.last_options["cookiefile"]
    assert not list(tmp_path.glob("probe-*"))


@pytest.mark.asyncio
async def test_ytdlp_probe_does_not_use_cookie_when_vault_is_disabled(tmp_path, monkeypatch):
    class _AccessDeniedYoutubeDL(_FixtureYoutubeDL):
        def extract_info(self, url, download=False):
            if "cookiefile" in self.options:
                raise AssertionError("disabled vault must not be used")
            raise RuntimeError("Sign in to confirm you are not a bot")

    class _FixtureVault:
        def has(self, platform):
            return True

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=_AccessDeniedYoutubeDL))
    monkeypatch.setattr("src.media.providers.ytdlp_provider.ensure_public_host", _allow_fixture_public_host)
    provider = YtDlpProvider(
        str(tmp_path), max_bytes=100, max_duration=0, timeout=3,
        cookie_vault=_FixtureVault(), cookie_enabled=False,
    )

    with pytest.raises(MediaDownloadError) as raised:
        await provider.probe("https://www.instagram.com/reel/fixture/")

    assert raised.value.code == "source_access_restricted"


@pytest.mark.asyncio
async def test_ytdlp_download_maps_public_source_rate_limit_to_safe_error_code(tmp_path, monkeypatch):
    class _RateLimitedYoutubeDL(_FixtureYoutubeDL):
        def extract_info(self, url, download=False):
            raise RuntimeError("HTTP Error 429: Too Many Requests")

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=_RateLimitedYoutubeDL))
    monkeypatch.setattr("src.media.providers.ytdlp_provider.ensure_public_host", _allow_fixture_public_host)
    provider = YtDlpProvider(str(tmp_path), max_bytes=100, max_duration=0, timeout=3, cookie_vault=object())

    with pytest.raises(MediaDownloadError) as raised:
        await provider.download("https://www.instagram.com/reel/fixture/", "job-rate-limit")

    assert raised.value.code == "source_rate_limited"
    assert "429" not in str(raised.value)
