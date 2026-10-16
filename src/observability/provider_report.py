"""Safe provider health formatting for administrator-only Telegram views."""

from __future__ import annotations

import html
from collections.abc import Iterable
from typing import Any


def format_provider_health(rows: Iterable[Any], *, title: str, limit: int = 18) -> str:
    """Format bounded, escaped provider health without exposing raw exceptions."""

    items = list(rows)
    bounded_limit = max(1, int(limit))
    lines = [f"<b>{html.escape(title)}</b>"]
    for row in items[:bounded_limit]:
        name = html.escape(str(getattr(row, "name", "unknown"))[:80])
        state = "âœ…" if bool(getattr(row, "healthy", False)) else "â›”"
        error_rate = getattr(row, "error_rate", None)
        try:
            error_text = "â€”" if error_rate is None else f"%{max(0.0, min(1.0, float(error_rate))) * 100:.1f}"
        except (TypeError, ValueError):
            error_text = "â€”"
        detail = html.escape(str(getattr(row, "detail", "") or "")[:180])
        score = getattr(row, "score", None)
        try:
            score_text = "â€”" if score is None else f"{max(0.0, min(100.0, float(score))):.1f}"
        except (TypeError, ValueError):
            score_text = "â€”"
        suffix = f" Â· skor {score_text}" + (f" Â· {detail}" if detail else "")
        lines.append(f"{state} <code>{name}</code> Â· hata {error_text}{suffix}")
    if len(items) > bounded_limit:
        lines.append(f"â€¦ {len(items) - bounded_limit} saÄŸlayÄ±cÄ± daha")
    return "\n".join(lines)


def format_ai_provider_health(rows: Iterable[dict[str, Any]], *, title: str = "AI saÄŸlayÄ±cÄ±larÄ±", limit: int = 18) -> str:
    """Format router-local AI health without exposing provider configuration."""
    items = list(rows)
    bounded_limit = max(1, int(limit))
    lines = [f"<b>{html.escape(title)}</b>"]
    for row in items[:bounded_limit]:
        name = html.escape(str(row.get("name", "unknown"))[:80])
        state = "âœ…" if bool(row.get("healthy", False)) else "â›”"
        try:
            error_rate = max(0.0, min(1.0, float(row.get("error_rate", 0.0)))) * 100
            error_text = f"%{error_rate:.1f}"
        except (TypeError, ValueError):
            error_text = "â€”"
        try:
            latency = float(row.get("latency_ms"))
            latency_text = f"{max(0.0, latency):.0f}ms"
        except (TypeError, ValueError):
            latency_text = "â€”"
        lines.append(f"{state} <code>{name}</code> Â· hata {error_text} Â· gecikme {latency_text}")
    if len(items) > bounded_limit:
        lines.append(f"â€¦ {len(items) - bounded_limit} saÄŸlayÄ±cÄ± daha")
    return "\n".join(lines)


def format_provider_governance(report: dict[str, Any], *, title: str = "Kaynak yÃ¶netiÅŸimi", limit: int = 24) -> str:
    """Render source-governance state without exposing URLs or legal payloads.

    Governance is deliberately separate from runtime health: an upstream can
    be reachable while its terms or production sign-off are still pending.
    The formatter accepts the bounded machine report produced by
    ``governance_report`` and only emits stable feature/provider identifiers.
    """

    rows = report.get("features") if isinstance(report, dict) else None
    items = rows if isinstance(rows, list) else []
    bounded_limit = max(1, int(limit))
    pending = int(report.get("pending_count", 0) or 0) if isinstance(report, dict) else 0
    disabled = int(report.get("disabled_count", 0) or 0) if isinstance(report, dict) else 0
    errors = len(report.get("errors", ())) if isinstance(report, dict) and isinstance(report.get("errors"), list) else 0
    lines = [
        f"<b>{html.escape(title)}</b>",
        f"Bekleyen: <code>{max(0, pending)}</code> Â· devre dÄ±ÅŸÄ±: <code>{max(0, disabled)}</code> Â· hata: <code>{max(0, errors)}</code>",
    ]
    for row in items[:bounded_limit]:
        if not isinstance(row, dict):
            continue
        key = html.escape(str(row.get("feature_key", "unknown"))[:80])
        provider = html.escape(str(row.get("provider_key") or "local")[:80])
        status = str(row.get("status") or "invalid").casefold()
        icon = {"approved": "âœ…", "disabled": "â›”", "not_required": "â„¹ï¸"}.get(status, "â³")
        robots = html.escape(str(row.get("robots_policy") or "unknown")[:40])
        terms = html.escape(str(row.get("terms_review") or "unknown")[:40])
        signoff = html.escape(str(row.get("production_signoff") or "unknown")[:40])
        lines.append(
            f"{icon} <code>{key}</code> Â· <code>{provider}</code> Â· {html.escape(status)}"
            f" Â· robots={robots} Â· terms={terms} Â· signoff={signoff}"
        )
    if len(items) > bounded_limit:
        lines.append(f"â€¦ {len(items) - bounded_limit} kayÄ±t daha")
    return "\n".join(lines)
