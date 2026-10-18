#!/usr/bin/env python3
"""Run deterministic Arelbot release checks and emit a safe JSON report.

The default gate is offline and deterministic.  ``--docker`` adds the real
Compose test image, MongoDB/Redis health and container pytest run without
turning live Telegram or third-party sites into a false release dependency.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.security.log_redaction import redact


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    exit_code: int
    duration_ms: float
    output_tail: str


def run_check(name: str, command: list[str], *, timeout: int = 300) -> CheckResult:
    started = time.perf_counter()
    environment = os.environ.copy()
    environment.setdefault("PYTHONIOENCODING", "utf-8")
    environment.setdefault("PYTHONUTF8", "1")
    try:
        result = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
            env=environment,
        )
        output = result.stdout or ""
        code = int(result.returncode)
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") + "\ncheck_timeout"
        code = 124
    except OSError as exc:
        output = f"{type(exc).__name__}: command unavailable"
        code = 127
    duration_ms = (time.perf_counter() - started) * 1000
    safe_output = redact(output)[-2000:]
    return CheckResult(name, code == 0, code, round(duration_ms, 2), safe_output)


def write_report(path: Path, report: dict) -> None:
    """Atomically write a machine-readable gate report without following links."""

    path = Path(path)
    if path.exists() and path.is_symlink():
        raise OSError("report target cannot be a symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise OSError("report parent cannot be a symlink")
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(report, handle, ensure_ascii=True, indent=2)
            handle.write("\n")
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def commands(*, include_docker: bool, requests: int, concurrency: int) -> list[tuple[str, list[str]]]:
    python = sys.executable
    checks = [
        ("architecture", [python, "scripts/architecture_check.py", "--strict"]),
        ("feature_catalog", [python, "scripts/feature_catalog_audit.py"]),
        ("provider_matrix", [python, "scripts/provider_matrix.py"]),
        ("provider_governance", [python, "scripts/provider_governance.py"]),
        ("compileall", [python, "-m", "compileall", "-q", "src", "main.py", "scripts"]),
        ("pytest", [python, "-m", "pytest", "-q"]),
        ("security_replay", [python, "scripts/security_replay.py"]),
        (
            "provider_load",
            [python, "scripts/provider_load_test.py", "--requests", str(requests), "--concurrency", str(concurrency)],
        ),
        (
            "media_delivery_replay",
            [python, "-m", "pytest", "-q", "src/tests/test_media_delivery_replay.py"],
        ),
        (
            "monitoring_alert_delivery_replay",
            [python, "-m", "pytest", "-q", "src/tests/test_monitoring_alert_staging.py"],
        ),
        (
            "media_replay_matrix",
            [
                python,
                "scripts/media_replay_matrix.py",
                "--config",
                "docs/media-replay-matrix.json",
                "--report",
                "deploy/evidence/media-replay-matrix.json",
            ],
        ),
        (
            "media_queue_load",
            [
                python,
                "scripts/media_queue_load_test.py",
                "--jobs",
                "100",
                "--workers",
                "10",
                "--chats",
                "100",
            ],
        ),
        ("evidence_audit", [python, "scripts/evidence_audit.py"]),
    ]
    if include_docker:
        compose = ["docker", "compose", "--profile", "test"]
        monitoring_dir = (PROJECT_ROOT / "deploy" / "monitoring").resolve().as_posix()
        empty_secret = (PROJECT_ROOT / ".docker" / "empty-ca.pem").resolve().as_posix()
        checks.extend(
            [
                (
                    "compose_profiles",
                    [
                        "docker",
                        "compose",
                        "--profile",
                        "dev",
                        "--profile",
                        "prod",
                        "--profile",
                        "monitoring",
                        "config",
                        "--quiet",
                    ],
                ),
                ("docker_build", compose + ["build", "test"]),
                ("docker_pytest", compose + ["run", "--rm", "test", "python", "-m", "pytest", "-q"]),
                ("docker_health", compose + ["run", "--rm", "test", "python", "scripts/healthcheck.py"]),
                (
                    "mongo_migration_smoke",
                    compose
                    + [
                        "run",
                        "--rm",
                        "-e",
                        "MONGODB_URI=mongodb://mongo:27017/arel_bot_migration_smoke",
                        "test",
                        "python",
                        "scripts/mongo_migration_smoke.py",
                    ],
                ),
                ("docker_provider_matrix", compose + ["run", "--rm", "test", "python", "scripts/provider_matrix.py"]),
                ("mongo_ledger_smoke", compose + ["run", "--rm", "test", "python", "scripts/mongo_notification_ledger_smoke.py"]),
                ("mongo_sticker_operation_smoke", compose + ["run", "--rm", "test", "python", "scripts/mongo_sticker_operation_smoke.py"]),
                (
                    "monitoring_config",
                    [
                        "docker", "run", "--rm", "--entrypoint", "promtool",
                        "-v", f"{monitoring_dir}:/etc/prometheus:ro",
                        "-v", f"{empty_secret}:/run/secrets/arelbot_observability_token:ro",
                        "prom/prometheus:v3.13.4",
                        "check", "config", "/etc/prometheus/prometheus.yml",
                    ],
                ),
                (
                    "monitoring_alert_replay",
                    [
                        "docker", "run", "--rm", "--entrypoint", "promtool",
                        "-v", f"{monitoring_dir}:/etc/prometheus:ro",
                        "prom/prometheus:v3.13.4",
                        "test", "rules", "/etc/prometheus/alerts.test.yml",
                    ],
                ),
            ]
        )
    return checks


def main() -> int:
    # Windows PowerShell may expose a CP1254 stdout while provider metadata
    # contains valid Turkish/Unicode text. Keep the machine-readable report
    # UTF-8 and never let console encoding mask a real gate result.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker", action="store_true", help="include the Compose test profile")
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--report", type=Path, help="atomically write the safe JSON gate report")
    args = parser.parse_args()
    if args.requests < 1 or args.concurrency < 1:
        parser.error("--requests and --concurrency must be positive")

    results = [
        run_check(name, command, timeout=900 if name in {"docker_build", "docker_pytest"} else 300)
        for name, command in commands(
            include_docker=args.docker,
            requests=args.requests,
            concurrency=args.concurrency,
        )
    ]
    report = {
        "ok": all(item.ok for item in results),
        "scope": "offline-plus-docker" if args.docker else "offline",
        "checks": [asdict(item) for item in results],
        "live_external_e2e": "not_included",
    }
    if args.report:
        try:
            write_report(args.report, report)
        except OSError:
            report["ok"] = False
            report["report_write_error"] = True
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
