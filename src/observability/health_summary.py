"""Bounded dependency-health summaries for authenticated operations endpoints."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def summarize_provider_rows(
    rows: Iterable[Any], *, limit: int = 24, probe_error: bool = False
) -> dict[str, Any]:
    """Return counts and bounded names without copying provider details."""

    def value(row: Any, key: str, default: Any = None) -> Any:
        if isinstance(row, Mapping):
            return row.get(key, default)
        return getattr(row, key, default)

    items = list(rows)
    unhealthy = [
        str(value(row, "name", "unknown"))[:80]
        for row in items
        if not bool(value(row, "healthy", False))
    ]
    bounded_limit = max(1, int(limit))
    summary = {
        "total": len(items),
        "healthy": len(items) - len(unhealthy),
        "unhealthy": unhealthy[:bounded_limit],
        "unhealthy_truncated": max(0, len(unhealthy) - bounded_limit),
    }
    if probe_error:
        # Preserve the distinction between "there are no providers" and
        # "the provider health subsystem could not be queried".
        summary["probe_error"] = True
    return summary


def health_status(
    dependencies: Mapping[str, str],
    provider_groups: Mapping[str, Mapping[str, Any]],
) -> str:
    """Classify core dependency failure separately from optional degradation."""

    if any(str(value).casefold() != "healthy" for value in dependencies.values()):
        return "unhealthy"

    def is_degraded(group: Mapping[str, Any]) -> bool:
        if group.get("probe_error"):
            return True
        unhealthy = group.get("unhealthy", 0)
        if isinstance(unhealthy, (list, tuple, set)):
            return bool(unhealthy)
        try:
            return int(unhealthy or 0) > 0
        except (TypeError, ValueError):
            return True

    if any(is_degraded(group) for group in provider_groups.values()):
        return "degraded"
    return "healthy"
