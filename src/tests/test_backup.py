import io
import hashlib
import json
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.backup import BackupError, create_backup
from scripts.restore import RestoreError, restore_backup, verify_backup


def test_backup_creates_sha256_manifest_without_printing_secrets(tmp_path):
    chroma = tmp_path / "chroma_db"
    chroma.mkdir()
    (chroma / "index.bin").write_bytes(b"fixture")
    output = tmp_path / "backups"

    with patch("scripts.backup.run_quiet") as run:
        def fake_run(command):
            target = command[2].split("=", 1)[1] if command[0] == "mongodump" else command[4]
            Path(target).write_bytes(b"mongo" if command[0] == "mongodump" else b"redis")
        run.side_effect = fake_run
        backup = create_backup(
            output,
            mongo_uri="mongodb://user:secret@localhost/arel_bot",
            redis_url="redis://:secret@localhost:6379/0",
            chroma_dir=chroma,
        )

    manifest = json.loads((backup / "BACKUP.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert {"mongo.archive.gz", "redis.rdb", "chroma_db.tar.gz"} <= set(manifest["artifacts"])
    assert all("secret" not in value for value in manifest["artifacts"])
    assert run.call_count == 2

    assert verify_backup(backup)["schema_version"] == 1
    # Restore without explicit confirmation is verification-only and must not
    # invoke a destructive database command.
    restored = restore_backup(backup, mongo_uri="mongodb://user:secret@localhost/arel_bot")
    assert restored["artifacts"] == manifest["artifacts"]


def test_restore_rejects_symlink_entries_in_chroma_archive(tmp_path):
    backup = tmp_path / "backup"
    backup.mkdir()
    archive_path = backup / "chroma_db.tar.gz"
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w:gz") as archive:
        link = tarfile.TarInfo("index")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        archive.addfile(link)
    archive_path.write_bytes(payload.getvalue())
    manifest = {
        "schema_version": 1,
        "artifacts": {
            "chroma_db.tar.gz": {
                "sha256": __import__("hashlib").sha256(archive_path.read_bytes()).hexdigest(),
                "bytes": archive_path.stat().st_size,
            }
        },
    }
    (backup / "BACKUP.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(RestoreError, match="unsafe Chroma archive entry"):
        restore_backup(
            backup,
            mongo_uri="mongodb://localhost/arel_bot",
            chroma_target=tmp_path / "restored",
            confirm=True,
        )


def test_restore_can_remap_mongo_database_with_allowlisted_names(tmp_path):
    backup = tmp_path / "backup"
    backup.mkdir()
    mongo = backup / "mongo.archive.gz"
    mongo.write_bytes(b"mongo-fixture")
    (backup / "BACKUP.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "artifacts": {
                    "mongo.archive.gz": {
                        "sha256": hashlib.sha256(mongo.read_bytes()).hexdigest(),
                        "bytes": mongo.stat().st_size,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    commands = []

    restore_backup(
        backup,
        mongo_uri="mongodb://localhost/arel_bot_restore",
        mongo_source_database="arel_bot",
        mongo_target_database="arel_bot_restore",
        confirm=True,
        command_runner=commands.append,
    )

    assert commands and commands[0][0] == "mongorestore"
    # Namespace remapping must use a URI without a database path. A target
    # database in the URI filters the archive before --nsFrom can match it.
    assert "--uri=mongodb://localhost" in commands[0]
    assert "--nsFrom=arel_bot.*" in commands[0]
    assert "--nsTo=arel_bot_restore.*" in commands[0]


def test_restore_requires_both_database_names_for_namespace_remap(tmp_path):
    backup = tmp_path / "backup"
    backup.mkdir()
    (backup / "BACKUP.json").write_text(json.dumps({"schema_version": 1, "artifacts": {}}), encoding="utf-8")

    with pytest.raises(RestoreError, match="both Mongo source and target"):
        restore_backup(
            backup,
            mongo_uri="mongodb://localhost/arel_bot",
            mongo_source_database="arel_bot",
            confirm=True,
        )


def test_restore_preserves_mongo_uri_query_options_when_remapping(tmp_path):
    backup = tmp_path / "backup"
    backup.mkdir()
    mongo = backup / "mongo.archive.gz"
    mongo.write_bytes(b"mongo-fixture")
    (backup / "BACKUP.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "artifacts": {
                    "mongo.archive.gz": {
                        "sha256": hashlib.sha256(mongo.read_bytes()).hexdigest(),
                        "bytes": mongo.stat().st_size,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    commands = []
    restore_backup(
        backup,
        mongo_uri="mongodb://user:secret@localhost/target?authSource=admin",
        mongo_source_database="source",
        mongo_target_database="target",
        confirm=True,
        command_runner=commands.append,
    )
    assert "--uri=mongodb://user:secret@localhost?authSource=admin" in commands[0]


def test_restore_rejects_symlinked_redis_target(tmp_path):
    backup = tmp_path / "backup"
    backup.mkdir()
    redis = backup / "redis.rdb"
    redis.write_bytes(b"redis-fixture")
    (backup / "BACKUP.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "artifacts": {
                    "redis.rdb": {
                        "sha256": hashlib.sha256(redis.read_bytes()).hexdigest(),
                        "bytes": redis.stat().st_size,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    outside = tmp_path / "outside.rdb"
    outside.write_bytes(b"keep")
    target = tmp_path / "target.rdb"
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows runner")

    with pytest.raises(RestoreError, match="unsafe restore target"):
        restore_backup(
            backup,
            mongo_uri="mongodb://localhost/arel_bot",
            redis_rdb_target=target,
            confirm=True,
        )
    assert outside.read_bytes() == b"keep"


def test_verify_backup_rejects_artifact_symlink_inside_backup(tmp_path):
    backup = tmp_path / "backup"
    backup.mkdir()
    real = backup / "real.bin"
    real.write_bytes(b"fixture")
    alias = backup / "redis.rdb"
    try:
        alias.symlink_to(real)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows runner")
    (backup / "BACKUP.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "artifacts": {
                    "redis.rdb": {
                        "sha256": hashlib.sha256(real.read_bytes()).hexdigest(),
                        "bytes": real.stat().st_size,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RestoreError, match="artifact is missing or unsafe"):
        verify_backup(backup)


def test_verify_backup_rejects_symlinked_backup_directory(tmp_path):
    real = tmp_path / "real-backup"
    real.mkdir()
    (real / "BACKUP.json").write_text(json.dumps({"schema_version": 1, "artifacts": {}}), encoding="utf-8")
    alias = tmp_path / "backup-alias"
    try:
        alias.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows runner")
    with pytest.raises(RestoreError, match="unsafe backup directory"):
        verify_backup(alias)


def test_create_backup_rejects_symlinked_output_directory(tmp_path):
    real = tmp_path / "real-output"
    real.mkdir()
    alias = tmp_path / "output-alias"
    try:
        alias.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows runner")
    with pytest.raises(BackupError, match="unsafe backup output directory"):
        create_backup(alias, mongo_uri="mongodb://localhost/arel_bot", redis_url="redis://localhost", chroma_dir=tmp_path / "missing")


@pytest.mark.parametrize(
    "metadata",
    [{"sha256": "bad", "bytes": 1}, {"sha256": "0" * 64, "bytes": True}, {"sha256": "0" * 64, "bytes": -1}],
)
def test_verify_backup_rejects_invalid_artifact_metadata(tmp_path, metadata):
    backup = tmp_path / "backup"
    backup.mkdir()
    artifact = backup / "redis.rdb"
    artifact.write_bytes(b"fixture")
    (backup / "BACKUP.json").write_text(
        json.dumps({"schema_version": 1, "artifacts": {"redis.rdb": metadata}}),
        encoding="utf-8",
    )
    with pytest.raises(RestoreError, match="artifact metadata is invalid"):
        verify_backup(backup)
