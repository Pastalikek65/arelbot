"""Telegram Mini App ``init_data`` reference implementation.

Two jobs:

1. Validate an ``init_data`` payload exactly the way the Telegram WebApp spec
   describes it (HMAC-SHA256 over ``data_check_string`` with the
   ``WebAppData``-derived secret key, optional Ed25519 signature check,
   freshness window, replay protection).
2. Sign synthetic payloads with a bot token the operator controls, so a test
   battery can prove that a server actually verifies signatures instead of
   trusting whatever the browser posted.

Reference: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from typing import Iterable, Mapping, MutableSet
from urllib.parse import parse_qsl, urlencode

HASH_KEY = "WebAppData"
DEFAULT_MAX_AGE = 3600
EXCLUDED_FROM_CHECK_STRING = frozenset({"hash", "signature"})


def secret_key(bot_token: str) -> bytes:
    """Derive the per-bot secret key: HMAC-SHA256(key="WebAppData", msg=bot_token)."""
    return hmac.new(HASH_KEY.encode(), bot_token.encode(), hashlib.sha256).digest()


def data_check_string(pairs: Iterable[tuple[str, str]]) -> str:
    """Sorted ``key=value`` lines, ``hash``/``signature`` excluded."""
    usable = [(k, v) for k, v in pairs if k not in EXCLUDED_FROM_CHECK_STRING]
    return "\n".join(f"{k}={v}" for k, v in sorted(usable))


def sign(pairs: Mapping[str, str], bot_token: str) -> dict[str, str]:
    """Return ``pairs`` plus a valid ``hash`` for ``bot_token``."""
    clean = {k: v for k, v in pairs.items() if k not in EXCLUDED_FROM_CHECK_STRING}
    digest = hmac.new(
        secret_key(bot_token),
        data_check_string(clean.items()).encode(),
        hashlib.sha256,
    ).hexdigest()
    return {**clean, "hash": digest}


def encode(pairs: Mapping[str, str]) -> str:
    return urlencode(pairs)


def parse(init_data: str) -> dict[str, str]:
    if not isinstance(init_data, str) or not init_data:
        return {}
    return dict(parse_qsl(init_data, keep_blank_values=True))


@dataclass
class ValidationResult:
    ok: bool
    reason: str
    user: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)


def verify_signature(params: Mapping[str, str], bot_token: str) -> bool:
    """Verify the Ed25519 ``signature`` field when present (Bot API 7.0+)."""
    signature_b64 = params.get("signature")
    if not signature_b64:
        return True
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:  # pragma: no cover - optional dependency
        return True

    import base64

    try:
        public_key = Ed25519PublicKey.from_public_bytes(
            base64.urlsafe_b64decode(bot_token + "=" * (-len(bot_token) % 4))
        )
        public_key.verify(base64.urlsafe_b64decode(signature_b64), data_check_string(params.items()).encode())
    except (InvalidSignature, ValueError):
        return False
    return True


def validate(
    init_data: str,
    bot_token: str,
    *,
    max_age: int = DEFAULT_MAX_AGE,
    now: float | None = None,
    replay_seen: MutableSet[str] | None = None,
) -> ValidationResult:
    """Validate ``init_data``. ``replay_seen`` enables replay detection."""
    if not init_data or not isinstance(init_data, str):
        return ValidationResult(False, "empty_init_data")

    params = parse(init_data)
    if not params:
        return ValidationResult(False, "empty_init_data")

    supplied_hash = params.get("hash", "")
    if not supplied_hash:
        return ValidationResult(False, "missing_hash", params=params)

    expected = hmac.new(
        secret_key(bot_token),
        data_check_string(params.items()).encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, supplied_hash):
        return ValidationResult(False, "hash_mismatch", params=params)

    if not verify_signature(params, bot_token):
        return ValidationResult(False, "bad_signature", params=params)

    raw_auth_date = params.get("auth_date")
    if raw_auth_date:
        try:
            auth_date = int(raw_auth_date)
        except ValueError:
            return ValidationResult(False, "bad_auth_date", params=params)
        current = time.time() if now is None else now
        if max_age and current - auth_date > max_age:
            return ValidationResult(False, "stale_auth_date", params=params)

    if replay_seen is not None:
        if supplied_hash in replay_seen:
            return ValidationResult(False, "replay", params=params)
        replay_seen.add(supplied_hash)

    user: dict = {}
    raw_user = params.get("user")
    if raw_user:
        try:
            parsed = json.loads(raw_user)
            if isinstance(parsed, dict):
                user = parsed
        except (ValueError, TypeError):
            return ValidationResult(False, "bad_user_json", params=params)

    return ValidationResult(True, "ok", user=user, params=params)


def build_valid_init_data(
    bot_token: str,
    *,
    user_id: int = 1,
    first_name: str = "audit",
    auth_date: int | None = None,
) -> str:
    """Build a correctly signed ``init_data`` payload for the given token."""
    stamp = int(time.time()) if auth_date is None else auth_date
    user = {"id": user_id, "first_name": first_name, "language_code": "tr"}
    pairs = {
        "auth_date": str(stamp),
        "query_id": "AAHdG6IQAAAAAN0XohDhrOrc",
        "user": json.dumps(user, separators=(",", ":")),
    }
    return encode(sign(pairs, bot_token))
