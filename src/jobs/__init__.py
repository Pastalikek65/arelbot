"""Generic job primitives; media queue remains the first production consumer."""

from .retry_policy import RetryPolicy

__all__ = ["JobQueue", "RetryPolicy", "JobStore"]


def __getattr__(name):
    if name == "JobQueue":
        from .queue import JobQueue
        return JobQueue
    if name == "JobStore":
        from .job_store import JobStore
        return JobStore
    raise AttributeError(name)
