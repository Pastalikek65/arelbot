"""Redis-backed media work queue with a process-local development fallback."""

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, Optional
from src.jobs.retry_policy import RetryPolicy
from src.security.log_redaction import summarize_exception

logger = logging.getLogger(__name__)


class MediaJobQueue:
    QUEUE_KEY = "arel:media:queue"
    JOB_TTL = 24 * 60 * 60
    DEDUP_TTL = 15 * 60

    def __init__(self, store=None, retry_policy: RetryPolicy | None = None):
        self.redis = None
        self.store = store
        self.local_queue: asyncio.Queue = asyncio.Queue()
        self.processor: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None
        self.tasks = []
        self.stopping = False
        self.local_dedup: Dict[str, tuple[str, float]] = {}
        self.local_jobs: Dict[str, Dict[str, Any]] = {}
        self._dedup_lock = asyncio.Lock()
        self.retry_policy = retry_policy or RetryPolicy()

    async def start(
        self,
        redis_url: str,
        workers: int = 4,
        require_redis: bool = False,
        *,
        force_memory: bool = False,
    ):
        """Start workers and recover persisted jobs.

        ``force_memory`` is intentionally explicit for deterministic tests and
        local development. Production roles leave it false and may require
        Redis, preventing an accidental split-brain queue after deployment.
        """
        if force_memory:
            self.redis = None
        else:
            try:
                import redis.asyncio as redis
                client = redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2)
                await client.ping()
                self.redis = client
                logger.info("Media queue connected to Redis")
            except Exception as exc:
                if require_redis:
                    raise RuntimeError("MEDIA_REDIS_REQUIRED=true but Redis is unavailable") from exc
                logger.warning("Redis unavailable; media queue uses process-local memory")
        self.stopping = False
        if self.store and workers > 0:
            await self.store.ensure_indexes()
            expire = getattr(self.store, "mark_expired", None)
            if expire is not None:
                await expire(self.JOB_TTL)
            # Jobs which were in-flight when a process died are made visible
            # to a fresh worker. The Mongo record is the recovery source of truth.
            for recovered in await self.store.recoverable():
                recovered["status"] = "queued"
                await self.store.update(recovered["job_id"], status="queued")
                if self.redis:
                    await self.redis.lpush(self.QUEUE_KEY, json.dumps(recovered, default=str))
                else:
                    await self.local_queue.put(recovered)
        self.tasks = [asyncio.create_task(self._worker(index), name=f"media-worker-{index}") for index in range(workers)]

    async def enqueue(self, payload: Dict[str, Any]) -> tuple[str, bool]:
        dedup_key = f"{payload['chat_id']}:{payload['user_id']}:{payload['url_hash']}"
        if self.redis:
            existing = await self.redis.get(f"arel:media:dedup:{dedup_key}")
            if existing:
                return existing, True
            job_id = uuid.uuid4().hex
            payload.update(job_id=job_id, status="queued", created_at=time.time())
            if self.store:
                await self.store.create(payload)
            key = f"arel:media:job:{job_id}"
            async with self.redis.pipeline(transaction=True) as pipe:
                await pipe.watch(f"arel:media:dedup:{dedup_key}")
                existing = await pipe.get(f"arel:media:dedup:{dedup_key}")
                if existing:
                    await pipe.unwatch()
                    # Mongo is the durable source of truth.  Remove the row
                    # created before the Redis WATCH claim so a dedup race
                    # cannot leave an orphan that restart recovery requeues.
                    if self.store:
                        await self.store.delete(job_id)
                    return existing, True
                pipe.multi()
                pipe.set(f"arel:media:dedup:{dedup_key}", job_id, ex=self.DEDUP_TTL)
                pipe.set(key, json.dumps(payload), ex=self.JOB_TTL)
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

    async def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        if self.store:
            persisted = await self.store.get(job_id)
            if persisted:
                return persisted
        if self.redis:
            value = await self.redis.get(f"arel:media:job:{job_id}")
            return json.loads(value) if value else None
        job = self.local_jobs.get(job_id)
        return dict(job) if job else None

    async def metrics(self) -> Dict[str, Any]:
        if self.redis:
            return {"backend": "redis", "queue_depth": await self.redis.llen(self.QUEUE_KEY), "workers": len(self.tasks)}
        return {"backend": "memory", "queue_depth": self.local_queue.qsize(), "workers": len(self.tasks)}

    async def _set_status(self, payload: Dict[str, Any], status: str, error_code: str | None = None):
        payload["status"] = status
        if status == "downloading" and not payload.get("started_at"):
            payload["started_at"] = time.time()
        if status in {"completed", "failed", "cancelled", "expired"}:
            payload["finished_at"] = time.time()
        if error_code:
            payload["error_code"] = error_code
        if self.store:
            await self.store.update(
                payload["job_id"],
                status=status,
                error_code=error_code,
                started_at=payload.get("started_at"),
                finished_at=payload.get("finished_at"),
                attempt=payload.get("attempt", 0),
            )
        if self.redis:
            await self.redis.set(f"arel:media:job:{payload['job_id']}", json.dumps(payload), ex=self.JOB_TTL)
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
                await self._set_status(payload, "downloading")
                if self.processor:
                    await self.processor(payload)
                await self._set_status(payload, "completed")
            except asyncio.CancelledError:
                if payload and self.redis:
                    await self._set_status(payload, "queued")
                    await self.redis.lpush(self.QUEUE_KEY, json.dumps(payload))
                elif payload:
                    await self.local_queue.put(payload)
                raise
            except asyncio.TimeoutError:
                # Empty local queue poll; this is normal worker idling.
                continue
            except Exception as exc:
                logger.error(
                    "Media worker %s failed job=%s (%s)",
                    index,
                    (payload or {}).get("job_id"),
                    summarize_exception(exc, label="media_worker"),
                )
                if payload:
                    code = getattr(exc, "code", "internal_error")
                    attempt = int(payload.get("attempt", 0))
                    if self.retry_policy.should_retry(code, attempt):
                        payload["attempt"] = attempt + 1
                        payload["status"] = "queued"
                        await self._set_status(payload, "queued", code)
                        if self.redis:
                            await self.redis.lpush(self.QUEUE_KEY, json.dumps(payload))
                        else:
                            await self.local_queue.put(payload)
                    else:
                        await self._set_status(payload, "failed", code)
                        if self.store:
                            await self.store.dead_letter(payload["job_id"], code, payload)

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
