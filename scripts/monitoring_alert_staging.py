#!/usr/bin/env python3
"""Verify a disposable Alertmanager notification delivery path.

The default command is validation-only. Live delivery requires ``--live``,
``--confirm-live``, ``--confirm-notification`` and
``ARELBOT_MONITORING_LIVE=1``. The runner only targets a loopback Alertmanager
API and posts a bounded canary alert; it never accepts an arbitrary webhook URL
from the command line. Reports contain counters and operation states only.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx


class MonitoringProbeError(ValueError):
    """Stable operator-facing monitoring staging error."""


_METRIC_LINE_RE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(?P<labels>[^}]*)\})?\s+(?P<value>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*$"
)
_WEBHOOK_LABEL_RE = re.compile(r'(?:^|,)\s*integration="webhook"\s*(?:,|$)')
_CANARY_RE = re.compile(r"^ArelbotStagingDeliveryCanary_[a-f0-9]{16}$")
_TOKEN_RE = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b")
_SENSITIVE_KEY_RE = re.compile(r"(?i)(?:token|cookie|password|authorization|api[_-]?key|private[_-]?key|secret)")


def validate_live_gate(
    *,
    live: bool,
    confirm_live: bool,
    confirm_notification: bool,
    env_value: str | None,
) -> None:
    if not (
        live
        and confirm_live
        and confirm_notification
        and str(env_value or "").strip() == "1"
    ):
        raise MonitoringProbeError("monitoring_live_gate_required")


def validate_local_base_url(value: str) -> str:
    raw = str(value or "").strip()
    try:
        parsed = urlsplit(raw)
    except ValueError as exc:
        raise MonitoringProbeError("alertmanager_url_invalid") from exc
    host = (parsed.hostname or "").casefold().rstrip(".")
    if (
        parsed.scheme != "http"
        or host not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise MonitoringProbeError("alertmanager_url_invalid")
    try:
        port = parsed.port or 9093
    except ValueError as exc:
        raise MonitoringProbeError("alertmanager_url_invalid") from exc
    if not 1 <= port <= 65535:
        raise MonitoringProbeError("alertmanager_url_invalid")
    netloc = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    return urlunsplit(("http", netloc, "", "", "")).rstrip("/")


def validate_monitoring_inputs(
    base_url: str,
    *,
    timeout_seconds: float = 15.0,
    poll_interval_seconds: float = 5.0,
    max_attempts: int = 3,
) -> str:
    """Validate all canary inputs before a live Alertmanager request."""

    normalized = validate_local_base_url(base_url)
    try:
        timeout = float(timeout_seconds)
        poll_interval = float(poll_interval_seconds)
        attempts = int(max_attempts)
    except (TypeError, ValueError) as exc:
        raise MonitoringProbeError("monitoring_timeout_invalid") from exc
    if not 0.1 <= timeout <= 60 or not 0 <= poll_interval <= 30:
        raise MonitoringProbeError("monitoring_timeout_invalid")
    if not 1 <= attempts <= 5:
        raise MonitoringProbeError("monitoring_attempts_invalid")
    return normalized


def parse_notification_counter(metrics: str, metric_name: str) -> int:
    """Read one bounded webhook integration counter from Prometheus text."""

    total = 0
    wanted = str(metric_name or "").strip()
    if not wanted:
        return 0
    for raw_line in str(metrics or "").splitlines():
        match = _METRIC_LINE_RE.fullmatch(raw_line.strip())
        if not match or match.group("name") != wanted:
            continue
        labels = match.group("labels") or ""
        if not _WEBHOOK_LABEL_RE.search(labels):
            continue
        try:
            value = float(match.group("value"))
        except ValueError:
            continue
        if value >= 0:
            total = max(total, int(value))
    return total


def _safe_report(path: Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    if target.exists() and target.is_symlink():
        raise MonitoringProbeError("report_target_symlink")
    for parent in (target.parent, *target.parent.parents):
        if parent.exists() and parent.is_symlink():
            raise MonitoringProbeError("report_parent_symlink")
    if _contains_sensitive(payload):
        raise MonitoringProbeError("report_secret_material")
    parent = target.parent.resolve()
    parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if os.name != "nt":
            os.chmod(temporary, 0o600)
        temporary.replace(target)
    except Exception:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def _contains_sensitive(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).casefold()
            if lowered == "secret_free" and isinstance(child, bool):
                continue
            if _SENSITIVE_KEY_RE.search(lowered) and child not in (None, False, "", 0):
                return True
            if _contains_sensitive(child):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_sensitive(item) for item in value)
    if isinstance(value, str):
        return bool(_TOKEN_RE.search(value) or "authorization:" in value.casefold())
    return False


async def run_live(
    base_url: str,
    *,
    client: httpx.AsyncClient | None = None,
    timeout_seconds: float = 15.0,
    poll_interval_seconds: float = 5.0,
    max_attempts: int = 3,
) -> dict[str, Any]:
    """Post a canary alert and prove Alertmanager counted its webhook delivery."""

    normalized = validate_monitoring_inputs(
        base_url,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        max_attempts=max_attempts,
    )
    owned = client is None
    if owned:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(float(timeout_seconds), connect=min(5.0, float(timeout_seconds))),
            follow_redirects=False,
        )
    assert client is not None
    alert_name = f"ArelbotStagingDeliveryCanary_{uuid.uuid4().hex[:16]}"
    canary_start = datetime.now(timezone.utc)
    canary_end = canary_start + timedelta(seconds=30)
    headers = {"Content-Type": "application/json"}
    payload = [
        {
            "labels": {
                "alertname": alert_name,
                "service": "arelbot-staging",
                "severity": "info",
            },
            "annotations": {"summary": "Arelbot staging delivery canary"},
            "startsAt": canary_start.isoformat().replace("+00:00", "Z"),
            "endsAt": canary_end.isoformat().replace("+00:00", "Z"),
            "generatorURL": "http://localhost/arelbot-staging-canary",
        }
    ]
    alerts: dict[str, bool] = {"webhook_canary": False}
    try:
        ready = await client.get(f"{normalized}/-/ready")
        if ready.status_code != 200:
            raise MonitoringProbeError("alertmanager_not_ready")
        baseline_response = await client.get(f"{normalized}/metrics")
        if baseline_response.status_code != 200:
            raise MonitoringProbeError("alertmanager_metrics_unavailable")
        baseline = parse_notification_counter(
            baseline_response.text,
            "alertmanager_notifications_total",
        )
        baseline_failed = parse_notification_counter(
            baseline_response.text,
            "alertmanager_notifications_failed_total",
        )
        delivered = False
        final_total = baseline
        final_failed = baseline_failed
        for attempt in range(int(max_attempts)):
            response = await client.post(
                f"{normalized}/api/v2/alerts",
                headers=headers,
                json=payload,
            )
            if response.status_code not in {200, 202}:
                raise MonitoringProbeError("alertmanager_canary_rejected")
            if poll_interval_seconds and attempt:
                await asyncio.sleep(float(poll_interval_seconds))
            metrics_response = await client.get(f"{normalized}/metrics")
            if metrics_response.status_code != 200:
                raise MonitoringProbeError("alertmanager_metrics_unavailable")
            final_total = parse_notification_counter(
                metrics_response.text,
                "alertmanager_notifications_total",
            )
            final_failed = parse_notification_counter(
                metrics_response.text,
                "alertmanager_notifications_failed_total",
            )
            if final_total > baseline and final_failed <= baseline_failed:
                delivered = True
                break
        alerts["webhook_canary"] = delivered
        return {
            "scope": "live-monitoring-alert-delivery",
            "ok": delivered,
            "alerts": alerts,
            "delivered": 1 if delivered else 0,
            "failed": 0 if delivered else 1,
            "attempts": int(attempt + 1),
            "baseline_notifications": baseline,
            "final_notifications": final_total,
            "baseline_failures": baseline_failed,
            "final_failures": final_failed,
            "secret_free": True,
        }
    except MonitoringProbeError:
        raise
    except (httpx.HTTPError, OSError) as exc:
        raise MonitoringProbeError("monitoring_delivery_failed") from exc
    finally:
        if owned:
            await client.aclose()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alertmanager-url", default="http://127.0.0.1:9093")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--confirm-notification", action="store_true")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--poll-interval", type=float, default=5.0)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--report", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        # Validation-only mode must still reject unsafe targets and bounds so
        # a copied command cannot silently become a probe of a remote host.
        validate_monitoring_inputs(
            args.alertmanager_url,
            timeout_seconds=args.timeout,
            poll_interval_seconds=args.poll_interval,
            max_attempts=args.max_attempts,
        )
        if not (args.live or args.confirm_live or args.confirm_notification):
            payload = {
                "scope": "monitoring-alert-delivery-validation",
                "ok": True,
                "mode": "validation-only",
                "checks": ["loopback_target_shape", "live_gate_not_entered"],
                "secret_free": True,
            }
        else:
            validate_live_gate(
                live=args.live,
                confirm_live=args.confirm_live,
                confirm_notification=args.confirm_notification,
                env_value=os.getenv("ARELBOT_MONITORING_LIVE"),
            )
            payload = asyncio.run(
                run_live(
                    args.alertmanager_url,
                    timeout_seconds=args.timeout,
                    poll_interval_seconds=args.poll_interval,
                    max_attempts=args.max_attempts,
                )
            )
    except MonitoringProbeError as exc:
        payload = {"scope": "live-monitoring-alert-delivery", "ok": False, "error": str(exc), "secret_free": True}
    except Exception:
        payload = {"scope": "live-monitoring-alert-delivery", "ok": False, "error": "monitoring_delivery_failed", "secret_free": True}
    if args.report:
        try:
            _safe_report(args.report, payload)
        except MonitoringProbeError:
            payload = {"scope": "live-monitoring-alert-delivery", "ok": False, "error": "report_write_failed", "secret_free": True}
    print(json.dumps(payload, ensure_ascii=False) if args.as_json else f"monitoring_alert_delivery={'ok' if payload.get('ok') else 'failed'}")
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
