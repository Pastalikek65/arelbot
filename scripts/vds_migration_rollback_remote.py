#!/usr/bin/env python3
"""Run the Mongo migration rollback/forward smoke in a disposable VDS database.

The worker receives a unique database name through ``MONGODB_URI`` and the
registered smoke drops that database after validating the latest version -> 3
-> latest version.
Only a bounded success report is returned; remote logs and connection details
are discarded.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

try:
    import paramiko
except ImportError:  # pragma: no cover - deployment-only dependency
    paramiko = None


class MigrationEvidenceError(ValueError):
    """Stable operator-facing failure."""


def validate_live_gate(*, live: bool, confirm_live: bool, confirm_destructive: bool, env_value: str | None) -> None:
    if not (live and confirm_live and confirm_destructive and str(env_value or "").strip() == "1"):
        raise MigrationEvidenceError("vds_migration_live_gate_required")


def _remote_script() -> str:
    return r'''set -euo pipefail
nonce="$(date -u +%Y%m%d%H%M%S)-$$"
database="arelbot_migration_live_${nonce//[^0-9]/}"
mongo="$(docker ps -q --filter label=com.docker.compose.project=arelbot-prod --filter label=com.docker.compose.service=mongo | head -n 1)"
worker="$(docker ps -q --filter label=com.docker.compose.project=arelbot-prod --filter label=com.docker.compose.service=worker | head -n 1)"
test -n "${mongo}" && test -n "${worker}"
# The smoke performs migrate twice, rolls back to v3, migrates to the latest,
# checks the resulting indexes, and drops this disposable database in finally.
docker exec -e "MONGODB_URI=mongodb://mongo:27017/${database}" "${worker}" python scripts/mongo_migration_smoke.py >/dev/null 2>&1
remaining="$(docker exec "${mongo}" mongosh --quiet --eval "db.getSiblingDB('${database}').getCollectionNames().length == 0" | tr -d '\r\n')"
test "${remaining}" = true
printf 'ARELBOT_VDS_MIGRATION_RESULT=%s\n' '{"scope":"vds-migration-rollback","ok":true,"mode":"live-disposable-staging","checks":{"rollback_to_v3":true,"forward_to_latest":true,"index_contract_verified":true,"disposable_database_cleaned":true,"production_namespace_untouched":true},"secret_free":true}'
'''


def _extract(stdout: str) -> dict[str, Any]:
    marker = "ARELBOT_VDS_MIGRATION_RESULT="
    rows = [line[len(marker) :] for line in str(stdout or "").splitlines() if line.startswith(marker)]
    if len(rows) != 1:
        raise MigrationEvidenceError("vds_migration_result_missing")
    try:
        payload = json.loads(rows[0])
    except json.JSONDecodeError as exc:
        raise MigrationEvidenceError("vds_migration_result_invalid") from exc
    checks = payload.get("checks") if isinstance(payload, dict) else None
    if not (
        payload.get("scope") == "vds-migration-rollback"
        and payload.get("ok") is True
        and payload.get("mode") == "live-disposable-staging"
        and isinstance(checks, dict)
        and checks
        and all(value is True for value in checks.values())
        and payload.get("secret_free") is True
    ):
        raise MigrationEvidenceError("vds_migration_result_invariant_failed")
    return payload


def run_live(*, host: str, user: str, port: int, known_hosts: Path, password: str, timeout: int = 240) -> dict[str, Any]:
    if paramiko is None:
        raise MigrationEvidenceError("paramiko_missing")
    if not known_hosts.is_file() or known_hosts.is_symlink():
        raise MigrationEvidenceError("known_hosts_missing")
    if not password:
        raise MigrationEvidenceError("ssh_credential_missing")
    client = paramiko.SSHClient()
    try:
        client.load_host_keys(str(known_hosts))
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        client.connect(hostname=str(host), port=int(port), username=str(user), password=password, timeout=20)
        stdin, stdout, _ = client.exec_command("bash -s", timeout=timeout)
        stdin.write(_remote_script())
        stdin.flush()
        stdin.channel.shutdown_write()
        payload = _extract(stdout.read().decode("utf-8", "replace"))
        if stdout.channel.recv_exit_status() != 0:
            raise MigrationEvidenceError("vds_migration_remote_failed")
        return payload
    except MigrationEvidenceError:
        raise
    except (paramiko.SSHException, OSError) as exc:
        raise MigrationEvidenceError("ssh_connection_failed") from exc
    finally:
        client.close()


def write_report(path: Path, payload: dict[str, Any]) -> None:
    if path.exists() and path.is_symlink():
        raise MigrationEvidenceError("report_target_symlink")
    for parent in (path.parent, *path.parent.parents):
        if parent.exists() and parent.is_symlink():
            raise MigrationEvidenceError("report_parent_symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", default="root")
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--known-hosts", default=str(Path.home() / ".ssh" / "known_hosts"))
    parser.add_argument("--password-env", default="AREL_SSH_PASSWORD")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--confirm-destructive", action="store_true")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    try:
        validate_live_gate(
            live=args.live,
            confirm_live=args.confirm_live,
            confirm_destructive=args.confirm_destructive,
            env_value=os.getenv("ARELBOT_VDS_MIGRATION_LIVE"),
        )
        payload = run_live(
            host=args.host,
            user=args.user,
            port=args.port,
            known_hosts=Path(args.known_hosts).expanduser().resolve(),
            password=os.getenv(str(args.password_env).strip(), ""),
        )
    except MigrationEvidenceError as exc:
        payload = {"scope": "vds-migration-rollback", "ok": False, "error": str(exc), "secret_free": True}
    if args.report:
        try:
            write_report(args.report, payload)
        except MigrationEvidenceError:
            payload = {"scope": "vds-migration-rollback", "ok": False, "error": "report_write_failed", "secret_free": True}
    print(json.dumps(payload, ensure_ascii=False) if args.as_json else f"vds_migration={'ok' if payload.get('ok') else 'failed'}")
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
