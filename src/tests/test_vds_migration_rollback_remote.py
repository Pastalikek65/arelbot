from __future__ import annotations

import json

import pytest

import scripts.vds_migration_rollback_remote as migration


def test_migration_gate_requires_all_signals():
    with pytest.raises(migration.MigrationEvidenceError, match="live_gate_required"):
        migration.validate_live_gate(live=True, confirm_live=True, confirm_destructive=False, env_value="1")


def test_migration_result_is_bounded_and_secret_free():
    payload = {
        "scope": "vds-migration-rollback",
        "ok": True,
        "mode": "live-disposable-staging",
        "checks": {"rollback_to_v3": True, "forward_to_latest": True},
        "secret_free": True,
    }
    assert migration._extract("noise\nARELBOT_VDS_MIGRATION_RESULT=" + json.dumps(payload)) == payload


def test_migration_script_uses_disposable_database_and_never_stops_production():
    script = migration._remote_script()
    assert "arelbot_migration_live_" in script
    assert "mongo:27017/${database}" in script
    assert "docker stop" not in script
    assert "docker compose down" not in script
