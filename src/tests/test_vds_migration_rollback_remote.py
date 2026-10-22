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
    assert "MONGODB_SMOKE_DATABASE=${database}" in script
    assert "docker stop" not in script
    assert "docker compose down" not in script


def test_migration_script_never_puts_the_mongo_credential_on_a_command_line():
    """The drill authenticates like the deployment does.

    The disposable database travels as a name and the connection string
    travels in the environment of the worker, so no argument vector of the
    drill contains a credential.
    """

    script = migration._remote_script()
    assert "MONGODB_URI=mongodb://" not in script
    assert "docker exec -e MONGODB_URI" in script
    assert "load('${mongo_auth}')" in script
    assert "MONGO_INITDB_ROOT_PASSWORD" in script


def test_migration_script_removes_the_credential_document_when_the_drill_fails():
    """A failed drill must not leave the plaintext password in the container.

    The credential document holds the administrative password in clear text,
    so the script removes it when it exits, not only when it succeeds.
    """

    script = migration._remote_script()
    assert "trap cleanup EXIT" in script
    assert 'rm -f "${mongo_auth}"' in script


def test_migration_script_validates_the_credential_character_set():
    """The drill builds the connection string by hand, so it checks the set.

    The password ends up in a URI that nothing escapes, so a reserved
    character would silently produce a broken connection string.  The user
    name lands in the same string and in the authentication script.
    """

    script = migration._remote_script()
    assert "*[!A-Za-z0-9._~-]*)" in script
    assert "mongo credential contains an unsupported character" in script
    assert "mongo user contains an unsupported character" in script

