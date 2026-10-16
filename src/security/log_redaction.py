"""Logging filter that removes credentials and private media URLs."""

from __future__ import annotations

import re
from pathlib import Path
from numbers import Number
from logging import Filter, LogRecord


_PATTERNS = (
    (re.compile(r"(?i)(bot\d{6,}:[a-z0-9_-]{20,})"), "<telegram-token>"),
    (re.compile(r"(?i)nvapi-[a-z0-9_-]{20,}"), "<nvidia-api-key>"),
    (re.compile(r"(?i)(bearer\s+)[a-z0-9._-]+"), r"\1<redacted>"),
    (re.compile(r"(?i)(cookie(?:file|_vault)?\s*[=:]\s*)[^\s,]+"), r"\1<redacted>"),
    (re.compile(r"(?i)https?://(?:www\.)?(?:instagram|instagr\.am|tiktok|youtube|youtu\.be|twitter|x\.com|facebook|fb\.watch|reddit|redd\.it)[^\s\"']*"), "<media-url>"),
    (re.compile(r"(?i)(https?://[^\s?#]+)\?[^\s]+"), r"\1?<query-redacted>"),
)


def summarize_private_text(value: object, *, label: str = "payload") -> str:
    """Describe private text without putting its contents in an audit channel."""

    safe_label = re.sub(r"[^A-Za-z0-9_.-]", "_", str(label or "payload"))[:32] or "payload"
    text = "" if value is None else str(value)
    return f"{safe_label} length={len(text)}"


def summarize_private_attachment(path: object) -> str:
    """Return bounded attachment metadata without exposing the filesystem path."""

    try:
        size = max(0, min(int(Path(str(path)).stat().st_size), 1_000_000_000))
    except (OSError, TypeError, ValueError):
        return "attachment present=0"
    return f"attachment present=1 bytes={size}"


def summarize_exception(value: BaseException, *, label: str = "error") -> str:
    """Return only an exception type for logs; never include its message."""

    safe_label = re.sub(r"[^A-Za-z0-9_.-]", "_", str(label or "error"))[:32] or "error"
    error_type = type(value).__name__ or "Exception"
    safe_type = re.sub(r"[^A-Za-z0-9_.]", "_", error_type)[:80] or "Exception"
    return f"{safe_label} type={safe_type}"


def redact(value: object) -> str:
    text = str(value)
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _redact_argument(value: object) -> object:
    """Redact a structured logging argument without breaking format specifiers.

    Logging format strings are evaluated *after* filters run.  Converting every
    argument to ``str`` therefore breaks valid formatters such as ``%d`` (the
    HTTPX request logger uses one for the status code).  Scalar numbers and
    booleans are safe to preserve, while strings and URL-like objects are
    replaced with their redacted textual representation.
    """

    if isinstance(value, BaseException):
        return summarize_exception(value, label="exception")
    if value is None or isinstance(value, (str, Number, bool)):
        return redact(value) if isinstance(value, str) else value
    if isinstance(value, bytes):
        return redact(value.decode("utf-8", errors="replace"))
    return redact(value)


class RedactionFilter(Filter):
    def filter(self, record: LogRecord) -> bool:
        record.msg = redact(record.msg)
        # Python's formatter appends ``exc_info`` after ``filter`` runs. A
        # traceback contains the original exception message verbatim and can
        # therefore bypass the redaction patterns above. Keep the stable log
        # message while dropping that unbounded private payload.
        if record.exc_info:
            record.exc_info = None
            record.exc_text = None
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    key: _redact_argument(value) for key, value in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(_redact_argument(value) for value in record.args)
            elif isinstance(record.args, list):
                record.args = [_redact_argument(value) for value in record.args]
            else:
                record.args = _redact_argument(record.args)
        return True

