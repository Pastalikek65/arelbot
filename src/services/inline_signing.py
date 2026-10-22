"""Stable, purpose-separated signing key without requiring cookie encryption."""
from __future__ import annotations

import hashlib
import hmac


def signing_key(bot_token: str) -> bytes:
    if not isinstance(bot_token, str) or not bot_token.strip():
        raise ValueError("inline_bot_token_required")
    return hmac.new(bot_token.encode(), b"arel-card-inline-v1", hashlib.sha256).digest()
