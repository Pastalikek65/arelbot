"""Persistence for multimodal attachment jobs."""

from datetime import datetime, timezone
import copy

from src.documents.job_store import DocumentJobStore


class AttachmentJobStore(DocumentJobStore):
    @property
    def collection(self):
        if self._disabled:
            return None
        return getattr(self.database, "attachment_jobs", None) if self.database is not None else None

    async def ensure_indexes(self) -> None:
        collection = self.collection
        if collection is None:
            return
        try:
            await collection.create_index([("status", 1), ("created_at", 1)], name="attachment_job_status_time")
            await collection.create_index([("user_id", 1), ("status", 1)], name="attachment_job_user_status")
            await collection.create_index("job_id", unique=True, name="attachment_job_id_unique")
            await collection.create_index("source_key", name="attachment_job_source_key")
        except Exception as exc:
            if self.required:
                raise RuntimeError("MongoDB attachment job store is unavailable") from exc
            self._disabled = True

    async def recoverable(self, limit: int = 500):
        statuses = {"queued", "downloading", "processing"}
        if self.collection is None:
            return [copy.deepcopy(v) for v in self._memory.values() if v.get("status") in statuses][:limit]
        cursor = self.collection.find({"status": {"$in": list(statuses)}}).sort("created_at", 1).limit(limit)
        return await cursor.to_list(length=limit)

    async def mark_expired(self, max_age_seconds: int) -> int:
        if self.collection is None:
            return 0
        cutoff = datetime.now(timezone.utc).timestamp() - max_age_seconds
        result = await self.collection.update_many(
            {"status": {"$in": ["queued", "downloading", "processing"]},
             "created_at": {"$lt": datetime.fromtimestamp(cutoff, tz=timezone.utc)}},
            {"$set": {"status": "expired", "error_code": "job_expired", "updated_at": datetime.now(timezone.utc)}},
        )
        return int(result.modified_count)

    async def dead_letter(self, job_id: str, error_code: str, payload: dict) -> None:
        safe = {
            key: value for key, value in payload.items()
            if key not in {
                "source_file_id", "file_id", "file_path", "path", "caption",
                "user_name", "username", "messages", "options",
            }
        }
        await super().dead_letter(job_id, error_code, safe)
