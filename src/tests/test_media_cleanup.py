"""Media spool cleanup, deferred download reaping and retry bookkeeping."""

from __future__ import annotations

import asyncio
import os
import subprocess
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.worker as worker
from config import Config
from src.media.job_queue import MediaJobQueue
from src.media.providers.ytdlp_provider import MediaDownloadError, YtDlpProvider
from src.media.service import BACKGROUND_CLEANUP_TIMEOUT_SECONDS, MediaDownloadService
from src.observability import MetricsRegistry
from src.orchestrator import Orchestrator
from src.storage.media_job_store import MediaJobStore
from src.utils import cleanup_directories, cleanup_old_files

_FIXTURE_URL = "https://www.youtube.com/watch?v=fixture"


async def _allow_fixture_public_host(_url: str) -> None:
    """Keep the extractor fixtures offline; the URL policy has its own tests."""
    return None


def _age(path: Path, seconds: float) -> None:
    """Move a file or directory into the past so the sweeper sees it as stale."""
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


async def _wait_until(predicate, timeout: float = 5.0) -> None:
    """Wait for a condition that a background task has to satisfy."""

    async def _poll() -> None:
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_poll(), timeout=timeout)


async def _async_false() -> bool:
    return False


async def _async_none() -> None:
    return None


def _make_directory_link(link: Path, target: Path) -> None:
    """Create a directory link that points at ``target``.

    A Windows junction is not a symlink and needs no extra privilege, so
    the containment test runs on every host that can create one.  POSIX
    hosts use a symbolic link, which is the same reparse point hazard.
    """
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            pytest.skip(f"directory junction unavailable: {result.stderr.strip()!r}")
        return
    os.symlink(target, link, target_is_directory=True)


def _service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MediaDownloadService:
    """Build a media service without MongoDB, Redis or Telegram."""
    monkeypatch.setattr(Config, "MEDIA_DOWNLOAD_DIR", str(tmp_path))
    service = object.__new__(MediaDownloadService)
    service.database = SimpleNamespace(ping=_async_false)
    service.job_store = MediaJobStore(None)
    service.queue = MediaJobQueue(store=service.job_store)
    service.bot = SimpleNamespace()
    service.provider = YtDlpProvider(
        str(tmp_path), max_bytes=1024, max_duration=0, timeout=3, cookie_vault=SimpleNamespace()
    )
    service.mongo_available = False
    service.maintenance = False
    service.cookie_enabled = False
    service.redis_required = False
    service.metrics_registry = MetricsRegistry()
    service.delivery_ledger = SimpleNamespace(ensure_indexes=_async_none)
    service._injected_delivery_ledger = SimpleNamespace()
    service._maintenance_task = None
    return service


class _FakeWorkerService:
    """A stand-in for every service the worker process starts."""

    def __init__(self, *args, **kwargs):
        self.closed = False

    def __getattr__(self, name):
        return _FakeWorkerService()

    def __call__(self, *args, **kwargs):
        return None

    async def start(self, bot):
        return None

    async def close(self):
        self.closed = True

    async def get_metrics(self):
        return {}

    async def wait_for_background_cleanup(self, timeout=None):
        return True


# --- cleanup_old_files -----------------------------------------------------


def test_cleanup_removes_old_job_directories(tmp_path):
    stale_dir = tmp_path / "job-stale"
    stale_dir.mkdir()
    (stale_dir / "media.mp4").write_bytes(b"stale")
    stale_file = tmp_path / "loose-stale.txt"
    stale_file.write_text("stale", encoding="utf-8")
    _age(stale_dir, 7200)
    _age(stale_dir / "media.mp4", 7200)
    _age(stale_file, 7200)

    removed = cleanup_old_files(str(tmp_path), max_age_seconds=3600)

    assert not stale_dir.exists()
    assert not stale_file.exists()
    # The reclaimed artifact, the emptied job directory and the loose file.
    assert removed == 3


def test_cleanup_keeps_recent_directories(tmp_path):
    fresh_dir = tmp_path / "job-fresh"
    fresh_dir.mkdir()
    (fresh_dir / "media.mp4").write_bytes(b"fresh")
    fresh_file = tmp_path / "loose-fresh.txt"
    fresh_file.write_text("fresh", encoding="utf-8")

    removed = cleanup_old_files(str(tmp_path), max_age_seconds=3600)

    assert fresh_dir.exists()
    assert (fresh_dir / "media.mp4").exists()
    assert fresh_file.exists()
    assert removed == 0


def test_cleanup_keeps_a_fresh_artifact_inside_an_old_directory(tmp_path):
    """An in-flight download writes into its own job directory."""
    job_dir = tmp_path / "job-active"
    job_dir.mkdir()
    artifact = job_dir / "media.mp4.part"
    artifact.write_bytes(b"growing")
    _age(job_dir, 7200)

    removed = cleanup_old_files(str(tmp_path), max_age_seconds=3600)

    assert job_dir.exists()
    assert artifact.exists()
    assert removed == 0


def test_cleanup_reclaims_the_emptied_job_directory_in_one_sweep(tmp_path):
    """A job directory must not survive the sweep that emptied it."""
    job_dir = tmp_path / "job-stale"
    job_dir.mkdir()
    artifact = job_dir / "media.mp4"
    artifact.write_bytes(b"stale")
    _age(job_dir, 7200)
    _age(artifact, 7200)

    cleanup_old_files(str(tmp_path), max_age_seconds=3600)

    assert not job_dir.exists()
    assert cleanup_old_files(str(tmp_path), max_age_seconds=3600) == 0


def test_cleanup_does_not_follow_a_symlinked_directory(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    try:
        os.symlink(outside, root / "linked", target_is_directory=True)
    except (OSError, NotImplementedError, AttributeError) as exc:
        pytest.skip(f"symlinks unavailable: {exc!r}")
    _age(outside, 7200)
    _age(outside / "keep.txt", 7200)

    removed = cleanup_old_files(str(root), max_age_seconds=3600)

    assert (root / "linked").is_symlink()
    assert (outside / "keep.txt").exists()
    assert removed == 0


def test_cleanup_does_not_traverse_a_directory_junction(tmp_path):
    """A junction inside the spool must not delete files outside of it."""
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "keep.txt"
    victim.write_text("keep", encoding="utf-8")
    _age(outside, 7200)
    _age(victim, 7200)
    _make_directory_link(root / "junction", outside)

    removed = cleanup_old_files(str(root), max_age_seconds=3600)

    assert victim.exists()
    assert (root / "junction").exists()
    assert removed == 0


def test_cleanup_keeps_a_stale_directory_that_holds_a_directory_junction(tmp_path):
    """A tree removal must not walk through a junction either."""
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "keep.txt"
    victim.write_text("keep", encoding="utf-8")
    _age(outside, 7200)
    _age(victim, 7200)
    job_dir = root / "job-stale"
    job_dir.mkdir()
    _make_directory_link(job_dir / "junction", outside)
    _age(job_dir, 7200)

    removed = cleanup_old_files(str(root), max_age_seconds=3600)

    assert victim.exists()
    assert job_dir.exists()
    assert removed == 0


def test_cleanup_never_removes_the_cleanup_root(tmp_path):
    stale = tmp_path / "stale.txt"
    stale.write_text("stale", encoding="utf-8")
    _age(stale, 7200)

    cleanup_old_files(str(tmp_path), max_age_seconds=3600)

    assert tmp_path.exists()


def test_cleanup_returns_zero_for_a_missing_directory(tmp_path):
    assert cleanup_old_files(str(tmp_path / "missing"), max_age_seconds=3600) == 0


def test_cleanup_directories_follow_the_configuration(monkeypatch):
    monkeypatch.setattr(Config, "MEDIA_DOWNLOAD_DIR", "spool/media")
    monkeypatch.setattr(Config, "RAG_UPLOAD_DIR", "spool/media")
    monkeypatch.setattr(Config, "TTS_OUTPUT_DIR", "spool/tts")
    monkeypatch.setattr(Config, "QUOTE_OUTPUT_DIR", "spool/quotes")
    monkeypatch.setattr(Config, "STICKER_TEMP_DIR", "spool/stickers")
    monkeypatch.setattr(Config, "ATTACHMENT_TEMP_DIR", "spool/attachments")
    monkeypatch.setattr(Config, "STICKER_TAG_TEMP_DIR", "spool/tags")

    assert cleanup_directories() == (
        "spool/media",
        "spool/tts",
        "spool/quotes",
        "spool/stickers",
        "spool/attachments",
        "spool/tags",
    )


# --- deferred download cleanup ---------------------------------------------


@pytest.mark.asyncio
async def test_wait_for_background_cleanup_drains_pending_tasks(tmp_path, monkeypatch):
    monkeypatch.setattr("src.media.providers.ytdlp_provider.ensure_public_host", _allow_fixture_public_host)
    provider = YtDlpProvider(str(tmp_path), max_bytes=100, max_duration=0, timeout=0.01, cookie_vault=object())

    def slow_download(_url, job_dir, _cookie_file):
        time.sleep(0.2)
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "media.mp4").write_bytes(b"late-worker-output")
        return {"path": str(job_dir / "media.mp4"), "size": 18, "duration": 1, "extension": ".mp4"}

    monkeypatch.setattr(provider, "_download_sync", slow_download)
    with pytest.raises(MediaDownloadError) as raised:
        await provider.download(_FIXTURE_URL, "job-timeout")

    assert raised.value.code == "timeout"
    assert (tmp_path / "job-timeout").exists()

    provider.cleanup(None, "job-timeout", str(tmp_path))
    assert (tmp_path / "job-timeout").exists()

    assert await provider.wait_for_background_cleanup(timeout=5) is True
    assert not (tmp_path / "job-timeout").exists()
    assert not provider._background_cleanup_tasks


@pytest.mark.asyncio
async def test_wait_for_background_cleanup_keeps_a_pending_reaper_alive(tmp_path, monkeypatch):
    monkeypatch.setattr("src.media.providers.ytdlp_provider.ensure_public_host", _allow_fixture_public_host)
    provider = YtDlpProvider(str(tmp_path), max_bytes=100, max_duration=0, timeout=0.01, cookie_vault=object())
    release = threading.Event()

    def blocked_download(_url, job_dir, _cookie_file):
        release.wait(10)
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "media.mp4").write_bytes(b"late-worker-output")
        return {"path": str(job_dir / "media.mp4"), "size": 18, "duration": 1, "extension": ".mp4"}

    monkeypatch.setattr(provider, "_download_sync", blocked_download)
    try:
        with pytest.raises(MediaDownloadError):
            await provider.download(_FIXTURE_URL, "job-stuck")

        assert await provider.wait_for_background_cleanup(timeout=0.05) is False
        assert provider._background_cleanup_tasks
        assert (tmp_path / "job-stuck").exists()

        release.set()
        assert await provider.wait_for_background_cleanup(timeout=5) is True
        assert not (tmp_path / "job-stuck").exists()
    finally:
        release.set()


@pytest.mark.asyncio
async def test_media_service_wait_for_background_cleanup_drains_the_provider(tmp_path, monkeypatch):
    monkeypatch.setattr("src.media.providers.ytdlp_provider.ensure_public_host", _allow_fixture_public_host)
    service = _service(tmp_path, monkeypatch)
    provider = service.provider
    provider.timeout = 0.01
    release = threading.Event()

    def blocked_download(_url, job_dir, _cookie_file):
        release.wait(10)
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "media.mp4").write_bytes(b"late-worker-output")
        return {"path": str(job_dir / "media.mp4"), "size": 18, "duration": 1, "extension": ".mp4"}

    monkeypatch.setattr(provider, "_download_sync", blocked_download)
    try:
        with pytest.raises(MediaDownloadError):
            await provider.download(_FIXTURE_URL, "job-service")

        assert await service.wait_for_background_cleanup(timeout=0.05) is False
        assert (tmp_path / "job-service").exists()

        release.set()
        assert await service.wait_for_background_cleanup(timeout=5) is True
        assert not (tmp_path / "job-service").exists()
    finally:
        release.set()


@pytest.mark.asyncio
async def test_media_service_start_schedules_and_close_cancels_the_maintenance_task(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "APP_ROLE", "combined")
    monkeypatch.setattr(Config, "MONGODB_REQUIRED", False)
    service = _service(tmp_path, monkeypatch)

    async def _memory_queue_start(self, redis_url, workers=4, require_redis=False, *, force_memory=False):
        self.tasks = []

    monkeypatch.setattr(MediaJobQueue, "start", _memory_queue_start)

    await service.start(SimpleNamespace())

    assert service._maintenance_task is not None
    assert not service._maintenance_task.done()

    await service.close()

    assert service._maintenance_task is None


@pytest.mark.asyncio
async def test_media_maintenance_task_drains_the_provider_reapers(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch)
    drained: list[float] = []

    async def _record(timeout: float = 0) -> bool:
        drained.append(timeout)
        return True

    monkeypatch.setattr(service, "wait_for_background_cleanup", _record)
    task = asyncio.create_task(service._maintenance_loop(interval=0.01))
    try:
        await _wait_until(lambda: bool(drained))
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert drained
    assert drained[0] == BACKGROUND_CLEANUP_TIMEOUT_SECONDS
    assert task.cancelled()


# --- worker process --------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_background_cleanup_reclaims_the_worker_spool(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Config, "MEDIA_DOWNLOAD_DIR", str(tmp_path / "downloads"))
    stale_dir = tmp_path / "downloads" / "job-stale"
    stale_dir.mkdir(parents=True)
    (stale_dir / "media.mp4").write_bytes(b"stale")
    _age(stale_dir, 7200)
    _age(stale_dir / "media.mp4", 7200)

    task = worker._start_background_cleanup()
    try:
        await _wait_until(lambda: not stale_dir.exists())
    finally:
        await worker._stop_background_cleanup(task)

    assert not stale_dir.exists()


@pytest.mark.asyncio
async def test_worker_background_cleanup_sweeps_the_configured_directories(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    calls: list[tuple[str, int]] = []
    expected = list(cleanup_directories())

    def _record(directory, max_age_seconds=3600):
        calls.append((str(directory), max_age_seconds))
        return 0

    monkeypatch.setattr(worker, "cleanup_old_files", _record)
    task = worker._start_background_cleanup()
    await _wait_until(lambda: len(calls) >= len(expected))

    await worker._stop_background_cleanup(task)

    assert task.cancelled()
    assert [directory for directory, _ in calls] == expected


@pytest.mark.asyncio
async def test_worker_sweep_counts_the_reclaimed_entries(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Config, "MEDIA_DOWNLOAD_DIR", str(tmp_path / "downloads"))
    stale_dir = tmp_path / "downloads" / "job-stale"
    stale_dir.mkdir(parents=True)
    (stale_dir / "media.mp4").write_bytes(b"stale")
    _age(stale_dir, 7200)
    _age(stale_dir / "media.mp4", 7200)
    registry = MetricsRegistry()

    task = worker._start_background_cleanup(registry)
    try:
        await _wait_until(
            lambda: registry.snapshot()["counters"].get("media_cleanup_entries_removed", 0) > 0
        )
    finally:
        await worker._stop_background_cleanup(task)

    assert registry.snapshot()["counters"]["media_cleanup_entries_removed"] >= 1


@pytest.mark.asyncio
async def test_worker_run_starts_and_cancels_the_background_cleanup(tmp_path, monkeypatch):
    """The worker process must own a sweeper and stop it on shutdown."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Config, "TELEGRAM_TOKEN", "test:token")
    monkeypatch.setattr(Config, "OBSERVABILITY_ENABLED", False)
    monkeypatch.setattr(Config, "validate", lambda: None)

    class _WorkerStopped(Exception):
        pass

    def _stop_waiting(*args, **kwargs):
        raise _WorkerStopped()

    monkeypatch.setattr(asyncio.Event, "wait", _stop_waiting)

    @asynccontextmanager
    async def _fake_build_bot(token):
        yield SimpleNamespace()

    monkeypatch.setattr(worker, "build_bot", _fake_build_bot)
    for name in (
        "MediaDownloadService",
        "Database",
        "ChatEngine",
        "KeyManager",
        "DocumentIngestionService",
        "SpeechSynthesisService",
        "StickerRenderService",
        "AttachmentProcessingService",
        "QuoteRenderService",
        "QuoteProcessingService",
        "StickerTagService",
        "MarketChartService",
        "MarketChartProcessingService",
        "ImageGenerationService",
        "ImageRouter",
    ):
        monkeypatch.setattr(worker, name, lambda *args, **kwargs: _FakeWorkerService())

    created: list[asyncio.Task] = []
    original_start = worker._start_background_cleanup

    def _record_start(registry=None):
        task = original_start(registry)
        created.append(task)
        return task

    monkeypatch.setattr(worker, "_start_background_cleanup", _record_start)

    with pytest.raises(_WorkerStopped):
        await worker.run()

    assert created
    assert created[0].cancelled()


# --- bot process -----------------------------------------------------------


@pytest.mark.asyncio
async def test_bot_sweep_runs_outside_the_event_loop(tmp_path, monkeypatch):
    orchestrator = object.__new__(Orchestrator)
    orchestrator.media_service = SimpleNamespace(metrics_registry=MetricsRegistry())
    calls: list[str] = []
    threads: list[int] = []

    def _record(directory, max_age_seconds=3600):
        calls.append(str(directory))
        threads.append(threading.get_ident())
        return 0

    monkeypatch.setattr("src.orchestrator.cleanup_old_files", _record)
    task = asyncio.create_task(orchestrator._background_cleanup_task())
    try:
        await _wait_until(lambda: bool(calls))
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert calls
    assert all(ident != threading.get_ident() for ident in threads)


@pytest.mark.asyncio
async def test_bot_registers_the_background_sweep_task(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Config, "APP_ROLE", "combined")
    monkeypatch.setattr(Config, "MONGODB_REQUIRED", False)
    monkeypatch.setattr(Config, "OBSERVABILITY_ENABLED", False)
    monkeypatch.setattr("src.orchestrator.cleanup_old_files", lambda *args, **kwargs: 0)
    orchestrator = object.__new__(Orchestrator)
    orchestrator._background_tasks = []
    orchestrator.media_service = SimpleNamespace(
        queue=SimpleNamespace(redis=None),
        metrics_registry=MetricsRegistry(),
        mongo_available=False,
    )
    orchestrator.user_states = SimpleNamespace(bind=lambda store: None, hydrate=_async_none)
    orchestrator._get_data_port = lambda: SimpleNamespace(
        ping=_async_false, ensure_indexes=_async_none
    )

    await orchestrator.start_background_tasks()

    assert len(orchestrator._background_tasks) == 1
    task = orchestrator._background_tasks[0]
    assert task.get_name() == "arel-file-cleanup"
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


# --- retry bookkeeping -----------------------------------------------------


@pytest.mark.asyncio
async def test_report_failure_survives_a_malformed_attempt_counter(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch)
    edited: list[str] = []
    deleted: list[int] = []

    async def _edit(chat_id, message_id, text, **_kwargs):
        edited.append(text)

    async def _delete(chat_id, message_id):
        deleted.append(message_id)

    service.bot = SimpleNamespace(edit_message_text=_edit, delete_message=_delete)
    payload = {
        "job_id": "job-attempt",
        "chat_id": -100,
        "status_message_id": 55,
        "chat_type": "group",
        "attempt": "abc",
    }

    assert service._job_will_retry(payload, "timeout") is True

    await service._report_failure(payload, "timeout", MediaDownloadError("timeout", "timeout"))

    assert edited
    assert not deleted


@pytest.mark.asyncio
async def test_malformed_attempt_counter_keeps_the_real_error_code(tmp_path, monkeypatch):
    """The durable error code must not degrade into internal_error."""
    service = _service(tmp_path, monkeypatch)
    store = service.job_store
    queue = service.queue
    payload = {
        "job_id": "job-attempt",
        "url": _FIXTURE_URL,
        "url_hash": "fixture-hash",
        "chat_id": -100,
        "status_message_id": 55,
        "chat_type": "group",
        "attempt": "abc",
    }

    async def _processor(job):
        try:
            raise MediaDownloadError("file_too_large", "Medya dosyası sınırı aşıyor")
        except MediaDownloadError as exc:
            await service._report_failure(job, exc.code, exc)
            raise

    queue.processor = _processor
    await queue.start("redis://unused", workers=1, require_redis=False, force_memory=True)
    try:
        await queue.local_queue.put(dict(payload))
        await _wait_until(lambda: bool(store._dead_letters))
    finally:
        await queue.close()

    dead_letter = store._dead_letters[0]
    assert dead_letter["error_code"] == "file_too_large"
    assert dead_letter["error_code"] != "internal_error"
    record = await store.get("job-attempt")
    assert record["status"] == "failed"
    assert record["error_code"] == "file_too_large"
