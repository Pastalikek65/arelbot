from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from scripts.release_gate import commands, run_check, write_report
from scripts.evidence_audit import audit


def test_release_gate_captures_success_and_redacts_output():
    result = run_check(
        "fixture",
        [sys.executable, "-c", "print('Bearer super-secret https://youtube.com/watch?v=secret')"],
    )
    assert result.ok is True
    assert result.exit_code == 0
    assert "super-secret" not in result.output_tail
    assert "youtube.com" not in result.output_tail


def test_release_gate_keeps_live_external_e2e_out_of_deterministic_commands():
    names = [name for name, _command in commands(include_docker=False, requests=100, concurrency=10)]
    assert names == [
        "architecture",
        "feature_catalog",
        "provider_matrix",
        "provider_governance",
        "compileall",
        "pytest",
        "security_replay",
        "provider_load",
        "media_delivery_replay",
        "monitoring_alert_delivery_replay",
        "media_replay_matrix",
        "media_queue_load",
        "evidence_audit",
        "phase_audit",
    ]


def test_release_gate_includes_phase_audit():
    """The phase contract has to be part of the gate, not a separate command.

    A phase whose deterministic check or artifact is missing is a real
    contract violation.  When the audit only ran as a separate command, the
    gate could stay green while a phase was broken.
    """

    names = [name for name, _argv in commands(include_docker=False, requests=2, concurrency=1)]
    assert "phase_audit" in names

    check = dict(commands(include_docker=False, requests=2, concurrency=1))["phase_audit"]
    assert check[-1] == "scripts/phase_audit.py"


def test_release_gate_keeps_provider_approval_out_of_the_code_gate():
    """The provider approval flag belongs to the deployment preflight.

    ``scripts/provider_governance.py --require-approved`` fails for every
    feature whose source governance is still ``pending``; more than ten
    features are legitimately pending, so the flag would keep the release gate
    red for a documented reason.  It stays in ``scripts/production_preflight.py
    --require-provider-approval``.  The remaining hole -- a new provider that
    arrives with pending governance -- is still open and will be closed once
    the license registry check of the release-gate hardening lands.
    """

    checks = dict(commands(include_docker=False, requests=2, concurrency=1))
    assert "--require-approved" not in checks["provider_governance"]
    assert checks["provider_governance"][-1] == "scripts/provider_governance.py"


def test_release_gate_architecture_check_is_strict():
    checks = dict(commands(include_docker=False, requests=2, concurrency=1))
    assert "--strict" in checks["architecture"]


def test_docker_release_gate_validates_monitoring_config():
    names = [name for name, _command in commands(include_docker=True, requests=20, concurrency=10)]
    assert "monitoring_config" in names
    command = dict(commands(include_docker=True, requests=20, concurrency=10))["monitoring_config"]
    assert "prom/prometheus:v3.13.4" in command
    assert "check" in command and "config" in command


def test_docker_release_gate_validates_all_compose_profiles():
    command = dict(commands(include_docker=True, requests=20, concurrency=10))["compose_profiles"]

    assert command[:2] == ["docker", "compose"]
    assert command.count("--profile") == 3
    assert command[-2:] == ["config", "--quiet"]


def test_docker_gate_isolates_its_project_and_interpolates_every_profile_from_test_env():
    checks = dict(commands(include_docker=True, requests=8, concurrency=2))
    for name, command in checks.items():
        if command[:2] != ["docker", "compose"]:
            continue
        assert command[command.index("--env-file") + 1] == ".env.test", name
        assert command[command.index("--project-name") + 1] == "arelbot-gate", name


def test_phase_check_consumes_the_current_run_instead_of_previous_failure(tmp_path, monkeypatch):
    from scripts import release_gate
    previous = tmp_path / "deploy/evidence/release-gate.json"
    previous.parent.mkdir(parents=True)
    previous.write_text(json.dumps({"checks": [{"name": "architecture", "ok": False}]}))
    monkeypatch.setattr(release_gate, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["release_gate.py"])
    monkeypatch.setattr(release_gate, "commands", lambda **kwargs: [
        ("architecture", ["check"]), ("phase_audit", ["phase"]),
    ])

    def execute(name, command, **kwargs):
        if name == "phase_audit":
            assert "--gate" in command
            current = json.loads(Path(command[command.index("--gate") + 1]).read_text())
            assert next(row for row in current["checks"] if row["name"] == "architecture")["ok"] is True
        return release_gate.CheckResult(name, True, 0, 1.0, "ok")

    monkeypatch.setattr(release_gate, "run_check", execute)
    assert release_gate.main() == 0


def test_evidence_audit_validates_deterministic_invariants_and_keeps_live_optional(tmp_path):
    (tmp_path / "media-queue-load.json").write_text(
        '{"ok": true, "jobs": 100, "completed": 100, "failed": 0, "queue_depth": 0, "workers": 10, "max_active_workers": 10, "active_chats": 100, "secret_free": true}',
        encoding="utf-8",
    )
    (tmp_path / "persistence-staging.json").write_text(
        '{"ok": true, "checks": {"mongo_dump_restore": true, "redis_snapshot_restore": true, "cleanup": true}, "secret_free": true}',
        encoding="utf-8",
    )
    (tmp_path / "provider-staging.json").write_text(
        '{"ok": true, "case_count": 1, "passed": 1, "failed": 0, "repeat_latency_ms": {"p95": 1.0}}',
        encoding="utf-8",
    )
    (tmp_path / "media-staging-validation.json").write_text(
        '{"ok": true, "disabled_count": 0, "secret_free": true}',
        encoding="utf-8",
    )
    (tmp_path / "media-replay-matrix.json").write_text(
        json.dumps({
            "ok": True,
            "case_count": 6,
            "passed": 6,
            "failed": 0,
            "network_calls": 0,
            "platforms": {name: {"cases": 1, "passed": 1, "failed": 0} for name in (
                "instagram", "tiktok", "youtube", "x", "facebook", "reddit"
            )},
            "secret_free": True,
        }),
        encoding="utf-8",
    )

    report = audit(tmp_path)

    assert report["ok"] is True
    assert report["scope"] == "deterministic"
    assert report["live_required"] is False


def test_evidence_audit_fails_closed_for_live_mode_and_secret_material(tmp_path):
    for name, payload in {
        "media-queue-load.json": {"ok": True, "jobs": 1, "completed": 1, "failed": 0, "queue_depth": 0, "workers": 1, "max_active_workers": 1, "active_chats": 100},
        "persistence-staging.json": {"ok": True, "checks": {"cleanup": True}},
        "provider-staging.json": {"ok": True, "case_count": 1, "passed": 1, "failed": 0, "repeat_latency_ms": {}},
        "media-staging-validation.json": {"ok": True, "disabled_count": 0},
    }.items():
        (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")
    (tmp_path / "telegram-staging.json").write_text('{"ok": false, "token": "Bearer secret"}', encoding="utf-8")
    (tmp_path / "media-staging-concurrency-docker-rerun.json").write_text('{"ok": true}', encoding="utf-8")

    report = audit(tmp_path, require_live=True)

    assert report["ok"] is False
    assert any(row["code"] == "secret_material" for row in report["checks"])
    assert any(row["code"] == "live_evidence_failed" for row in report["checks"])
    assert any(row["code"] == "missing" and row["name"] == "telegram-mutation-live.json" for row in report["checks"])


def test_evidence_audit_accepts_provider_link_warning_but_requires_live_success(tmp_path):
    for name, payload in {
        "media-queue-load.json": {"ok": True, "jobs": 100, "completed": 100, "failed": 0, "queue_depth": 0, "workers": 10, "max_active_workers": 10, "active_chats": 100},
        "persistence-staging.json": {"ok": True, "checks": {"cleanup": True}},
        "provider-staging.json": {"ok": True, "case_count": 1, "passed": 1, "failed": 0, "repeat_latency_ms": {}},
        "media-staging-validation.json": {"ok": True, "disabled_count": 0},
        "media-replay-matrix.json": {
            "ok": True,
            "case_count": 6,
            "passed": 6,
            "failed": 0,
            "network_calls": 0,
            "platforms": {name: {"cases": 1, "passed": 1, "failed": 0} for name in (
                "instagram", "tiktok", "youtube", "x", "facebook", "reddit"
            )},
        },
        "provider-governance-links.json": {
            "ok": False,
            "links": {
                "scope": "live-link-health",
                "link_count": 1,
                "passed": 0,
                "failed": 1,
                "approval_unchanged": True,
                "results": [{"outcome": "http_error"}],
            },
        },
    }.items():
        (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")

    normal = audit(tmp_path)
    live = audit(tmp_path, require_live=True)

    assert normal["ok"] is True
    assert any(row["name"] == "provider_governance_links:invariants" and row["ok"] for row in normal["checks"])
    assert live["ok"] is False
    assert any(row["name"] == "provider-governance-links.json:success" and not row["ok"] for row in live["checks"])


def test_release_gate_report_is_atomic_and_rejects_symlink_target(tmp_path):
    report_path = tmp_path / "gate.json"
    write_report(report_path, {"ok": True, "checks": [{"name": "fixture"}]})

    assert json.loads(report_path.read_text(encoding="utf-8"))["ok"] is True
    assert not list(tmp_path.glob(".gate.json.*"))

    link = tmp_path / "link.json"
    try:
        link.symlink_to(report_path)
    except (OSError, NotImplementedError):
        pytest.skip("symlink capability unavailable")
    with pytest.raises(OSError, match="symlink"):
        write_report(link, {"ok": True})
