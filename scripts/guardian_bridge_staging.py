#!/usr/bin/env python3
"""Secret-free checks for the optional Kek Guardian Unix-socket bridge.

The default command performs no socket/network operation.  ``--self-test``
starts an in-process bridge with a synthetic *allow* evaluator, exercises the
wire protocol, and tears it down.  It is a protocol smoke test only; it never
claims that the real Guardian decision engine is installed or healthy.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import stat
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.moderation.guardian_protocol import (  # noqa: E402
    GuardianEvent,
    build_request,
    parse_response,
    safe_error_code,
    socket_path_is_safe,
)
from src.moderation.guardian_server import (  # noqa: E402
    GuardianBridgeError,
    GuardianEvaluation,
    GuardianUnixSocketServer,
)


class GuardianBridgeStagingError(ValueError):
    """Bounded staging error with no provider/user payload."""

    def __init__(self, code: str):
        self.code = safe_error_code(code, "guardian_bridge_staging_failed")
        super().__init__(self.code)


def build_validation_report() -> dict[str, Any]:
    return {
        "scope": "guardian-bridge-staging",
        "ok": True,
        "mode": "validation-only",
        "checks": ["secret_free_report_contract", "bounded_protocol_contract", "peer_allowlist_required"],
        "real_guardian_engine_tested": False,
        "secret_free": True,
    }


def build_check_report(socket_path: str) -> dict[str, Any]:
    safe = socket_path_is_safe(socket_path)
    path = Path(socket_path)
    present = bool(safe and path.exists() and not path.is_symlink())
    mode: int | None = None
    if present:
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
        except OSError:
            present = False
    return {
        "scope": "guardian-bridge-staging",
        "ok": safe,
        "mode": "check-only",
        "socket_configured": safe,
        "socket_present": present,
        "socket_mode": oct(mode) if mode is not None else None,
        "peer_allowlist_required": True,
        "real_guardian_engine_tested": False,
        "secret_free": True,
    }


def build_failure_report(error: BaseException | object) -> dict[str, Any]:
    return {
        "scope": "guardian-bridge-staging",
        "ok": False,
        "error": safe_error_code(getattr(error, "code", None), "guardian_bridge_staging_failed"),
        "exception_type": type(error).__name__[:80],
        "secret_free": True,
    }


def _event() -> GuardianEvent:
    return GuardianEvent(
        chat_id=-100000001,
        user_id=1,
        message_id=1,
        event_kind="staging_probe",
        text="bounded bridge self test",
        is_admin=False,
        settings_version=1,
        correlation_id="bridge-staging",
    )


async def _self_test() -> dict[str, Any]:
    if os.name == "nt":
        raise GuardianBridgeStagingError("unix_socket_unsupported")
    with tempfile.TemporaryDirectory(prefix="arel-guardian-bridge-") as directory:
        socket_path = str(Path(directory) / "guardian.sock")

        async def evaluator(_event: GuardianEvent) -> GuardianEvaluation:
            return GuardianEvaluation(decision="allow")

        server = GuardianUnixSocketServer(
            socket_path=socket_path,
            evaluator=evaluator,
            allowed_peer_uids={os.getuid()},
            request_timeout_seconds=0.5,
        )
        started = time.perf_counter()
        await server.start()
        try:
            reader, writer = await asyncio.open_unix_connection(socket_path, limit=17 * 1024)
            raw = build_request(_event(), ttl_seconds=2)
            writer.write(raw)
            await writer.drain()
            response = await asyncio.wait_for(reader.readuntil(b"\n"), timeout=1)
            writer.close()
            await writer.wait_closed()
            parsed = parse_response(response)
            if parsed.provider != "kek_guardian" or parsed.decision != "allow":
                raise GuardianBridgeStagingError("self_test_response_invalid")
            return {
                "scope": "guardian-bridge-staging",
                "ok": True,
                "mode": "self-test",
                "provider": parsed.provider,
                "decision": parsed.decision,
                "latency_ms": round((time.perf_counter() - started) * 1000.0, 2),
                "metrics": server.metrics(),
                "real_guardian_engine_tested": False,
                "secret_free": True,
            }
        finally:
            await server.stop()


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    if target.exists() and target.is_symlink():
        raise GuardianBridgeStagingError("report_target_symlink")
    for parent in (target.parent, *target.parent.parents):
        if parent.exists() and parent.is_symlink():
            raise GuardianBridgeStagingError("report_parent_symlink")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    if temporary.exists() and temporary.is_symlink():
        raise GuardianBridgeStagingError("report_temp_symlink")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
        if os.name != "nt":
            os.chmod(temporary, 0o600)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", default=os.getenv("GUARDIAN_SOCKET_PATH", "/run/kek-guardian/guardian.sock"))
    parser.add_argument("--check", action="store_true", help="inspect socket path without opening it")
    parser.add_argument("--self-test", action="store_true", help="run synthetic local protocol test")
    parser.add_argument("--report", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.self_test:
            payload = asyncio.run(_self_test())
        elif args.check:
            payload = build_check_report(args.socket)
        else:
            payload = build_validation_report()
    except Exception as exc:
        payload = build_failure_report(exc)
    if args.report:
        try:
            _write_report(args.report, payload)
        except Exception:
            payload = build_failure_report(GuardianBridgeStagingError("report_write_failed"))
    print(json.dumps(payload, ensure_ascii=True))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
