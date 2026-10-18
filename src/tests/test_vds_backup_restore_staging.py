from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import scripts.vds_backup_restore_staging as staging
from scripts.vds_backup_restore_staging import VdsEvidenceError


def test_vds_backup_gate_requires_destructive_confirmation():
    with pytest.raises(VdsEvidenceError, match="vds_backup_live_gate_required"):
        staging.validate_live_gate(
            live=True,
            confirm_live=True,
            confirm_destructive=False,
            env_value="1",
        )


def test_vds_database_names_must_be_distinct_and_bounded():
    assert staging.validate_database_names("arel_bot", "arel_bot_restore") == (
        "arel_bot",
        "arel_bot_restore",
    )
    with pytest.raises(VdsEvidenceError, match="must_differ"):
        staging.validate_database_names("arel_bot", "arel_bot")
    with pytest.raises(VdsEvidenceError, match="name_invalid"):
        staging.validate_database_names("arel bot", "restore")


def test_artifact_content_digest_is_stable_and_path_free(tmp_path: Path):
    backup = tmp_path / "backup"
    backup.mkdir()
    mongo = backup / "mongo.archive.gz"
    redis = backup / "redis.rdb"
    mongo.write_bytes(b"mongo")
    redis.write_bytes(b"redis")
    manifest = {
        "schema_version": 1,
        "artifacts": {
            "redis.rdb": {"sha256": hashlib.sha256(redis.read_bytes()).hexdigest(), "bytes": 5},
            "mongo.archive.gz": {"sha256": hashlib.sha256(mongo.read_bytes()).hexdigest(), "bytes": 5},
        },
    }
    first = staging.artifact_content_digest(backup, manifest)
    second = staging.artifact_content_digest(backup, manifest)
    assert first == second
    assert len(first) == 64
    assert str(tmp_path) not in first


def test_run_live_uses_backup_restore_contract_without_real_services(monkeypatch, tmp_path: Path):
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    artifact = backup_dir / "redis.rdb"
    artifact.write_bytes(b"redis")
    manifest = {
        "schema_version": 1,
        "artifacts": {
            "redis.rdb": {"sha256": hashlib.sha256(b"redis").hexdigest(), "bytes": 5},
        },
    }
    (backup_dir / "BACKUP.json").write_text(json.dumps(manifest), encoding="utf-8")
    calls: list[tuple[str, object]] = []

    def fake_create_backup(*args, **kwargs):
        calls.append(("backup", kwargs))
        return backup_dir

    def fake_verify_backup(path):
        calls.append(("verify", path))
        return manifest

    def fake_restore_backup(path, **kwargs):
        calls.append(("restore", kwargs))
        return manifest

    monkeypatch.setattr(staging, "create_backup", fake_create_backup)
    monkeypatch.setattr(staging, "verify_backup", fake_verify_backup)
    monkeypatch.setattr(staging, "restore_backup", fake_restore_backup)

    report = staging.run_live(
        output_dir=tmp_path / "output",
        mongo_uri="mongodb://source/arel_bot",
        redis_url="redis://localhost:6379/0",
        chroma_dir=tmp_path / "chroma",
        restore_mongo_uri="mongodb://target/arel_bot_restore",
        restore_redis_target=tmp_path / "restore.rdb",
        restore_chroma_target=tmp_path / "restore-chroma",
        mongo_source_database="arel_bot",
        mongo_target_database="arel_bot_restore",
    )

    assert report["ok"] is True
    assert report["secret_free"] is True
    assert all(report["checks"].values())
    assert report["archive_sha256"]
    assert [name for name, _ in calls] == ["backup", "verify", "restore"]
    restore_kwargs = calls[-1][1]
    assert restore_kwargs["confirm"] is True
    assert restore_kwargs["mongo_source_database"] == "arel_bot"
    assert restore_kwargs["mongo_target_database"] == "arel_bot_restore"
