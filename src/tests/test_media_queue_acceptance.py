from __future__ import annotations

import asyncio
import time

import pytest

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
