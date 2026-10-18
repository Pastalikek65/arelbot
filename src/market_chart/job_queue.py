from src.attachments.job_queue import AttachmentJobQueue
from src.jobs.retry_policy import RetryPolicy


class MarketChartJobQueue(AttachmentJobQueue):
    QUEUE_KEY = "arel:market_chart:queue"
    NAMESPACE = "market_chart"
    DEDUP_TTL = 5 * 60

    def __init__(self, store=None):
        super().__init__(store=store)
        self.retry_policy = RetryPolicy(
            max_attempts=3,
            transient_codes=frozenset({"chart_processing_error", "timeout"}),
        )
