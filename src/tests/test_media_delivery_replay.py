from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from config import Config
from src.jobs.delivery_ledger import JobDeliveryLedger
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


class _SimulatedWorkerCrash(BaseException):
    """A worker process that dies mid-send; ``except Exception`` cannot stop it."""


class _CrashingTelegramBot(_TelegramReplayBot):
    """Kills the worker while the media upload is still in flight."""

    def __init__(self) -> None:
        super().__init__()
        self.crashed = False

    async def send_video(self, **kwargs):
        if not self.crashed:
            self.crashed = True
            self.video_calls.append({**kwargs, "content": None})
            raise _SimulatedWorkerCrash("worker died mid-send")
        return await super().send_video(**kwargs)


class _FailingTelegramBot(_TelegramReplayBot):
    """Rejects the media upload the way a transient Telegram failure does."""

    def __init__(self) -> None:
        super().__init__()
        self.failed = False

    async def send_video(self, **kwargs):
        if not self.failed:
            self.failed = True
            self.video_calls.append({**kwargs, "content": None})
            raise RuntimeError("telegram upload failed")
        return await super().send_video(**kwargs)


class _CancellingTelegramBot(_TelegramReplayBot):
    """Cancels the worker while the media upload is in flight."""

    async def send_video(self, **kwargs):
        raise asyncio.CancelledError()


class _FailingBeginSendLedger(JobDeliveryLedger):
    """Fails the send-boundary transition once, like a transient ledger error."""

    def __init__(self) -> None:
        super().__init__(None)
        self.failures = 1

    async def begin_send(self, delivery_key: str) -> bool:
        if self.failures:
            self.failures -= 1
            raise RuntimeError("transient ledger failure")
        return await super().begin_send(delivery_key)


class _LostSentMarkLedger(JobDeliveryLedger):
    """Reports a delivery boundary that could not be recorded."""

    async def mark_sent(self, delivery_key: str, telegram_message_id: int | None = None) -> bool:
        await super().mark_sent(delivery_key, telegram_message_id)
        return False


class _BadRequestTelegramBot(_TelegramReplayBot):
    """Rejects the upload with a Telegram error."""

    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error

    async def send_video(self, **kwargs):
        raise self.error


class _SlowUncertainLedger(JobDeliveryLedger):
    """Blocks the uncertain write until the test lets it finish."""

    def __init__(self) -> None:
        super().__init__(None)
        self.started = asyncio.Event()
        self.resume = asyncio.Event()
        self.completed = asyncio.Event()

    async def mark_uncertain(self, delivery_key: str, error: str | None = None) -> None:
        self.started.set()
        await self.resume.wait()
        await super().mark_uncertain(delivery_key, error)
        self.completed.set()


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
    service.delivery_ledger = JobDeliveryLedger(None)
    service._injected_delivery_ledger = None
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


@pytest.mark.asyncio
async def test_worker_crash_mid_send_does_not_redeliver(tmp_path, monkeypatch):
    """A worker that dies during the upload must not deliver the video twice."""
    service = _service(tmp_path, monkeypatch)
    crashed_bot = _CrashingTelegramBot()
    service.bot = crashed_bot
    payload = {
        "job_id": "fixture-crash",
        "url": "https://www.youtube.com/shorts/crash",
        "url_hash": "crash-hash",
        "chat_id": 10,
        "user_id": 20,
        "status_message_id": 30,
        "source": "youtube",
    }

    with pytest.raises(_SimulatedWorkerCrash):
        await service._process_job_inner(payload)

    # The upload was in flight exactly once when the worker died.
    assert len(crashed_bot.video_calls) == 1

    # The crashed row reached the send boundary, so the store no longer
    # offers it for recovery; the delivery ledger owns that state now.
    crashed_job = await service.job_store.get("fixture-crash")
    assert crashed_job["status"] == "sending"
    assert not await service.job_store.recoverable()

    # A replacement worker takes the same job over and must not send again.
    service.bot = _TelegramReplayBot()
    await service._process_job_inner(payload)

    assert not service.bot.video_calls
    assert not service.bot.document_calls
    assert len(crashed_bot.video_calls) == 1
    job = await service.job_store.get("fixture-crash")
    assert job["finished_at"]


@pytest.mark.asyncio
async def test_media_uses_delivery_ledger_before_sending(tmp_path, monkeypatch):
    """The service must claim the durable ledger before it touches Telegram."""
    service = _service(tmp_path, monkeypatch)
    payload = {
        "job_id": "fixture-ledger",
        "url": "https://www.youtube.com/shorts/ledger",
        "url_hash": "ledger-hash",
        "chat_id": 10,
        "user_id": 20,
        "status_message_id": 30,
        "source": "youtube",
    }
    delivery_key = f"media:{payload['job_id']}"

    # Another worker already crossed the send boundary for this job.
    assert await service.delivery_ledger.claim(
        delivery_key, job_id=payload["job_id"], chat_id=payload["chat_id"]
    )
    assert await service.delivery_ledger.begin_send(delivery_key)

    await service._process_job_inner(payload)

    assert not service.bot.video_calls
    assert not service.bot.document_calls
    job = await service.job_store.get("fixture-ledger")
    assert job["finished_at"]
    # A suppressed delivery must not stay in flight for the operator or the
    # user; the job is closed and the status message leaves the progress state.
    assert job["status"] == "completed"
    # The other worker still holds the send boundary, so the message must
    # not claim that the media was delivered.
    assert service.bot.edits[-1] == "⏳ Medya gönderimi sürüyor; tekrar gönderim yapılmadı."
    assert not any("Medya gönderildi" in text for text in service.bot.edits)


@pytest.mark.asyncio
async def test_media_cached_delivery_uses_the_same_delivery_ledger_key(tmp_path, monkeypatch):
    """A cached file id is still a Telegram delivery and must be claimed once."""
    service = _service(tmp_path, monkeypatch)
    service._cache_get = lambda key: _async_value("cached-telegram-file")
    payload = {
        "job_id": "fixture-cached-ledger",
        "url": "https://www.youtube.com/shorts/cached-ledger",
        "url_hash": "cached-ledger-hash",
        "chat_id": 10,
        "user_id": 20,
        "status_message_id": 30,
        "source": "youtube",
    }
    delivery_key = f"media:{payload['job_id']}"
    assert await service.delivery_ledger.claim(
        delivery_key, job_id=payload["job_id"], chat_id=payload["chat_id"]
    )
    assert await service.delivery_ledger.begin_send(delivery_key)

    await service._process_job_inner(payload)

    assert not service.bot.video_calls
    assert not service.bot.document_calls
    assert service.providers.download_calls == 0


@pytest.mark.asyncio
async def test_media_send_failure_marks_delivery_uncertain_and_blocks_the_retry(tmp_path, monkeypatch):
    """A failed upload is uncertain, so the queue retry must not send again."""
    service = _service(tmp_path, monkeypatch)
    failing_bot = _FailingTelegramBot()
    service.bot = failing_bot
    payload = {
        "job_id": "fixture-uncertain",
        "url": "https://www.youtube.com/shorts/uncertain",
        "url_hash": "uncertain-hash",
        "chat_id": 10,
        "user_id": 20,
        "status_message_id": 30,
        "source": "youtube",
    }

    with pytest.raises(MediaDownloadError):
        await service._process_job_inner(payload)

    assert len(failing_bot.video_calls) == 1

    # The queue retries the job; an uncertain send is never replayed.
    service.bot = _TelegramReplayBot()
    await service._process_job_inner(payload)

    assert not service.bot.video_calls
    assert not service.bot.document_calls
    assert len(failing_bot.video_calls) == 1


@pytest.mark.asyncio
async def test_media_job_store_never_requeues_a_job_whose_send_already_started():
    """The store filter and the delivery ledger have to move together."""
    store = MediaJobStore(None)
    await store.create({"job_id": "send-in-flight", "status": "sending"})
    await store.create({"job_id": "transcode-in-flight", "status": "transcoding"})
    await store.create({"job_id": "queued-job", "status": "queued"})

    recoverable = {row["job_id"] for row in await store.recoverable()}

    assert "send-in-flight" not in recoverable
    assert "transcode-in-flight" in recoverable
    assert "queued-job" in recoverable


@pytest.mark.asyncio
async def test_stale_cache_provider_failure_does_not_lose_the_delivery(tmp_path, monkeypatch):
    """A refused stale file id plus a provider failure must stay deliverable."""
    service = _service(tmp_path, monkeypatch)
    service.bot = _TelegramReplayBot(invalid_cache_once=True)
    cache: dict[str, str] = {"arel:media:cache:stale-provider-hash": "cached-telegram-file"}

    async def cache_get(key):
        return cache.get(key)

    async def cache_set(key, value, platform="unknown"):
        cache[key] = value

    async def cache_delete(key):
        cache.pop(key, None)

    service._cache_get = cache_get
    service._cache_set = cache_set
    service._cache_delete = cache_delete
    original_download = service.providers.download
    failures = {"remaining": 1}

    async def flaky_download(request):
        if failures["remaining"]:
            failures["remaining"] -= 1
            raise MediaDownloadError("source_rate_limited", "Kaynak yanıt vermedi.")
        return await original_download(request)

    service.providers.download = flaky_download
    payload = {
        "job_id": "fixture-stale-provider",
        "url": "https://www.youtube.com/shorts/stale-provider",
        "url_hash": "stale-provider-hash",
        "chat_id": 10,
        "user_id": 20,
        "status_message_id": 30,
        "source": "youtube",
    }
    delivery_key = f"media:{payload['job_id']}"

    with pytest.raises(MediaDownloadError):
        await service._process_job_inner(payload)

    # Telegram refused the stale reference, so nothing was delivered and the
    # send boundary went back to the claimable state.
    assert not service.bot.video_calls
    assert service.delivery_ledger._items[delivery_key]["status"] == "failed"

    # The provider failure must not consume the delivery; the retry sends it.
    await service._process_job_inner(payload)

    assert len(service.bot.video_calls) == 1
    assert service.bot.video_calls[0]["content"] == b"x" * 16


@pytest.mark.asyncio
async def test_provider_failure_leaves_the_delivery_unclaimed_and_the_retry_delivers(tmp_path, monkeypatch):
    """A provider failure must not consume the delivery boundary."""
    service = _service(tmp_path, monkeypatch)
    original_download = service.providers.download
    failures = {"remaining": 1}

    async def flaky_download(request):
        if failures["remaining"]:
            failures["remaining"] -= 1
            raise MediaDownloadError("source_rate_limited", "Kaynak yanıt vermedi.")
        return await original_download(request)

    service.providers.download = flaky_download
    payload = {
        "job_id": "fixture-provider-failure",
        "url": "https://www.youtube.com/shorts/provider-failure",
        "url_hash": "provider-failure-hash",
        "chat_id": 10,
        "user_id": 20,
        "status_message_id": 30,
        "source": "youtube",
    }

    with pytest.raises(MediaDownloadError):
        await service._process_job_inner(payload)

    # The provider failed before the upload, so the delivery key was never
    # claimed: the send boundary belongs to the upload, not to the download.
    assert not service.bot.video_calls
    assert not service.delivery_ledger._items

    # The queue retries the job and the media is delivered exactly once.
    await service._process_job_inner(payload)

    assert len(service.bot.video_calls) == 1


@pytest.mark.asyncio
async def test_ledger_failure_between_claim_and_send_releases_the_claim(tmp_path, monkeypatch):
    """A ledger error must not leave a claim that refuses the retry."""
    service = _service(tmp_path, monkeypatch)
    service.delivery_ledger = _FailingBeginSendLedger()
    payload = {
        "job_id": "fixture-begin-send",
        "url": "https://www.youtube.com/shorts/begin-send",
        "url_hash": "begin-send-hash",
        "chat_id": 10,
        "user_id": 20,
        "status_message_id": 30,
        "source": "youtube",
    }
    delivery_key = f"media:{payload['job_id']}"

    with pytest.raises(MediaDownloadError):
        await service._process_job_inner(payload)

    # The upload never started, so the claim was given back instead of
    # refusing every retry for the next five minutes.
    assert not service.bot.video_calls
    assert service.delivery_ledger._items[delivery_key]["status"] == "failed"

    await service._process_job_inner(payload)

    assert len(service.bot.video_calls) == 1


@pytest.mark.asyncio
async def test_cancelled_media_delivery_is_recorded_as_uncertain(tmp_path, monkeypatch):
    """A cancelled upload must not leave the send boundary in flight."""
    service = _service(tmp_path, monkeypatch)
    service.bot = _CancellingTelegramBot()
    payload = {
        "job_id": "fixture-cancelled",
        "url": "https://www.youtube.com/shorts/cancelled",
        "url_hash": "cancelled-hash",
        "chat_id": 10,
        "user_id": 20,
        "status_message_id": 30,
        "source": "youtube",
    }
    delivery_key = f"media:{payload['job_id']}"

    with pytest.raises(asyncio.CancelledError):
        await service._process_job_inner(payload)

    assert service.delivery_ledger._items[delivery_key]["status"] == "uncertain"


@pytest.mark.asyncio
async def test_media_reports_a_lost_sent_mark(tmp_path, monkeypatch, caplog):
    """A delivery the ledger cannot record is reported to the operator."""
    service = _service(tmp_path, monkeypatch)
    service.delivery_ledger = _LostSentMarkLedger()
    payload = {
        "job_id": "fixture-lost-mark",
        "url": "https://www.youtube.com/shorts/lost-mark",
        "url_hash": "lost-mark-hash",
        "chat_id": 10,
        "user_id": 20,
        "status_message_id": 30,
        "source": "youtube",
    }

    with caplog.at_level(logging.WARNING, logger="src.media.service"):
        await service._process_job_inner(payload)

    assert any("delivery ledger" in record.getMessage().lower() for record in caplog.records)


@pytest.mark.asyncio
async def test_suppressed_delivery_keeps_the_honest_error_message(tmp_path, monkeypatch):
    """A retry must not rewrite an honest error as a delivered media."""
    service = _service(tmp_path, monkeypatch)
    failed_bot = _FailingTelegramBot()
    service.bot = failed_bot
    payload = {
        "job_id": "fixture-honest-error",
        "url": "https://www.youtube.com/shorts/honest-error",
        "url_hash": "honest-error-hash",
        "chat_id": 10,
        "user_id": 20,
        "status_message_id": 30,
        "source": "youtube",
    }
    delivery_key = f"media:{payload['job_id']}"

    with pytest.raises(MediaDownloadError):
        await service._process_job_inner(payload)

    # The upload failed, so nothing was delivered and the boundary is uncertain.
    assert len(failed_bot.video_calls) == 1
    assert service.delivery_ledger._items[delivery_key]["status"] == "uncertain"
    errors = [text for text in failed_bot.edits if text.startswith("❌")]
    assert errors

    # The queue retries the job; the boundary refuses the second delivery.
    retry_bot = _TelegramReplayBot()
    service.bot = retry_bot
    await service._process_job_inner(payload)

    assert not retry_bot.video_calls
    assert not retry_bot.document_calls
    # The retry does not touch the user message, so the honest error text
    # survives it and no delivery is claimed.
    assert retry_bot.edits == []
    assert failed_bot.edits[-1] == errors[-1]
    assert not any("Medya gönderildi" in text for text in failed_bot.edits)
    # The refused retry does not download the media again either.
    assert service.providers.download_calls == 1


@pytest.mark.asyncio
async def test_suppressed_delivery_reports_a_finished_delivery(tmp_path, monkeypatch):
    """A job whose delivery is already finished says so honestly."""
    service = _service(tmp_path, monkeypatch)
    payload = {
        "job_id": "fixture-sent-boundary",
        "url": "https://www.youtube.com/shorts/sent-boundary",
        "url_hash": "sent-boundary-hash",
        "chat_id": 10,
        "user_id": 20,
        "status_message_id": 30,
        "source": "youtube",
    }
    delivery_key = f"media:{payload['job_id']}"
    assert await service.delivery_ledger.claim(
        delivery_key, job_id=payload["job_id"], chat_id=payload["chat_id"]
    )
    assert await service.delivery_ledger.begin_send(delivery_key)
    assert await service.delivery_ledger.mark_sent(delivery_key, 77)

    await service._process_job_inner(payload)

    assert not service.bot.video_calls
    assert not service.bot.document_calls
    assert service.bot.edits[-1] == "✅ Medya gönderildi."


@pytest.mark.asyncio
async def test_unrelated_bad_request_does_not_release_the_send_boundary(tmp_path, monkeypatch):
    """Only a genuinely invalid cached reference may release the boundary."""
    from telegram.error import BadRequest

    service = _service(tmp_path, monkeypatch)
    service.bot = _BadRequestTelegramBot(BadRequest("VIDEO_FILE_INVALID_DIMENSIONS"))
    service._cache_get = lambda key: _async_value("cached-telegram-file")
    payload = {
        "job_id": "fixture-unrelated-bad-request",
        "url": "https://www.youtube.com/shorts/unrelated-bad-request",
        "url_hash": "unrelated-bad-request-hash",
        "chat_id": 10,
        "user_id": 20,
        "status_message_id": 30,
        "source": "youtube",
    }
    delivery_key = f"media:{payload['job_id']}"

    with pytest.raises(MediaDownloadError):
        await service._process_job_inner(payload)

    # The error is not a stale reference, so the job fails instead of
    # releasing the boundary and downloading the media again.
    assert not service.bot.video_calls
    assert service.providers.download_calls == 0
    assert service.delivery_ledger._items[delivery_key]["status"] == "uncertain"


@pytest.mark.asyncio
async def test_cancelled_delivery_records_uncertain_even_when_cancelled_again(tmp_path, monkeypatch):
    """A second cancellation must not strand the send boundary."""
    service = _service(tmp_path, monkeypatch)
    service.bot = _CancellingTelegramBot()
    ledger = _SlowUncertainLedger()
    service.delivery_ledger = ledger
    payload = {
        "job_id": "fixture-cancelled-twice",
        "url": "https://www.youtube.com/shorts/cancelled-twice",
        "url_hash": "cancelled-twice-hash",
        "chat_id": 10,
        "user_id": 20,
        "status_message_id": 30,
        "source": "youtube",
    }
    delivery_key = f"media:{payload['job_id']}"

    task = asyncio.create_task(service._process_job_inner(payload))
    await asyncio.wait_for(ledger.started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # The shielded write finishes even though the task was cancelled again.
    ledger.resume.set()
    await asyncio.wait_for(ledger.completed.wait(), timeout=5)
    assert service.delivery_ledger._items[delivery_key]["status"] == "uncertain"


@pytest.mark.asyncio
async def test_media_degraded_mode_keeps_the_delivery_ledger_process_local(tmp_path, monkeypatch):
    """Without Mongo the ledger must not fail every media delivery."""
    monkeypatch.setattr(Config, "APP_ROLE", "combined")
    monkeypatch.setattr(Config, "MONGODB_REQUIRED", False)
    service = _service(tmp_path, monkeypatch)
    service.database = SimpleNamespace(ping=_async_ping(False), job_deliveries=object())
    service.redis_required = False
    started = asyncio.Event()

    async def fake_queue_start(*args, **kwargs):
        started.set()

    service.queue.start = fake_queue_start
    await service.start(_TelegramReplayBot())

    assert started.is_set()
    assert service.delivery_ledger.collection is None
    assert service.job_store._disabled is True


@pytest.mark.asyncio
async def test_media_degraded_mode_keeps_an_injected_delivery_ledger(tmp_path, monkeypatch):
    """A ledger supplied by the caller is used as it is in degraded mode."""
    monkeypatch.setattr(Config, "APP_ROLE", "combined")
    monkeypatch.setattr(Config, "MONGODB_REQUIRED", False)
    service = _service(tmp_path, monkeypatch)
    service.database = SimpleNamespace(ping=_async_ping(False), job_deliveries=object())
    injected = JobDeliveryLedger(service.database)
    service.delivery_ledger = injected
    service._injected_delivery_ledger = injected
    service.redis_required = False

    async def fake_queue_start(*args, **kwargs):
        return None

    service.queue.start = fake_queue_start
    await service.start(_TelegramReplayBot())

    # The caller's ledger is neither replaced nor stripped of its backing.
    assert service.delivery_ledger is injected
    assert injected.database is service.database
    assert injected.collection is not None


async def _async_value(value):
    return value


def _async_ping(value):
    async def ping():
        return value

    return ping


async def _record_cache_delete(target: list[str], key: str) -> None:
    target.append(key)
