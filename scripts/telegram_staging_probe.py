#!/usr/bin/env python3
"""Run a bounded, read-only Telegram staging probe.

The normal mode validates only the deployment token shape and makes no
network request.  A live probe requires ``--live``, ``--confirm-live`` and
``ARELBOT_TELEGRAM_STAGING_LIVE=1`` simultaneously.  The live path reads bot
identity, webhook status and published commands; it never sends a message,
changes webhook configuration or mutates bot commands.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.telegram_request import build_bot
from scripts.production_preflight import read_dotenv


class ProbeError(ValueError):
    """Safe, stable staging-probe failure code."""


def write_report(path: Path, payload: dict[str, Any]) -> None:
    """Atomically archive a bounded, secret-free probe report.

    The report is deployment evidence, so a symlink target or symlinked parent
    is rejected before writing.  The temporary file is fsynced and replaced in
    one operation to avoid leaving a truncated report after an interruption.
    """

    target = Path(path)
    if target.exists() and target.is_symlink():
        raise ProbeError("report_target_symlink")
    parent = target.parent
    for candidate in (parent, *parent.parents):
        if candidate.exists() and candidate.is_symlink():
            raise ProbeError("report_parent_symlink")
    parent = parent.resolve()
    parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=parent,
            prefix=f".{target.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2)
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


_TOKEN_RE = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{20,}$")
_COMMAND_RE = re.compile(r"^[a-z0-9_]{1,32}$")
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")
_DEFAULT_PENDING_WARNING_THRESHOLD = 100
_MAX_PENDING_THRESHOLD = 100000


def validate_token_shape(token: str | None) -> bool:
    """Validate a token without logging or returning its contents."""

    return bool(_TOKEN_RE.fullmatch(str(token or "").strip()))


def validate_live_gate(*, live: bool, confirm_live: bool, env_value: str | None) -> None:
    """Require three independent signals before contacting Telegram."""

    if not (live and confirm_live and str(env_value or "").strip() == "1"):
        raise ProbeError("live_gate_required")


def apply_pending_threshold(payload: dict[str, Any], max_pending_updates: int | None) -> dict[str, Any]:
    """Apply an optional fail-closed backlog gate to an already safe report."""

    if max_pending_updates is None:
        return payload
    try:
        threshold = int(max_pending_updates)
    except (TypeError, ValueError) as exc:
        raise ProbeError("pending_threshold_invalid") from exc
    if not 0 <= threshold <= _MAX_PENDING_THRESHOLD:
        raise ProbeError("pending_threshold_invalid")
    webhook = payload.get("webhook") if isinstance(payload, dict) else None
    pending = webhook.get("pending_updates") if isinstance(webhook, dict) else None
    try:
        exceeded = int(pending or 0) > threshold
    except (TypeError, ValueError):
        exceeded = True
    if not exceeded:
        return payload
    gated = dict(payload)
    gated["ok"] = False
    gated["error"] = "pending_updates_exceeded"
    return gated


def _safe_username(value: Any) -> str | None:
    candidate = str(value or "").strip()
    return candidate if _USERNAME_RE.fullmatch(candidate) else None


def _safe_commands(items: Any) -> list[str]:
    values: set[str] = set()
    for item in items or ():
        command = str(getattr(item, "command", "") or "").strip().casefold()
        if _COMMAND_RE.fullmatch(command):
            values.add(command)
    return sorted(values)


async def probe_bot(
    bot: Any,
    *,
    pending_warning_threshold: int = _DEFAULT_PENDING_WARNING_THRESHOLD,
) -> dict[str, Any]:
    """Read Telegram state and return a bounded, secret-free report."""

    try:
        pending_warning_threshold = max(0, min(_MAX_PENDING_THRESHOLD, int(pending_warning_threshold)))
    except (TypeError, ValueError):
        pending_warning_threshold = _DEFAULT_PENDING_WARNING_THRESHOLD

    try:
        identity = await bot.get_me()
    except Exception as exc:
        raise ProbeError("get_me_failed") from exc
    if not bool(getattr(identity, "is_bot", False)) or not int(getattr(identity, "id", 0) or 0):
        raise ProbeError("bot_identity_invalid")

    try:
        webhook = await bot.get_webhook_info()
    except Exception as exc:
        raise ProbeError("get_webhook_info_failed") from exc

    try:
        commands = await bot.get_my_commands()
    except Exception as exc:
        raise ProbeError("get_my_commands_failed") from exc

    pending = getattr(webhook, "pending_update_count", 0) or 0
    try:
        pending = max(0, min(100000, int(pending)))
    except (TypeError, ValueError):
        pending = 0

    command_names = _safe_commands(commands)
    warnings: list[str] = []
    if pending > pending_warning_threshold:
        warnings.append("telegram_pending_updates_high")
    if bool(str(getattr(webhook, "last_error_message", "") or "").strip()):
        warnings.append("telegram_webhook_last_error")
    return {
        "ok": True,
        "checks": ["get_me", "get_webhook_info", "get_my_commands"],
        "warnings": warnings,
        "bot": {
            "id": int(identity.id),
            "is_bot": True,
            "username": _safe_username(getattr(identity, "username", None)),
        },
        "webhook": {
            "configured": bool(str(getattr(webhook, "url", "") or "").strip()),
            "pending_updates": pending,
            "pending_warning_threshold": pending_warning_threshold,
            "backlog_status": "warning" if pending > pending_warning_threshold else "ok",
            "last_error_present": bool(str(getattr(webhook, "last_error_message", "") or "").strip()),
        },
        "commands": {"count": len(command_names), "names": command_names},
    }


async def _run_live(token: str, *, pending_warning_threshold: int) -> dict[str, Any]:
    bot = build_bot(token)
    try:
        return await probe_bot(bot, pending_warning_threshold=pending_warning_threshold)
    finally:
        await bot.shutdown()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--live", action="store_true", help="contact Telegram read-only")
    parser.add_argument("--confirm-live", action="store_true", help="second live confirmation")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--report", type=Path, help="write a safe JSON report atomically")
    parser.add_argument(
        "--max-pending-updates",
        type=int,
        help="fail the live probe when Telegram backlog exceeds this bounded threshold",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        validate_live_gate(
            live=args.live,
            confirm_live=args.confirm_live,
            env_value=os.getenv("ARELBOT_TELEGRAM_STAGING_LIVE"),
        ) if args.live or args.confirm_live else None
        values = read_dotenv(args.env_file.resolve())
        token = values.get("TELEGRAM_TOKEN", "")
        if not validate_token_shape(token):
            raise ProbeError("telegram_token_invalid")
        threshold = _DEFAULT_PENDING_WARNING_THRESHOLD
        if args.max_pending_updates is not None:
            try:
                threshold = int(args.max_pending_updates)
            except (TypeError, ValueError) as exc:
                raise ProbeError("pending_threshold_invalid") from exc
            if not 0 <= threshold <= _MAX_PENDING_THRESHOLD:
                raise ProbeError("pending_threshold_invalid")
        if not args.live:
            payload = {"ok": True, "mode": "validation-only", "checks": ["token_shape"]}
        else:
            payload = {"mode": "live-read-only", **asyncio.run(_run_live(token, pending_warning_threshold=threshold))}
            payload = apply_pending_threshold(payload, args.max_pending_updates)
    except ProbeError as exc:
        payload = {"ok": False, "error": str(exc)}
        if args.report:
            try:
                write_report(args.report, payload)
            except ProbeError:
                payload = {"ok": False, "error": "probe_failed"}
        print(json.dumps(payload, ensure_ascii=False) if args.as_json else f"telegram_staging=failed error={exc}")
        return 1
    except Exception:
        payload = {"ok": False, "error": "probe_failed"}
        if args.report:
            try:
                write_report(args.report, payload)
            except ProbeError:
                pass
        print(json.dumps(payload, ensure_ascii=False) if args.as_json else "telegram_staging=failed error=probe_failed")
        return 1
    if args.report:
        try:
            write_report(args.report, payload)
        except ProbeError:
            payload = {"ok": False, "error": "report_write_failed"}
            print(json.dumps(payload, ensure_ascii=False) if args.as_json else "telegram_staging=failed error=report_write_failed")
            return 1
    print(json.dumps(payload, ensure_ascii=False) if args.as_json else f"telegram_staging=ok mode={payload['mode']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
