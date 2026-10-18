"""Validation and target selection for structured group replies."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import uuid4

from .models import GroupMessageRef, GroupReplyPlan, GroupTurnContext, ReplySegment


class MetricsSink(Protocol):
    """Small injected metric interface with no user-controlled labels."""

    def increment(self, name: str, value: int = 1) -> None: ...

    def inc(self, name: str, value: int = 1) -> None: ...


class GroupReplyRouter:
    """Select recent targets and validate the model's structured reply plan.

    The router deliberately does not infer a target from a username, display
    name, mention, or prose.  A model can target only IDs already selected by
    the application from the Telegram update stream.
    """

    _METRIC_NAMES = frozenset(
        {
            "group_reply_candidates_selected",
            "group_reply_requests_total",
            "group_reply_split_total",
            "group_reply_plan_rejected",
            "group_reply_validation_reject_total",
            "group_reply_unknown_target_total",
            "group_reply_segments_created",
            "group_reply_segments_total",
            "group_reply_fallback_created",
            "group_reply_reply_fallback_total",
        }
    )
    _METRIC_ALIASES = {
        "group_reply_candidates_selected": "group_reply_split_total",
        "group_reply_plan_rejected": "group_reply_validation_reject_total",
        "group_reply_segments_created": "group_reply_segments_total",
        "group_reply_fallback_created": "group_reply_reply_fallback_total",
    }
    _LABEL_RE = re.compile(
        r"(?im)^\s*(?:@[a-z0-9_\.]{2,}|[a-zçğıöşü][a-zçğıöşü0-9]*(?:\s+[a-zçğıöşü][a-zçğıöşü0-9]*){0,3})\s*:"
    )
    _MENTION_RE = re.compile(r"(?<!\w)@[a-z0-9_]{2,}", re.IGNORECASE)

    def __init__(self, *, metrics: MetricsSink | None = None) -> None:
        self._metrics = metrics

    def select_candidates(
        self,
        context: GroupTurnContext,
        *,
        now: datetime | None = None,
    ) -> tuple[GroupMessageRef, ...]:
        """Return newest eligible message per user within the scoped window.

        The current message's timestamp is used by default.  A small future
        clock skew is tolerated, while messages older than the context window
        are rejected.  Results are ordered newest first and bounded by the
        context's maximum segment count.
        """

        reference_now = now or datetime.now(timezone.utc)
        if reference_now.tzinfo is None or reference_now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        thread_id = context.current_message.thread_id
        chat_id = context.current_message.chat_id
        unique: dict[int, GroupMessageRef] = {}
        for item in (context.current_message, *context.candidates):
            if item.chat_id != chat_id or item.thread_id != thread_id or not item.is_replyable_text:
                continue
            age = (reference_now - item.created_at).total_seconds()
            # Telegram event timestamps can differ by a few milliseconds when
            # updates are assembled; future messages beyond 5 seconds are not
            # part of this turn.
            if age < -5 or age > context.window_seconds:
                continue
            previous = unique.get(item.user_id)
            if previous is None or item.created_at > previous.created_at:
                unique[item.user_id] = item

        selected = tuple(
            sorted(unique.values(), key=lambda item: (item.created_at, item.message_id), reverse=True)[
                : context.max_segments
            ]
        )
        self._emit("group_reply_candidates_selected", len(selected))
        return selected

    def build_plan(
        self,
        context: GroupTurnContext,
        model_response: str | bytes | Mapping[str, Any] | Any,
        *,
        safe_fallback_text: str | None = None,
        now: datetime | None = None,
    ) -> GroupReplyPlan:
        """Parse and validate a model response against selected message IDs.

        Any malformed or unsafe plan is rejected as a whole.  If a trusted,
        internally generated fallback string is supplied, only the current
        trigger receives that fallback reply.
        """

        correlation_id = uuid4().hex
        self._emit("group_reply_requests_total")
        selected = self.select_candidates(context, now=now)
        by_id = {item.message_id: item for item in selected}
        all_by_id = {item.message_id: item for item in (context.current_message, *context.candidates)}

        if context.current_message.message_id not in by_id:
            return self._reject_or_fallback(
                context,
                "current_target_not_eligible",
                correlation_id,
                safe_fallback_text,
            )

        payload, parse_reason = self._decode(model_response)
        if parse_reason is not None:
            return self._reject_or_fallback(context, parse_reason, correlation_id, safe_fallback_text)
        if not isinstance(payload, Mapping) or not isinstance(payload.get("replies"), list):
            return self._reject_or_fallback(context, "malformed_response", correlation_id, safe_fallback_text)

        replies = payload["replies"]
        if len(replies) > context.max_segments:
            return self._reject_or_fallback(context, "too_many_replies", correlation_id, safe_fallback_text)

        segments: list[ReplySegment] = []
        seen: set[int] = set()
        for item in replies:
            if not isinstance(item, Mapping):
                return self._reject_or_fallback(context, "malformed_response", correlation_id, safe_fallback_text)
            target_id = item.get("target_message_id")
            text = item.get("text")
            if isinstance(target_id, bool) or not isinstance(target_id, int) or not isinstance(text, str):
                return self._reject_or_fallback(context, "malformed_response", correlation_id, safe_fallback_text)
            if target_id in seen:
                return self._reject_or_fallback(context, "duplicate_target", correlation_id, safe_fallback_text)
            seen.add(target_id)
            if target_id not in by_id:
                if target_id in all_by_id:
                    return self._reject_or_fallback(context, "stale_target", correlation_id, safe_fallback_text)
                self._emit("group_reply_unknown_target_total")
                return self._reject_or_fallback(context, "unknown_target", correlation_id, safe_fallback_text)
            if not self._safe_text(text):
                return self._reject_or_fallback(context, "malformed_response", correlation_id, safe_fallback_text)
            if self._looks_like_multi_address(text, by_id.values()):
                return self._reject_or_fallback(context, "multi_address_prose", correlation_id, safe_fallback_text)
            segments.append(ReplySegment(target_message=by_id[target_id], text=text.strip()))

        self._emit("group_reply_segments_created", len(segments))
        return GroupReplyPlan(tuple(segments), None, correlation_id)

    def _decode(self, model_response: Any) -> tuple[Any, str | None]:
        if isinstance(model_response, bytes):
            try:
                model_response = model_response.decode("utf-8")
            except UnicodeDecodeError:
                return None, "malformed_json"
        if isinstance(model_response, str):
            try:
                return json.loads(model_response), None
            except (json.JSONDecodeError, TypeError):
                return None, "malformed_json"
        if isinstance(model_response, Mapping):
            return model_response, None
        return None, "malformed_response"

    def _reject_or_fallback(
        self,
        context: GroupTurnContext,
        reason: str,
        correlation_id: str,
        safe_fallback_text: str | None,
    ) -> GroupReplyPlan:
        self._emit("group_reply_plan_rejected")
        if self._safe_text(safe_fallback_text or "") and self._looks_like_multi_address(
            safe_fallback_text or "", (context.current_message,)
        ):
            safe_fallback_text = None
        if safe_fallback_text and self._safe_text(safe_fallback_text):
            segment = ReplySegment(context.current_message, safe_fallback_text.strip())
            self._emit("group_reply_fallback_created")
            return GroupReplyPlan((segment,), reason, correlation_id)
        return GroupReplyPlan((), reason, correlation_id)

    @staticmethod
    def _safe_text(value: str) -> bool:
        if not isinstance(value, str) or not value.strip() or len(value) > 4096:
            return False
        return not any(ord(char) < 9 or (13 < ord(char) < 32) for char in value)

    def _looks_like_multi_address(self, text: str, candidates: Any) -> bool:
        labels = self._LABEL_RE.findall(text)
        if len(labels) >= 2:
            return True
        lowered = text.casefold()
        for candidate in candidates:
            name = candidate.display_name.strip().casefold()
            if name and re.search(rf"(?<!\w){re.escape(name)}\s*:", lowered):
                return True
        return len(self._MENTION_RE.findall(text)) >= 2

    def _emit(self, name: str, value: int = 1) -> None:
        if self._metrics is not None and name in self._METRIC_NAMES:
            # ``MetricsRegistry`` uses ``inc`` while the small protocol used
            # by replay tests historically exposed ``increment``.  Support
            # both so metrics cannot break a live group reply after the AI
            # response has already been produced.
            emit = getattr(self._metrics, "increment", None) or getattr(self._metrics, "inc", None)
            if not callable(emit):
                return
            emit(name, value)
            alias = self._METRIC_ALIASES.get(name)
            if alias:
                emit(alias, value)

