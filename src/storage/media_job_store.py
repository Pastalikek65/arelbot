"""Mongo-backed media job persistence with an explicit no-DB test fallback."""

from __future__ import annotations

import copy
import logging
from datetime import datetime, timezone
from typing import Any
from src.security.log_redaction import summarize_exception

logger = logging.getLogger(__name__)


class MediaJobStore:
    def __init__(self, database: Any | None = None, *, required: bool = False) -> None:
        self.database = database
        self.required = required
        self._memory: dict[str, dict[str, Any]] = {}
        self._dead_letters: list[dict[str, Any]] = []
        self._disabled = False

    @property
    def collection(self):
        if self._disabled:
            return None
        return getattr(self.database, "media_jobs", None) if self.database is not None else None

    async def ensure_indexes(self) -> None:
        collection = self.collection
        if collection is None:
            return
        try:
            await collection.create_index([("status", 1), ("created_at", 1)])
            await collection.create_index([("user_id", 1), ("status", 1)])
            await collection.create_index("job_id", unique=True)
            await collection.create_index("url_hash")
        except Exception as exc:
            if self.required:
                raise RuntimeError("MongoDB media job store is unavailable") from exc
            logger.warning(
                "Mongo media job store unavailable; using memory for this process: %s",
                summarize_exception(exc, label="media_job_store"),
            )
            self._disabled = True

    async def create(self, payload: dict[str, Any]) -> None:
        document = copy.deepcopy(payload)
        document.setdefault("created_at", datetime.now(timezone.utc))
        if isinstance(document.get("created_at"), (int, float)):
            document["created_at"] = datetime.fromtimestamp(document["created_at"], tz=timezone.utc)
        document.setdefault("attempt", 0)
        if self.collection is None:
            self._memory[document["job_id"]] = document
            return
        try:
            await self.collection.update_one(
                {"job_id": document["job_id"]}, {"$setOnInsert": document}, upsert=True
            )
        except Exception as exc:
            if self.required:
                raise RuntimeError("MongoDB media job write failed") from exc
            logger.warning(
                "Mongo media job write failed; retaining job in memory: %s",
                summarize_exception(exc, label="media_job_write"),
            )
            self._disabled = True
            self._memory[document["job_id"]] = document

    async def update(self, job_id: str, **changes: Any) -> None:
        changes["updated_at"] = datetime.now(timezone.utc)
        if self.collection is None:
            self._memory.setdefault(job_id, {"job_id": job_id}).update(copy.deepcopy(changes))
            return
        try:
            await self.collection.update_one({"job_id": job_id}, {"$set": changes}, upsert=True)
        except Exception as exc:
            if self.required:
                raise RuntimeError("MongoDB media job update failed") from exc
            logger.warning(
                "Mongo media job update failed; retaining job in memory: %s",
                summarize_exception(exc, label="media_job_update"),
            )
            self._disabled = True
            self._memory.setdefault(job_id, {"job_id": job_id}).update(copy.deepcopy(changes))

    async def get(self, job_id: str) -> dict[str, Any] | None:
        if self.collection is None:
            value = self._memory.get(job_id)
            return copy.deepcopy(value) if value else None
        return await self.collection.find_one({"job_id": job_id}, {"_id": 0})

    async def delete(self, job_id: str) -> None:
        """Delete a pre-claim job row that lost a Redis dedup race."""
        if self.collection is None:
            self._memory.pop(job_id, None)
            return
        try:
            await self.collection.delete_one({"job_id": job_id})
        except Exception as exc:
            if self.required:
                raise RuntimeError("MongoDB media job delete failed") from exc
            logger.warning(
                "Mongo media job delete failed: %s",
                summarize_exception(exc, label="media_job_delete"),
            )

    async def recoverable(self, limit: int = 500) -> list[dict[str, Any]]:
        statuses = {"queued", "probing", "downloading", "transcoding", "sending"}
        if self.collection is None:
            return [copy.deepcopy(v) for v in self._memory.values() if v.get("status") in statuses][:limit]
        cursor = self.collection.find({"status": {"$in": list(statuses)}}).sort("created_at", 1).limit(limit)
        return await cursor.to_list(length=limit)

    async def mark_expired(self, max_age_seconds: int) -> int:
        if self.collection is None:
            return 0
        cutoff = datetime.now(timezone.utc).timestamp() - max_age_seconds
        result = await self.collection.update_many(
            {"status": {"$in": ["queued", "probing", "downloading", "transcoding", "sending"]},
             "created_at": {"$lt": datetime.fromtimestamp(cutoff, tz=timezone.utc)}},
            {"$set": {"status": "expired", "error_code": "job_expired", "updated_at": datetime.now(timezone.utc)}}
        )
        return int(result.modified_count)

    async def dead_letter(self, job_id: str, error_code: str, payload: dict[str, Any]) -> None:
        if self.collection is None or self.database is None:
            self._dead_letters.append({
                "job_id": job_id, "error_code": error_code,
                "payload": {key: value for key, value in payload.items() if key != "url"},
                "created_at": datetime.now(timezone.utc),
            })
            return
        method = getattr(self.database, "add_dead_letter", None)
        if method:
            try:
                await method(job_id, error_code, payload)
            except Exception as exc:
                logger.warning(
                    "Dead-letter write failed for %s: %s",
                    job_id,
                    summarize_exception(exc, label="media_dead_letter"),
                )
