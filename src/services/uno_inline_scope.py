"""Public, signed group/round query shared by UNO state and gallery."""
from __future__ import annotations

import base64
import hashlib
import hmac
import re


def create_query(state, secret: str | bytes) -> str:
    key = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
    if len(key) < 32 or not re.fullmatch(r"[0-9a-f]{16}", str(state["round_id"])):
        raise ValueError("UNO_INLINE_SCOPE_INVALID")
    number = int(state["chat_id"])
    sign, value = ("-" if number < 0 else ""), abs(number)
    digits = ""
    while value:
        value, digit = divmod(value, 36)
        digits = "0123456789abcdefghijklmnopqrstuvwxyz"[digit] + digits
    scope = f"{sign}{digits or '0'}.{state['round_id']}"
    digest = hmac.new(key, f"uno-inline:query:{scope}".encode(), hashlib.sha256).digest()[:9]
    signature = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return f"uno {scope}.{signature}"
