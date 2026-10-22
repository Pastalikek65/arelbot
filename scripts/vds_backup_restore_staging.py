#!/usr/bin/env python3
"""Run an explicitly gated VDS backup/restore staging drill.

The default invocation is validation-only and never touches MongoDB, Redis or
the filesystem outside report validation. A real drill requires ``--live``,
``--confirm-live``, ``--confirm-destructive`` and
``ARELBOT_VDS_BACKUP_LIVE=1``. The restore target must be a disposable staging
namespace/host; this command does not infer that safety from an URI.

Reports contain only bounded check names and an artifact-content digest. URIs,
credentials, host paths and subprocess output are deliberately excluded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.backup import BackupError, create_backup
from scripts.restore import RestoreError, restore_backup, verify_backup


class VdsEvidenceError(ValueError):
    """Stable operator-facing failure code."""


_DATABASE_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def validate_live_gate(
    *,
    live: bool,
    confirm_live: bool,
    confirm_destructive: bool,
    env_value: str | None,
) -> None:
    """Require four independent signals before a destructive staging drill."""

    if not (
        live
        and confirm_live
        and confirm_destructive
        and str(env_value or "").strip() == "1"
    ):
        raise VdsEvidenceError("vds_backup_live_gate_required")


def validate_database_names(source: str, target: str) -> tuple[str, str]:
    source_name = str(source or "").strip()
    target_name = str(target or "").strip()
    if not _DATABASE_RE.fullmatch(source_name) or not _DATABASE_RE.fullmatch(target_name):
        raise VdsEvidenceError("mongo_database_name_invalid")
    if source_name == target_name:
        raise VdsEvidenceError("mongo_restore_namespace_must_differ")
    return source_name, target_name


def _validate_mongo_uri(value: str, label: str) -> str:
    raw = str(value or "").strip()
    try:
        parsed = urlsplit(raw)
    except ValueError as exc:
        raise VdsEvidenceError(f"{label}_invalid") from exc
    if parsed.scheme not in {"mongodb", "mongodb+srv"} or not parsed.netloc:
        raise VdsEvidenceError(f"{label}_invalid")
    return raw


def artifact_content_digest(backup_dir: Path, manifest: dict[str, Any]) -> str:
    """Hash artifact names and bytes in stable order without exposing paths."""

    digest = hashlib.sha256()
    artifacts = manifest.get("artifacts") if isinstance(manifest, dict) else None
    if not isinstance(artifacts, dict) or not artifacts:
        raise VdsEvidenceError("backup_artifacts_missing")
    root = Path(backup_dir).resolve()
    for name in sorted(artifacts):
        if not isinstance(name, str) or not name or Path(name).name != name:
            raise VdsEvidenceError("backup_artifact_name_invalid")
        artifact = root / name
        if artifact.is_symlink() or not artifact.is_file():
            raise VdsEvidenceError("backup_artifact_missing")
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        with artifact.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    """Atomically write a secret-free report and reject symlink paths."""

    target = Path(path)
    if target.exists() and target.is_symlink():
        raise VdsEvidenceError("report_target_symlink")
    for parent in (target.parent, *target.parent.parents):
        if parent.exists() and parent.is_symlink():
            raise VdsEvidenceError("report_parent_symlink")
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


def run_live(
    *,
    output_dir: Path,
    mongo_uri: str,
    redis_url: str,
    chroma_dir: Path,
    restore_mongo_uri: str,
    restore_redis_target: Path,
    restore_chroma_target: Path,
    mongo_source_database: str,
    mongo_target_database: str,
) -> dict[str, Any]:
    """Create, verify and restore a disposable staging backup."""

    source_uri = _validate_mongo_uri(mongo_uri, "mongo_uri")
    target_uri = _validate_mongo_uri(restore_mongo_uri, "restore_mongo_uri")
    source_db, target_db = validate_database_names(
        mongo_source_database,
        mongo_target_database,
    )
    if not str(redis_url or "").strip().startswith(("redis://", "rediss://")):
        raise VdsEvidenceError("redis_url_invalid")
    if Path(restore_redis_target).is_symlink() or Path(restore_chroma_target).is_symlink():
        raise VdsEvidenceError("restore_target_symlink")

    checks: dict[str, bool] = {
        "backup_created": False,
        "manifest_verified": False,
        "artifact_checksum_verified": False,
        "restore_completed": False,
    }
    backup_dir: Path | None = None
    try:
        backup_dir = create_backup(
            output_dir,
            mongo_uri=source_uri,
            redis_url=str(redis_url).strip(),
            chroma_dir=Path(chroma_dir),
        )
        checks["backup_created"] = True
        manifest = verify_backup(backup_dir)
        checks["manifest_verified"] = True
        archive_sha256 = artifact_content_digest(backup_dir, manifest)
        checks["artifact_checksum_verified"] = True
        restore_backup(
            backup_dir,
            mongo_uri=target_uri,
            redis_rdb_target=Path(restore_redis_target),
            chroma_target=Path(restore_chroma_target),
            mongo_source_database=source_db,
            mongo_target_database=target_db,
            confirm=True,
        )
        checks["restore_completed"] = True
        return {
            "scope": "vds-backup-restore",
            "ok": all(checks.values()),
            "mode": "live-disposable-staging",
            "checks": checks,
            "archive_sha256": archive_sha256,
            "artifact_count": len(manifest.get("artifacts", {})),
            "secret_free": True,
        }
    except (BackupError, RestoreError, VdsEvidenceError) as exc:
        raise VdsEvidenceError("vds_backup_restore_failed") from exc


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("backups"))
    parser.add_argument("--chroma-dir", type=Path, default=Path("chroma_db"))
    parser.add_argument("--mongo-uri")
    parser.add_argument("--redis-url")
    parser.add_argument("--restore-mongo-uri")
    parser.add_argument("--restore-redis-target", type=Path)
    parser.add_argument("--restore-chroma-target", type=Path)
    parser.add_argument("--mongo-source-database")
    parser.add_argument("--mongo-target-database")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--confirm-destructive", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--report", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        if not (args.live or args.confirm_live or args.confirm_destructive):
            payload = {
                "scope": "vds-backup-restore-validation",
                "ok": True,
                "mode": "validation-only",
                "checks": ["destructive_gate_not_entered", "secret_free_report_contract"],
                "secret_free": True,
            }
        else:
            validate_live_gate(
                live=args.live,
                confirm_live=args.confirm_live,
                confirm_destructive=args.confirm_destructive,
                env_value=os.getenv("ARELBOT_VDS_BACKUP_LIVE"),
            )
            required = {
                "mongo_uri": args.mongo_uri,
                "redis_url": args.redis_url,
                "restore_mongo_uri": args.restore_mongo_uri,
                "restore_redis_target": args.restore_redis_target,
                "restore_chroma_target": args.restore_chroma_target,
                "mongo_source_database": args.mongo_source_database,
                "mongo_target_database": args.mongo_target_database,
            }
            if any(value in (None, "") for value in required.values()):
                raise VdsEvidenceError("vds_backup_restore_arguments_required")
            payload = run_live(
                output_dir=args.output_dir,
                mongo_uri=str(args.mongo_uri),
                redis_url=str(args.redis_url),
                chroma_dir=args.chroma_dir,
                restore_mongo_uri=str(args.restore_mongo_uri),
                restore_redis_target=Path(args.restore_redis_target),
                restore_chroma_target=Path(args.restore_chroma_target),
                mongo_source_database=str(args.mongo_source_database),
                mongo_target_database=str(args.mongo_target_database),
            )
    except VdsEvidenceError as exc:
        payload = {"scope": "vds-backup-restore", "ok": False, "error": str(exc), "secret_free": True}
    except Exception:
        payload = {"scope": "vds-backup-restore", "ok": False, "error": "vds_backup_restore_failed", "secret_free": True}
    if args.report:
        try:
            _write_report(args.report, payload)
        except VdsEvidenceError:
            payload = {"scope": "vds-backup-restore", "ok": False, "error": "report_write_failed", "secret_free": True}
    print(json.dumps(payload, ensure_ascii=False) if args.as_json else f"vds_backup_restore={'ok' if payload.get('ok') else 'failed'}")
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
