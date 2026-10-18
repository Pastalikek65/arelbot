"""Mongo persistence for image generation jobs."""

from __future__ import annotations

import copy
from datetime import datetime, timezone

from src.documents.job_store import DocumentJobStore


class ImageJobStore(DocumentJobStore):
    @property
    def collection(self):
        if self._disabled:
            return None
        return getattr(self.database, "image_jobs", None) if self.database is not None else None

    async def ensure_indexes(self) -> None:
        collection = self.collection
        if collection is None:
            return
        try:
            await collection.create_index([("status", 1), ("created_at", 1)], name="image_job_status_time")
            await collection.create_index([("user_id", 1), ("status", 1)], name="image_job_user_status")
            await collection.create_index("job_id", unique=True, name="image_job_id_unique")
            await collection.create_index("source_key", name="image_job_source_key")
        except Exception as exc:
            if self.required:
                raise RuntimeError("MongoDB image job store is unavailable") from exc
            self._disabled = True

    async def dead_letter(self, job_id: str, error_code: str, payload: dict) -> None:
        # Prompts are user content and must never be copied into dead-letter
        # records or logs.
        safe = {key: value for key, value in payload.items() if key not in {"prompt", "user_name", "username"}}
        await super().dead_letter(job_id, error_code, safe)

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
