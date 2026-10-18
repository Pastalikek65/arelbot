from src.attachments.job_store import AttachmentJobStore


class QuoteJobStore(AttachmentJobStore):
    @property
    def collection(self):
        if self._disabled:
            return None
        return getattr(self.database, "quote_jobs", None) if self.database is not None else None

    async def ensure_indexes(self) -> None:
        collection = self.collection
        if collection is None:
            return
        try:
            await collection.create_index([("status", 1), ("created_at", 1)], name="quote_job_status_time")
            await collection.create_index([("user_id", 1), ("status", 1)], name="quote_job_user_status")
            await collection.create_index("job_id", unique=True, name="quote_job_id_unique")
            await collection.create_index("source_key", name="quote_job_source_key")
        except Exception as exc:
            if self.required:
                raise RuntimeError("MongoDB quote job store is unavailable") from exc
            self._disabled = True
