from __future__ import annotations

from scripts.phase_audit import evaluate


def _gate(*names: str) -> dict:
    return {"checks": [{"name": name, "ok": True} for name in names]}


def _evidence(*names: str) -> dict:
    return {"checks": [{"name": name, "ok": True} for name in names]}


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


def test_phase_audit_does_not_call_missing_gate_checks_verified():
    manifest = {
        "schema_version": 1,
        "phases": [{"id": "1", "name": "media", "required_checks": ["provider_load"]}],
    }
    report = evaluate(manifest, _gate(), _evidence())

    assert report["ok"] is False
    assert report["phases"][0]["status"] == "needs_attention"
    assert report["phases"][0]["missing_checks"] == ["provider_load"]


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
