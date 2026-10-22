"""Compatibility facade for the Mongo-backed job store."""

from src.storage.media_job_store import MediaJobStore

JobStore = MediaJobStore

__all__ = ["JobStore"]

