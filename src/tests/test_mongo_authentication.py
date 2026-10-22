"""Contract of the authenticated MongoDB and Redis Compose services.

The checks below describe the deployment contract that the security
hardening of the persistence layer established: every service requires a
credential, the application connects with a least-privilege account, and
the administrative account stays out of the application connection
strings.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[2]
COMPOSE = ROOT / "docker-compose.yml"
INIT_SCRIPT = ROOT / "deploy" / "mongo" / "init-app-users.js"


def _compose() -> str:
    return COMPOSE.read_text(encoding="utf-8")


def test_every_database_service_requires_a_credential():
    compose = _compose()

    assert "MONGO_INITDB_ROOT_PASSWORD: ${MONGO_ROOT_PASSWORD:?MONGO_ROOT_PASSWORD is required}" in compose
    assert "MONGO_INITDB_APP_PASSWORD: ${MONGO_APP_PASSWORD:?MONGO_APP_PASSWORD is required}" in compose
    assert "MONGO_INITDB_TEST_PASSWORD: ${MONGO_TEST_PASSWORD:?MONGO_TEST_PASSWORD is required}" in compose
    assert '"--requirepass", "${REDIS_PASSWORD:?REDIS_PASSWORD is required}"' in compose


def test_application_services_connect_with_the_least_privilege_account():
    """The application must never connect with the administrative account."""

    compose = _compose()

    # The bot, the worker and the development profile share the application
    # account; the test profile uses the account of the disposable test
    # databases.
    assert compose.count("mongodb://${MONGO_APP_USERNAME:-arel_app}:") == 3
    assert compose.count("mongodb://${MONGO_TEST_USERNAME:-arel_test}:") == 1
    assert "mongodb://${MONGO_ROOT_USERNAME" not in compose
    assert compose.count("?authSource=arel_bot") == 4


def test_init_script_creates_a_separate_application_account():
    """The init script must not widen the administrative account."""

    script = INIT_SCRIPT.read_text(encoding="utf-8")

    assert 'MONGO_INITDB_APP_USERNAME || "arel_app"' in script
    assert '{ role: "readWrite", db: appDatabase }' in script
    assert 'MONGO_INITDB_TEST_USERNAME || "arel_test"' in script
    # The administrative account keeps the roles the image gives it.
    assert "grantRolesToUser" not in script
    assert "getSiblingDB(\"admin\")" not in script


def test_compose_profile_resolves_with_the_synthetic_credentials():
    """The mandatory credentials have to be resolvable by the Compose parser.

    This is skipped on developer machines without Docker, while CI and the
    release gate validate the actual Compose parser rather than duplicating
    its interpolation rules in Python.
    """

    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("docker is not installed")
    environment = dict(os.environ)
    environment.update(
        {
            "MONGO_ROOT_USERNAME": "areladmin",
            "MONGO_ROOT_PASSWORD": "compose-graph-mongo-pass",
            "MONGO_APP_USERNAME": "arel_app",
            "MONGO_APP_PASSWORD": "compose-graph-mongo-app-pass",
            "MONGO_TEST_USERNAME": "arel_test",
            "MONGO_TEST_PASSWORD": "compose-graph-mongo-test-pass",
            "REDIS_PASSWORD": "compose-graph-redis-pass",
        }
    )
    result = subprocess.run(
        [docker, "compose", "--profile", "dev", "--profile", "test", "config"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )
    assert result.returncode == 0, result.stderr[-1000:]
    # The resolved services have to carry the least-privilege credential.
    assert "mongodb://arel_app:compose-graph-mongo-app-pass@mongo:27017/arel_bot?authSource=arel_bot" in result.stdout
    assert "mongodb://arel_test:compose-graph-mongo-test-pass@mongo:27017/arel_bot_test?authSource=arel_bot_test" in result.stdout
    # The administrative account stays out of the application services.
    assert "mongodb://areladmin:" not in result.stdout
