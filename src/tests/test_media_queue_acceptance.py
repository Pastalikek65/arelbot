from __future__ import annotations

import asyncio
import time

import pytest
from redis import exceptions as redis_exceptions

from src.jobs.retry_policy import RetryPolicy
from src.media import job_queue as job_queue_module
from src.media.job_queue import MediaJobQueue
from src.storage.media_job_store import MediaJobStore


@pytest.mark.asyncio
async def test_media_queue_holds_ten_workers_for_one_hundred_jobs_and_deduplicates():
    queue = MediaJobQueue()
    active = 0
    maximum_active = 0
    processed = 0
    lock = asyncio.Lock()
    done = asyncio.Event()

    async def processor(payload):
        nonlocal active, maximum_active, processed
        async with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        await asyncio.sleep(0.001)
        async with lock:
            active -= 1
            processed += 1
            if processed == 100:
                done.set()

    queue.processor = processor
    queue.tasks = [asyncio.create_task(queue._worker(index)) for index in range(10)]
    started = time.perf_counter()
    try:
        duplicate_payload = {
            "chat_id": -1,
            "user_id": 7,
            "url_hash": "same-url",
            "status_message_id": 1,
        }
        first, duplicate = await queue.enqueue(dict(duplicate_payload))
        second, duplicate_again = await queue.enqueue(dict(duplicate_payload))
        assert first == second
        assert duplicate is False
        assert duplicate_again is True

        for index in range(99):
            await queue.enqueue({
                "chat_id": -1,
                "user_id": 7,
                "url_hash": f"url-{index}",
                "status_message_id": index + 2,
            })
        await asyncio.wait_for(done.wait(), timeout=5)
    finally:
        await queue.close()

    elapsed = time.perf_counter() - started
    metrics = await queue.metrics()
    assert processed == 100
    assert maximum_active <= 10
    assert elapsed < 5
    assert metrics["workers"] == 0


@pytest.mark.asyncio
async def test_media_queue_recovers_inflight_job_after_worker_restart_without_redis():
    store = MediaJobStore(None)
    await store.create(
        {
            "job_id": "restart-job",
            "chat_id": -100,
            "user_id": 42,
            "url_hash": "restart-hash",
            "status": "downloading",
        }
    )
    queue = MediaJobQueue(store=store)
    processed = asyncio.Event()

    async def processor(payload):
        assert payload["job_id"] == "restart-job"
        processed.set()

    queue.processor = processor
    await queue.start("redis://unavailable", workers=1, force_memory=True)
    try:
        await asyncio.wait_for(processed.wait(), timeout=2)
        async def wait_for_completion():
            while True:
                current = await store.get("restart-job")
                if current and current.get("status") == "completed":
                    return current
                await asyncio.sleep(0.01)

        recovered = await asyncio.wait_for(wait_for_completion(), timeout=2)
        assert recovered["status"] == "completed"
    finally:
        await queue.close()


class _FailingPipeline:
    """A Redis pipeline that fails at a chosen point of the claim."""

    def __init__(self, error: Exception, fail_at: str = "watch") -> None:
        self.error = error
        self.fail_at = fail_at
        self.watched: list[str] = []
        self.executed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def watch(self, *keys):
        self.watched.extend(keys)
        if self.fail_at == "watch":
            raise self.error
        return None

    async def get(self, *args, **kwargs):
        return None

    async def unwatch(self):
        return None

    def multi(self):
        return self

    def set(self, *args, **kwargs):
        return self

    def lpush(self, *args, **kwargs):
        return self

    async def execute(self):
        self.executed = True
        if self.fail_at == "execute":
            raise self.error
        return []


class _FailingRedis:
    """Minimal Redis stand-in whose pipeline never accepts the job."""

    def __init__(self, error: Exception, fail_at: str = "watch") -> None:
        self.pipeline_instance = _FailingPipeline(error, fail_at)

    async def get(self, key):
        return None

    def pipeline(self, transaction=True):
        return self.pipeline_instance

    async def aclose(self):
        return None


class _CancellablePipeline(_FailingPipeline):
    """A pipeline whose WATCH never returns, so the claim can be cancelled."""

    def __init__(self, reached: asyncio.Event) -> None:
        super().__init__(RuntimeError("unused"))
        self.reached = reached

    async def watch(self, *keys):
        self.watched.extend(keys)
        self.reached.set()
        await asyncio.Event().wait()


class _CancellableRedis(_FailingRedis):
    """Redis stand-in whose WATCH blocks until the enqueue is cancelled."""

    def __init__(self, reached: asyncio.Event) -> None:
        super().__init__(RuntimeError("unused"))
        self.pipeline_instance = _CancellablePipeline(reached)


class _GatedDeleteStore:
    """A store whose delete waits for a gate, so a cancel can race the cleanup."""

    def __init__(self, gate: asyncio.Event) -> None:
        self.gate = gate
        self.rows: dict[str, dict] = {}
        self.delete_started = asyncio.Event()
        self.deleted = asyncio.Event()

    async def create(self, payload):
        self.rows[payload["job_id"]] = dict(payload)

    async def get(self, job_id):
        return self.rows.get(job_id)

    async def delete(self, job_id):
        self.delete_started.set()
        await self.gate.wait()
        self.rows.pop(job_id, None)
        self.deleted.set()


class _TransientMediaFailure(Exception):
    """A provider failure the retry policy treats as transient."""

    code = "timeout"


class _RecordingLocalQueue(asyncio.Queue):
    """Local queue that records every re-push the worker performs."""

    def __init__(self, events: list[tuple[str, object]]) -> None:
        super().__init__()
        self.events = events

    async def put(self, item):
        self.events.append(("requeue", item.get("attempt", 0)))
        await super().put(item)


class _SleepRecordingAsyncio:
    """Delegates to asyncio but records every sleep and shortens the wait."""

    def __init__(self, real, events: list[tuple[str, object]]) -> None:
        self._real = real
        self._events = events

    def __getattr__(self, name):
        return getattr(self._real, name)

    async def sleep(self, delay, *args, **kwargs):
        self._events.append(("sleep", delay))
        return await self._real.sleep(0)


@pytest.mark.parametrize("error_type", [redis_exceptions.WatchError, redis_exceptions.ConnectionError])
@pytest.mark.asyncio
async def test_failed_redis_watch_removes_the_orphan_mongo_row(error_type):
    store = MediaJobStore(None)
    queue = MediaJobQueue(store=store)
    redis = _FailingRedis(error_type("claim failed"))
    queue.redis = redis
    payload = {"chat_id": -1, "user_id": 7, "url_hash": "orphan-url", "status_message_id": 3}

    with pytest.raises(error_type):
        await queue.enqueue(payload)

    job_id = payload["job_id"]
    assert job_id
    assert redis.pipeline_instance.executed is False
    # The Mongo row is written before the Redis claim, so a failed claim must
    # not leave a row behind that no worker can ever pick up.
    assert await store.get(job_id) is None


@pytest.mark.asyncio
async def test_failed_redis_execute_removes_the_orphan_mongo_row():
    store = MediaJobStore(None)
    queue = MediaJobQueue(store=store)
    redis = _FailingRedis(redis_exceptions.ConnectionError("connection lost before the reply"), fail_at="execute")
    queue.redis = redis
    payload = {"chat_id": -1, "user_id": 7, "url_hash": "orphan-url", "status_message_id": 3}

    with pytest.raises(redis_exceptions.ConnectionError):
        await queue.enqueue(payload)

    job_id = payload["job_id"]
    assert job_id
    assert redis.pipeline_instance.executed is True
    assert await store.get(job_id) is None


@pytest.mark.asyncio
async def test_cancelled_enqueue_removes_the_orphan_mongo_row():
    store = MediaJobStore(None)
    queue = MediaJobQueue(store=store)
    reached = asyncio.Event()
    queue.redis = _CancellableRedis(reached)
    payload = {"chat_id": -1, "user_id": 7, "url_hash": "cancelled-url", "status_message_id": 5}

    task = asyncio.create_task(queue.enqueue(payload))
    await asyncio.wait_for(reached.wait(), timeout=2)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    job_id = payload["job_id"]
    assert job_id
    # The cancellation is not swallowed: the task ends cancelled.
    assert task.cancelled()
    assert await store.get(job_id) is None


@pytest.mark.asyncio
async def test_cancelled_enqueue_cleanup_survives_a_second_cancel():
    gate = asyncio.Event()
    store = _GatedDeleteStore(gate)
    queue = MediaJobQueue(store=store)
    reached = asyncio.Event()
    queue.redis = _CancellableRedis(reached)
    payload = {"chat_id": -1, "user_id": 7, "url_hash": "cancelled-url", "status_message_id": 5}

    task = asyncio.create_task(queue.enqueue(payload))
    await asyncio.wait_for(reached.wait(), timeout=2)
    task.cancel()
    await asyncio.wait_for(store.delete_started.wait(), timeout=2)
    # A second cancel must not abort the cleanup: the row still has to go.
    task.cancel()
    gate.set()
    await asyncio.wait_for(store.deleted.wait(), timeout=2)

    assert store.rows == {}
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()


@pytest.mark.asyncio
async def test_retry_uses_backoff_instead_of_immediate_requeue(monkeypatch):
    store = MediaJobStore(None)
    policy = RetryPolicy(max_attempts=3)
    queue = MediaJobQueue(store=store, retry_policy=policy)
    events: list[tuple[str, object]] = []
    queue.local_queue = _RecordingLocalQueue(events)

    async def processor(payload):
        events.append(("process", payload.get("attempt", 0)))
        raise _TransientMediaFailure("upstream is briefly unavailable")

    queue.processor = processor
    monkeypatch.setattr(job_queue_module, "asyncio", _SleepRecordingAsyncio(asyncio, events))

    dead_letters: list[tuple[str, str]] = []
    original_dead_letter = store.dead_letter

    async def recording_dead_letter(job_id, error_code, payload):
        dead_letters.append((job_id, error_code))
        await original_dead_letter(job_id, error_code, payload)

    monkeypatch.setattr(store, "dead_letter", recording_dead_letter)

    job_id, duplicate = await queue.enqueue({
        "chat_id": -1,
        "user_id": 7,
        "url_hash": "backoff-url",
        "status_message_id": 4,
    })
    assert duplicate is False
    # The first enqueue is not a retry; only the worker's re-pushes are recorded.
    events.clear()

    queue.tasks = [asyncio.create_task(queue._worker(0))]
    try:
        deadline = time.perf_counter() + 3.0
        while not dead_letters:
            if time.perf_counter() >= deadline:
                raise AssertionError(f"media job was never dead-lettered: {events}")
            await asyncio.sleep(0.01)
    finally:
        await queue.close()

    # Every retry waits an exponential backoff before the job becomes visible
    # to a worker again, and the attempt counter travels with the payload.
    assert events == [
        ("process", 0),
        ("sleep", 5),
        ("requeue", 1),
        ("process", 1),
        ("sleep", 10),
        ("requeue", 2),
        ("process", 2),
    ]
    delays = [value for kind, value in events if kind == "sleep"]
    assert delays == [policy.backoff_seconds(0), policy.backoff_seconds(1)] == [5, 10]
    assert dead_letters == [(job_id, "timeout")]
    failed = await store.get(job_id)
    assert failed["status"] == "failed"
    assert failed["error_code"] == "timeout"


def test_retry_policy_backoff_is_exponential_and_capped():
    policy = RetryPolicy()
    assert policy.backoff_seconds(0) == 5
    assert policy.backoff_seconds(1) == 10
    assert policy.backoff_seconds(2) == 20
    assert policy.backoff_seconds(6) == 300
    assert policy.backoff_seconds(20) == 300
    assert policy.backoff_seconds(-1) == 5


@pytest.mark.parametrize("raw_attempt", ["abc", None, -5])
@pytest.mark.asyncio
async def test_worker_survives_an_unusable_attempt_counter(raw_attempt, monkeypatch):
    store = MediaJobStore(None)
    await store.create({
        "job_id": "malformed-attempt",
        "chat_id": -1,
        "user_id": 7,
        "url_hash": "malformed-attempt",
        "status": "queued",
        "attempt": raw_attempt,
    })
    queue = MediaJobQueue(store=store, retry_policy=RetryPolicy(max_attempts=3))
    events: list[tuple[str, object]] = []
    monkeypatch.setattr(job_queue_module, "asyncio", _SleepRecordingAsyncio(asyncio, events))

    deliveries: list[object] = []

    async def processor(payload):
        deliveries.append(payload.get("attempt"))
        raise _TransientMediaFailure("upstream is briefly unavailable")

    queue.processor = processor

    dead_letters: list[tuple[str, str]] = []
    original_dead_letter = store.dead_letter

    async def recording_dead_letter(job_id, error_code, payload):
        dead_letters.append((job_id, error_code))
        await original_dead_letter(job_id, error_code, payload)

    monkeypatch.setattr(store, "dead_letter", recording_dead_letter)

    await queue.start("redis://unavailable", workers=1, force_memory=True)
    try:
        deadline = time.perf_counter() + 3.0
        while not dead_letters:
            if time.perf_counter() >= deadline:
                raise AssertionError(f"the job never reached the dead letter: {deliveries}")
            await asyncio.sleep(0.01)
    finally:
        await queue.close()

    # The counter is repaired instead of raising inside the worker, and the
    # job is delivered exactly as many times as the policy allows.
    assert len(deliveries) == 3
    assert deliveries[1:] == [1, 2]
    assert dead_letters == [("malformed-attempt", "timeout")]
    failed = await store.get("malformed-attempt")
    assert failed["status"] == "failed"
    assert failed["error_code"] == "timeout"
