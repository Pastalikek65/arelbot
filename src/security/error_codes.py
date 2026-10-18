"""Stable, bounded error-code normalization for external boundaries."""

from __future__ import annotations

import re
from collections.abc import Mapping


_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")


def normalize_error_code(value: object, fallback: str = "INTERNAL_ERROR") -> str:
    """Keep provider-controlled error values from becoming data channels.

    Error codes are operational identifiers, not user or upstream messages.
    Values outside the bounded identifier grammar are collapsed to a stable
    fallback so URLs, credentials and arbitrary response text cannot enter
    Telegram responses, audit rows or retry decisions.
    """

    candidate = str(value or "").strip().upper()
    if _ERROR_CODE.fullmatch(candidate):
        return candidate
    safe_fallback = str(fallback or "INTERNAL_ERROR").strip().upper()
    return safe_fallback if _ERROR_CODE.fullmatch(safe_fallback) else "INTERNAL_ERROR"


def public_error_message(
    error: BaseException | object,
    fallback: str,
    *,
    messages: Mapping[str, str] | None = None,
) -> str:
    """Return a bounded user message without echoing exception text.

    Exception messages can contain provider responses, file paths, URLs or
    credentials.  User-facing boundaries must therefore select text from an
    explicit code map and fall back to a generic message.  A controlled
    exception may expose its stable ``code`` attribute; arbitrary ``str(exc)``
    is deliberately never returned.
    """

    code = normalize_error_code(getattr(error, "code", ""), "")
    candidate = (messages or {}).get(code)
    if candidate:
        return str(candidate)[:512]
    return str(fallback)[:512]


_VALIDATION_SUSPICIOUS = re.compile(
    r"(?i)(https?://|bearer\s|token\s*=|cookie\s*=|password\s*=|secret\s*=|api[_-]?key\s*=|[\\]{2,})"
)


def public_validation_message(error: BaseException | object, fallback: str) -> str:
    """Bound parser-owned validation text before it reaches Telegram.

    Some legacy parsers still carry a short, human-readable ``ValueError``
    instead of a code. Preserve that UX only when the value is clearly a
    bounded validation sentence; suspicious payloads and non-validation
    exceptions collapse to the supplied fallback.
    """

    if not isinstance(error, ValueError):
        return str(fallback)[:512]
    text = str(error).strip()
    if (
        not text
        or len(text) > 256
        or "\n" in text
        or "\r" in text
        or _VALIDATION_SUSPICIOUS.search(text)
        or "<" in text
        or ">" in text
    ):
        return str(fallback)[:512]
    return text
