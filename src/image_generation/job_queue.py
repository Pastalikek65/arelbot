"""Redis-backed image queue with an explicit memory mode for tests."""

from src.attachments.job_queue import AttachmentJobQueue
from src.jobs.retry_policy import RetryPolicy


class ImageJobQueue(AttachmentJobQueue):
    QUEUE_KEY = "arel:image:queue"
    NAMESPACE = "image"
    DEDUP_TTL = 10 * 60

    def __init__(self, store=None):
        super().__init__(store=store)
        self.retry_policy = RetryPolicy(
            max_attempts=2,
            transient_codes=frozenset({"image_provider_unavailable", "image_generation_error", "timeout"}),
        )
