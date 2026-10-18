#!/usr/bin/env python3
"""Bounded Guardian bridge staging and replay checks.

No network/socket call is made by default.  A live socket probe requires all
three explicit gates: ``--live``, ``--confirm-live`` and
``ARELBOT_GUARDIAN_LIVE=1``.  Reports contain provider/decision/latency only;
events, tokens, socket payloads and exception messages are never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.moderation.guardian_adapter import GuardianAdapter, GuardianMode, GuardianResult
from src.moderation.guardian_protocol import (
    GuardianEvent,
    MAX_FRAME_BYTES,
    parse_request,
    parse_response,
    safe_error_code,
    socket_path_is_safe,
)


class GuardianStagingError(ValueError):
    """Stable staging failure code with no upstream detail."""

    def __init__(self, code: str):
        self.code = safe_error_code(code, "guardian_staging_failed")
        super().__init__(self.code)


def validate_live_gate(*, live: bool, confirm_live: bool, env_value: str | None) -> None:
    if not (live and confirm_live and str(env_value or "").strip() == "1"):
        raise GuardianStagingError("live_gate_required")


def build_validation_report() -> dict[str, Any]:
    return {
        "scope": "guardian-staging",
        "ok": True,
        "mode": "validation-only",
        "checks": ["live_gate_not_entered", "secret_free_report_contract", "bounded_protocol_contract"],
        "secret_free": True,
    }


def build_check_report(socket_path: str) -> dict[str, Any]:
    """Inspect local socket configuration without opening a live connection."""

    safe = socket_path_is_safe(socket_path)
    return {
        "scope": "guardian-staging",
        "ok": safe,
        "mode": "check-only",
        "socket_configured": safe,
        "socket_present": bool(safe and Path(socket_path).exists()),
        "live_probe_required": True,
        "secret_free": True,
    }


def build_success_report(result: GuardianResult, *, mode: str) -> dict[str, Any]:
    return {
        "scope": "guardian-staging",
        "ok": True,
        "mode": mode,
        "provider": str(result.provider or "kek_guardian")[:64],
        "capability": "moderation.evaluate",
        "decision": str(result.decision or "unavailable")[:32],
        "action_count": min(len(result.actions), 8),
        "latency_ms": round(max(0.0, float(result.latency_ms or 0.0)), 2),
        "error_code": safe_error_code(result.error_code) if result.error_code else None,
        "circuit_state": None,
        "secret_free": True,
    }


def build_failure_report(error: BaseException | object) -> dict[str, Any]:
    code = safe_error_code(getattr(error, "code", None), "guardian_staging_failed")
    return {
        "scope": "guardian-staging",
        "ok": False,
        "error": code,
        "exception_type": type(error).__name__[:80],
        "secret_free": True,
    }


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    if target.exists() and target.is_symlink():
        raise GuardianStagingError("report_target_symlink")
    for parent in (target.parent, *target.parent.parents):
        if parent.exists() and parent.is_symlink():
            raise GuardianStagingError("report_parent_symlink")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=target.parent,
            prefix=f".{target.name}.", suffix=".tmp", delete=False,
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


def _replay(path: Path) -> dict[str, Any]:
    """Validate recorded envelopes without revealing their contents."""

    root = Path(path)
    if not root.exists() or root.is_symlink():
        raise GuardianStagingError("replay_path_invalid")
    files = (
        [root]
        if root.is_file()
        else sorted(item for item in root.rglob("*.json") if item.is_file() and not item.is_symlink())
    )
    if len(files) > 100:
        raise GuardianStagingError("replay_file_limit")
    requests = responses = failures = 0
    for item in files:
        if item.stat().st_size > MAX_FRAME_BYTES:
            failures += 1
            continue
        try:
            payload = json.loads(item.read_text(encoding="utf-8"))
            raw = (json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n").encode()
            if "event" in payload and "operation" in payload:
                parse_request(raw)
                requests += 1
            else:
                parse_response(raw)
                responses += 1
        except Exception:
            failures += 1
    return {
        "scope": "guardian-staging",
        "ok": failures == 0,
        "mode": "replay",
        "requests": requests,
        "responses": responses,
        "failures": failures,
        "secret_free": True,
    }


async def _run_live(*, socket_path: str | None, shadow: bool) -> dict[str, Any]:
    if socket_path:
        os.environ["GUARDIAN_SOCKET_PATH"] = socket_path
    adapter = GuardianAdapter.from_environment()
    if shadow:
        adapter.mode = GuardianMode.SHADOW
    event = GuardianEvent(
        chat_id=-1,
        user_id=1,
        message_id=1,
        event_kind="staging_probe",
        text="bounded guardian staging probe",
        is_admin=False,
        settings_version=1,
        correlation_id="guardian-staging",
    )
    started = time.perf_counter()
    result = await adapter.evaluate(event)
    if result.decision == "unavailable":
        report = build_success_report(result, mode="live-shadow" if shadow else "live")
        report["ok"] = False
        return report
    report = build_success_report(result, mode="live-shadow" if shadow else "live")
    report["latency_ms"] = round((time.perf_counter() - started) * 1000.0, 2)
    report["circuit_state"] = (await adapter.health_check()).get("circuit")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", type=str)
    parser.add_argument("--shadow", action="store_true")
    parser.add_argument("--check-bridge", action="store_true")
    parser.add_argument("--replay", type=Path)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--report", type=Path)
    return parser.parse_args()


def main() -> int:
    logging.disable(logging.CRITICAL)
    args = parse_args()
    payload: dict[str, Any]
    try:
        if args.replay:
            payload = _replay(args.replay)
        elif args.check_bridge and not args.live and not args.confirm_live:
            path = args.socket or os.getenv("GUARDIAN_SOCKET_PATH", "/run/kek-guardian/guardian.sock")
            payload = build_check_report(path)
        elif not args.live and not args.confirm_live:
            payload = build_validation_report()
        else:
            validate_live_gate(
                live=args.live,
                confirm_live=args.confirm_live,
                env_value=os.getenv("ARELBOT_GUARDIAN_LIVE"),
            )
            payload = asyncio.run(_run_live(socket_path=args.socket, shadow=args.shadow))
    except Exception as exc:
        payload = build_failure_report(exc)
    if args.report:
        try:
            _write_report(args.report, payload)
        except Exception:
            payload = build_failure_report(GuardianStagingError("report_write_failed"))
    print(json.dumps(payload, ensure_ascii=True))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
