#!/usr/bin/env python3
"""Exercise Mongo and Redis persistence backup/restore against Compose.

This is an explicit operator/staging check, not part of the default release
gate. It writes only disposable, namespaced sentinel data and removes it in a
``finally`` block. No credentials or command output are printed.

The MongoDB tools authenticate like the production deployment does: the
administrative credential is read from the configuration of the running
MongoDB container and handed to the tools through their standard input, so it
never reaches a command line. The Redis half uses the ``REDISCLI_AUTH``
environment variable of the Redis container.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import build_mongodb_uri
from src.security.tool_credentials import mongo_tool_config_document, mongosh_auth_script


class PersistenceSmokeError(RuntimeError):
    """Stable failure for the operator-facing persistence smoke."""


_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")

# Paths of the documents that carry the credential inside the MongoDB
# container.  They are written on the standard input of the container
# command and removed again when the smoke finishes.
MONGO_AUTH_SCRIPT = "/tmp/arel-mongo-auth.js"
MONGO_TOOL_CONFIG = "/tmp/arel-mongo-tool.yaml"
MONGO_HOST = "127.0.0.1:27017"
MONGO_AUTH_SOURCE = "admin"


def _run(
    command: Sequence[str],
    *,
    timeout: int = 120,
    check: bool = True,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            list(command),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            input=input_text if input_text is not None else None,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PersistenceSmokeError("persistence_command_failed") from exc
    if check and result.returncode:
        # Never copy Docker/Mongo/Redis diagnostics into an archived report.
        raise PersistenceSmokeError("persistence_command_failed")
    return result


def _compose_prefix(compose_file: Path, project_name: str | None) -> list[str]:
    command = ["docker", "compose", "--file", str(compose_file)]
    if project_name:
        command.extend(["--project-name", project_name])
    return command


def _compose_exec(prefix: Sequence[str], service: str, *args: str, timeout: int = 120) -> str:
    result = _run([*prefix, "exec", "-T", service, *args], timeout=timeout)
    return result.stdout.strip()


def _compose_exec_with_input(
    prefix: Sequence[str],
    service: str,
    document: str,
    *args: str,
    timeout: int = 120,
) -> str:
    """Run a container command with a document delivered on standard input.

    The document carries the database credential, so it must not become part
    of the argument vector of the container command.
    """

    result = _run([*prefix, "exec", "-T", service, *args], timeout=timeout, input_text=document)
    return result.stdout.strip()


def _mongo_admin_credential(prefix: Sequence[str]) -> tuple[str, str]:
    """Return the administrative MongoDB account of the running stack.

    The credential is read from the configuration of the MongoDB container.
    It never reaches a command line and it never has to be written to a file
    on the host.
    """

    listing = _run([*prefix, "ps", "-q", "mongo"], timeout=30).stdout.strip().splitlines()
    container = listing[0].strip() if listing else ""
    if not container:
        raise PersistenceSmokeError("mongo_container_missing")
    environment = _run(
        ["docker", "inspect", "-f", "{{range .Config.Env}}{{println .}}{{end}}", container],
        timeout=30,
    ).stdout
    user = ""
    secret = ""
    for line in environment.splitlines():
        name, _, value = line.partition("=")
        if name == "MONGO_INITDB_ROOT_USERNAME":
            user = value.strip()
        elif name == "MONGO_INITDB_ROOT_PASSWORD":
            secret = value.strip()
    if not user or not secret:
        raise PersistenceSmokeError("mongo_credential_missing")
    return user, secret


def _validate_name(value: str, label: str) -> str:
    if not _NAME_RE.fullmatch(value):
        raise PersistenceSmokeError(f"invalid_{label}")
    return value


def _safe_report(path: Path, payload: dict[str, Any]) -> None:
    """Write a bounded report without following a symlink target/parent."""

    target = Path(path)
    if target.exists() and target.is_symlink():
        raise PersistenceSmokeError("report_target_symlink")
    for parent in (target.parent, *target.parent.parents):
        if parent.exists() and parent.is_symlink():
            raise PersistenceSmokeError("report_parent_symlink")
    parent = target.parent.resolve()
    parent.mkdir(parents=True, exist_ok=True)
    temporary = parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if os.name != "nt":
            os.chmod(temporary, 0o600)
        temporary.replace(target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def run_smoke(*, compose_file: Path, project_name: str | None = None) -> dict[str, Any]:
    compose_file = compose_file.resolve()
    if not compose_file.is_file() or compose_file.is_symlink():
        raise PersistenceSmokeError("compose_file_invalid")
    prefix = _compose_prefix(compose_file, project_name)
    suffix = uuid.uuid4().hex[:12]
    source_db = _validate_name(f"arel_bot_persistence_smoke_{suffix}", "source_database")
    target_db = _validate_name(f"arel_bot_persistence_restore_{suffix}", "target_database")
    marker = f"arelbot-persistence-smoke-{suffix}"
    redis_key = f"arelbot:persistence:smoke:{suffix}"
    redis_value = marker
    archive_path = f"/tmp/arelbot-persistence-{suffix}.archive.gz"
    redis_snapshot = f"/tmp/arelbot-persistence-{suffix}.rdb"
    restore_container = f"arelbot-redis-restore-{suffix}"
    checks = {"mongo_dump_restore": False, "redis_snapshot_restore": False, "cleanup": False}
    redis_container_id = ""

    try:
        mongo_user, mongo_secret = _mongo_admin_credential(prefix)
        # The credential reaches the tools through their standard input: the
        # shell authenticates the session with a script and the database
        # tools read the connection string from a configuration file.
        _compose_exec_with_input(
            prefix,
            "mongo",
            mongosh_auth_script(mongo_user, mongo_secret, auth_source=MONGO_AUTH_SOURCE),
            "sh",
            "-c",
            f"cat > {MONGO_AUTH_SCRIPT}",
            timeout=60,
        )
        _compose_exec_with_input(
            prefix,
            "mongo",
            mongo_tool_config_document(
                build_mongodb_uri(mongo_user, mongo_secret, MONGO_HOST, "", auth_source=MONGO_AUTH_SOURCE)
            ),
            "sh",
            "-c",
            f"cat > {MONGO_TOOL_CONFIG}",
            timeout=60,
        )
        mongo_doc = (
            "const smokeDb=db.getSiblingDB(" + json.dumps(source_db) + ");"
            "smokeDb.smoke.insertOne({marker:" + json.dumps(marker) + "});"
        )
        _compose_exec(
            prefix,
            "mongo",
            "mongosh",
            "--quiet",
            "--eval",
            f"load('{MONGO_AUTH_SCRIPT}'); {mongo_doc}",
        )
        _compose_exec(
            prefix,
            "mongo",
            "mongodump",
            "--quiet",
            f"--config={MONGO_TOOL_CONFIG}",
            f"--db={source_db}",
            f"--archive={archive_path}",
            "--gzip",
        )
        _compose_exec(
            prefix,
            "mongo",
            "mongorestore",
            "--quiet",
            f"--config={MONGO_TOOL_CONFIG}",
            f"--archive={archive_path}",
            "--gzip",
            "--drop",
            f"--nsFrom={source_db}.*",
            f"--nsTo={target_db}.*",
        )
        verify_doc = (
            "const doc=db.getSiblingDB(" + json.dumps(target_db) + ")"
            ".smoke.findOne({marker:" + json.dumps(marker) + " });"
            "if (!doc) quit(2); print('ok');"
        )
        if (
            _compose_exec(
                prefix,
                "mongo",
                "mongosh",
                "--quiet",
                "--eval",
                f"load('{MONGO_AUTH_SCRIPT}'); {verify_doc}",
            )
            != "ok"
        ):
            raise PersistenceSmokeError("mongo_restore_verification_failed")
        checks["mongo_dump_restore"] = True

        _compose_exec(prefix, "redis", "redis-cli", "-n", "15", "SET", redis_key, redis_value)
        _compose_exec(prefix, "redis", "redis-cli", "-n", "15", "--rdb", redis_snapshot)
        container_result = _run([*prefix, "ps", "-q", "redis"], timeout=30)
        redis_container_id = container_result.stdout.strip().splitlines()[0] if container_result.stdout.strip() else ""
        if not redis_container_id:
            raise PersistenceSmokeError("redis_container_missing")

        with tempfile.TemporaryDirectory(prefix="arelbot-persistence-smoke-") as temporary_dir:
            host_dir = Path(temporary_dir).resolve()
            host_snapshot = host_dir / Path(redis_snapshot).name
            _run(["docker", "cp", f"{redis_container_id}:{redis_snapshot}", str(host_snapshot)], timeout=30)
            volume = f"{host_dir.as_posix()}:/data"
            _run(
                [
                    "docker", "run", "-d", "--name", restore_container,
                    "-v", volume, "redis:7.4-alpine", "redis-server",
                    "--dir", "/data", "--dbfilename", host_snapshot.name,
                    "--port", "6380", "--save", "", "--appendonly", "no",
                ],
                timeout=60,
            )
            try:
                restored = ""
                for _ in range(20):
                    ping = _run(
                        ["docker", "exec", restore_container, "redis-cli", "-p", "6380", "PING"],
                        timeout=10,
                        check=False,
                    )
                    if ping.returncode == 0 and ping.stdout.strip() == "PONG":
                        restored = _run(
                            ["docker", "exec", restore_container, "redis-cli", "-p", "6380", "-n", "15", "GET", redis_key],
                            timeout=10,
                        ).stdout.strip()
                        break
                    time.sleep(0.25)
                if restored != redis_value:
                    raise PersistenceSmokeError("redis_restore_verification_failed")
                checks["redis_snapshot_restore"] = True
            finally:
                _run(["docker", "rm", "-f", restore_container], timeout=30, check=False)
    finally:
        # Cleanup is best effort after a failure, but a successful run must
        # prove that both sentinel databases/keys were removed.
        cleanup_ok = True
        try:
            cleanup = (
                "db.getSiblingDB(" + json.dumps(source_db) + ").dropDatabase();"
                "db.getSiblingDB(" + json.dumps(target_db) + ").dropDatabase();"
            )
            _compose_exec(
                prefix,
                "mongo",
                "mongosh",
                "--quiet",
                "--eval",
                f"load('{MONGO_AUTH_SCRIPT}'); {cleanup}",
                timeout=30,
            )
            _compose_exec(prefix, "mongo", "rm", "-f", archive_path, timeout=30)
            _compose_exec(prefix, "redis", "redis-cli", "-n", "15", "DEL", redis_key, timeout=30)
            _compose_exec(prefix, "redis", "rm", "-f", redis_snapshot, timeout=30)
        except PersistenceSmokeError:
            cleanup_ok = False
        # The credential documents never survive the smoke.
        try:
            _compose_exec(prefix, "mongo", "rm", "-f", MONGO_AUTH_SCRIPT, MONGO_TOOL_CONFIG, timeout=30)
        except PersistenceSmokeError:
            cleanup_ok = False
        checks["cleanup"] = cleanup_ok

    if not all(checks.values()):
        raise PersistenceSmokeError("persistence_cleanup_failed" if not checks["cleanup"] else "persistence_restore_failed")
    return {"ok": True, "mode": "compose-smoke", "checks": checks, "secret_free": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose-file", type=Path, default=Path("docker-compose.yml"))
    parser.add_argument("--project-name")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        report = run_smoke(compose_file=args.compose_file, project_name=args.project_name)
        if args.report:
            _safe_report(args.report, report)
        print(json.dumps(report, ensure_ascii=False))
        return 0
    except PersistenceSmokeError as exc:
        report = {"ok": False, "error": str(exc)}
        if args.report:
            try:
                _safe_report(args.report, report)
            except PersistenceSmokeError:
                report = {"ok": False, "error": "report_write_failed"}
        print(json.dumps(report, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
