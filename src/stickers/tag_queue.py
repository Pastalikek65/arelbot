from src.attachments.job_queue import AttachmentJobQueue
from src.jobs.retry_policy import RetryPolicy


class StickerTagJobQueue(AttachmentJobQueue):
    QUEUE_KEY = "arel:sticker_tag:queue"
    NAMESPACE = "sticker_tag"
    DEDUP_TTL = 24 * 60 * 60

    def __init__(self, store=None):
        super().__init__(store=store)
        self.retry_policy = RetryPolicy(
            max_attempts=3,
            transient_codes=frozenset({"telegram_download_failed", "ai_provider_unavailable", "sticker_tag_processing_error", "timeout"}),
        )
