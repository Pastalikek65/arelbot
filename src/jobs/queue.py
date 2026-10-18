"""Compatibility facade for the bounded Redis/local queue."""

from src.media.job_queue import MediaJobQueue

JobQueue = MediaJobQueue

__all__ = ["JobQueue"]

