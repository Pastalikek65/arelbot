import json
import time

from src.attachments.job_queue import AttachmentJobQueue
from src.jobs.retry_policy import RetryPolicy


class QuoteJobQueue(AttachmentJobQueue):
    QUEUE_KEY = "arel:quote:queue"
    NAMESPACE = "quote"
    JOB_TTL = 24 * 60 * 60
    DEDUP_TTL = 10 * 60

    def __init__(self, store=None):
        super().__init__(store=store)
        self.retry_policy = RetryPolicy(
            max_attempts=3,
            transient_codes=frozenset({"telegram_download_failed", "quote_processing_error", "timeout"}),
        )

    async def enqueue(self, payload):
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
