from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from config import Config
from src.media.providers.base import MediaMetadata, MediaRequest
from src.media.providers.ytdlp_provider import MediaDownloadError, YtDlpProvider
from src.media.service import MediaDownloadService
from src.media.transcoder import MediaTranscoder
from src.media.job_queue import MediaJobQueue
from src.observability import MetricsRegistry
from src.storage.media_job_store import MediaJobStore


@pytest.mark.asyncio
async def test_media_job_store_can_remove_a_job_lost_to_dedup_race():
    store = MediaJobStore(None)
    await store.create({"job_id": "orphan", "status": "queued"})
    assert await store.get("orphan") is not None

    await store.delete("orphan")

    assert await store.get("orphan") is None


class _TelegramReplayBot:
    def __init__(self, *, invalid_cache_once: bool = False) -> None:
        self.video_calls: list[dict] = []
        self.document_calls: list[dict] = []
        self.edits: list[str] = []
        self.invalid_cache_once = invalid_cache_once

    async def send_video(self, **kwargs):
        value = kwargs["video"]
        if self.invalid_cache_once and value == "cached-telegram-file":
            self.invalid_cache_once = False
            raise ValueError("wrong file identifier/HTTP URL specified")
        content = value.read() if hasattr(value, "read") else value
        self.video_calls.append({**kwargs, "content": content})
        return SimpleNamespace(video=SimpleNamespace(file_id="telegram-fixture-file"))

    async def send_document(self, **kwargs):
        value = kwargs["document"]
        content = value.read() if hasattr(value, "read") else value
        self.document_calls.append({**kwargs, "content": content})
        return SimpleNamespace(document=SimpleNamespace(file_id="telegram-fixture-document"))

    async def edit_message_text(self, **kwargs):
        self.edits.append(str(kwargs.get("text", "")))


class _ReplayProviders:
    def __init__(self, root: Path, *, oversized: bool = False) -> None:
        self.root = root
        self.oversized = oversized
        self.probe_calls = 0
        self.download_calls = 0

    async def probe(self, request: MediaRequest) -> MediaMetadata:
        self.probe_calls += 1
        return MediaMetadata(title="Replay video", platform=request.platform, duration=2, size=16)

    async def download(self, request: MediaRequest):
        self.download_calls += 1
        job_dir = self.root / request.job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        content = b"x" * (1025 if self.oversized else 16)
        path = job_dir / "media.mp4"
        path.write_bytes(content)
        return {
            "path": str(path),
            "title": "Replay video",
            "duration": 2,
            "size": len(content),
            "extension": ".mp4",
            "platform": request.platform,
        }, "fixture-provider"


def _service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, oversized: bool = False):
    monkeypatch.setattr(Config, "MEDIA_DOWNLOAD_DIR", str(tmp_path))
    service = object.__new__(MediaDownloadService)
    service.database = SimpleNamespace()
    service.job_store = MediaJobStore(None)
    service.queue = MediaJobQueue(store=service.job_store)
    service.bot = _TelegramReplayBot()
    service.providers = _ReplayProviders(tmp_path, oversized=oversized)
    service.provider = YtDlpProvider(
        str(tmp_path), max_bytes=1024, max_duration=0, timeout=3, cookie_vault=SimpleNamespace()
    )
    service.transcoder = MediaTranscoder(1024)
    service.mongo_available = False
    service.metrics_registry = MetricsRegistry()
    service._user_limits = defaultdict(lambda: asyncio.Semaphore(2))
    service._chat_limits = defaultdict(lambda: asyncio.Semaphore(2))
    return service


@pytest.mark.asyncio
async def test_media_delivery_replay_sends_fixture_and_cleans_temp_artifact(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch)
    payload = {
        "job_id": "fixture-ok",
        "url": "https://www.youtube.com/shorts/fixture",
        "url_hash": "fixture-hash",
        "chat_id": 10,
        "user_id": 20,
        "status_message_id": 30,
        "source": "youtube",
    }

    await service._process_job_inner(payload)

    assert len(service.bot.video_calls) == 1
    assert service.bot.video_calls[0]["content"] == b"x" * 16
    assert service.providers.probe_calls == 1
    assert service.providers.download_calls == 1
    assert not (tmp_path / "fixture-ok").exists()
    assert (await service.job_store.get("fixture-ok"))["telegram_file_id"] == "telegram-fixture-file"


@pytest.mark.asyncio
async def test_media_delivery_replay_cache_hit_skips_provider_and_reuploads(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch)
    service._cache_get = lambda key: _async_value("cached-telegram-file")
    payload = {
        "job_id": "fixture-cache",
        "url": "https://www.youtube.com/shorts/cached",
        "url_hash": "cached-hash",
        "chat_id": 10,
        "user_id": 20,
        "status_message_id": 30,
        "source": "youtube",
    }

    await service._process_job_inner(payload)

    assert service.bot.video_calls[0]["content"] == "cached-telegram-file"
    assert service.providers.probe_calls == 0
    assert service.providers.download_calls == 0
    assert (await service.job_store.get("fixture-cache"))["telegram_file_id"] == "cached-telegram-file"


@pytest.mark.asyncio
async def test_media_delivery_replay_evicts_stale_cache_and_redownloads(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch)
    service.bot = _TelegramReplayBot(invalid_cache_once=True)
    service._cache_get = lambda key: _async_value("cached-telegram-file")
    evicted: list[str] = []
    service._cache_delete = lambda key: _record_cache_delete(evicted, key)
    payload = {
        "job_id": "fixture-stale-cache",
        "url": "https://www.youtube.com/shorts/stale",
        "url_hash": "stale-hash",
        "chat_id": 10,
        "user_id": 20,
        "status_message_id": 30,
        "source": "youtube",
    }

    await service._process_job_inner(payload)

    assert evicted == ["arel:media:cache:stale-hash"]
    assert service.providers.probe_calls == 1
    assert service.providers.download_calls == 1
    assert service.metrics_registry.snapshot()["counters"]["media_cache_invalidations"] == 1


@pytest.mark.asyncio
async def test_media_delivery_replay_never_sends_artifact_above_limit(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch, oversized=True)
    payload = {
        "job_id": "fixture-too-large",
        "url": "https://www.youtube.com/shorts/large",
        "url_hash": "large-hash",
        "chat_id": 10,
        "user_id": 20,
        "status_message_id": 30,
        "source": "youtube",
    }

    with pytest.raises(MediaDownloadError) as error:
        await service._process_job_inner(payload)

    assert error.value.code == "file_too_large"
    assert not service.bot.video_calls
    assert not service.bot.document_calls
    assert not (tmp_path / "fixture-too-large").exists()


@pytest.mark.asyncio
async def test_media_delivery_replay_handles_ten_concurrent_jobs_and_cleans_each(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch)
    payloads = [
        {
            "job_id": f"fixture-load-{index}",
            "url": f"https://www.youtube.com/shorts/load-{index}",
            "url_hash": f"load-hash-{index}",
            "chat_id": 100 + index,
            "user_id": 200 + index,
            "status_message_id": 300 + index,
            "source": "youtube",
        }
        for index in range(10)
    ]

    started = time.perf_counter()
    await asyncio.gather(*(service._process_job(payload) for payload in payloads))
    elapsed = time.perf_counter() - started

    assert len(service.bot.video_calls) == 10
    assert service.providers.probe_calls == 10
    assert service.providers.download_calls == 10
    assert elapsed < 10
    assert all(not (tmp_path / payload["job_id"]).exists() for payload in payloads)


@pytest.mark.asyncio
async def test_media_delivery_coalesces_same_url_across_chats_via_file_id_cache(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch)
    cache: dict[str, str] = {}

    async def cache_get(key):
        return cache.get(key)

    async def cache_set(key, value, platform="unknown"):
        cache[key] = value

    service._cache_get = cache_get
    service._cache_set = cache_set
    original_download = service.providers.download

    async def delayed_download(request):
        await asyncio.sleep(0.02)
        return await original_download(request)

    service.providers.download = delayed_download
    payloads = [
        {
            "job_id": f"fixture-shared-{index}",
            "url": "https://www.youtube.com/shorts/shared",
            "url_hash": "shared-hash",
            "chat_id": 100 + index,
            "user_id": 200 + index,
            "status_message_id": 300 + index,
            "source": "youtube",
        }
        for index in range(2)
    ]

    await asyncio.gather(*(service._process_job(payload) for payload in payloads))

    assert service.providers.download_calls == 1
    assert service.providers.probe_calls == 1
    assert len(service.bot.video_calls) == 2
    assert service.bot.video_calls[1]["content"] == "telegram-fixture-file"
    assert service.metrics_registry.snapshot()["counters"]["media_inflight_waiters"] == 1


@pytest.mark.asyncio
async def test_media_delivery_records_completion_budget_without_cancelling_job(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch)
    original = service.providers.download

    async def delayed_download(request):
        # Keep a wide margin over the 10 ms budget so Windows scheduling
        # jitter cannot make the breach assertion nondeterministic.
        await asyncio.sleep(0.05)
        return await original(request)

    service.providers.download = delayed_download
    monkeypatch.setattr(Config, "MEDIA_COMPLETION_SLO_SECONDS", 0.01)
    payload = {
        "job_id": "fixture-slo-breach",
        "url": "https://www.youtube.com/shorts/slo",
        "url_hash": "slo-hash",
        "chat_id": 10,
        "user_id": 20,
        "status_message_id": 30,
        "source": "youtube",
    }

    await service._process_job_inner(payload)

    snapshot = service.metrics_registry.snapshot()
    assert snapshot["counters"]["media_completion_slo_breaches"] == 1
    assert snapshot["latency"]["media_completion_seconds"]["count"] == 1
    assert len(service.bot.video_calls) == 1


@pytest.mark.asyncio
async def test_media_queue_replay_keeps_ten_workers_and_hundred_active_chats_isolated():
    """Exercise the pilot boundary without contacting external media sites."""
    queue = MediaJobQueue(store=MediaJobStore(None))
    completed: list[tuple[str, int]] = []

    async def processor(payload):
        await asyncio.sleep(0.005)
        completed.append((payload["job_id"], int(payload["chat_id"])))

    queue.processor = processor
    await queue.start("redis://unused", workers=10, force_memory=True)
    try:
        jobs = []
        for index in range(100):
            job_id, duplicate = await queue.enqueue(
                {
                    "chat_id": -1000 - index,
                    "user_id": 2000 + index,
                    "url_hash": f"queue-load-{index}",
                    "url": f"https://example.test/media/{index}",
                }
            )
            assert duplicate is False
            jobs.append(job_id)

        deadline = time.monotonic() + 5
        while len(completed) < 100 and time.monotonic() < deadline:
            await asyncio.sleep(0.01)

        assert len(completed) == 100
        assert len({chat_id for _, chat_id in completed}) == 100
        assert {job_id for job_id, _ in completed} == set(jobs)
        assert (await queue.metrics())["queue_depth"] == 0
    finally:
        await queue.close()


async def _async_value(value):
    return value


async def _record_cache_delete(target: list[str], key: str) -> None:
    target.append(key)
