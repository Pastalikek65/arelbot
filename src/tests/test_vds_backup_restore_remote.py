from __future__ import annotations

import json

import pytest

import scripts.vds_backup_restore_remote as remote


def _payload():
    return {
        "scope": "vds-backup-restore",
        "ok": True,
        "mode": "live-disposable-staging",
        "checks": {
            "backup_created": True,
            "manifest_verified": True,
            "artifact_checksum_verified": True,
            "mongo_restore_verified": True,
            "redis_restore_verified": True,
            "chroma_restore_verified": True,
            "cleanup_verified": True,
        },
        "archive_sha256": "a" * 64,
        "artifact_count": 3,
        "secret_free": True,
    }


def test_remote_backup_gate_requires_all_signals():
    with pytest.raises(remote.RemoteBackupError, match="vds_backup_live_gate_required"):
        remote.validate_live_gate(live=True, confirm_live=True, confirm_destructive=False, env_value="1")


def test_extract_result_accepts_only_bounded_secret_free_payload():
    payload = _payload()
    result = remote.extract_result("noise\n" + remote._JSON_MARKER + json.dumps(payload) + "\n")
    assert result == payload
    with pytest.raises(remote.RemoteBackupError, match="remote_result_missing"):
        remote.extract_result("noise")


def test_extract_result_rejects_false_restore_report():
    payload = _payload()
    payload["checks"]["redis_restore_verified"] = False
    with pytest.raises(remote.RemoteBackupError, match="remote_report_invariant_failed"):
        remote.extract_result(remote._JSON_MARKER + json.dumps(payload))


def test_remote_script_has_disposable_namespace_and_no_production_restore():
    script = remote._remote_script()
    assert "--nsFrom='arel_bot.*'" in script
    assert "target_db=\"arel_bot_restore_" in script
    assert "dropDatabase" in script
    assert "docker stop" not in script
    assert "docker compose down" not in script
    assert "redis_restore" in script
