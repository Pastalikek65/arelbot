from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.evidence_audit import OBSERVED_LIVE_FILES
from scripts.phase_audit import evaluate

REPO_ROOT = Path(__file__).resolve().parents[2]
PHASE_MANIFEST = REPO_ROOT / "docs" / "PHASE_STATUS.json"
EVIDENCE_DIR = REPO_ROOT / "deploy" / "evidence"


def _gate(*names: str) -> dict:
    return {"checks": [{"name": name, "ok": True} for name in names]}


def _evidence(*names: str) -> dict:
    return {"checks": [{"name": name, "ok": True} for name in names]}


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "scripts/phase_audit.py", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def test_phase_audit_marks_deterministic_phases_verified_and_live_phase_pending():
    manifest = {
        "schema_version": 1,
        "phases": [
            {"id": "0", "name": "stability", "required_checks": ["architecture"]},
            {"id": "9", "name": "deployment", "required_live_files": ["telegram-mutation-live.json"]},
        ],
    }
    report = evaluate(
        manifest,
        _gate("architecture"),
        _evidence("telegram-mutation-live.json"),
    )

    assert report["ok"] is False
    assert report["verified_count"] == 1
    assert report["next_phase"] == "9"
    assert report["phases"][1]["status"] == "deployment_pending"
    assert report["blocking_phases"] == []
    assert report["awaiting_live_evidence"] == ["9"]


def test_phase_audit_does_not_call_missing_gate_checks_verified():
    manifest = {
        "schema_version": 1,
        "phases": [{"id": "1", "name": "media", "required_checks": ["provider_load"]}],
    }
    report = evaluate(manifest, _gate(), _evidence())

    assert report["ok"] is False
    assert report["phases"][0]["status"] == "needs_attention"
    assert report["phases"][0]["missing_checks"] == ["provider_load"]
    assert report["blocking_phases"] == ["1"]
    assert report["awaiting_live_evidence"] == []


def test_phase_audit_requires_success_for_live_file():
    manifest = {
        "schema_version": 1,
        "phases": [{"id": "9", "name": "deployment", "required_live_files": ["live.json"]}],
    }
    report = evaluate(manifest, _gate(), {"checks": [{"name": "live.json:success", "ok": False}]})

    assert report["phases"][0]["status"] == "deployment_pending"
    assert report["phases"][0]["missing_live"] == ["live.json"]


def test_phase_nine_requires_vds_readiness_to_pass_not_only_exist():
    manifest = {
        "schema_version": 1,
        "phases": [{"id": "9", "name": "deployment", "required_live_files": ["vds-readiness.json"]}],
    }
    report = evaluate(
        manifest,
        _gate(),
        {"checks": [{"name": "vds-readiness.json:success", "ok": False}]},
    )

    assert report["phases"][0]["status"] == "deployment_pending"
    assert report["phases"][0]["missing_live"] == ["vds-readiness.json"]


def test_phase_audit_cli_succeeds_for_the_repository_manifest(tmp_path):
    """A phase that only waits for live deployment evidence must not block CI.

    Phase 9 produces its evidence from a real deployment and can therefore
    never be satisfied inside CI.  The audit stays honest (``ok`` is still
    ``False``) while the exit code only reports real contract violations.

    The assertions describe the relation between the report fields instead
    of the current phase numbers, so a legitimate advance of the phase
    contract does not turn this test red.
    """

    manifest = json.loads(PHASE_MANIFEST.read_text(encoding="utf-8"))
    names = {name for phase in manifest["phases"] for name in phase.get("required_checks", [])}
    fixture = tmp_path / "unit-fixture-gate.json"
    fixture.write_text(json.dumps(_gate(*names)), encoding="utf-8")
    result = _run_cli("--gate", str(fixture))

    assert result.returncode == 0, result.stdout
    report = json.loads(result.stdout)
    assert report["blocking_phases"] == []
    assert {phase["id"] for phase in report["phases"] if phase["status"] != "verified"} == set(
        report["awaiting_live_evidence"]
    )
    assert report["verified_count"] + len(report["awaiting_live_evidence"]) == report["phase_count"]
    assert all(phase["status"] in {"verified", "deployment_pending"} for phase in report["phases"])


def test_phase_audit_cli_fails_when_a_required_check_is_missing(tmp_path):
    """An unmet deterministic check is a real contract violation and must fail."""

    manifest = json.loads(PHASE_MANIFEST.read_text(encoding="utf-8"))
    phase_zero = next(phase for phase in manifest["phases"] if phase["id"] == "0")
    phase_zero["required_checks"] = [*phase_zero["required_checks"], "check_that_does_not_exist"]
    broken_manifest = tmp_path / "PHASE_STATUS.json"
    broken_manifest.write_text(json.dumps(manifest), encoding="utf-8")

    result = _run_cli("--manifest", str(broken_manifest))

    assert result.returncode == 1, result.stdout
    report = json.loads(result.stdout)
    assert "0" in report["blocking_phases"]
    assert report["phases"][0]["status"] == "needs_attention"
    assert report["ok"] is False
    assert report["next_phase"] == "0"


def test_phase_audit_cli_fails_when_required_evidence_is_missing(tmp_path):
    """A missing required artifact is a real contract violation and must fail.

    The deterministic evidence artifacts are committed to the repository, so
    an absent artifact is a repository regression rather than a pending
    deployment.
    """

    manifest = {
        "schema_version": 1,
        "phases": [
            {
                "id": "2",
                "name": "durable_state_and_persistence",
                "required_evidence": ["persistence-staging.json"],
            },
        ],
    }
    evidence_manifest = tmp_path / "PHASE_STATUS.json"
    evidence_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    empty_evidence_dir = tmp_path / "evidence"
    empty_evidence_dir.mkdir()

    result = _run_cli("--manifest", str(evidence_manifest), "--evidence-dir", str(empty_evidence_dir))

    assert result.returncode == 1, result.stdout
    report = json.loads(result.stdout)
    assert "2" in report["blocking_phases"]
    assert report["phases"][0]["status"] == "needs_attention"
    assert report["phases"][0]["missing_evidence"] == ["persistence-staging.json"]


def test_phase_audit_cli_succeeds_when_only_live_evidence_is_pending(tmp_path):
    """A synthetic manifest that only waits for live evidence exits zero."""

    manifest = {
        "schema_version": 1,
        "phases": [
            {"id": "0", "name": "stabilization", "required_checks": ["architecture"]},
            {
                "id": "9",
                "name": "live_deployment_validation",
                "required_live_files": ["telegram-mutation-live.json"],
            },
        ],
    }
    live_only_manifest = tmp_path / "PHASE_STATUS.json"
    live_only_manifest.write_text(json.dumps(manifest), encoding="utf-8")

    fixture = tmp_path / "unit-fixture-gate.json"
    fixture.write_text(json.dumps(_gate("architecture")), encoding="utf-8")
    result = _run_cli("--manifest", str(live_only_manifest), "--gate", str(fixture))

    assert result.returncode == 0, result.stdout
    report = json.loads(result.stdout)
    assert report["blocking_phases"] == []
    assert report["awaiting_live_evidence"] == ["9"]
    assert report["ok"] is False
    assert report["next_phase"] == "9"


def test_committed_live_evidence_inventory_is_intact():
    """The committed live artifacts must stay in the repository.

    A phase that only waits for live evidence degrades to
    ``deployment_pending`` instead of failing when its artifact disappears,
    so the loss of a committed artifact would silently downgrade a verified
    phase.  The deterministic half of the inventory is covered by
    ``scripts/evidence_audit.py`` in the CI workflow; this test guards the
    observed live artifacts.
    """

    missing = sorted(name for name in OBSERVED_LIVE_FILES if not (EVIDENCE_DIR / name).is_file())

    assert missing == []
