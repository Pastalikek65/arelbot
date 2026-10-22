"""Persistent, idempotent moderation decisions around the low-latency engine."""

from __future__ import annotations

import logging
import html
import inspect
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from typing import Any, Awaitable, Callable

from .engine import ModerationDecision, ModerationEngine
from .contracts import ModerationEvent

logger = logging.getLogger(__name__)


class ModerationService:
    """Keep the fast in-process decision and durable strike history separate."""

    def __init__(
        self,
        database: Any,
        engine: ModerationEngine | None = None,
        *,
        window_seconds: int = 600,
        guardian_adapter: Any | None = None,
        member_admin_checker: Callable[[int, int], bool | Awaitable[bool]] | None = None,
    ):
        self.database = database
        self.engine = engine or ModerationEngine(window_seconds=window_seconds)
        self.window_seconds = max(60, int(window_seconds))
        self.guardian_adapter = guardian_adapter
        self.member_admin_checker = member_admin_checker

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

    async def evaluate_event(
        self,
        event: ModerationEvent,
        settings: dict | None = None,
    ) -> ModerationDecision:
        """Evaluate any Telegram message kind through the common event contract.

        Captions are policy input for photos, GIFs and documents; attachment
        metadata itself is never passed to the text policy engine.  The event's
        stable chat/message identity makes Telegram retries idempotent.
        """
        if not isinstance(event, ModerationEvent):
            raise TypeError("moderation_event_required")
        if not event.is_admin and self.member_admin_checker is not None:
            try:
                checked = self.member_admin_checker(event.chat_id, event.user_id)
                if inspect.isawaitable(checked):
                    checked = await checked
                if bool(checked):
                    event = replace(event, is_admin=True)
            except Exception:
                logger.warning("Could not resolve moderation member role")
        if self.guardian_adapter is not None and not event.is_admin:
            try:
                from .guardian_protocol import GuardianEvent

                guardian_event = GuardianEvent(
                    chat_id=event.chat_id,
                    user_id=event.user_id,
                    message_id=event.message_id,
                    thread_id=event.thread_id,
                    event_kind=event.event_kind,
                    text=event.text or None,
                    caption=event.caption,
                    attachment_type=event.attachment_type,
                    attachment_mime=event.attachment_mime,
                    is_admin=event.is_admin,
                    settings_version=event.settings_version,
                    correlation_id=event.correlation_id,
                )
                guardian_result = await self.guardian_adapter.evaluate(guardian_event)
                await self._record_guardian_observation(event, guardian_result)
                if bool(getattr(guardian_result, "enforcement_required", False)) or (
                    bool(getattr(guardian_result, "enforce", False))
                    and str(getattr(guardian_result, "decision", "")).casefold() in {"deny", "unavailable"}
                ):
                    actions = tuple(getattr(guardian_result, "actions", ()) or ())
                    if len(actions) != 1:
                        logger.warning("Guardian returned an unsupported action plan size=%s", len(actions))
                        actions = ()
                    action = actions[0] if actions else ""
                    action = {"quarantine": "mute", "kick": "ban", "delete": "delete"}.get(action, action)
                    if action in {"delete", "mute", "ban"}:
                        return ModerationDecision(
                            False,
                            str(getattr(guardian_result, "reason_code", None) or "guardian_rule")[:128],
                            str(action),
                        )
            except Exception as exc:
                logger.warning("Guardian moderation evaluation unavailable: type=%s", type(exc).__name__[:64])
        content = "\n".join(part for part in (event.text, event.caption or "") if part)
        event_id = f"telegram:{event.chat_id}:{event.message_id}"
        return await self.evaluate(
            event.chat_id,
            event.user_id,
            content[:8192],
            settings,
            event.is_admin,
            event_id=event_id,
        )

    async def _record_guardian_observation(self, event: ModerationEvent, result: Any) -> None:
        recorder = getattr(self.database, "record_moderation_provider_event", None)
        if recorder is None:
            return
        try:
            await recorder(
                request_id=f"guardian:{event.chat_id}:{event.message_id}",
                provider=str(getattr(result, "provider", None) or "kek_guardian")[:64],
                decision=str(getattr(result, "decision", None) or "unavailable")[:32],
                error_code=str(getattr(result, "error_code", None) or "")[:96] or None,
                latency_ms=getattr(result, "latency_ms", None),
                enforced=bool(getattr(result, "enforce", False)),
            )
        except Exception:
            logger.warning("Could not persist Guardian provider observation")

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
            "🛡 <b>Moderasyon özeti</b>",
            f"<i>Son {data['days']} gün · içerik saklanmadan yalnızca ihlal sayıları</i>",
            "",
            f"İhlal: <b>{int(data.get('total', 0))}</b> · Etkilenen kullanıcı: <b>{int(data.get('active_users', 0))}</b>",
        ]
        reasons = data.get("by_reason") or []
        if reasons:
            lines.extend(["", "<b>Neden dağılımı</b>"])
            labels = {
                "flood": "Flood",
                "link_filter": "Bağlantı filtresi",
                "keyword_filter": "Kelime filtresi",
                "repeat_filter": "Tekrar filtresi",
            }
            for row in reasons[:10]:
                reason = labels.get(str(row.get("reason")), str(row.get("reason")))
                lines.append(f"• {html.escape(reason[:64])}: <b>{int(row.get('count', 0))}</b>")
        users = data.get("top_users") or []
        if users:
            lines.extend(["", "<b>En çok ihlal alan kullanıcılar</b>"])
            for index, row in enumerate(users[:10], 1):
                name = html.escape(str(row.get("name") or "Bilinmeyen")[:80])
                lines.append(f"{index}. {name} · <b>{int(row.get('count', 0))}</b>")
        if not reasons and not users:
            lines.extend(["", "✅ Bu dönemde kayıtlı ihlal yok. Moderasyon motoru bugünlük dedikodu üretmedi."])
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
