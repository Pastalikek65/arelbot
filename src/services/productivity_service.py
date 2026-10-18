"""Telegram-native notes, tasks and reminder persistence."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ProductivityService:
    """Persistence facade with a safe development fallback."""

    # A worker can die after claiming a reminder but before it sends the
    # Telegram message.  The lease makes that reminder eligible again after a
    # bounded period while still preventing concurrent duplicate deliveries.
    REMINDER_CLAIM_TIMEOUT_SECONDS = 300

    def __init__(self, db: Any):
        self.db = db
        self._notes: dict[str, dict[str, Any]] = {}
        self._tasks: dict[str, dict[str, Any]] = {}
        self._reminders: dict[str, dict[str, Any]] = {}

    @property
    def persistent(self) -> bool:
        return bool(getattr(self.db, "available", False))

    async def add_note(self, user_id: int, chat_id: int, text: str, tags: list[str] | None = None) -> dict[str, Any]:
        text = text.strip()[:4000]
        if not text:
            raise ValueError("Not metni bo\u015f olamaz.")
        document = {
            "note_id": uuid4().hex,
            "user_id": user_id,
            "chat_id": chat_id,
            "text": text,
            "tags": tags or [],
            "created_at": _now(),
            "updated_at": _now(),
        }
        if self.persistent:
            await self.db.notes.insert_one(document)
        else:
            self._notes[document["note_id"]] = document
        return document

    async def list_notes(self, user_id: int, query: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        if self.persistent:
            criteria: dict[str, Any] = {"user_id": user_id}
            if query:
                criteria["$text"] = {"$search": query}
            cursor = self.db.notes.find(criteria, {"_id": 0}).sort("updated_at", -1).limit(limit)
            return await cursor.to_list(length=limit)
        values = [note for note in self._notes.values() if note["user_id"] == user_id]
        if query:
            values = [note for note in values if query.casefold() in note["text"].casefold()]
        return sorted(values, key=lambda item: item["updated_at"], reverse=True)[:limit]

    async def export_user_data(self, user_id: int, *, limit: int = 1000) -> dict[str, list[dict[str, Any]]]:
        """Return all personal productivity records in a portable envelope."""
        user_id = int(user_id)
        if self.persistent:
            async def collect(collection: Any) -> list[dict[str, Any]]:
                cursor = collection.find({"user_id": user_id}, {"_id": 0}).limit(limit)
                return await cursor.to_list(length=limit)

            return {
                "notes": await collect(self.db.notes),
                "tasks": await collect(self.db.tasks),
                "reminders": await collect(self.db.reminders),
            }
        return {
            "notes": [dict(item) for item in self._notes.values() if item["user_id"] == user_id][:limit],
            "tasks": [dict(item) for item in self._tasks.values() if item["user_id"] == user_id][:limit],
            "reminders": [dict(item) for item in self._reminders.values() if item["user_id"] == user_id][:limit],
        }

    async def clear_user_data(
        self,
        user_id: int,
        *,
        sections: set[str] | None = None,
    ) -> dict[str, int]:
        """Delete selected productivity sections without crossing user scope."""
        user_id = int(user_id)
        selected = sections or {"notes", "tasks", "reminders"}
        invalid = selected - {"notes", "tasks", "reminders"}
        if invalid:
            raise ValueError("Ge\u00e7ersiz veri b\u00f6l\u00fcm\u00fc.")
        result = {section: 0 for section in ("notes", "tasks", "reminders")}
        if self.persistent:
            collections = {"notes": self.db.notes, "tasks": self.db.tasks, "reminders": self.db.reminders}
            for section in selected:
                deleted = await collections[section].delete_many({"user_id": user_id})
                result[section] = int(deleted.deleted_count)
            return result
        stores = {"notes": self._notes, "tasks": self._tasks, "reminders": self._reminders}
        for section in selected:
            store = stores[section]
            owned = [key for key, item in store.items() if item["user_id"] == user_id]
            for key in owned:
                del store[key]
            result[section] = len(owned)
        return result

    async def delete_note(self, user_id: int, note_id: str) -> bool:
        if self.persistent:
            result = await self.db.notes.delete_one({"user_id": user_id, "note_id": note_id})
            return bool(result.deleted_count)
        note = self._notes.get(note_id)
        if not note or note["user_id"] != user_id:
            return False
        del self._notes[note_id]
        return True

    async def add_task(self, user_id: int, chat_id: int, text: str, priority: str = "normal") -> dict[str, Any]:
        text = text.strip()[:1000]
        if not text:
            raise ValueError("G\u00f6rev metni bo\u015f olamaz.")
        if priority not in {"low", "normal", "high"}:
            priority = "normal"
        document = {
            "task_id": uuid4().hex,
            "user_id": user_id,
            "chat_id": chat_id,
            "text": text,
            "priority": priority,
            "completed": False,
            "created_at": _now(),
            "completed_at": None,
        }
        if self.persistent:
            await self.db.tasks.insert_one(document)
        else:
            self._tasks[document["task_id"]] = document
        return document

    async def list_tasks(self, user_id: int, include_completed: bool = False, limit: int = 30) -> list[dict[str, Any]]:
        if self.persistent:
            criteria: dict[str, Any] = {"user_id": user_id}
            if not include_completed:
                criteria["completed"] = False
            cursor = self.db.tasks.find(criteria, {"_id": 0}).sort("created_at", -1).limit(limit)
            return await cursor.to_list(length=limit)
        values = [task for task in self._tasks.values() if task["user_id"] == user_id]
        if not include_completed:
            values = [task for task in values if not task["completed"]]
        return sorted(values, key=lambda item: item["created_at"], reverse=True)[:limit]

    async def complete_task(self, user_id: int, task_id: str) -> bool:
        if self.persistent:
            result = await self.db.tasks.update_one(
                {"user_id": user_id, "task_id": task_id, "completed": False},
                {"$set": {"completed": True, "completed_at": _now()}},
            )
            return bool(result.modified_count)
        task = self._tasks.get(task_id)
        if not task or task["user_id"] != user_id or task["completed"]:
            return False
        task["completed"] = True
        task["completed_at"] = _now()
        return True

    async def delete_task(self, user_id: int, task_id: str) -> bool:
        if self.persistent:
            result = await self.db.tasks.delete_one({"user_id": user_id, "task_id": task_id})
            return bool(result.deleted_count)
        task = self._tasks.get(task_id)
        if not task or task["user_id"] != user_id:
            return False
        del self._tasks[task_id]
        return True

    async def add_reminder(
        self,
        user_id: int,
        chat_id: int,
        text: str,
        due_at: datetime,
        *,
        recurrence: dict[str, Any] | None = None,
        timezone_name: str = "Europe/Istanbul",
    ) -> dict[str, Any]:
        document = {
            "reminder_id": uuid4().hex,
            "user_id": user_id,
            "chat_id": chat_id,
            "text": text[:1000],
            "due_at": due_at,
            "recurrence": dict(recurrence or {}),
            "timezone": timezone_name,
            "sent": False,
            "created_at": _now(),
        }
        if self.persistent:
            await self.db.reminders.insert_one(document)
        else:
            self._reminders[document["reminder_id"]] = document
        return document

    async def reschedule_reminder(self, reminder_id: str, due_at: datetime) -> bool:
        """Advance a recurring reminder while keeping it pending and claim-safe."""
        update = {
            "due_at": due_at,
            "sent": False,
            "delivery_claimed": False,
            "updated_at": _now(),
        }
        if self.persistent:
            result = await self.db.reminders.update_one(
                {"reminder_id": reminder_id, "sent": False}, {"$set": update, "$unset": {"claimed_at": ""}}
            )
            return bool(result.modified_count)
        reminder = self._reminders.get(reminder_id)
        if not reminder or reminder.get("sent"):
            return False
        reminder.update(update)
        reminder.pop("claimed_at", None)
        return True

    async def list_reminders(self, user_id: int, limit: int = 20) -> list[dict[str, Any]]:
        if self.persistent:
            cursor = self.db.reminders.find({"user_id": user_id, "sent": False}, {"_id": 0}).sort("due_at", 1).limit(limit)
            return await cursor.to_list(length=limit)
        values = [item for item in self._reminders.values() if item["user_id"] == user_id and not item["sent"]]
        return sorted(values, key=lambda item: item["due_at"])[:limit]

    async def daily_summary(
        self,
        user_id: int,
        *,
        days: int = 1,
        timezone_name: str = "Europe/Istanbul",
        now: datetime | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Build a bounded local-time plan without sending Telegram messages.

        Tasks have no due date in the current model, so all open tasks are
        returned as actionable work. Reminders are split into overdue and the
        requested local calendar window. The same typed result is used by the
        command surface and future scheduled daily summaries.
        """
        days = 7 if int(days) > 1 else 1
        try:
            zone = ZoneInfo(str(timezone_name))
        except (KeyError, ValueError):
            zone = ZoneInfo("Europe/Istanbul")
            timezone_name = "Europe/Istanbul"
        current = now or _now()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        current = current.astimezone(timezone.utc)
        local_now = current.astimezone(zone)
        window_end = datetime.combine(
            local_now.date() + timedelta(days=days),
            datetime.min.time(),
            tzinfo=zone,
        ).astimezone(timezone.utc)
        if self.persistent:
            reminder_scope = {"user_id": int(user_id), "sent": False}
            overdue_query = {**reminder_scope, "due_at": {"$lt": current}}
            upcoming_query = {
                **reminder_scope,
                "due_at": {"$gte": current, "$lt": window_end},
            }
            overdue_cursor = self.db.reminders.find(overdue_query, {"_id": 0}).sort("due_at", 1).limit(limit)
            upcoming_cursor = self.db.reminders.find(upcoming_query, {"_id": 0}).sort("due_at", 1).limit(limit)
            overdue, upcoming = await overdue_cursor.to_list(length=limit), await upcoming_cursor.to_list(length=limit)
            overdue_count = int(await self.db.reminders.count_documents(overdue_query))
            upcoming_count = int(await self.db.reminders.count_documents(upcoming_query))
            task_count = int(await self.db.tasks.count_documents({"user_id": int(user_id), "completed": False}))
        else:
            reminders = [
                item for item in self._reminders.values()
                if item["user_id"] == int(user_id) and not item.get("sent")
            ]
            reminders.sort(key=lambda item: item.get("due_at") or current)
            overdue = [item for item in reminders if item.get("due_at") and item["due_at"] < current][:limit]
            upcoming = [
                item for item in reminders
                if item.get("due_at") and current <= item["due_at"] < window_end
            ][:limit]
            overdue_count = sum(1 for item in reminders if item.get("due_at") and item["due_at"] < current)
            upcoming_count = sum(
                1 for item in reminders
                if item.get("due_at") and current <= item["due_at"] < window_end
            )
            task_count = len([item for item in self._tasks.values() if item["user_id"] == int(user_id) and not item["completed"]])
        tasks = await self.list_tasks(user_id, include_completed=False, limit=max(limit, 1))
        return {
            "user_id": int(user_id),
            "timezone": timezone_name,
            "days": days,
            "local_date": local_now.date().isoformat(),
            "window_end": window_end,
            "tasks": tasks[:limit],
            "overdue_reminders": overdue[:limit],
            "upcoming_reminders": upcoming[:limit],
            "open_task_count": task_count,
            "overdue_count": overdue_count,
            "upcoming_count": upcoming_count,
            "tasks_truncated": task_count > len(tasks),
            "overdue_truncated": overdue_count > len(overdue),
            "upcoming_truncated": upcoming_count > len(upcoming),
        }

    async def pending_reminders(self, limit: int = 1000) -> list[dict[str, Any]]:
        if self.persistent:
            cursor = self.db.reminders.find({"sent": False}).sort("due_at", 1).limit(limit)
            return await cursor.to_list(length=limit)
        values = [item for item in self._reminders.values() if not item["sent"]]
        return sorted(values, key=lambda item: item["due_at"])[:limit]

    async def mark_reminder_sent(self, reminder_id: str) -> None:
        if self.persistent:
            await self.db.reminders.update_one(
                {"reminder_id": reminder_id},
                {
                    "$set": {"sent": True, "delivery_claimed": True, "sent_at": _now()},
                    "$unset": {"claimed_at": ""},
                },
            )
            return
        if reminder_id in self._reminders:
            self._reminders[reminder_id]["sent"] = True
            self._reminders[reminder_id].pop("claimed_at", None)

    async def claim_reminder(
        self,
        reminder_id: str,
        claim_timeout_seconds: int = REMINDER_CLAIM_TIMEOUT_SECONDS,
    ) -> bool:
        """Claim one reminder delivery with a recoverable lease.

        The atomic MongoDB update prevents two workers from claiming the same
        reminder.  An expired claim is intentionally reclaimable so a process
        crash cannot leave a reminder permanently stuck after restart.
        """
        now = _now()
        claim_cutoff = now - timedelta(seconds=max(30, int(claim_timeout_seconds)))
        claim_filter = {
            "$or": [
                {"delivery_claimed": {"$ne": True}},
                {"claimed_at": {"$lte": claim_cutoff}},
                {"claimed_at": {"$exists": False}},
            ]
        }
        if self.persistent:
            result = await self.db.reminders.update_one(
                {"reminder_id": reminder_id, "sent": False, **claim_filter},
                {"$set": {"delivery_claimed": True, "claimed_at": now}},
            )
            return bool(result.modified_count)
        reminder = self._reminders.get(reminder_id)
        claim_expired = bool(reminder and reminder.get("claimed_at") and reminder["claimed_at"] <= claim_cutoff)
        if not reminder or reminder.get("sent") or (reminder.get("delivery_claimed") and not claim_expired):
            return False
        reminder["delivery_claimed"] = True
        reminder["claimed_at"] = now
        return True

    async def release_reminder(self, reminder_id: str) -> None:
        """Release a claim when Telegram delivery fails so the job can retry."""
        if self.persistent:
            await self.db.reminders.update_one(
                {"reminder_id": reminder_id, "sent": False},
                {"$set": {"delivery_claimed": False}, "$unset": {"claimed_at": ""}},
            )
            return
        if reminder_id in self._reminders and not self._reminders[reminder_id].get("sent"):
            self._reminders[reminder_id]["delivery_claimed"] = False
            self._reminders[reminder_id].pop("claimed_at", None)

    async def delete_reminder(self, user_id: int, reminder_id: str) -> bool:
        """Cancel an unsent reminder without affecting another user's record."""
        if self.persistent:
            result = await self.db.reminders.delete_one(
                {"user_id": user_id, "reminder_id": reminder_id, "sent": False}
            )
            return bool(result.deleted_count)
        reminder = self._reminders.get(reminder_id)
        if not reminder or reminder["user_id"] != user_id or reminder.get("sent"):
            return False
        del self._reminders[reminder_id]
        return True
