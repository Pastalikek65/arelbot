"""Persistent, idempotent moderation decisions around the low-latency engine."""

from __future__ import annotations

import logging
import html
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from typing import Any

from .engine import ModerationDecision, ModerationEngine

logger = logging.getLogger(__name__)


class ModerationService:
    """Keep the fast in-process decision and durable strike history separate."""

    def __init__(self, database: Any, engine: ModerationEngine | None = None, *, window_seconds: int = 600):
        self.database = database
        self.engine = engine or ModerationEngine(window_seconds=window_seconds)
        self.window_seconds = max(60, int(window_seconds))

    async def evaluate(
        self,
        chat_id: int,
        user_id: int,
        text: str,
        settings: dict | None = None,
        is_admin: bool = False,
        *,
        event_id: str | None = None,
    ) -> ModerationDecision:
        settings = settings or {}
        decision = self.engine.evaluate(chat_id, user_id, text, settings, is_admin)
        if decision.allowed or not event_id or is_admin:
            return decision

        try:
            strike_count = await self.database.record_moderation_violation(
                event_id=event_id,
                chat_id=chat_id,
                user_id=user_id,
                reason=decision.reason or "unknown",
                window_seconds=self.window_seconds,
            )
        except Exception:
            # A moderation decision must remain enforceable even while the
            # audit store is temporarily unavailable; production health still
            # exposes the persistence failure through normal Mongo metrics.
            logger.warning("Could not persist moderation violation", exc_info=True)
            return decision

        action = decision.action
        if decision.reason == "flood" and settings.get("auto_ban") and strike_count >= 3:
            action = "ban"
        audit = getattr(self.database, "add_audit_log", None)
        if audit is not None:
            try:
                await audit(
                    int(user_id),
                    "moderation_violation",
                    chat_id=int(chat_id),
                    reason=str(decision.reason or "unknown")[:64],
                    strike_count=int(strike_count),
                    enforcement_action=action,
                )
            except Exception:
                logger.warning("Could not persist moderation audit", exc_info=True)
        return replace(decision, action=action, strike_count=int(strike_count), persistent=bool(strike_count))

    async def summary(self, chat_id: int, *, days: int = 7, limit: int = 10) -> dict[str, Any]:
        """Return a bounded, content-free moderation report for group admins."""
        if not isinstance(chat_id, int) or chat_id >= 0:
            raise ValueError("group_chat_required")
        if int(days) not in {1, 7, 30}:
            raise ValueError("days_out_of_range")
        since = datetime.now(timezone.utc) - timedelta(days=int(days))
        getter = getattr(self.database, "get_moderation_summary", None)
        if getter is None:
            result = {"total": 0, "active_users": 0, "by_reason": [], "top_users": []}
        else:
            result = await getter(chat_id, since=since, limit=max(1, min(int(limit), 20)))
        return {"chat_id": chat_id, "days": int(days), "since": since, **result}

    async def render_summary(self, chat_id: int, *, days: int = 7) -> str:
        data = await self.summary(chat_id, days=days)
        lines = [
            "ğŸ›¡ <b>Moderasyon Ã¶zeti</b>",
            f"<i>Son {data['days']} gÃ¼n Â· iÃ§erik saklanmadan yalnÄ±zca ihlal sayÄ±larÄ±</i>",
            "",
            f"Ä°hlal: <b>{int(data.get('total', 0))}</b> Â· Etkilenen kullanÄ±cÄ±: <b>{int(data.get('active_users', 0))}</b>",
        ]
        reasons = data.get("by_reason") or []
        if reasons:
            lines.extend(["", "<b>Neden daÄŸÄ±lÄ±mÄ±</b>"])
            labels = {
                "flood": "Flood",
                "link_filter": "BaÄŸlantÄ± filtresi",
                "keyword_filter": "Kelime filtresi",
                "repeat_filter": "Tekrar filtresi",
            }
            for row in reasons[:10]:
                reason = labels.get(str(row.get("reason")), str(row.get("reason")))
                lines.append(f"â€¢ {html.escape(reason[:64])}: <b>{int(row.get('count', 0))}</b>")
        users = data.get("top_users") or []
        if users:
            lines.extend(["", "<b>En Ã§ok ihlal alan kullanÄ±cÄ±lar</b>"])
            for index, row in enumerate(users[:10], 1):
                name = html.escape(str(row.get("name") or "Bilinmeyen")[:80])
                lines.append(f"{index}. {name} Â· <b>{int(row.get('count', 0))}</b>")
        if not reasons and not users:
            lines.extend(["", "âœ… Bu dÃ¶nemde kayÄ±tlÄ± ihlal yok. Moderasyon motoru bugÃ¼nlÃ¼k dedikodu Ã¼retmedi."])
        return "\n".join(lines)

    async def clear_strikes(self, chat_id: int, user_id: int, *, actor_id: int) -> int:
        """Clear a user's retained strikes and write a minimal audit event."""
        if not isinstance(chat_id, int) or chat_id >= 0:
            raise ValueError("group_chat_required")
        if not isinstance(user_id, int) or not isinstance(actor_id, int):
            raise ValueError("user_id_invalid")
        clearer = getattr(self.database, "clear_moderation_strikes", None)
        deleted = int(await clearer(chat_id, user_id)) if clearer is not None else 0
        audit = getattr(self.database, "add_audit_log", None)
        if audit is not None:
            await audit(
                actor_id,
                "moderation_strikes_cleared",
                chat_id=int(chat_id),
                target_user_id=int(user_id),
                deleted_count=deleted,
            )
        return deleted
