from src.quotes.job_queue import QuoteJobQueue


class SpeechJobQueue(QuoteJobQueue):
    QUEUE_KEY = "arel:speech:queue"
    NAMESPACE = "speech"
    DEDUP_TTL = 5 * 60

    def __init__(self, store=None):
        super().__init__(store=store)
        from src.jobs.retry_policy import RetryPolicy
        self.retry_policy = RetryPolicy(max_attempts=3, transient_codes=frozenset({"tts_unavailable", "speech_processing_error", "timeout"}))
