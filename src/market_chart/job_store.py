from src.attachments.job_store import AttachmentJobStore


class MarketChartJobStore(AttachmentJobStore):
    @property
    def collection(self):
        if self._disabled:
            return None
        return getattr(self.database, "market_chart_jobs", None) if self.database is not None else None

    async def ensure_indexes(self):
        collection = self.collection
        if collection is None:
            return
        try:
            await collection.create_index([("status", 1), ("created_at", 1)], name="market_chart_job_status_time")
            await collection.create_index([("user_id", 1), ("status", 1)], name="market_chart_job_user_status")
            await collection.create_index("job_id", unique=True, name="market_chart_job_id_unique")
            await collection.create_index("source_key", name="market_chart_job_source_key")
        except Exception as exc:
            if self.required:
                raise RuntimeError("MongoDB market chart job store is unavailable") from exc
            self._disabled = True
