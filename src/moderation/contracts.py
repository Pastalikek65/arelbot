"""Framework-neutral contracts for moderation decisions and enforcement.

Telegram update parsing and provider integrations should translate into these
bounded value objects before any moderation action is executed.  The contracts
intentionally contain transport identifiers and policy metadata only; raw
messages are never required by the action executor.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Mapping
from uuid import uuid4


ModerationActionName = Literal[
    "delete_message",
    "mute",
    "unmute",
    "temp_mute",
    "kick",
    "ban",
    "temp_ban",
    "unban",
    "warn",
    "pin",
    "unpin",
    "promote",
    "demote",
    "captcha_quarantine",
    "log",
]

_ACTION_NAMES = frozenset(
    {
        "delete_message",
        "mute",
        "unmute",
        "temp_mute",
        "kick",
        "ban",
        "temp_ban",
        "unban",
        "warn",
        "pin",
        "unpin",
        "promote",
        "demote",
        "captcha_quarantine",
        "log",
    }
)
_ACTION_ALIASES = {
    # Existing local moderation engine names are accepted at the boundary and
    # immediately normalized to the typed action vocabulary.
    "delete": "delete_message",
    "restrict": "mute",
    "temporary_mute": "temp_mute",
    "quarantine": "captcha_quarantine",
}
_MESSAGE_ACTIONS = frozenset({"delete_message", "pin", "unpin"})
_USER_ACTIONS = frozenset(
    {
        "mute",
        "unmute",
        "temp_mute",
        "kick",
        "ban",
        "temp_ban",
        "unban",
        "warn",
        "promote",
        "demote",
        "captcha_quarantine",
    }
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _require_int(value: Any, name: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name}_must_be_integer")
    if positive and value <= 0:
        raise ValueError(f"{name}_must_be_positive")
    return value


def _bounded_text(value: Any, name: str, *, limit: int = 4096, required: bool = False) -> str:
    if value is None:
        if required:
            raise ValueError(f"{name}_required")
        return ""
    if not isinstance(value, str):
        raise TypeError(f"{name}_must_be_string")
    if len(value) > limit:
        raise ValueError(f"{name}_too_long")
    if any(ord(char) < 9 or (13 < ord(char) < 32) for char in value):
        raise ValueError(f"{name}_contains_control_character")
    return value


@dataclass(frozen=True, slots=True)
class ModerationEvent:
    """A bounded message/member event supplied to a moderation provider."""

    chat_id: int
    message_id: int
    user_id: int
    event_kind: str
    text: str = ""
    caption: str | None = None
    attachment_type: str | None = None
    attachment_mime: str | None = None
    attachment_id: str | None = None
    sticker_set_name: str | None = None
    is_edited: bool = False
    thread_id: int | None = None
    update_id: int | None = None
    is_admin: bool = False
    settings_version: int = 1
    created_at: datetime = field(default_factory=_utc_now)
    correlation_id: str = field(default_factory=lambda: uuid4().hex)

    def __post_init__(self) -> None:
        _require_int(self.chat_id, "chat_id")
        _require_int(self.message_id, "message_id", positive=True)
        _require_int(self.user_id, "user_id", positive=True)
        if self.thread_id is not None:
            _require_int(self.thread_id, "thread_id", positive=True)
        if self.update_id is not None:
            _require_int(self.update_id, "update_id", positive=True)
        if not isinstance(self.event_kind, str) or not self.event_kind.strip():
            raise ValueError("event_kind_required")
        if len(self.event_kind) > 64:
            raise ValueError("event_kind_too_long")
        _bounded_text(self.text, "text")
        if self.caption is not None:
            _bounded_text(self.caption, "caption")
        for value, name, limit in (
            (self.attachment_type, "attachment_type", 64),
            (self.attachment_mime, "attachment_mime", 128),
            (self.attachment_id, "attachment_id", 256),
            (self.sticker_set_name, "sticker_set_name", 128),
            (self.correlation_id, "correlation_id", 128),
        ):
            if value is not None and (not isinstance(value, str) or len(value) > limit):
                raise ValueError(f"{name}_invalid")
        if not isinstance(self.settings_version, int) or self.settings_version < 1:
            raise ValueError("settings_version_invalid")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at_must_be_timezone_aware")
        if not isinstance(self.is_edited, bool):
            raise TypeError("is_edited_must_be_boolean")


@dataclass(frozen=True, slots=True)
class ModerationDecision:
    """Normalized provider decision consumed by policy and enforcement."""

    allowed: bool
    action: str = "none"
    reason: str | None = None
    rule_ids: tuple[str, ...] = ()
    severity: int = 0
    confidence: float | None = None
    strike_count: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.allowed, bool):
            raise TypeError("allowed_must_be_boolean")
        action = str(self.action or "none").strip().casefold()
        if action != "none" and action not in _ACTION_NAMES:
            raise ValueError("action_invalid")
        object.__setattr__(self, "action", action)
        if self.reason is not None:
            _bounded_text(self.reason, "reason", limit=128)
        normalized_rules = tuple(dict.fromkeys(str(item)[:96] for item in (self.rule_ids or ()) if str(item).strip()))
        object.__setattr__(self, "rule_ids", normalized_rules)
        if isinstance(self.severity, bool) or not isinstance(self.severity, int) or not 0 <= self.severity <= 5:
            raise ValueError("severity_out_of_range")
        if self.confidence is not None and not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("confidence_out_of_range")
        if isinstance(self.strike_count, bool) or not isinstance(self.strike_count, int) or self.strike_count < 0:
            raise ValueError("strike_count_invalid")
        if not isinstance(self.metadata, Mapping):
            raise TypeError("metadata_must_be_mapping")


@dataclass(frozen=True, slots=True)
class ModerationAction:
    """A single idempotent, authorized Telegram moderation operation."""

    action: ModerationActionName | str
    chat_id: int
    actor_id: int
    target_user_id: int | None = None
    target_message_id: int | None = None
    thread_id: int | None = None
    reason: str | None = None
    duration_seconds: int | None = None
    correlation_id: str | None = None
    idempotency_key: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        action = str(self.action or "").strip().casefold()
        action = _ACTION_ALIASES.get(action, action)
        if action not in _ACTION_NAMES:
            raise ValueError("action_invalid")
        object.__setattr__(self, "action", action)
        _require_int(self.chat_id, "chat_id")
        _require_int(self.actor_id, "actor_id", positive=True)
        if self.target_user_id is not None:
            _require_int(self.target_user_id, "target_user_id", positive=True)
        if self.target_message_id is not None:
            _require_int(self.target_message_id, "target_message_id", positive=True)
        if self.thread_id is not None:
            _require_int(self.thread_id, "thread_id", positive=True)
        if action in _MESSAGE_ACTIONS and self.target_message_id is None:
            raise ValueError("target_message_required")
        if action in _USER_ACTIONS and self.target_user_id is None:
            raise ValueError("target_user_required")
        if action == "temp_mute":
            if self.duration_seconds is None or isinstance(self.duration_seconds, bool):
                raise ValueError("duration_required")
            if not 1 <= int(self.duration_seconds) <= 28 * 24 * 60 * 60:
                raise ValueError("duration_out_of_range")
        elif self.duration_seconds is not None:
            if isinstance(self.duration_seconds, bool) or not 1 <= int(self.duration_seconds) <= 28 * 24 * 60 * 60:
                raise ValueError("duration_out_of_range")
        if self.reason is not None:
            _bounded_text(self.reason, "reason", limit=128)
        for value, name, limit in (
            (self.correlation_id, "correlation_id", 128),
            (self.idempotency_key, "idempotency_key", 128),
        ):
            if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > limit):
                raise ValueError(f"{name}_invalid")
        if not isinstance(self.metadata, Mapping):
            raise TypeError("metadata_must_be_mapping")
        if self.idempotency_key is None:
            raw = "|".join(
                str(value or "")
                for value in (
                    action,
                    self.chat_id,
                    self.thread_id,
                    self.target_user_id,
                    self.target_message_id,
                    self.reason,
                    self.duration_seconds,
                    self.correlation_id
                    or (self.metadata.get("generation_id") if isinstance(self.metadata, Mapping) else ""),
                )
            )
            object.__setattr__(self, "idempotency_key", f"mod:{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:64]}")


@dataclass(frozen=True, slots=True)
class ActionResult:
    """Safe result returned by the executor; exception details never escape."""

    status: Literal["sent", "skipped", "rejected", "failed", "uncertain"]
    action_key: str
    error_code: str | None = None
    error_detail: str | None = None
    telegram_message_id: int | None = None

    @property
    def ok(self) -> bool:
        return self.status == "sent"


__all__ = [
    "ActionResult",
    "ModerationAction",
    "ModerationActionName",
    "ModerationDecision",
    "ModerationEvent",
]
