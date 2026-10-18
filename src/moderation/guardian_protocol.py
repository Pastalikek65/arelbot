"""Bounded, authenticated-by-peer protocol primitives for Kek Guardian.

The bridge deliberately uses a small newline-delimited JSON envelope.  It is
not a general RPC channel: unknown fields, oversized payloads, stale requests,
replays, and unbounded event values are rejected before a provider can inspect
the event.  The Unix socket's OS credentials are an additional trust boundary;
the caller must configure an explicit UID/GID allow-list in production.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import socket
import struct
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any, Mapping
from uuid import uuid4


PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 16 * 1024
MAX_EVENT_TEXT_CHARS = 4096
MAX_EVENT_FIELD_CHARS = 256
MAX_ACTIONS = 8
MAX_RULE_IDS = 32
MAX_REQUEST_TTL_SECONDS = 30
MAX_CLOCK_SKEW_SECONDS = 30

_REQUEST_FIELDS = frozenset(
    {"version", "request_id", "issued_at", "expires_at", "nonce", "operation", "event"}
)
_EVENT_FIELDS = frozenset(
    {
        "chat_id",
        "thread_id",
        "message_id",
        "user_id",
        "event_kind",
        "text",
        "caption",
        "attachment_type",
        "attachment_mime",
        "is_admin",
        "settings_version",
        "correlation_id",
    }
)
_RESPONSE_FIELDS = frozenset(
    {
        "version",
        "request_id",
        "provider",
        "decision",
        "actions",
        "rule_ids",
        "reason_code",
        "expires_at",
    }
)
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_SAFE_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_DECISIONS = frozenset({"allow", "deny", "indeterminate", "unavailable"})
_ACTIONS = frozenset(
    {
        "delete",
        "delete_message",
        "mute",
        "temp_mute",
        "ban",
        "kick",
        "warn",
        "quarantine",
        "captcha_quarantine",
        "none",
    }
)


class GuardianProtocolError(ValueError):
    """Stable protocol error; the message is always a bounded reason code."""

    def __init__(self, code: str):
        normalized = str(code or "protocol_error").strip().lower()
        self.code = normalized if _SAFE_ERROR_CODE.fullmatch(normalized) else "protocol_error"
        super().__init__(self.code)


class GuardianPeerError(GuardianProtocolError):
    """Raised when a Unix socket peer is outside the configured allow-list."""


def _utc_now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise GuardianProtocolError("timestamp_timezone_required")
    return current.astimezone(timezone.utc)


def _timestamp(value: object, *, field: str) -> datetime:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            parsed = datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError) as exc:
            raise GuardianProtocolError(f"{field}_invalid") from exc
        return parsed
    if not isinstance(value, str) or not value or len(value) > 64:
        raise GuardianProtocolError(f"{field}_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GuardianProtocolError(f"{field}_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GuardianProtocolError("timestamp_timezone_required")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _utc_now(value).isoformat(timespec="milliseconds")


def _require_mapping(value: object, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise GuardianProtocolError(code)
    return value


def _check_fields(payload: Mapping[str, Any], allowed: frozenset[str]) -> None:
    unknown = set(payload) - set(allowed)
    if unknown:
        raise GuardianProtocolError("unknown_field")


def _bounded_string(value: object, *, field: str, limit: int, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise GuardianProtocolError(f"{field}_required")
        return None
    if not isinstance(value, str) or not value.strip():
        raise GuardianProtocolError(f"{field}_invalid")
    value = value.strip()
    if len(value) > limit:
        raise GuardianProtocolError(f"{field}_too_large")
    if any(ord(char) < 9 or (13 < ord(char) < 32) for char in value):
        raise GuardianProtocolError(f"{field}_control_character")
    return value


def _bounded_identifier(value: object, *, field: str, required: bool = True) -> str | None:
    result = _bounded_string(value, field=field, limit=128, required=required)
    if result is None and not required:
        return None
    if result is None or not _SAFE_IDENTIFIER.fullmatch(result):
        raise GuardianProtocolError(f"{field}_invalid")
    return result


@dataclass(frozen=True)
class GuardianEvent:
    """Minimal event representation sent across the Guardian trust boundary."""

    chat_id: int
    user_id: int
    message_id: int
    event_kind: str
    thread_id: int | None = None
    text: str | None = None
    caption: str | None = None
    attachment_type: str | None = None
    attachment_mime: str | None = None
    is_admin: bool = False
    settings_version: int = 0
    correlation_id: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.chat_id, bool) or not isinstance(self.chat_id, int) or self.chat_id >= 0:
            raise GuardianProtocolError("chat_id_invalid")
        for value, field in ((self.user_id, "user_id"), (self.message_id, "message_id")):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise GuardianProtocolError(f"{field}_invalid")
        if self.thread_id is not None and (isinstance(self.thread_id, bool) or not isinstance(self.thread_id, int) or self.thread_id <= 0):
            raise GuardianProtocolError("thread_id_invalid")
        _bounded_identifier(self.event_kind, field="event_kind")
        for value, field, limit in (
            (self.text, "text", MAX_EVENT_TEXT_CHARS),
            (self.caption, "caption", MAX_EVENT_TEXT_CHARS),
            (self.attachment_type, "attachment_type", 64),
            (self.attachment_mime, "attachment_mime", 128),
            (self.correlation_id or "x", "correlation_id", 128),
        ):
            if value is not None:
                _bounded_string(value, field=field, limit=limit, required=True)
        if isinstance(self.settings_version, bool) or not isinstance(self.settings_version, int) or self.settings_version < 0:
            raise GuardianProtocolError("settings_version_invalid")

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": int(self.chat_id),
            "user_id": int(self.user_id),
            "message_id": int(self.message_id),
            "event_kind": str(self.event_kind),
            "is_admin": bool(self.is_admin),
            "settings_version": int(self.settings_version),
            "correlation_id": self.correlation_id or uuid4().hex,
        }
        for key in ("thread_id", "text", "caption", "attachment_type", "attachment_mime"):
            value = getattr(self, key)
            if value is not None:
                payload[key] = value
        return payload


@dataclass(frozen=True)
class GuardianRequest:
    version: int
    request_id: str
    issued_at: datetime
    expires_at: datetime
    nonce: str
    operation: str
    event: GuardianEvent


@dataclass(frozen=True)
class GuardianResponse:
    version: int
    request_id: str
    provider: str
    decision: str
    actions: tuple[str, ...]
    rule_ids: tuple[str, ...]
    reason_code: str | None
    expires_at: datetime


class ReplayCache:
    """Small process-local replay cache; production can wrap the same policy in Redis."""

    def __init__(self, *, max_entries: int = 4096):
        self.max_entries = max(32, min(int(max_entries), 100_000))
        self._entries: OrderedDict[str, float] = OrderedDict()
        self._lock = Lock()

    def claim(self, key: str, *, expires_at: datetime, now: datetime | None = None) -> None:
        current = _utc_now(now).timestamp()
        expiry = _utc_now(expires_at).timestamp()
        with self._lock:
            for existing, existing_expiry in list(self._entries.items()):
                if existing_expiry <= current:
                    self._entries.pop(existing, None)
            if key in self._entries:
                raise GuardianProtocolError("request_replayed")
            self._entries[key] = expiry
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)


def _validate_event(payload: object) -> GuardianEvent:
    data = _require_mapping(payload, "event_invalid")
    _check_fields(data, _EVENT_FIELDS)
    try:
        chat_id = int(data["chat_id"])
        user_id = int(data["user_id"])
        message_id = int(data["message_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise GuardianProtocolError("event_identity_invalid") from exc
    if message_id <= 0 or user_id <= 0 or chat_id == 0:
        raise GuardianProtocolError("event_identity_invalid")
    try:
        thread_id = data.get("thread_id")
        thread_id = None if thread_id is None else int(thread_id)
        settings_version = int(data.get("settings_version", 0))
    except (TypeError, ValueError) as exc:
        raise GuardianProtocolError("event_numeric_invalid") from exc
    if settings_version < 0 or settings_version > 2_147_483_647:
        raise GuardianProtocolError("settings_version_invalid")
    event_kind = _bounded_identifier(data.get("event_kind"), field="event_kind")
    correlation_id = _bounded_identifier(data.get("correlation_id"), field="correlation_id")
    text = _bounded_string(data.get("text"), field="text", limit=MAX_EVENT_TEXT_CHARS)
    caption = _bounded_string(data.get("caption"), field="caption", limit=MAX_EVENT_TEXT_CHARS)
    attachment_type = _bounded_string(data.get("attachment_type"), field="attachment_type", limit=MAX_EVENT_FIELD_CHARS)
    attachment_mime = _bounded_string(data.get("attachment_mime"), field="attachment_mime", limit=MAX_EVENT_FIELD_CHARS)
    is_admin = data.get("is_admin", False)
    if not isinstance(is_admin, bool):
        raise GuardianProtocolError("is_admin_invalid")
    return GuardianEvent(
        chat_id=chat_id,
        user_id=user_id,
        message_id=message_id,
        thread_id=thread_id,
        event_kind=event_kind,
        text=text,
        caption=caption,
        attachment_type=attachment_type,
        attachment_mime=attachment_mime,
        is_admin=is_admin,
        settings_version=settings_version,
        correlation_id=correlation_id,
    )


def _encode(payload: Mapping[str, Any]) -> bytes:
    try:
        encoded = (
            json.dumps(payload, ensure_ascii=True, separators=(",", ":"), allow_nan=False) + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise GuardianProtocolError("json_encode_failed") from exc
    if len(encoded) > MAX_FRAME_BYTES:
        raise GuardianProtocolError("frame_too_large")
    return encoded


def _decode(raw: bytes | bytearray | str) -> Mapping[str, Any]:
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if not isinstance(raw, (bytes, bytearray)):
        raise GuardianProtocolError("frame_type_invalid")
    if len(raw) > MAX_FRAME_BYTES:
        raise GuardianProtocolError("frame_too_large")
    try:
        text = bytes(raw).decode("utf-8")
        def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in items:
                if key in result:
                    raise GuardianProtocolError("duplicate_field")
                result[key] = value
            return result

        payload = json.loads(
            text,
            object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")),
        )
    except GuardianProtocolError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise GuardianProtocolError("json_invalid") from exc
    return _require_mapping(payload, "json_object_required")


def build_request(
    event: GuardianEvent,
    *,
    request_id: str | None = None,
    nonce: str | None = None,
    ttl_seconds: float = 2.0,
    now: datetime | None = None,
) -> bytes:
    current = _utc_now(now)
    ttl = max(0.05, min(float(ttl_seconds), MAX_REQUEST_TTL_SECONDS))
    req_id = _bounded_identifier(request_id or uuid4().hex, field="request_id")
    req_nonce = _bounded_identifier(nonce or secrets.token_urlsafe(18), field="nonce")
    expires = current + timedelta(seconds=ttl)
    payload = {
        "version": PROTOCOL_VERSION,
        "request_id": req_id,
        "issued_at": _iso(current),
        "expires_at": _iso(expires),
        "nonce": req_nonce,
        "operation": "evaluate",
        "event": event.to_payload(),
    }
    # Validate our own payload before it crosses the socket.
    _validate_event(payload["event"])
    return _encode(payload)


def parse_request(
    raw: bytes | bytearray | str,
    *,
    replay_cache: ReplayCache | None = None,
    now: datetime | None = None,
) -> GuardianRequest:
    payload = _decode(raw)
    _check_fields(payload, _REQUEST_FIELDS)
    if payload.get("version") != PROTOCOL_VERSION:
        raise GuardianProtocolError("protocol_version_unsupported")
    request_id = _bounded_identifier(payload.get("request_id"), field="request_id")
    nonce = _bounded_identifier(payload.get("nonce"), field="nonce")
    if payload.get("operation") != "evaluate":
        raise GuardianProtocolError("operation_unsupported")
    issued_at = _timestamp(payload.get("issued_at"), field="issued_at")
    expires_at = _timestamp(payload.get("expires_at"), field="expires_at")
    current = _utc_now(now)
    if (expires_at - issued_at).total_seconds() > MAX_REQUEST_TTL_SECONDS + 0.1:
        raise GuardianProtocolError("expiry_too_long")
    if issued_at > current + timedelta(seconds=MAX_CLOCK_SKEW_SECONDS):
        raise GuardianProtocolError("issued_at_in_future")
    if expires_at <= current:
        raise GuardianProtocolError("request_expired")
    if expires_at <= issued_at:
        raise GuardianProtocolError("expiry_invalid")
    event = _validate_event(payload.get("event"))
    if replay_cache is not None:
        # Request IDs and nonces are independently single-use.  Reusing one
        # half of the envelope with a fresh value for the other half must not
        # create a second evaluation opportunity.
        replay_cache.claim(f"request:{request_id}", expires_at=expires_at, now=current)
        replay_cache.claim(f"nonce:{nonce}", expires_at=expires_at, now=current)
    return GuardianRequest(
        version=PROTOCOL_VERSION,
        request_id=request_id,
        issued_at=issued_at,
        expires_at=expires_at,
        nonce=nonce,
        operation="evaluate",
        event=event,
    )


def build_response(
    request_id: str,
    *,
    decision: str,
    actions: tuple[str, ...] | list[str] = (),
    rule_ids: tuple[str, ...] | list[str] = (),
    provider: str = "kek_guardian",
    reason_code: str | None = None,
    expires_at: datetime | None = None,
) -> bytes:
    req_id = _bounded_identifier(request_id, field="request_id")
    decision = _bounded_identifier(decision, field="decision")
    if decision not in _DECISIONS:
        raise GuardianProtocolError("decision_invalid")
    normalized_actions = tuple(_bounded_identifier(item, field="action") for item in actions)
    if len(normalized_actions) > MAX_ACTIONS or any(item not in _ACTIONS for item in normalized_actions):
        raise GuardianProtocolError("actions_invalid")
    normalized_rules = tuple(_bounded_identifier(item, field="rule_id") for item in rule_ids)
    if len(normalized_rules) > MAX_RULE_IDS:
        raise GuardianProtocolError("rule_ids_invalid")
    expiry = _utc_now(expires_at or (datetime.now(timezone.utc) + timedelta(seconds=30)))
    payload: dict[str, Any] = {
        "version": PROTOCOL_VERSION,
        "request_id": req_id,
        "provider": _bounded_identifier(provider, field="provider"),
        "decision": decision,
        "actions": list(normalized_actions),
        "rule_ids": list(normalized_rules),
        "expires_at": _iso(expiry),
    }
    if reason_code is not None:
        payload["reason_code"] = _bounded_identifier(reason_code, field="reason_code")
    return _encode(payload)


def parse_response(
    raw: bytes | bytearray | str,
    *,
    expected_request_id: str | None = None,
    expected_provider: str | None = "kek_guardian",
    now: datetime | None = None,
) -> GuardianResponse:
    payload = _decode(raw)
    _check_fields(payload, _RESPONSE_FIELDS)
    if payload.get("version") != PROTOCOL_VERSION:
        raise GuardianProtocolError("protocol_version_unsupported")
    request_id = _bounded_identifier(payload.get("request_id"), field="request_id")
    if expected_request_id is not None and request_id != expected_request_id:
        raise GuardianProtocolError("request_id_mismatch")
    provider = _bounded_identifier(payload.get("provider"), field="provider")
    if expected_provider is not None and provider != expected_provider:
        raise GuardianProtocolError("provider_mismatch")
    decision = _bounded_identifier(payload.get("decision"), field="decision")
    if decision not in _DECISIONS:
        raise GuardianProtocolError("decision_invalid")
    actions_value = payload.get("actions", [])
    rule_ids_value = payload.get("rule_ids", [])
    if not isinstance(actions_value, list) or len(actions_value) > MAX_ACTIONS:
        raise GuardianProtocolError("actions_invalid")
    if not isinstance(rule_ids_value, list) or len(rule_ids_value) > MAX_RULE_IDS:
        raise GuardianProtocolError("rule_ids_invalid")
    actions = tuple(_bounded_identifier(item, field="action") for item in actions_value)
    if any(item not in _ACTIONS for item in actions):
        raise GuardianProtocolError("actions_invalid")
    rule_ids = tuple(_bounded_identifier(item, field="rule_id") for item in rule_ids_value)
    reason_code = _bounded_identifier(payload.get("reason_code"), field="reason_code", required=False)
    expires_at = _timestamp(payload.get("expires_at"), field="expires_at")
    if expires_at <= _utc_now(now):
        raise GuardianProtocolError("response_expired")
    return GuardianResponse(
        version=PROTOCOL_VERSION,
        request_id=request_id,
        provider=provider,
        decision=decision,
        actions=actions,
        rule_ids=rule_ids,
        reason_code=reason_code,
        expires_at=expires_at,
    )


def peer_credentials(sock: socket.socket) -> tuple[int, int, int]:
    """Read Linux ``SO_PEERCRED`` (pid, uid, gid); fail closed elsewhere."""

    option = getattr(socket, "SO_PEERCRED", None)
    if option is None:
        raise GuardianPeerError("peer_credentials_unsupported")
    try:
        raw = sock.getsockopt(socket.SOL_SOCKET, option, struct.calcsize("3i"))
        pid, uid, gid = struct.unpack("3i", raw)
    except (OSError, struct.error) as exc:
        raise GuardianPeerError("peer_credentials_unavailable") from exc
    if pid <= 0 or uid < 0 or gid < 0:
        raise GuardianPeerError("peer_credentials_invalid")
    return pid, uid, gid


def validate_peer_credentials(
    *,
    uid: int,
    gid: int,
    allowed_uids: set[int] | frozenset[int] | None,
    allowed_gids: set[int] | frozenset[int] | None,
) -> None:
    """Require an explicit UID or GID allow-list for a bridge peer."""

    uid_set = {int(item) for item in (allowed_uids or set())}
    gid_set = {int(item) for item in (allowed_gids or set())}
    if not uid_set and not gid_set:
        raise GuardianPeerError("peer_allowlist_missing")
    if uid_set and int(uid) in uid_set:
        return
    if gid_set and int(gid) in gid_set:
        return
    raise GuardianPeerError("peer_unauthorized")


def safe_error_code(value: object, fallback: str = "guardian_protocol_error") -> str:
    """Keep provider/bridge errors from becoming an unbounded data channel."""

    candidate = str(value or "").strip().lower()
    if _SAFE_ERROR_CODE.fullmatch(candidate):
        return candidate
    return fallback if _SAFE_ERROR_CODE.fullmatch(fallback) else "guardian_protocol_error"


def socket_path_is_safe(path: str) -> bool:
    """Reject relative, NUL-containing, oversized, and symlink socket paths."""

    if not isinstance(path, str) or not path or "\x00" in path:
        return False
    encoded = os.fsencode(path)
    if len(encoded) >= 108 or not os.path.isabs(path):
        return False
    try:
        return not os.path.islink(path)
    except OSError:
        return False


__all__ = [
    "GuardianEvent",
    "GuardianProtocolError",
    "GuardianPeerError",
    "GuardianRequest",
    "GuardianResponse",
    "MAX_FRAME_BYTES",
    "ReplayCache",
    "PROTOCOL_VERSION",
    "build_request",
    "build_response",
    "parse_request",
    "parse_response",
    "peer_credentials",
    "safe_error_code",
    "socket_path_is_safe",
    "validate_peer_credentials",
]
