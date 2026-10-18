#!/usr/bin/env python3
"""Create a verifiable Arelbot persistence backup.

The script intentionally delegates database format handling to the official
MongoDB and Redis CLIs.  It never prints connection URIs (which may contain
credentials) and writes a SHA-256 manifest only after every artifact is
complete.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


CommandRunner = Callable[[list[str]], None]


class BackupError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_quiet(command: list[str]) -> None:
    try:
        result = subprocess.run(command, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError as exc:
        raise BackupError(f"required command is missing: {command[0]}") from exc
    if result.returncode:
        raise BackupError(f"backup command failed: {command[0]} (exit {result.returncode})")


def archive_directory(source: Path, target: Path) -> bool:
    if not source.exists():
        return False
    if not source.is_dir() or source.is_symlink():
        raise BackupError("invalid backup directory")
    with tarfile.open(target, "w:gz") as archive:
        for path in sorted(source.rglob("*")):
            if path.is_symlink():
                raise BackupError("symlink in backup directory")
            if not path.is_dir() and not path.is_file():
                raise BackupError("special file in backup directory")
            archive.add(path, arcname=path.relative_to(source), recursive=False)
    return True


def create_backup(output_dir: Path, *, mongo_uri: str, redis_url: str,
                  chroma_dir: Path, now: datetime | None = None,
                  command_runner: CommandRunner | None = None) -> Path:
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    raw_output_dir = Path(output_dir)
    if raw_output_dir.exists() and raw_output_dir.is_symlink():
        raise BackupError("unsafe backup output directory")
    for parent in (raw_output_dir.parent, *raw_output_dir.parent.parents):
        if parent.exists() and parent.is_symlink():
            raise BackupError("unsafe backup output directory")
    output_dir = raw_output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    temp_root = Path(tempfile.mkdtemp(prefix=f"arel-backup-{stamp}-", dir=output_dir))
    final_dir = output_dir / f"arel-{stamp}"
    if final_dir.exists():
        final_dir = output_dir / f"arel-{stamp}-{os.urandom(4).hex()}"
    run_command = command_runner or run_quiet
    try:
        mongo_archive = temp_root / "mongo.archive.gz"
        redis_snapshot = temp_root / "redis.rdb"
        run_command(["mongodump", f"--uri={mongo_uri}", f"--archive={mongo_archive}", "--gzip"])
        run_command(["redis-cli", "-u", redis_url, "--rdb", str(redis_snapshot)])
        artifacts = [mongo_archive, redis_snapshot]
        chroma_archive = temp_root / "chroma_db.tar.gz"
        if archive_directory(chroma_dir, chroma_archive):
            artifacts.append(chroma_archive)
        manifest = {
            "schema_version": 1,
            "created_at": (now or datetime.now(timezone.utc)).isoformat(),
            "artifacts": {path.name: {"sha256": sha256(path), "bytes": path.stat().st_size} for path in artifacts},
        }
        (temp_root / "BACKUP.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        temp_root.replace(final_dir)
        return final_dir
    except Exception:
        shutil.rmtree(temp_root, ignore_errors=True)
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("backups"))
    parser.add_argument("--mongo-uri", default=os.getenv("MONGODB_URI", "mongodb://localhost:27017/arel_bot"))
    parser.add_argument("--redis-url", default=os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    parser.add_argument("--chroma-dir", type=Path, default=Path("chroma_db"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        path = create_backup(
            args.output_dir,
            mongo_uri=args.mongo_uri,
            redis_url=args.redis_url,
            chroma_dir=args.chroma_dir,
        )
    except BackupError as exc:
        print(f"backup failed: {exc}", file=sys.stderr)
        return 1
    print(f"backup created: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
