"""Prompt boundary helpers for external documents and web results."""

from __future__ import annotations

import re
from html import escape


_INSTRUCTION_MARKERS = re.compile(
    r"(?i)\b(ignore|disregard|override|system prompt|developer message|previous instructions)\b"
)


def untrusted_context(text: str, max_chars: int = 12000) -> str:
    value = (text or "")[:max_chars]
    value = _INSTRUCTION_MARKERS.sub("[instruction-marker]", value)
    # Escape markup supplied by a document or web page so it cannot close or
    # impersonate this boundary in the model transcript.
    value = escape(value, quote=False)
    return "<external_context>\n" + value + "\n</external_context>"
