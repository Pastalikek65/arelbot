"""Redis-backed document ingestion queue with explicit local test fallback."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Awaitable, Callable, Optional

from src.jobs.retry_policy import RetryPolicy

logger = logging.getLogger(__name__)


class QueueConfigurationError(RuntimeError):
    """Raised when a worker starts without its service processor binding."""

    code = "queue_processor_unconfigured"


class DocumentJobQueue:
    QUEUE_KEY = "arel:document:queue"
    JOB_TTL = 24 * 60 * 60
    DEDUP_TTL = 60 * 60

    def __init__(self, store=None, retry_policy: RetryPolicy | None = None):
        self.redis = None
        self.store = store
        self.local_queue: asyncio.Queue = asyncio.Queue()
        self.processor: Optional[Callable[[dict[str, Any]], Awaitable[None]]] = None
        self.tasks: list[asyncio.Task] = []
        self.stopping = False
        self.local_dedup: dict[str, tuple[str, float]] = {}
        self.local_jobs: dict[str, dict[str, Any]] = {}
        self._dedup_lock = asyncio.Lock()
        self.retry_policy = retry_policy or RetryPolicy(
            max_attempts=3,
            transient_codes=frozenset({"telegram_download_failed", "document_storage_unavailable", "document_embedding_unavailable", "timeout"}),
        )

    async def start(self, redis_url: str, workers: int = 2, require_redis: bool = False, *, force_memory: bool = False):
        if force_memory:
            self.redis = None
        else:
            try:
                import redis.asyncio as redis
                client = redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2)
                await client.ping()
                self.redis = client
            except Exception as exc:
                if require_redis:
                    raise RuntimeError("DOCUMENT_REDIS_REQUIRED=true but Redis is unavailable") from exc
                logger.warning("Document queue uses process-local memory in this environment: %s", type(exc).__name__)
        self.stopping = False
        if self.store and workers > 0:
            await self.store.ensure_indexes()
            await self.store.mark_expired(self.JOB_TTL)
            for recovered in await self.store.recoverable():
                recovered["status"] = "queued"
                await self.store.update(recovered["job_id"], status="queued")
                if self.redis:
                    await self.redis.lpush(self.QUEUE_KEY, json.dumps(recovered, default=str))
                else:
                    await self.local_queue.put(recovered)
        self.tasks = [asyncio.create_task(self._worker(i), name=f"document-worker-{i}") for i in range(max(0, workers))]

    async def enqueue(self, payload: dict[str, Any]) -> tuple[str, bool]:
        dedup_key = f"{payload['chat_id']}:{payload['user_id']}:{payload['source_key']}"
        redis_key = f"arel:document:dedup:{dedup_key}"
        if self.redis:
            existing = await self.redis.get(redis_key)
            if existing:
                return existing, True
            job_id = uuid.uuid4().hex
            payload.update(job_id=job_id, status="queued", created_at=time.time())
            if self.store:
                await self.store.create(payload)
            async with self.redis.pipeline(transaction=True) as pipe:
                await pipe.watch(redis_key)
                existing = await pipe.get(redis_key)
                if existing:
                    await pipe.unwatch()
                    if self.store and hasattr(self.store, "delete"):
                        await self.store.delete(job_id)
                    return existing, True
                pipe.multi()
                pipe.set(redis_key, job_id, ex=self.DEDUP_TTL)
                pipe.set(f"arel:document:job:{job_id}", json.dumps(payload), ex=self.JOB_TTL)
                pipe.lpush(self.QUEUE_KEY, json.dumps(payload))
                await pipe.execute()
            return job_id, False
        async with self._dedup_lock:
            now = time.time()
            previous = self.local_dedup.get(dedup_key)
            if previous and previous[1] > now:
                return previous[0], True
            job_id = uuid.uuid4().hex
            payload.update(job_id=job_id, status="queued", created_at=now)
            if self.store:
                await self.store.create(payload)
            self.local_dedup[dedup_key] = (job_id, now + self.DEDUP_TTL)
            self.local_jobs[job_id] = dict(payload)
            await self.local_queue.put(dict(payload))
            return job_id, False

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        if self.store:
            persisted = await self.store.get(job_id)
            if persisted:
                return persisted
        if self.redis:
            value = await self.redis.get(f"arel:document:job:{job_id}")
            return json.loads(value) if value else None
        value = self.local_jobs.get(job_id)
        return dict(value) if value else None

    async def metrics(self) -> dict[str, Any]:
        if self.redis:
            return {"backend": "redis", "queue_depth": await self.redis.llen(self.QUEUE_KEY), "workers": len(self.tasks)}
        return {"backend": "memory", "queue_depth": self.local_queue.qsize(), "workers": len(self.tasks)}

    async def _set_status(self, payload: dict[str, Any], status: str, error_code: str | None = None):
        payload["status"] = status
        if status in {"downloading", "ingesting"} and not payload.get("started_at"):
            payload["started_at"] = time.time()
        if status in {"completed", "failed", "cancelled", "expired"}:
            payload["finished_at"] = time.time()
        if error_code:
            payload["error_code"] = error_code
        if self.store:
            await self.store.update(
                payload["job_id"], status=status, error_code=error_code,
                started_at=payload.get("started_at"), finished_at=payload.get("finished_at"),
                attempt=payload.get("attempt", 0), document_id=payload.get("document_id"),
                chunk_count=payload.get("chunk_count"),
            )
        if self.redis:
            await self.redis.set(f"arel:document:job:{payload['job_id']}", json.dumps(payload, default=str), ex=self.JOB_TTL)
        else:
            self.local_jobs[payload["job_id"]] = dict(payload)

    async def _worker(self, index: int):
        while not self.stopping:
            payload = None
            try:
                if self.redis:
                    item = await self.redis.brpop(self.QUEUE_KEY, timeout=1)
                    if item:
                        payload = json.loads(item[1])
                else:
                    payload = await asyncio.wait_for(self.local_queue.get(), timeout=1)
                if not payload:
                    continue
                if self.processor is None:
                    # Never acknowledge a job merely because it was dequeued.
                    # A missing processor is a deployment wiring failure and
                    # must follow the normal retry/dead-letter path.
                    raise QueueConfigurationError("queue_processor_unconfigured")
                await self._set_status(payload, "downloading")
                await self.processor(payload)
                await self._set_status(payload, "completed")
            except asyncio.CancelledError:
                if payload:
                    await self._requeue(payload)
                raise
            except asyncio.TimeoutError as exc:
                # Queue polling timeouts are harmless; a timeout raised by a
                # processor belongs to the active job and must be retried or
                # dead-lettered rather than leaving it stuck in ``ingesting``.
                if payload is None:
                    continue
                code = "timeout"
                logger.warning("Document worker %s timed out job %s", index, payload.get("job_id"))
                attempt = int(payload.get("attempt", 0))
                if self.retry_policy.should_retry(code, attempt):
                    payload["attempt"] = attempt + 1
                    await self._set_status(payload, "queued", code)
                    await self._requeue(payload)
                else:
                    await self._set_status(payload, "failed", code)
                    if self.store:
                        await self.store.dead_letter(payload["job_id"], code, payload)
            except Exception as exc:
                code = getattr(exc, "code", "internal_error")
                logger.warning("Document worker %s failed job %s (%s)", index, (payload or {}).get("job_id"), code)
                if payload:
                    attempt = int(payload.get("attempt", 0))
                    if self.retry_policy.should_retry(code, attempt):
                        payload["attempt"] = attempt + 1
                        await self._set_status(payload, "queued", code)
                        await self._requeue(payload)
                    else:
                        await self._set_status(payload, "failed", code)
                        if self.store:
                            await self.store.dead_letter(payload["job_id"], code, payload)

    async def _requeue(self, payload: dict[str, Any]) -> None:
        if self.redis:
            await self.redis.lpush(self.QUEUE_KEY, json.dumps(payload, default=str))
        else:
            await self.local_queue.put(payload)

    async def close(self):
        self.stopping = True
        for task in self.tasks:
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
        if self.redis:
            await self.redis.aclose()
            self.redis = None
