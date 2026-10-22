#!/usr/bin/env python3
"""Verify an Arelbot backup and optionally restore its persistence artifacts.

Database restoration is destructive by design and therefore requires the
explicit ``--confirm`` flag.  Verification never changes the target system.
The MongoDB connection string never reaches the command line: the tools
read it from a private configuration file, because every process on the host
can read the argument vector of another process.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from pathlib import PurePosixPath
from typing import Callable, Mapping
from urllib.parse import urlsplit, urlunsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.security.tool_credentials import mongo_tool_config, mongo_tool_environment


CommandRunner = Callable[[list[str], dict[str, str]], None]


class RestoreError(RuntimeError):
    pass


def _mongo_uri_without_database(uri: str) -> str:
    """Remove the database path so mongorestore can apply nsFrom/nsTo.

    When a Mongo URI includes a database, mongorestore scopes the archive to
    that database before namespace remapping. Supplying the target database
    therefore makes a source namespace match zero documents. Credentials and
    query options are preserved; only the path is removed.
    """

    try:
        parsed = urlsplit(str(uri))
    except ValueError as exc:
        raise RestoreError("invalid Mongo URI") from exc
    if parsed.scheme not in {"mongodb", "mongodb+srv"} or not parsed.netloc:
        raise RestoreError("invalid Mongo URI")
    return urlunsplit((parsed.scheme, parsed.netloc, "", parsed.query, ""))


def _safe_target_path(path: Path) -> Path:
    """Reject symlink targets/parents before a restore writes to disk."""

    raw = Path(path)
    if raw.is_symlink():
        raise RestoreError("unsafe restore target")
    for parent in (raw.parent, *raw.parent.parents):
        if parent.is_symlink():
            raise RestoreError("unsafe restore target")
    return raw.resolve()


def _extract_chroma_archive(archive_path: Path, destination: Path) -> None:
    """Extract only regular files and directories below ``destination``.

    ``TarFile.extractall`` has historically been unsafe for untrusted archives:
    symlink, hardlink and special-file entries can escape the destination even
    when their textual names look harmless. Chroma backups are application
    state, so restore must reject those entries before writing anything.
    """

    destination = destination.resolve()
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        for member in members:
            name = str(member.name or "")
            posix_name = PurePosixPath(name)
            if (
                not name
                or posix_name.is_absolute()
                or name.startswith(("/", "\\"))
                or any(part in {"", ".", ".."} for part in posix_name.parts if part != ".")
                or not (member.isdir() or member.isreg())
            ):
                raise RestoreError("unsafe Chroma archive entry")
            target = (destination / posix_name).resolve()
            if target != destination and destination not in target.parents:
                raise RestoreError("unsafe Chroma archive entry")

        for member in members:
            target = (destination / PurePosixPath(member.name)).resolve()
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            stream = archive.extractfile(member)
            if stream is None:
                raise RestoreError("invalid Chroma archive file")
            with stream, target.open("wb") as output:
                shutil.copyfileobj(stream, output, length=1024 * 1024)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_backup(backup_dir: Path) -> dict:
    raw_backup_dir = Path(backup_dir)
    if raw_backup_dir.is_symlink():
        raise RestoreError("unsafe backup directory")
    backup_dir = raw_backup_dir.resolve()
    manifest_path = backup_dir / "BACKUP.json"
    if not manifest_path.is_file():
        raise RestoreError("BACKUP.json is missing")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RestoreError("BACKUP.json is invalid") from exc
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("artifacts"), dict):
        raise RestoreError("unsupported backup schema")
    for name, metadata in manifest["artifacts"].items():
        if not isinstance(name, str) or not name or not isinstance(metadata, dict):
            raise RestoreError("backup artifact metadata is invalid")
        raw_path = backup_dir / name
        # Check the un-resolved path first. A symlink pointing to another file
        # inside the backup directory must not be accepted as an artifact.
        if raw_path.is_symlink():
            raise RestoreError(f"artifact is missing or unsafe: {name}")
        path = raw_path.resolve()
        if backup_dir not in path.parents or not path.is_file() or path.is_symlink():
            raise RestoreError(f"artifact is missing or unsafe: {name}")
        expected_sha = metadata.get("sha256")
        expected_bytes = metadata.get("bytes")
        if (
            not isinstance(expected_sha, str)
            or not re.fullmatch(r"[0-9a-f]{64}", expected_sha)
            or not isinstance(expected_bytes, int)
            or isinstance(expected_bytes, bool)
            or expected_bytes < 0
        ):
            raise RestoreError(f"artifact metadata is invalid: {name}")
        if sha256(path) != expected_sha or path.stat().st_size != expected_bytes:
            raise RestoreError(f"artifact checksum mismatch: {name}")
    return manifest


def restore_backup(backup_dir: Path, *, mongo_uri: str, redis_rdb_target: Path | None = None,
                   chroma_target: Path | None = None, confirm: bool = False,
                   mongo_source_database: str | None = None,
                   mongo_target_database: str | None = None,
                   command_runner: CommandRunner | None = None) -> dict:
    manifest = verify_backup(backup_dir)
    if not confirm:
        return manifest
    if bool(mongo_source_database) != bool(mongo_target_database):
        raise RestoreError("both Mongo source and target database names are required for remapping")
    if mongo_source_database and mongo_target_database:
        for value in (mongo_source_database, mongo_target_database):
            if not value.replace("_", "").replace("-", "").isalnum() or len(value) > 64:
                raise RestoreError("invalid Mongo database name")
    run_command = command_runner or _run_quiet
    artifacts = manifest["artifacts"]
    mongo = Path(backup_dir) / "mongo.archive.gz"
    if "mongo.archive.gz" in artifacts:
        restore_uri = (
            _mongo_uri_without_database(mongo_uri)
            if mongo_source_database and mongo_target_database
            else mongo_uri
        )
        with mongo_tool_config(restore_uri) as mongo_config:
            command = ["mongorestore", f"--config={mongo_config}", f"--archive={mongo}", "--gzip", "--drop"]
            if mongo_source_database and mongo_target_database:
                command.extend(
                    [
                        f"--nsFrom={mongo_source_database}.*",
                        f"--nsTo={mongo_target_database}.*",
                    ]
                )
            run_command(command, mongo_tool_environment(restore_uri))
    if redis_rdb_target is not None and "redis.rdb" in artifacts:
        redis_rdb_target = _safe_target_path(redis_rdb_target)
        if redis_rdb_target.exists() and not redis_rdb_target.is_file():
            raise RestoreError("unsafe Redis RDB target")
        redis_rdb_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(backup_dir) / "redis.rdb", redis_rdb_target)
    if chroma_target is not None and "chroma_db.tar.gz" in artifacts:
        chroma_target = _safe_target_path(chroma_target)
        chroma_target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="arel-chroma-", dir=chroma_target.parent) as temp:
            temp_path = Path(temp)
            _extract_chroma_archive(Path(backup_dir) / "chroma_db.tar.gz", temp_path)
            if chroma_target.exists():
                if chroma_target.is_symlink() or not chroma_target.is_dir():
                    raise RestoreError("unsafe Chroma target")
                shutil.rmtree(chroma_target)
            shutil.move(str(temp_path), str(chroma_target))
    return manifest


def _run_quiet(command: list[str], environment: Mapping[str, str]) -> None:
    try:
        result = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(environment),
        )
    except FileNotFoundError as exc:
        raise RestoreError(f"required command is missing: {command[0]}") from exc
    if result.returncode:
        raise RestoreError(f"restore command failed: {command[0]} (exit {result.returncode})")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("backup_dir", type=Path)
    parser.add_argument("--mongo-uri", required=True)
    parser.add_argument("--redis-rdb-target", type=Path)
    parser.add_argument("--chroma-target", type=Path)
    parser.add_argument("--mongo-source-database")
    parser.add_argument("--mongo-target-database")
    parser.add_argument("--confirm", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        manifest = restore_backup(
            args.backup_dir,
            mongo_uri=args.mongo_uri,
            redis_rdb_target=args.redis_rdb_target,
            chroma_target=args.chroma_target,
            mongo_source_database=args.mongo_source_database,
            mongo_target_database=args.mongo_target_database,
            confirm=args.confirm,
        )
    except RestoreError as exc:
        print(f"restore failed: {exc}", file=sys.stderr)
        return 1
    print("backup verified" if not args.confirm else "backup restored")
    print(f"artifacts={len(manifest['artifacts'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
