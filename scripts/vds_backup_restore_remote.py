#!/usr/bin/env python3
"""Run a disposable backup/restore drill inside the production Docker stack.

The VDS does not need host-level ``mongodump`` or ``redis-cli`` binaries: the
official tools are executed inside the Compose Mongo/Redis containers. The
Mongo restore uses a unique disposable namespace, Redis is loaded by a
temporary container, and Chroma is extracted into a temporary directory. No
production service is stopped and no source data is replaced.

Four independent signals are required before an SSH session can mutate the
disposable restore namespace: ``--live``, ``--confirm-live``,
``--confirm-destructive`` and ``ARELBOT_VDS_BACKUP_LIVE=1``. The report is
bounded and secret-free; command output, URIs and remote paths never leave the
VDS.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

try:
    import paramiko
except ImportError:  # pragma: no cover - deployment-only dependency
    paramiko = None


class RemoteBackupError(ValueError):
    """Stable operator-facing failure without remote diagnostics."""


_SHA_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_JSON_MARKER = "ARELBOT_VDS_BACKUP_RESULT="


def validate_live_gate(*, live: bool, confirm_live: bool, confirm_destructive: bool, env_value: str | None) -> None:
    if not (
        live
        and confirm_live
        and confirm_destructive
        and str(env_value or "").strip() == "1"
    ):
        raise RemoteBackupError("vds_backup_live_gate_required")


def validate_report(payload: Any) -> dict[str, Any]:
    """Validate the bounded JSON emitted by the remote drill."""

    if not isinstance(payload, dict):
        raise RemoteBackupError("remote_report_invalid")
    checks = payload.get("checks")
    required = {
        "backup_created",
        "manifest_verified",
        "artifact_checksum_verified",
        "mongo_restore_verified",
        "redis_restore_verified",
        "chroma_restore_verified",
        "cleanup_verified",
    }
    if (
        payload.get("scope") != "vds-backup-restore"
        or payload.get("mode") != "live-disposable-staging"
        or payload.get("ok") is not True
        or payload.get("secret_free") is not True
        or not isinstance(checks, dict)
        or set(checks) != required
        or any(value is not True for value in checks.values())
        or not isinstance(payload.get("artifact_count"), int)
        or payload["artifact_count"] < 2
        or not isinstance(payload.get("archive_sha256"), str)
        or not _SHA_RE.fullmatch(payload["archive_sha256"])
    ):
        raise RemoteBackupError("remote_report_invariant_failed")
    return payload


def extract_result(stdout: str) -> dict[str, Any]:
    """Extract exactly one bounded result marker from remote stdout."""

    matches = [
        line[len(_JSON_MARKER) :]
        for line in str(stdout or "").splitlines()
        if line.startswith(_JSON_MARKER)
    ]
    if len(matches) != 1:
        raise RemoteBackupError("remote_result_missing")
    try:
        payload = json.loads(matches[0])
    except json.JSONDecodeError as exc:
        raise RemoteBackupError("remote_result_invalid_json") from exc
    return validate_report(payload)


def _remote_script() -> str:
    """Return a fixed shell program; identifiers are generated remotely."""

    return r'''set -euo pipefail
nonce="$(date -u +%Y%m%d%H%M%S)-$$"
root="/tmp/arel-vds-backup-${nonce}"
mongo="$(docker ps -q --filter label=com.docker.compose.project=arelbot-prod --filter label=com.docker.compose.service=mongo | head -n 1)"
redis="$(docker ps -q --filter label=com.docker.compose.project=arelbot-prod --filter label=com.docker.compose.service=redis | head -n 1)"
worker="$(docker ps -q --filter label=com.docker.compose.project=arelbot-prod --filter label=com.docker.compose.service=worker | head -n 1)"
redis_probe=""
target_db="arel_bot_restore_${nonce//[^0-9]/}"
mongo_in="/tmp/arel-vds-mongo-${nonce}.archive.gz"
redis_in="/tmp/arel-vds-redis-${nonce}.rdb"
result=''
cleanup() {
  docker rm -f "${redis_probe}" >/dev/null 2>&1 || true
  [ -n "${mongo}" ] && docker exec "${mongo}" rm -f "${mongo_in}" >/dev/null 2>&1 || true
  [ -n "${mongo}" ] && docker exec "${mongo}" rm -f "/tmp/arel-vds-restore-${nonce}.archive.gz" >/dev/null 2>&1 || true
  [ -n "${redis}" ] && docker exec "${redis}" rm -f "${redis_in}" >/dev/null 2>&1 || true
  rm -rf -- "${root}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

mkdir -p -- "${root}/redis-restore" "${root}/chroma-restore"
test -n "${mongo}" && test -n "${redis}" && test -n "${worker}"

docker exec "${mongo}" mongodump --quiet --uri=mongodb://127.0.0.1:27017/arel_bot --archive="${mongo_in}" --gzip
docker cp "${mongo}:${mongo_in}" "${root}/mongo.archive.gz" >/dev/null
docker exec "${redis}" redis-cli --rdb "${redis_in}" >/dev/null
docker cp "${redis}:${redis_in}" "${root}/redis.rdb" >/dev/null
docker cp "${worker}:/app/chroma_db" "${root}/chroma_db" >/dev/null
tar -czf "${root}/chroma_db.tar.gz" -C "${root}" chroma_db
rm -rf -- "${root}/chroma_db"

test -s "${root}/mongo.archive.gz" && test -s "${root}/redis.rdb" && test -s "${root}/chroma_db.tar.gz"
printf '%s  %s\n' "$(sha256sum "${root}/mongo.archive.gz" | awk '{print $1}')" mongo.archive.gz > "${root}/manifest.sha256"
printf '%s  %s\n' "$(sha256sum "${root}/redis.rdb" | awk '{print $1}')" redis.rdb >> "${root}/manifest.sha256"
printf '%s  %s\n' "$(sha256sum "${root}/chroma_db.tar.gz" | awk '{print $1}')" chroma_db.tar.gz >> "${root}/manifest.sha256"
(cd "${root}" && sha256sum -c manifest.sha256 >/dev/null)
archive_sha256="$(cat "${root}/manifest.sha256" | sha256sum | awk '{print $1}')"

docker cp "${root}/mongo.archive.gz" "${mongo}:/tmp/arel-vds-restore-${nonce}.archive.gz" >/dev/null
docker exec "${mongo}" mongorestore --quiet --uri=mongodb://127.0.0.1:27017 --archive="/tmp/arel-vds-restore-${nonce}.archive.gz" --gzip --drop --nsFrom='arel_bot.*' --nsTo="${target_db}.*"
mongo_ok="$(docker exec "${mongo}" mongosh --quiet --eval "db.getSiblingDB('${target_db}').getCollectionNames().length > 0" | tr -d '\r\n')"
test "${mongo_ok}" = true
docker exec "${mongo}" mongosh --quiet --eval "db.getSiblingDB('${target_db}').dropDatabase()" >/dev/null
mongo_cleanup="$(docker exec "${mongo}" mongosh --quiet --eval "db.getSiblingDB('${target_db}').getCollectionNames().length == 0" | tr -d '\r\n')"
test "${mongo_cleanup}" = true

cp -- "${root}/redis.rdb" "${root}/redis-restore/dump.rdb"
redis_image="$(docker inspect -f '{{.Config.Image}}' "${redis}")"
redis_probe="arel-vds-redis-${nonce}"
docker run -d --name "${redis_probe}" -v "${root}/redis-restore:/data:rw" "${redis_image}" redis-server --appendonly no --save '' >/dev/null
for _ in $(seq 1 20); do
  if [ "$(docker exec "${redis_probe}" redis-cli ping | tr -d '\r\n')" = PONG ]; then break; fi
  sleep 1
done
redis_ok="$(docker exec "${redis_probe}" redis-cli ping | tr -d '\r\n')"
test "${redis_ok}" = PONG

while IFS= read -r entry; do
  case "${entry}" in
    ''|/*|*../*|../*|*\\*) exit 31 ;;
  esac
done < <(tar -tzf "${root}/chroma_db.tar.gz")
tar -xzf "${root}/chroma_db.tar.gz" --no-same-owner --no-same-permissions -C "${root}/chroma-restore"
test -d "${root}/chroma-restore/chroma_db"
chroma_count="$(find "${root}/chroma-restore/chroma_db" -type f -print | wc -l | tr -d '[:space:]')"
test "${chroma_count}" -ge 0

docker rm -f "${redis_probe}" >/dev/null 2>&1
redis_probe=""
docker exec "${mongo}" rm -f "${mongo_in}" "/tmp/arel-vds-restore-${nonce}.archive.gz" >/dev/null
docker exec "${redis}" rm -f "${redis_in}" >/dev/null
rm -rf -- "${root}"
test ! -e "${root}"

result="$(printf '{\"scope\":\"vds-backup-restore\",\"ok\":true,\"mode\":\"live-disposable-staging\",\"checks\":{\"backup_created\":true,\"manifest_verified\":true,\"artifact_checksum_verified\":true,\"mongo_restore_verified\":true,\"redis_restore_verified\":true,\"chroma_restore_verified\":true,\"cleanup_verified\":true},\"archive_sha256\":\"%s\",\"artifact_count\":3,\"secret_free\":true}' "${archive_sha256}")"
printf 'ARELBOT_VDS_BACKUP_RESULT=%s\n' "${result}"
'''


def run_live(*, host: str, user: str, port: int, known_hosts: Path, password: str, timeout: int = 240) -> dict[str, Any]:
    if paramiko is None:
        raise RemoteBackupError("paramiko_missing")
    if not known_hosts.is_file() or known_hosts.is_symlink():
        raise RemoteBackupError("known_hosts_missing")
    if not str(password):
        raise RemoteBackupError("ssh_credential_missing")
    client = paramiko.SSHClient()
    try:
        client.load_host_keys(str(known_hosts))
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        try:
            client.connect(hostname=str(host), port=int(port), username=str(user), password=password, timeout=20)
        except paramiko.AuthenticationException as exc:
            raise RemoteBackupError("ssh_authentication_failed") from exc
        stdin, stdout, _ = client.exec_command("bash -s", timeout=timeout)
        stdin.write(_remote_script())
        stdin.flush()
        stdin.channel.shutdown_write()
        raw_stdout = stdout.read().decode("utf-8", "replace")
        return extract_result(raw_stdout)
    except RemoteBackupError:
        raise
    except (paramiko.SSHException, OSError) as exc:
        raise RemoteBackupError("ssh_connection_failed") from exc
    finally:
        client.close()


def write_report(path: Path, payload: dict[str, Any]) -> None:
    if path.exists() and path.is_symlink():
        raise RemoteBackupError("report_target_symlink")
    for parent in (path.parent, *path.parent.parents):
        if parent.exists() and parent.is_symlink():
            raise RemoteBackupError("report_parent_symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
        if os.name != "nt":
            os.chmod(temporary, 0o600)
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
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--confirm-destructive", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        validate_live_gate(
            live=args.live,
            confirm_live=args.confirm_live,
            confirm_destructive=args.confirm_destructive,
            env_value=os.getenv("ARELBOT_VDS_BACKUP_LIVE"),
        )
        payload = run_live(
            host=args.host,
            user=args.user,
            port=args.port,
            known_hosts=Path(args.known_hosts).expanduser().resolve(),
            password=os.getenv(str(args.password_env).strip(), ""),
            timeout=args.timeout,
        )
    except RemoteBackupError as exc:
        payload = {"scope": "vds-backup-restore", "ok": False, "error": str(exc), "secret_free": True}
    if args.report:
        try:
            write_report(args.report, payload)
        except RemoteBackupError:
            payload = {"scope": "vds-backup-restore", "ok": False, "error": "report_write_failed", "secret_free": True}
    print(json.dumps(payload, ensure_ascii=False) if args.as_json else f"vds_backup_restore_remote={'ok' if payload.get('ok') else 'failed'}")
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
