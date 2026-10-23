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


# The Compose checks that build an image or start a container need more than
# the default budget: the image pull and the health wait of the dependencies
# happen inside the check.  The isolation probe starts no dependency, but it
# shares the container startup with the other Compose checks, so it keeps the
# same budget as the first ``docker compose run``.
DOCKER_TIMEOUT_CHECKS = frozenset({"docker_build", "docker_pytest", "docker_test_env_isolation"})

# The synthetic environment of the Compose test profile.  The file is read by
# the container through ``env_file`` and it is the interpolation source of
# the test profile, so the Compose checks need it to exist.  The committed
# example is the single source of truth; the copy is ignored by Git.
TEST_ENV_FILE = PROJECT_ROOT / ".env.test"
TEST_ENV_EXAMPLE = PROJECT_ROOT / ".env.test.example"


def check_timeout(name: str) -> int:
    """Return the timeout budget of a check in seconds."""

    return 900 if name in DOCKER_TIMEOUT_CHECKS else 300


def ensure_test_env_file() -> Path:
    """Create the synthetic test environment file when it is missing.

    The Compose ``test`` profile reads the file through ``env_file`` and the
    gate interpolates the test profile from it, so the file has to exist
    before the Compose checks run.  The committed example is the source of
    truth, so a missing file is created from it; the copy stays ignored by
    Git and never enters a commit.
    """

    if TEST_ENV_FILE.is_file():
        return TEST_ENV_FILE
    if not TEST_ENV_EXAMPLE.is_file():
        raise OSError("the committed synthetic test environment example is missing")
    TEST_ENV_FILE.write_bytes(TEST_ENV_EXAMPLE.read_bytes())
    return TEST_ENV_FILE


def run_check(name: str, command: list[str], *, timeout: int = 300) -> CheckResult:
    started = time.perf_counter()
    environment = os.environ.copy()
    # Compose reads its explicit env files itself. Python checks must never
    # import a workstation/CI .env back into a deliberately isolated fixture.
    environment["PYTHON_DOTENV_DISABLED"] = "1"
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
        # The governance report is a code-level inventory of the source
        # metadata.  It deliberately runs without ``--require-approved``:
        # that flag belongs to the production preflight
        # (``scripts/production_preflight.py --require-provider-approval``)
        # and would keep this gate permanently red for the more than ten
        # features whose source governance is legitimately ``pending``.
        #
        # The license registry check closes the remaining gap: the
        # architecture step rejects a host that a provider module names as a
        # literal URL or as a host allowlist entry when it is not recorded in
        # ``licenses.json``, so a new external provider cannot enter with
        # ``pending`` governance while staying unregistered.  A record only
        # makes a host visible in the inventory; ``source_governance:
        # pending`` still means the source is not approved.
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
        # The phase contract audit is part of the gate: a phase whose
        # deterministic check or required artifact is missing is a real
        # contract violation and must not pass as green.  The audit exits 1
        # only for a blocking phase; a phase that merely waits for live
        # deployment evidence stays green because CI can never produce it.
        # The audit reads the committed ``deploy/evidence/release-gate.json``,
        # so that artifact has to be regenerated whenever the check list of
        # the gate changes.
        ("phase_audit", [python, "scripts/phase_audit.py"]),
    ]
    if include_docker:
        # The test profile is interpolated from the synthetic environment file
        # and not from the project `.env`: the project file holds the
        # credentials of a running deployment, and the test container must
        # never receive them. Graph validation uses the same synthetic file.
        # A dedicated project also isolates the test volumes from a developer's
        # existing, possibly unauthenticated local Mongo/Redis deployment.
        compose_base = ["docker", "compose", "--project-name", "arelbot-gate", "--env-file", ".env.test"]
        compose = compose_base + ["--profile", "test"]
        monitoring_dir = (PROJECT_ROOT / "deploy" / "monitoring").resolve().as_posix()
        empty_secret = (PROJECT_ROOT / ".docker" / "empty-ca.pem").resolve().as_posix()
        checks.extend(
            [
                (
                    "compose_profiles",
                    compose_base + [
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
                # The test profile reads `.env.test` through `env_file`, so
                # the container must not carry a credential of the production
                # environment.  The probe reports the length and the verdict
                # of every credential variable and never a value, so the
                # report of this check cannot leak one either.
                #
                # The probe reads only the environment of the container, so it
                # runs with `--no-deps`: starting MongoDB and Redis and
                # waiting for their health check would add the image pull and
                # the health wait to a check that needs no database.  Compose
                # resolves the `environment:` block of the service on the host
                # and injects it into the container, so the probe still sees
                # the interpolated connection strings without the
        # dependencies.
                (
                    "docker_test_env_isolation",
                    compose + ["run", "--no-deps", "--rm", "test", "python", "scripts/test_env_probe.py"],
                ),
                ("docker_pytest", compose + ["run", "--rm", "test", "python", "-m", "pytest", "-q"]),
                ("docker_health", compose + ["run", "--rm", "test", "python", "scripts/healthcheck.py"]),
                (
                    "mongo_migration_smoke",
                    compose
                    + [
                        "run",
                        "--rm",
                        "-e",
                        # The disposable database travels as a name because the
                        # connection string carries the credential and must
                        # never reach a command line.
                        "MONGODB_SMOKE_DATABASE=arel_bot_migration_smoke",
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
    # Offline pytest also resolves the Compose test profile when Docker is
    # installed, so it needs this file before any checks start.
    try:
        ensure_test_env_file()
    except OSError as exc:
        print(f"the synthetic test environment file is unavailable ({type(exc).__name__})")
        return 1

    planned = commands(include_docker=args.docker, requests=args.requests, concurrency=args.concurrency)
    results = [
        run_check(name, command, timeout=check_timeout(name))
        for name, command in planned if name != "phase_audit"
    ]
    # Audit this run after its checks finish. Reading yesterday's report here
    # makes a failed report prevent every subsequent run from recovering.
    previous_path = PROJECT_ROOT / "deploy/evidence/release-gate.json"
    try:
        previous = json.loads(previous_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = {}
    current_names = {item.name for item in results}
    historical = [row for row in previous.get("checks", [])
                  if row.get("name") not in current_names and row.get("name") != "phase_audit"]
    with tempfile.TemporaryDirectory(prefix="arel-gate-") as directory:
        candidate = Path(directory) / "current.json"
        candidate.write_text(json.dumps({"checks": historical + [asdict(item) for item in results]}), encoding="utf-8")
        for name, command in planned:
            if name == "phase_audit":
                results.append(run_check(name, command + ["--gate", str(candidate)], timeout=check_timeout(name)))
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
