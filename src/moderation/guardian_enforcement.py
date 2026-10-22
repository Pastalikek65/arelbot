"""Pure Guardian message-enforcement and challenge policy.

The Telegram handlers deliberately stay out of this module.  They pass the
persisted challenge and the current request here, then use the typed result to
decide whether a callback/message is still allowed.  Keeping expiry, scope and
generation rules in one pure module prevents the text and callback paths from
drifting apart after a restart.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class GuardianEnforcementDecision:
    allowed: bool = True
    reason: str | None = None
    action: str = "none"


@dataclass(frozen=True, slots=True)
class CaptchaValidation:
    """Bounded result for a persisted Guardian join challenge.

    ``valid`` means that the challenge is current and belongs to the exact
    chat/user supplied by the caller.  It does not consume the challenge;
    consumption still belongs to the repository/store transaction so a
    callback retry cannot race a message update.
    """

    valid: bool
    reason: str
    expires_at: datetime | None = None


_CAPTCHA_TYPES = frozenset({"button", "math", "text"})
_MAX_CAPTCHA_TIMEOUT_SECONDS = 3600
_MAX_CLOCK_SKEW_SECONDS = 30


def _parse_utc_datetime(value: object) -> datetime | None:
    """Parse the bounded ISO representation used by Mongo/Redis state."""

    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str) and value.strip():
        try:
            result = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if result.tzinfo is None or result.utcoffset() is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _normalise_captcha_answer(value: object) -> str:
    """Apply the same small normalization to text and math answers."""

    raw = str(value or "").strip()
    if len(raw) > 256:
        return ""
    return " ".join(raw.split()).casefold()


def validate_captcha_challenge(
    challenge: Mapping[str, Any] | None,
    *,
    chat_id: int,
    user_id: int,
    now: datetime | None = None,
    timeout_seconds: int = 300,
    supplied_answer: str | None = None,
    require_created_at: bool = True,
) -> CaptchaValidation:
    """Validate a persisted challenge without mutating storage.

    The challenge map is untrusted persisted state.  A malformed timestamp is
    rejected instead of being treated as an unexpiring challenge.  Optional
    ``chat_id``/``user_id`` fields are checked when present; the surrounding
    store key remains the scope for legacy records that predate these fields.
    ``supplied_answer`` is optional so callers can first validate a pending
    callback, then compare/consume in the same store transaction.
    """

    if not isinstance(challenge, Mapping):
        return CaptchaValidation(False, "missing")
    if isinstance(chat_id, bool) or not isinstance(chat_id, int):
        return CaptchaValidation(False, "scope_invalid")
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        return CaptchaValidation(False, "scope_invalid")
    try:
        challenge_chat = challenge.get("chat_id")
        challenge_user = challenge.get("user_id", challenge.get("target_user_id"))
        if isinstance(challenge_chat, bool) or (
            challenge_chat is not None and int(challenge_chat) != int(chat_id)
        ):
            return CaptchaValidation(False, "wrong_chat")
        if isinstance(challenge_user, bool) or (
            challenge_user is not None and int(challenge_user) != int(user_id)
        ):
            return CaptchaValidation(False, "wrong_user")
    except (TypeError, ValueError, OverflowError):
        return CaptchaValidation(False, "scope_invalid")

    raw_type = str(challenge.get("type") or "button").strip().casefold()
    if raw_type not in _CAPTCHA_TYPES:
        return CaptchaValidation(False, "type_invalid")
    if bool(challenge.get("consumed")) or str(challenge.get("status") or "").casefold() in {
        "consumed",
        "completed",
        "expired",
    }:
        return CaptchaValidation(False, "consumed")

    try:
        bounded_timeout = max(30, min(_MAX_CAPTCHA_TIMEOUT_SECONDS, int(timeout_seconds)))
    except (TypeError, ValueError, OverflowError):
        bounded_timeout = 300
    current = _parse_utc_datetime(now) if now is not None else datetime.now(timezone.utc)
    if current is None:
        current = datetime.now(timezone.utc)
    created_at = _parse_utc_datetime(challenge.get("created_at"))
    if created_at is None:
        if require_created_at:
            return CaptchaValidation(False, "created_at_invalid")
    else:
        # A future-dated challenge can otherwise survive indefinitely after a
        # clock skew or a forged persisted value.  Allow only a small skew.
        if created_at - current > timedelta(seconds=_MAX_CLOCK_SKEW_SECONDS):
            return CaptchaValidation(False, "created_at_future")
        expires_at = created_at + timedelta(seconds=bounded_timeout)
        if current >= expires_at:
            return CaptchaValidation(False, "expired", expires_at=expires_at)

    if supplied_answer is not None:
        expected = challenge.get("answer")
        if expected is None or not _normalise_captcha_answer(supplied_answer):
            return CaptchaValidation(False, "answer_invalid")
        if _normalise_captcha_answer(supplied_answer) != _normalise_captcha_answer(expected):
            return CaptchaValidation(False, "answer_mismatch")

    return CaptchaValidation(
        True,
        "valid",
        expires_at=(created_at + timedelta(seconds=bounded_timeout)) if created_at else None,
    )


def new_guardian_challenge_nonce() -> str:
    """Return an opaque per-join nonce for action idempotency.

    It is intentionally generated outside Telegram/database code so rejoining
    the same user always gets a new logical generation while Telegram retries
    for that generation keep the same idempotency key.
    """

    return secrets.token_urlsafe(18)


def build_guardian_generation_id(
    action: str,
    *,
    chat_id: int,
    user_id: int,
    nonce: object,
) -> str:
    """Build a stable, bounded idempotency id for one join/challenge cycle.

    ``nonce`` must be persisted with the challenge.  Reusing a fixed
    ``chat_id:user_id`` key is intentionally rejected because it suppresses
    enforcement for a later rejoin after an earlier action was recorded.
    """

    action_value = str(action or "").strip().casefold()
    nonce_value = str(nonce or "").strip()
    if not action_value or len(action_value) > 64:
        raise ValueError("generation_action_invalid")
    if isinstance(chat_id, bool) or not isinstance(chat_id, int):
        raise ValueError("generation_chat_invalid")
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        raise ValueError("generation_user_invalid")
    if not nonce_value or len(nonce_value) > 256:
        raise ValueError("generation_nonce_required")
    digest = hashlib.sha256(
        f"{action_value}|{int(chat_id)}|{int(user_id)}|{nonce_value}".encode("utf-8")
    ).hexdigest()[:48]
    return f"guardian:{action_value}:{digest}"


def evaluate_guardian_message(
    *,
    attachment_type: str | None,
    settings: Mapping[str, Any] | None,
    user: Mapping[str, Any] | None,
    user_id: int | None = None,
    text: str | None = None,
    is_admin: bool = False,
    sticker_set_name: str | None = None,
    attachment_id: str | None = None,
    is_edited: bool = False,
) -> GuardianEnforcementDecision:
    """Evaluate global bans and per-chat Guardian media locks.

    The function has no Telegram or persistence dependency.  Unknown state is
    permissive; destructive actions are only returned for explicit, bounded
    settings so a temporary Mongo outage cannot swallow a legitimate message.
    """

    settings = settings or {}
    user = user or {}
    if is_admin:
        return GuardianEnforcementDecision()
    roles = settings.get("guardian_roles") if isinstance(settings, Mapping) else None
    approved = {str(item) for item in (settings.get("approved_users") or [])}
    approved.update(str(item) for item in (settings.get("guardian_federation_approved") or []))
    role = str(roles.get(str(user_id), "")).casefold() if isinstance(roles, Mapping) else ""
    fed_bans = {str(item) for item in (settings.get("guardian_fed_bans") or [])}
    if user_id is not None and str(int(user_id)) in fed_bans:
        return GuardianEnforcementDecision(False, "guardian_federation_ban", "ban")
    shadow_bans = {str(item) for item in (settings.get("guardian_shadow_bans") or [])}
    if user_id is not None and str(int(user_id)) in shadow_bans:
        return GuardianEnforcementDecision(False, "guardian_shadow_ban", "delete")
    gban = user.get("guardian_gban")
    if isinstance(gban, Mapping) and bool(gban.get("enabled")):
        return GuardianEnforcementDecision(False, "guardian_gban", "ban")
    # Approved/free users bypass local content policy, never global or
    # federation bans checked above.
    if str(user_id) in approved or role == "free":
        return GuardianEnforcementDecision()
    if bool(settings.get("fake_account_protection")) and bool(
        user.get("is_fake") or user.get("is_scam") or user.get("guardian_fake_account")
    ):
        return GuardianEnforcementDecision(False, "guardian_fake_account", "ban")
    if bool(settings.get("edit_protection")) and is_edited:
        return GuardianEnforcementDecision(False, "guardian_edit_protection", "delete")
    blocked_reactions = {str(item) for item in (settings.get("guardian_blocked_reactions") or [])}
    if blocked_reactions and text and any(emoji in str(text) for emoji in blocked_reactions):
        return GuardianEnforcementDecision(False, "guardian_blocked_reaction", "mute")
    kind = str(attachment_type or "").casefold()
    if not kind:
        return GuardianEnforcementDecision()
    locked = {str(item).casefold() for item in (settings.get("locked_types") or []) if str(item).strip()}
    media_bans = {str(item).casefold() for item in (settings.get("media_bans") or []) if str(item).strip()}
    if kind in locked or kind in media_bans or (kind == "animation" and "gif" in locked):
        mode = str(settings.get("lock_mode") or "delete").casefold()
        action = mode if mode in {"delete", "mute", "ban"} else "delete"
        return GuardianEnforcementDecision(False, "guardian_media_lock", action)
    if kind == "photo" and bool(settings.get("image_protection")):
        return GuardianEnforcementDecision(False, "guardian_image_protection", "delete")
    blocked_packs = {str(item).casefold() for item in (settings.get("guardian_blocked_sticker_packs") or [])}
    if kind == "sticker" and blocked_packs and sticker_set_name and str(sticker_set_name).casefold() in blocked_packs:
        return GuardianEnforcementDecision(False, "guardian_sticker_pack", "delete")
    blocked_gifs = {str(item).casefold() for item in (settings.get("guardian_blocked_gifs") or [])}
    if kind in {"animation", "gif"} and blocked_gifs and attachment_id and str(attachment_id).casefold() in blocked_gifs:
        return GuardianEnforcementDecision(False, "guardian_gif", "delete")
    return GuardianEnforcementDecision()


__all__ = [
    "CaptchaValidation",
    "GuardianEnforcementDecision",
    "build_guardian_generation_id",
    "evaluate_guardian_message",
    "new_guardian_challenge_nonce",
    "validate_captcha_challenge",
]
