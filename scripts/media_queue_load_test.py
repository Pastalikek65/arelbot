"""Deterministic media-queue load acceptance for 10 workers and 100 chats.

This is a fixture-replay harness around the production ``MediaJobQueue``. It
does not contact Telegram or external media sites, and its bounded report never
contains URLs, user text, credentials, or exception messages. A live platform
load test remains a separately gated deployment operation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.media.job_queue import MediaJobQueue
from src.storage.media_job_store import MediaJobStore


class LoadConfigError(ValueError):
    pass


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(len(ordered) * quantile))
    return round(ordered[min(len(ordered), rank) - 1], 2)


def _validate(jobs: int, workers: int, chats: int) -> None:
    if not 1 <= jobs <= 1000:
        raise LoadConfigError("jobs must be between 1 and 1000")
    if not 1 <= workers <= 32:
        raise LoadConfigError("workers must be between 1 and 32")
    if not 1 <= chats <= jobs:
        raise LoadConfigError("chats must be between 1 and jobs")


async def run_load(*, jobs: int = 100, workers: int = 10, chats: int = 100) -> dict[str, Any]:
    _validate(jobs, workers, chats)
    queue = MediaJobQueue(store=MediaJobStore(None))
    durations: list[float] = []
    completed: list[tuple[str, int]] = []
    active = 0
    max_active = 0
    active_lock = asyncio.Lock()

    async def processor(payload: dict[str, Any]) -> None:
        nonlocal active, max_active
        started = time.perf_counter()
        async with active_lock:
            active += 1
            max_active = max(max_active, active)
        try:
            # Represents a bounded provider/download/send fixture without
            # making the acceptance gate depend on an external website.
            await asyncio.sleep(0.005)
            completed.append((str(payload["job_id"]), int(payload["chat_id"])))
        finally:
            async with active_lock:
                active -= 1
            durations.append((time.perf_counter() - started) * 1000)

    queue.processor = processor
    await queue.start("redis://fixture-disabled", workers=workers, force_memory=True)
    try:
        for index in range(jobs):
            _job_id, duplicate = await queue.enqueue(
                {
                    "chat_id": -100000 - (index % chats),
                    "user_id": 200000 + index,
                    "url_hash": f"fixture-load-{index}",
                    "url": f"https://fixture.invalid/media/{index}",
                }
            )
            if duplicate:
                raise RuntimeError("unexpected_fixture_deduplication")

        deadline = time.monotonic() + max(5.0, jobs / max(1, workers) + 5.0)
        while len(completed) < jobs and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        metrics = await queue.metrics()
        unique_chats = len({chat_id for _, chat_id in completed})
        success = (
            len(completed) == jobs
            and len({job_id for job_id, _ in completed}) == jobs
            and unique_chats == chats
            and metrics["queue_depth"] == 0
            and max_active <= workers
        )
        return {
            "ok": success,
            "mode": "fixture-replay",
            "jobs": jobs,
            "workers": workers,
            "active_chats": unique_chats,
            "completed": len(completed),
            "failed": jobs - len(completed),
            "max_active_workers": max_active,
            "queue_depth": int(metrics["queue_depth"]),
            "latency_ms": {
                "p50": _percentile(durations, 0.50),
                "p95": _percentile(durations, 0.95),
            },
            "secret_free": True,
        }
    finally:
        await queue.close()


def _safe_report(path: Path, payload: dict[str, Any]) -> None:
    path = Path(path)
    if path.exists() and path.is_symlink():
        raise OSError("report target cannot be a symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise OSError("report parent cannot be a symlink")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=100)
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--chats", type=int, default=100)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        payload = asyncio.run(run_load(jobs=args.jobs, workers=args.workers, chats=args.chats))
    except (LoadConfigError, OSError, RuntimeError, ValueError) as exc:
        payload = {"ok": False, "error": type(exc).__name__.lower()}
    if args.report:
        try:
            _safe_report(args.report, payload)
        except OSError:
            payload = {"ok": False, "error": "report_write_failed"}
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
