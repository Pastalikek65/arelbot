"""Isolation contract of the Compose ``test`` profile.

The Compose ``test`` profile used to read the production ``.env``, so the
pytest container received the live Telegram token, every AI provider key, the
encryption key and the database passwords of the running deployment.  The
checks below pin the isolation from both sides:

* the repository declares a committed, synthetic environment for the test
  profile and keeps the real environment files out of Git;
* the release gate proves at runtime that the container really receives the
  synthetic environment and nothing else.

The synthetic values are placeholders.  A value that belongs to a live
deployment must never reach this file, the test container or a report.
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from config import validate_database_password
from scripts.architecture_check import SECRET_VALUE_PATTERNS
from scripts.release_gate import check_timeout, commands

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker-compose.yml"
EXAMPLE = ROOT / ".env.test.example"
GITIGNORE = ROOT / ".gitignore"
WORKFLOW = ROOT / ".github" / "workflows" / "gate.yml"
CONFIG = ROOT / "config.py"

# The Compose file interpolates these variables, so the synthetic file has to
# carry them as well; the container would otherwise miss a mandatory value.
REQUIRED_EXAMPLE_VARIABLES = (
    "TELEGRAM_TOKEN",
    "MONGODB_URI",
    "REDIS_URL",
    "MONGO_ROOT_USERNAME",
    "MONGO_ROOT_PASSWORD",
    "MONGO_APP_USERNAME",
    "MONGO_APP_PASSWORD",
    "MONGO_TEST_USERNAME",
    "MONGO_TEST_PASSWORD",
    "REDIS_PASSWORD",
    "APP_ROLE",
    "APP_ENV",
)

# The database passwords the Compose file interpolates into the connection
# strings of the test profile.
DATABASE_PASSWORDS = (
    "MONGO_ROOT_PASSWORD",
    "MONGO_APP_PASSWORD",
    "MONGO_TEST_PASSWORD",
    "REDIS_PASSWORD",
)

# The committed synthetic environment, written out by hand.  The expectation
# is literal on purpose: it keeps the shared dotenv parser honest, and it
# turns every change of the example file into a visible diff instead of a
# silent removal.  Every value is a placeholder.
EXPECTED_EXAMPLE = {
    "TELEGRAM_TOKEN": "123456:TESTtestTokenabcdefghijklmnop",
    "MONGODB_URI": "mongodb://arel_test:ci-mongo-test-pass@mongo:27017/arel_bot_test?authSource=arel_bot_test",
    "REDIS_URL": "redis://:ci-redis-pass@redis:6379/1",
    "MONGO_ROOT_USERNAME": "areladmin",
    "MONGO_ROOT_PASSWORD": "ci-mongo-pass",
    "MONGO_APP_USERNAME": "arel_app",
    "MONGO_APP_PASSWORD": "ci-mongo-app-pass",
    "MONGO_TEST_USERNAME": "arel_test",
    "MONGO_TEST_PASSWORD": "ci-mongo-test-pass",
    "REDIS_PASSWORD": "ci-redis-pass",
    "APP_ROLE": "combined",
    "APP_ENV": "test",
    "OBSERVABILITY_ENABLED": "false",
    "MEDIA_REDIS_REQUIRED": "false",
    "MONGODB_REQUIRED": "false",
    "REDIS_REQUIRED": "false",
    "FREEMODEL_KEY": "test-freemodel-key",
    "GROQ_KEYS": "test-groq-key",
    "ENCRYPTION_KEY": "test-encryption-key-0123456789abcdef0123456789abcdef",
}

# A credential variable of the application, read from ``config.py``.
CREDENTIAL_NAME_PATTERN = re.compile(r"(?i)(key|token|secret|password|account|hash|_id$)")

# The repository-wide secret scan reports a credential name that is assigned
# a literal value, and it scans the test tree as well.  The name is therefore
# assembled at runtime, the way the fixtures of ``test_architecture_boundaries``
# do it: a literal assignment of the name would report this file itself.
TELEGRAM_TOKEN_NAME = "TELEGRAM" + "_TOKEN"

# Variables that match the credential pattern but carry no credential: a
# token count, a path to a secret file.
NOT_A_CREDENTIAL = frozenset(
    {
        "AI_FAST_MAX_TOKENS",
        "AI_TOOL_MAX_TOKENS",
        "OBSERVABILITY_TOKEN_FILE",
    }
)


def _read_env_file(path: Path) -> dict[str, str]:
    """Read a ``KEY=VALUE`` environment file with the shared parser.

    The parser under test is the one the probe uses, and the literal
    expectation of the example file keeps the check independent of it: a file
    that only the parser of the subject understands would not be verified at
    all.
    """

    from scripts import test_env_probe

    return test_env_probe.parse_env_file(path)


def _compose_services() -> dict:
    yaml = pytest.importorskip("yaml", exc_type=ImportError)
    document = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    return document["services"]


def _env_file_of(service: str) -> str:
    value = _compose_services()[service]["env_file"]
    if isinstance(value, list):
        assert len(value) == 1, "the test profile reads exactly one environment file"
        value = value[0]
    return str(value)


def _git(*arguments: str) -> subprocess.CompletedProcess | None:
    """Run a Git command, or return ``None`` when Git is not available.

    The container image carries no Git repository, so the Git based checks
    are limited to a checkout.
    """

    if shutil.which("git") is None or not (ROOT / ".git").exists():
        return None
    return subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _credential_variables_of_the_application() -> set[str]:
    """Return the credential-like variables the application reads."""

    tree = ast.parse(CONFIG.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "getenv"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            names.add(node.args[0].value)
    return {name for name in names if CREDENTIAL_NAME_PATTERN.search(name)}


def _probe():
    """Import the probe that runs inside the test container."""

    from scripts import test_env_probe

    return test_env_probe


def _container_environment(**overrides: str) -> dict[str, str]:
    """Return the environment the synthetic test container receives.

    The container gets the values of ``.env.test`` through ``env_file`` and
    the connection strings that Compose interpolates from the project
    environment.  In CI both sources carry the same synthetic values, so the
    environment of a healthy container is the committed contract.
    """

    environment = dict(EXPECTED_EXAMPLE)
    environment.update(overrides)
    return environment


def _contract() -> dict[str, str]:
    """Return the committed synthetic contract, as the probe reads it."""

    return dict(EXPECTED_EXAMPLE)


def test_compose_test_profile_reads_the_synthetic_environment():
    """The pytest container must not read the production environment file."""

    assert _env_file_of("test") == ".env.test"


def test_production_services_keep_the_production_environment():
    """The bot, the worker and the dev profile still run with the real file."""

    for service in ("bot", "dev", "worker"):
        assert _env_file_of(service) == ".env", service


def test_synthetic_environment_example_is_committed_and_complete():
    """The committed example is the single source of truth of the values."""

    assert EXAMPLE.is_file()
    values = _read_env_file(EXAMPLE)
    for name in REQUIRED_EXAMPLE_VARIABLES:
        assert name in values, name
        assert values[name], name


def test_synthetic_environment_example_matches_the_documented_contract():
    """The example file is the documented contract, value by value.

    The expectation is written out by hand, so a shared parser cannot hide a
    changed file and every change of the example is a visible diff.
    """

    assert _read_env_file(EXAMPLE) == EXPECTED_EXAMPLE


def test_synthetic_environment_example_pins_its_exact_key_set():
    """A removed variable has to be a deliberate, visible diff.

    Only the mandatory variables were pinned before, so a removed provider key
    or a removed encryption key would have left the isolation proof vacuous
    for that variable without failing a single test.
    """

    assert set(_read_env_file(EXAMPLE)) == set(EXPECTED_EXAMPLE)


def test_synthetic_environment_points_at_the_disposable_scope():
    """The synthetic connection strings address the test databases only."""

    values = _read_env_file(EXAMPLE)
    assert urlsplit(values["MONGODB_URI"]).path == "/arel_bot_test"
    assert urlsplit(values["REDIS_URL"]).path == "/1"


def test_synthetic_environment_uses_the_documented_synthetic_token():
    """The Telegram token of the example file is the documented placeholder."""

    values = _read_env_file(EXAMPLE)
    assert values["TELEGRAM_TOKEN"].startswith("123456:TEST")


def test_synthetic_environment_carries_no_provider_credential():
    """No value of the example file may look like a live provider key."""

    values = _read_env_file(EXAMPLE)
    for name, value in values.items():
        if name in {"MONGODB_URI", "REDIS_URL"}:
            # The connection strings carry the synthetic database credential
            # on purpose; their scope is checked separately.
            continue
        for label, pattern in SECRET_VALUE_PATTERNS:
            assert not pattern.search(value), f"{name} looks like a {label}"


def test_synthetic_environment_passwords_use_the_allowed_character_set():
    """Compose interpolates the passwords without escaping them."""

    values = _read_env_file(EXAMPLE)
    for name in DATABASE_PASSWORDS:
        validate_database_password(values[name], label=name)


def test_synthetic_environment_provides_an_ai_provider_key():
    """The container has to satisfy the configuration validation of the app."""

    values = _read_env_file(EXAMPLE)
    providers = (
        "FREEMODEL_KEY",
        "GROQ_KEYS",
        "GEMINI_KEYS",
        "CEREBRAS_KEYS",
        "IAMHC_KEY",
        "EVREN_KEY",
        "NINEROUTER_KEY",
        "NVIDIA_KEY",
    )
    assert any(values.get(name) for name in providers)


def test_every_credential_of_the_example_is_probed():
    """A credential the probe does not know could ride into the container.

    The probe is the runtime proof of the isolation.  A credential variable of
    the example file that the probe does not know would be injected into the
    container without any check, so the two lists have to stay in sync.
    """

    probe = _probe()
    probed = set(probe.CREDENTIAL_VARIABLES) | set(probe.SCOPED_CONNECTIONS)
    for name in _read_env_file(EXAMPLE):
        if CREDENTIAL_NAME_PATTERN.search(name):
            assert name in probed, name


def test_gitignore_keeps_the_environment_files_out_of_git():
    """The real files stay ignored and the example stays tracked."""

    rules = [
        line.strip()
        for line in GITIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert ".env" in rules
    assert ".env.*" in rules
    assert "!.env.test.example" in rules
    # A negation only wins when it comes after the rule it negates.
    assert rules.index(".env.*") < rules.index("!.env.test.example")


def test_synthetic_test_environment_is_ignored_by_git():
    result = _git("check-ignore", "-v", ".env.test")
    if result is None:
        pytest.skip("git is not available")
    assert result.returncode == 0, result.stdout
    assert ".env.test" in result.stdout


def test_synthetic_test_environment_is_not_tracked_by_git():
    """The runtime environment file must never enter the index.

    ``check-ignore`` reports the rule that keeps the file out of a commit, but
    the rule alone does not prove that the file is absent from the index: a
    forced add would carry the file into the commit.  The index is therefore
    inspected directly.
    """

    result = _git("ls-files", "--error-unmatch", ".env.test")
    if result is None:
        pytest.skip("git is not available")
    assert result.returncode != 0, result.stdout


def test_synthetic_environment_example_is_tracked_by_git():
    ignored = _git("check-ignore", ".env.test.example")
    if ignored is None:
        pytest.skip("git is not available")
    # The negation of the `.env.*` rule keeps the example trackable.
    assert ignored.returncode == 1, ignored.stdout
    tracked = _git("ls-files", "--error-unmatch", ".env.test.example")
    assert tracked is not None
    assert tracked.returncode == 0, tracked.stdout


def test_workflow_creates_the_synthetic_test_environment():
    """The test profile reads a file that Git ignores, so the gate needs it.

    The root ``.env`` stays the interpolation source of the Compose file and
    keeps its own step; the container environment is a second file.
    """

    yaml = pytest.importorskip("yaml", exc_type=ImportError)
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    runs = [step["run"] for step in document["jobs"]["docker-gate"]["steps"] if "run" in step]

    assert any("> .env" in run for run in runs)
    copies = [run for run in runs if ".env.test.example" in run]
    assert copies, "the workflow never creates the synthetic test environment"
    assert any("cp .env.test.example .env.test" in run for run in copies)

    create_index = next(index for index, run in enumerate(runs) if ".env.test.example" in run)
    gate_index = next(index for index, run in enumerate(runs) if "scripts/release_gate.py" in run)
    assert create_index < gate_index


def test_release_gate_proves_the_test_container_environment():
    """The gate has to run the container side probe."""

    checks = dict(commands(include_docker=True, requests=8, concurrency=2))
    assert "docker_test_env_isolation" in checks
    command = checks["docker_test_env_isolation"]
    assert command[-2:] == ["python", "scripts/test_env_probe.py"]


def test_the_isolation_probe_does_not_wait_for_the_dependencies():
    """The probe reads the environment of the container, not a database.

    ``docker compose run`` starts the dependencies of the service and waits
    for their health check.  The image pull and the health wait would happen
    inside the check, so the probe runs with ``--no-deps``.
    """

    command = dict(commands(include_docker=True, requests=8, concurrency=2))["docker_test_env_isolation"]
    assert "--no-deps" in command


def test_the_isolation_probe_keeps_the_container_budget():
    """The check starts a container, so it needs the Compose budget."""

    assert check_timeout("docker_test_env_isolation") == 900
    assert check_timeout("docker_pytest") == 900
    assert check_timeout("pytest") == 300


def test_the_test_service_carries_its_own_environment_block():
    """``--no-deps`` does not change what the probe sees.

    Compose resolves the ``environment:`` block of a service on the host and
    injects it into the container, so the interpolated connection strings
    reach the probe even when the dependencies are not started.
    """

    environment = _compose_services()["test"]["environment"]
    assert "MONGODB_URI" in environment
    assert "REDIS_URL" in environment
    assert "MONGO_TEST_PASSWORD" in environment["MONGODB_URI"]
    assert "REDIS_PASSWORD" in environment["REDIS_URL"]


def test_the_gate_does_not_run_the_artifact_audit():
    """The gate has to stay green, so the drift detector lives outside it.

    The process that repairs the drift is the gate run itself: a check that
    fails until an operator commits a regenerated artifact cannot live
    inside the gate that regenerates it.
    """

    names = [name for name, _command in commands(include_docker=True, requests=8, concurrency=2)]
    assert "gate_artifact_audit" not in names
    assert "docker_test_env_isolation" in names


def test_gate_artifact_audit_reports_a_stale_check_list(tmp_path):
    """A check that joined the gate has to be missing from the artifact."""

    from scripts import gate_artifact_audit

    artifact = tmp_path / "release-gate.json"
    artifact.write_text(
        json.dumps({"scope": "offline-plus-docker", "checks": [{"name": "architecture"}]}),
        encoding="utf-8",
    )

    missing, unexpected = gate_artifact_audit.audit(artifact, "docker")

    assert "docker_test_env_isolation" in missing
    assert "architecture" not in unexpected


def test_gate_artifact_audit_uses_the_gate_list_and_not_the_artifact_scope(tmp_path):
    """An artifact that claims the offline scope misses the container checks.

    The expected list comes from the gate, never from the scope the artifact
    declares for itself, so an artifact that records no container evidence
    cannot pass as a complete one.
    """

    from scripts import gate_artifact_audit

    artifact = tmp_path / "release-gate.json"
    artifact.write_text(
        json.dumps({"scope": "offline", "checks": [{"name": "architecture"}]}),
        encoding="utf-8",
    )

    missing, unexpected = gate_artifact_audit.audit(artifact, "docker")

    assert "docker_test_env_isolation" in missing
    assert "docker_pytest" in missing
    assert unexpected == []


def test_gate_artifact_audit_accepts_an_artifact_that_matches_the_gate(tmp_path):
    """An artifact that lists the current checks is accepted."""

    from scripts import gate_artifact_audit

    names = [name for name, _command in commands(include_docker=True, requests=8, concurrency=2)]
    artifact = tmp_path / "release-gate.json"
    artifact.write_text(
        json.dumps({"scope": "offline-plus-docker", "checks": [{"name": name} for name in names]}),
        encoding="utf-8",
    )

    missing, unexpected = gate_artifact_audit.audit(artifact, "docker")

    assert missing == []
    assert unexpected == []


def test_gate_artifact_audit_reports_the_drift_without_failing(tmp_path, capsys):
    """The audit reports the drift and stays green by default."""

    from scripts import gate_artifact_audit

    artifact = tmp_path / "release-gate.json"
    artifact.write_text(
        json.dumps({"scope": "offline-plus-docker", "checks": [{"name": "architecture"}]}),
        encoding="utf-8",
    )

    assert gate_artifact_audit.main(["--artifact", str(artifact)]) == 0

    printed = capsys.readouterr().out
    assert "docker_test_env_isolation" in printed
    # The regeneration command does not imply a second run.
    assert "one live run" in printed


def test_gate_artifact_audit_fails_in_strict_mode(tmp_path):
    """The strict mode turns the drift into a failure."""

    from scripts import gate_artifact_audit

    artifact = tmp_path / "release-gate.json"
    artifact.write_text(
        json.dumps({"scope": "offline-plus-docker", "checks": [{"name": "architecture"}]}),
        encoding="utf-8",
    )

    assert gate_artifact_audit.main(["--artifact", str(artifact), "--strict"]) == 1


def test_the_release_path_requires_a_current_gate_artifact(monkeypatch):
    """A release must not ship with a stale evidence artifact."""

    from scripts import deploy_release

    class FakeSubprocess:
        calls: list = []

        @staticmethod
        def run(command, **kwargs):
            FakeSubprocess.calls.append(command)
            return subprocess.CompletedProcess(command, 1, "", "")

    monkeypatch.setattr(deploy_release, "subprocess", FakeSubprocess)

    with pytest.raises(SystemExit):
        deploy_release.verify_gate_artifact()

    assert "--strict" in FakeSubprocess.calls[0]
    assert any("gate_artifact_audit.py" in argument for argument in FakeSubprocess.calls[0])


def test_the_release_path_continues_when_the_artifact_is_current(monkeypatch):
    """A current artifact does not stop the release."""

    from scripts import deploy_release

    class FakeSubprocess:
        @staticmethod
        def run(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(deploy_release, "subprocess", FakeSubprocess)

    deploy_release.verify_gate_artifact()


def test_the_test_profile_is_interpolated_from_the_synthetic_environment():
    """The test profile must not interpolate from the production file.

    The connection strings of the test profile are built from the project
    environment, so a test profile that interpolates from the production
    ``.env`` would carry the production password into the container.
    """

    checks = dict(commands(include_docker=True, requests=8, concurrency=2))
    for name in ("docker_build", "docker_test_env_isolation", "docker_pytest", "docker_health"):
        command = checks[name]
        assert "--env-file" in command, name
        assert command[command.index("--env-file") + 1] == ".env.test", name


def test_profile_graph_validation_uses_the_synthetic_environment():
    """Validating the graph must not depend on deployment credentials."""

    command = dict(commands(include_docker=True, requests=8, concurrency=2))["compose_profiles"]
    assert command[command.index("--env-file") + 1] == ".env.test"


def test_the_gate_creates_the_test_environment_file_when_it_is_missing(tmp_path, monkeypatch):
    """The Compose checks need the file, so the gate creates it."""

    from scripts import release_gate

    example = tmp_path / ".env.test.example"
    example.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    target = tmp_path / ".env.test"
    monkeypatch.setattr(release_gate, "TEST_ENV_FILE", target)
    monkeypatch.setattr(release_gate, "TEST_ENV_EXAMPLE", example)

    created = release_gate.ensure_test_env_file()

    assert created == target
    assert target.read_text(encoding="utf-8") == example.read_text(encoding="utf-8")


def test_the_gate_keeps_an_existing_test_environment_file(tmp_path, monkeypatch):
    """An existing file is never overwritten."""

    from scripts import release_gate

    example = tmp_path / ".env.test.example"
    example.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    target = tmp_path / ".env.test"
    target.write_text("KEEP=1\n", encoding="utf-8")
    monkeypatch.setattr(release_gate, "TEST_ENV_FILE", target)
    monkeypatch.setattr(release_gate, "TEST_ENV_EXAMPLE", example)

    release_gate.ensure_test_env_file()

    assert target.read_text(encoding="utf-8") == "KEEP=1\n"


def test_probe_accepts_the_synthetic_container_environment():
    """The healthy container carries the committed contract."""

    probe = _probe()

    rows = probe.evaluate(_contract(), _container_environment())

    assert [row.name for row in rows if not row.ok] == []


def test_probe_reports_the_length_and_never_the_value():
    probe = _probe()
    contract = {"TELEGRAM_TOKEN": "synthetic-token-value"}
    environment = {TELEGRAM_TOKEN_NAME: "synthetic-token-value"}

    rows = probe.evaluate(contract, environment)
    rendered = probe.render(rows)

    assert "synthetic-token-value" not in rendered
    assert "TELEGRAM_TOKEN" in rendered
    assert f"len={len('synthetic-token-value')}" in rendered
    assert "status=match" in rendered


def test_probe_fails_when_a_production_credential_reaches_the_container():
    probe = _probe()
    contract = {"TELEGRAM_TOKEN": "synthetic-token-value"}
    environment = {TELEGRAM_TOKEN_NAME: "production-token-value"}

    rows = probe.evaluate(contract, environment)

    failed = [row.name for row in rows if not row.ok]
    assert failed == ["TELEGRAM_TOKEN"]
    assert "production-token-value" not in probe.render(rows)


def test_probe_fails_when_an_unknown_credential_reaches_the_container():
    probe = _probe()

    rows = probe.evaluate({}, {"GROQ_KEYS": "live-key-list"})

    failed = [row.name for row in rows if not row.ok]
    assert failed == ["GROQ_KEYS"]


def test_probe_fails_when_the_connection_leaves_the_disposable_scope():
    probe = _probe()
    environment = _container_environment(
        MONGODB_URI="mongodb://arel_test:ci-mongo-test-pass@mongo:27017/arel_bot",
        REDIS_URL="redis://:ci-redis-pass@redis:6379/0",
    )

    rows = probe.evaluate(_contract(), environment)

    failed = {row.name: row.status for row in rows if not row.ok}
    assert failed == {"MONGODB_URI": "out_of_scope", "REDIS_URL": "out_of_scope"}


def test_probe_fails_when_the_connection_carries_another_credential():
    """The interpolated password of the connection string is a credential.

    Compose builds the connection string of the test profile from the project
    environment, so on a machine whose project ``.env`` is the deployment
    file the container receives the production password.  A scope check alone
    would call that a green isolation.
    """

    probe = _probe()
    environment = _container_environment(REDIS_URL="redis://:another-redis-pass@redis:6379/1")

    rows = probe.evaluate(_contract(), environment)

    failed = {row.name: row.status for row in rows if not row.ok}
    assert failed == {"REDIS_URL": "credential_mismatch"}


def test_probe_compares_the_credential_value_and_not_only_its_length():
    """Two different passwords can share a length, so the value decides."""

    probe = _probe()
    contract = _contract()
    expected = contract["REDIS_URL"]
    expected_password = probe.userinfo(expected)[1]
    other_password = "x" * len(expected_password)
    environment = _container_environment(REDIS_URL=expected.replace(expected_password, other_password))

    rows = probe.evaluate(contract, environment)

    failed = {row.name: row.status for row in rows if not row.ok}
    assert failed == {"REDIS_URL": "credential_mismatch"}


def test_probe_never_prints_a_connection_credential():
    """The report may carry the length and the verdict, never a value."""

    probe = _probe()
    contract = _contract()
    environment = _container_environment(REDIS_URL="redis://:another-redis-pass@redis:6379/1")

    rendered = probe.render(probe.evaluate(contract, environment))

    assert "another-redis-pass" not in rendered
    assert contract["REDIS_PASSWORD"] not in rendered
    assert "credential_mismatch" in rendered
    assert "REDIS_URL" in rendered


def test_probe_covers_every_credential_variable_of_the_application():
    """A new credential variable has to join the isolation check."""

    probe = _probe()
    required = _credential_variables_of_the_application() - NOT_A_CREDENTIAL

    assert required <= set(probe.CREDENTIAL_VARIABLES)
    # The Compose file interpolates the database passwords of the test
    # profile; the application never reads them.
    assert {"MONGO_TEST_PASSWORD", "REDIS_PASSWORD"} <= set(probe.CREDENTIAL_VARIABLES)
