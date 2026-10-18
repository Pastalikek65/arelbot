"""Telegram delivery boundary for validated group reply plans."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from src.group_replies import GroupReplyPlan
from src.security.output_sanitizer import render_model_html, render_model_plain_text
from src.storage.group_reply_store import ReplyDeliveryLedger


@dataclass(frozen=True, slots=True)
class GroupReplyDeliveryResult:
    """Bounded outcome of one validated plan."""

    sent_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    sent_message_ids: tuple[int, ...] = field(default_factory=tuple)
    sent_target_message_ids: tuple[int, ...] = field(default_factory=tuple)
    failed_target_message_ids: tuple[int, ...] = field(default_factory=tuple)
    missing_target_message_ids: tuple[int, ...] = field(default_factory=tuple)


class GroupReplyDeliveryService:
    """Send each validated segment once, replying to its exact source message."""

    FEATURE = "group_targeted_reply"

    def __init__(self, *, ledger: ReplyDeliveryLedger, metrics: Any | None = None) -> None:
        self.ledger = ledger
        self.metrics = metrics

    async def deliver(
        self,
        *,
        bot: Any,
        plan: GroupReplyPlan,
        generation_id: str,
    ) -> GroupReplyDeliveryResult:
        sent_ids: list[int] = []
        sent_target_ids: list[int] = []
        skipped = 0
        failed = 0
        failed_target_ids: list[int] = []
        missing_target_ids: list[int] = []
        started = time.perf_counter()
        for segment in plan.segments:
            target = segment.target_message
            key = self.ledger.make_key(
                target.chat_id,
                target.thread_id,
                target.message_id,
                feature=self.FEATURE,
                generation_id=generation_id,
            )
            if not await self.ledger.claim(
                key,
                chat_id=target.chat_id,
                target_message_id=target.message_id,
                generation_id=generation_id,
                thread_id=target.thread_id,
                feature=self.FEATURE,
            ):
                skipped += 1
                self._inc("group_reply_duplicate_skipped_total")
                continue
            if not await self.ledger.begin_send(key):
                skipped += 1
                self._inc("group_reply_claim_lost_total")
                continue
            kwargs = {
                "chat_id": target.chat_id,
                "text": render_model_html(segment.text),
                "parse_mode": "HTML",
                "reply_to_message_id": target.message_id,
            }
            if target.thread_id is not None:
                kwargs["message_thread_id"] = target.thread_id
            try:
                try:
                    sent = await bot.send_message(**kwargs)
                except Exception as html_error:
                    if "parse" not in str(html_error).casefold() and "entity" not in str(html_error).casefold():
                        raise
                    kwargs["text"] = render_model_plain_text(segment.text)
                    kwargs["parse_mode"] = None
                    sent = await bot.send_message(**kwargs)
                sent_id = getattr(sent, "message_id", None)
                await self.ledger.mark_sent(key, sent_id)
                sent_ids.append(int(sent_id)) if sent_id is not None else None
                sent_target_ids.append(int(target.message_id))
                self._inc("group_reply_segments_total")
            except Exception as exc:
                error_text = str(exc).casefold()
                failed_target_ids.append(int(target.message_id))
                if any(marker in error_text for marker in (
                    "message to reply not found",
                    "message to be replied not found",
                    "message not found",
                    "message_id_invalid",
                    "reply message not found",
                )):
                    missing_target_ids.append(int(target.message_id))
                    await self.ledger.mark_skipped(key, "target_message_missing")
                    self._inc("group_reply_target_missing_total")
                else:
                    failed += 1
                    await self.ledger.mark_uncertain(key, "telegram_delivery_error")
                    self._inc("group_reply_send_failures_total")
        self._observe("group_reply_send_seconds", time.perf_counter() - started)
        return GroupReplyDeliveryResult(
            sent_count=len(sent_ids),
            skipped_count=skipped,
            failed_count=failed,
            sent_message_ids=tuple(sent_ids),
            sent_target_message_ids=tuple(sent_target_ids),
            failed_target_message_ids=tuple(failed_target_ids),
            missing_target_message_ids=tuple(missing_target_ids),
        )

    def _inc(self, name: str) -> None:
        if self.metrics is None:
            return
        increment = getattr(self.metrics, "inc", None) or getattr(self.metrics, "increment", None)
        if increment is not None:
            increment(name)

    def _observe(self, name: str, seconds: float) -> None:
        if self.metrics is None:
            return
        observe = getattr(self.metrics, "observe", None)
        if observe is not None:
            observe(name, seconds)


__all__ = ["GroupReplyDeliveryResult", "GroupReplyDeliveryService"]
