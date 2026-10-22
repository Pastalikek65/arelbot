from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.release_gate import commands

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "gate.yml"


def _content() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_workflow_exists():
    assert WORKFLOW.is_file()


def test_workflow_triggers_on_pull_request_and_push():
    content = _content()
    assert "pull_request:" in content
    assert "push:" in content


def test_workflow_delegates_to_the_release_gate():
    """CI must run the release gate, not a hand-written copy of its checks.

    A workflow that repeats the gate commands can only ever be a subset of the
    gate, so "CI is green" stopped implying "the release gate is green".  The
    workflow therefore calls the single aggregator for both scopes.
    """

    content = _content()
    assert "python scripts/release_gate.py --requests 8 --concurrency 2" in content
    assert "python scripts/release_gate.py --docker --requests 8 --concurrency 2" in content


def test_workflow_gate_covers_the_required_checks():
    """The delegated gate still has to run every check the workflow used to list.

    These assertions describe the gate's own check list, so they detect a
    check that disappears from the gate.  They are **not** a proof that CI
    runs each of those checks: that guarantee comes from
    ``test_workflow_delegates_to_the_release_gate``, which pins the workflow
    to the gate entry point itself.  Read the two together.
    """

    checks = dict(commands(include_docker=False, requests=8, concurrency=2))
    for required in (
        "scripts/architecture_check.py",
        "scripts/evidence_audit.py",
        "scripts/feature_catalog_audit.py",
        "scripts/phase_audit.py",
        "scripts/provider_governance.py",
    ):
        assert any(required in argument for argument in checks.values()), required
    assert checks["pytest"][-3:] == ["-m", "pytest", "-q"]


def test_workflow_gate_covers_the_docker_checks():
    """The Compose scope of the gate has to run in CI as well.

    The MongoDB smoke checks and the Prometheus configuration/alert replay
    were the docker-scope checks the workflow never ran.  Like
    ``test_workflow_gate_covers_the_required_checks``, this pins the gate's
    check list; the CI side of the guarantee is the pinned gate invocation in
    ``test_workflow_delegates_to_the_release_gate``.
    """

    checks = dict(commands(include_docker=True, requests=8, concurrency=2))
    for required in (
        "compose_profiles",
        "docker_build",
        "docker_pytest",
        "docker_health",
        "docker_provider_matrix",
        "mongo_migration_smoke",
        "mongo_ledger_smoke",
        "mongo_sticker_operation_smoke",
        "monitoring_config",
        "monitoring_alert_replay",
    ):
        assert required in checks, required


def test_workflow_never_pulls_secrets_into_pr_builds():
    content = _content()
    assert "pull_request_target" not in content


def test_workflow_does_not_use_provider_approval_flag():
    """The provider approval flag is a deployment preflight, not a code gate.

    ``scripts/provider_governance.py --require-approved`` fails for every
    feature whose source governance is still ``pending``; more than ten
    features are legitimately pending, so the flag would keep the gate red
    for a documented reason.  It stays in ``scripts/production_preflight.py``.
    """

    content = _content()
    assert "--require-approved" not in content


def test_workflow_references_no_repository_secrets():
    """Every check reads committed artifacts, so no job needs a secret."""

    content = _content()
    assert "secrets." not in content


def test_workflow_creates_synthetic_env_before_the_gate_runs():
    """The Compose file interpolates from ``.env``, which is ignored by Git.

    The project ``.env`` is the interpolation source of the Compose file: it
    resolves the mandatory credentials of the MongoDB and Redis services and
    it is never injected into a container.  The Compose ``test`` profile
    reads a second file, ``.env.test``, through ``env_file``, and that file
    is ignored by Git as well.

    The release gate runs the Compose checks, so the synthetic environment
    file has to exist before the gate is called in every job that runs it.
    The file must stay free of real credentials.
    """

    content = _content()
    # The names are listed without the assignment character and the
    # character is appended below, so the list stays a list of names.
    for variable in (
        "TELEGRAM_TOKEN",
        "MONGODB_URI",
        "REDIS_URL",
        # The MongoDB and Redis services require authentication, so the
        # Compose file interpolates mandatory credentials.  A missing
        # variable makes `docker compose config` fail and breaks the docker
        # gate silently.
        "MONGO_ROOT_USERNAME",
        "MONGO_ROOT_PASSWORD",
        "MONGO_APP_USERNAME",
        "MONGO_APP_PASSWORD",
        "MONGO_TEST_USERNAME",
        "MONGO_TEST_PASSWORD",
        "REDIS_PASSWORD",
    ):
        assert f"{variable}=" in content, variable
    assert content.index("> .env") < content.index("python scripts/release_gate.py")


def test_workflow_synthetic_env_names_the_test_database():
    """The synthetic environment must not point at the production database.

    The offline job reads the environment file outside the container, so a
    production connection string would give the job write access to the
    production database.
    """

    content = _content()
    assert "mongodb://arel_test:ci-mongo-test-pass@mongo:27017/arel_bot_test?authSource=arel_bot_test" in content
    assert "mongodb://arel_app:ci-mongo-app-pass@mongo:27017/arel_bot?authSource=arel_bot" not in content


def test_workflow_structure_matches_the_gate_contract():
    """Structural contract, verified with the pinned YAML parser.

    The substring tests above cannot see a wrong branch list, a missing
    ``needs`` link or a step that moved into the wrong job.  PyYAML is a
    declared and pinned dependency, so this test runs instead of skipping.
    """

    yaml = pytest.importorskip("yaml", exc_type=ImportError)
    document = yaml.safe_load(_content())

    triggers = document.get("on", document.get(True))
    assert "pull_request" in triggers
    assert triggers["push"]["branches"] == ["main"]
    assert document["permissions"] == {"contents": "read"}
    assert document["concurrency"]["cancel-in-progress"] is True
    assert "github.ref" in document["concurrency"]["group"]

    jobs = document["jobs"]
    assert set(jobs) == {"offline-gate", "docker-gate"}
    assert jobs["docker-gate"]["needs"] == "offline-gate"
    for job in jobs.values():
        assert job["timeout-minutes"] > 0

    for job in jobs.values():
        runs = [step["run"] for step in job["steps"] if "run" in step]
        env_index = next(index for index, run in enumerate(runs) if "> .env" in run)
        gate_index = next(index for index, run in enumerate(runs) if "scripts/release_gate.py" in run)
        assert env_index < gate_index

    docker_runs = [step["run"] for step in jobs["docker-gate"]["steps"] if "run" in step]
    assert any("scripts/release_gate.py --docker" in run for run in docker_runs)


def test_structural_workflow_test_dependency_is_declared():
    """The structural contract test needs PyYAML, so PyYAML must be pinned.

    An undeclared parser turns the structural test into a silent skip, which
    is exactly the drift the workflow rewrite is meant to remove.
    """

    requirements = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    constraints = (REPO_ROOT / "constraints.txt").read_text(encoding="utf-8").lower()
    licenses = json.loads((REPO_ROOT / "licenses.json").read_text(encoding="utf-8"))

    assert "pyyaml" in requirements
    assert "pyyaml==" in constraints
    assert any(str(item.get("name", "")).lower() == "pyyaml" for item in licenses["packages"])
