"""Redis-backed multimodal attachment queue."""

import json
import time

from src.documents.job_queue import DocumentJobQueue
from src.jobs.retry_policy import RetryPolicy


class AttachmentJobQueue(DocumentJobQueue):
    QUEUE_KEY = "arel:attachment:queue"
    NAMESPACE = "attachment"
    JOB_TTL = 24 * 60 * 60
    DEDUP_TTL = 60 * 60

    def __init__(self, store=None):
        super().__init__(
            store=store,
            retry_policy=RetryPolicy(
                max_attempts=3,
                transient_codes=frozenset({
                    "telegram_download_failed", "ai_provider_unavailable",
                    "attachment_processing_error", "timeout",
                }),
            ),
        )

    @property
    def _prefix(self):
        return "attachment"

    async def start(self, redis_url: str, workers: int = 2, require_redis: bool = False, *, force_memory: bool = False):
        """Start with attachment-specific recovery keys."""
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
                    raise RuntimeError("ATTACHMENT_REDIS_REQUIRED=true but Redis is unavailable") from exc
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
        import asyncio
        self.tasks = [asyncio.create_task(self._worker(i), name=f"attachment-worker-{i}") for i in range(max(0, workers))]

    async def enqueue(self, payload):
        # The base implementation is deliberately reused, but its Redis key
        # namespace is replaced below to keep document and attachment jobs
        # completely isolated.
        import uuid
        dedup_key = f"{payload['chat_id']}:{payload['user_id']}:{payload['source_key']}"
        redis_key = f"arel:{self.NAMESPACE}:dedup:{dedup_key}"
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
                    if self.store:
                        await self.store.delete(job_id)
                    return existing, True
                pipe.multi()
                pipe.set(redis_key, job_id, ex=self.DEDUP_TTL)
                pipe.set(f"arel:{self.NAMESPACE}:job:{job_id}", json.dumps(payload), ex=self.JOB_TTL)
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

    async def get_job(self, job_id):
        if self.store:
            persisted = await self.store.get(job_id)
            if persisted:
                return persisted
        if self.redis:
            value = await self.redis.get(f"arel:{self.NAMESPACE}:job:{job_id}")
            return json.loads(value) if value else None
        value = self.local_jobs.get(job_id)
        return dict(value) if value else None

    async def _set_status(self, payload, status, error_code=None):
        payload["status"] = status
        if status in {"downloading", "processing"} and not payload.get("started_at"):
            payload["started_at"] = time.time()
        if status in {"completed", "failed", "cancelled", "expired"}:
            payload["finished_at"] = time.time()
        if error_code:
            payload["error_code"] = error_code
        if self.store:
            await self.store.update(
                payload["job_id"], status=status, error_code=error_code,
                started_at=payload.get("started_at"), finished_at=payload.get("finished_at"),
                attempt=payload.get("attempt", 0), output_type=payload.get("output_type"),
            )
        if self.redis:
            await self.redis.set(f"arel:{self.NAMESPACE}:job:{payload['job_id']}", json.dumps(payload, default=str), ex=self.JOB_TTL)
        else:
            self.local_jobs[payload["job_id"]] = dict(payload)
