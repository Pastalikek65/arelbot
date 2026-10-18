"""Shared Telegram rendering for typed feature results.

Commands, scheduled deliveries and callbacks all consume the same
``FeatureResult`` envelope. Keeping this formatter in the feature layer avoids
small presentation differences that can hide stale data or expose an unsafe
source link in one Telegram surface.
"""

from __future__ import annotations

from html import escape
from typing import Any
from urllib.parse import urlsplit

from src.security.output_sanitizer import sanitize_html
from src.services.time_format import format_turkey_datetime


def _safe_source_url(value: Any) -> str:
    candidate = str(value or "").strip()
    if not candidate:
        return ""
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return ""
    if parsed.username or parsed.password:
        return ""
    return candidate


def format_feature_result(result: Any, fallback: str) -> str:
    """Render a typed result with bounded source and freshness annotations."""

    if not getattr(result, "ok", False):
        return sanitize_html(str(fallback or "Veri alınamadı."))

    blocks = getattr(result, "display_blocks", ()) or ()
    content = "\n\n".join(sanitize_html(str(block)) for block in blocks if str(block).strip()) if blocks else "Veri bulunamadı."
    annotations: list[str] = []
    metadata = getattr(result, "metadata", {}) or {}
    if getattr(result, "stale", False):
        annotations.append(
            "<i>Kaynak geçici olarak kullanılamadı; önbellekteki son veri gösteriliyor.</i>"
        )
    elif metadata.get("fresh") is False:
        annotations.append(
            "<i>Uyarı: Veri tazelik hedefinin dışında; kaynak zamanını kontrol edin.</i>"
        )

    source = str(getattr(result, "source", "") or "").strip()
    if source:
        safe_source = escape(source)
        source_url = _safe_source_url(getattr(result, "source_url", None))
        source_text = (
            f'<a href="{escape(source_url, quote=True)}">{safe_source}</a>'
            if source_url
            else safe_source
        )
        annotations.append(f"<i>Kaynak: {source_text}</i>")

    fetched_at = getattr(result, "fetched_at", None)
    if fetched_at is not None:
        try:
            fetched = format_turkey_datetime(fetched_at)
        except (AttributeError, TypeError, ValueError):
            fetched = ""
        if fetched:
            annotations.append(f"<i>Veri zamanı: {escape(fetched)}</i>")

    return content + (("\n\n" + "\n".join(annotations)) if annotations else "")
