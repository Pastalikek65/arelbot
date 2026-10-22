from __future__ import annotations

from scripts.phase9_evidence_checklist import build_checklist


def _manifest() -> dict:
    return {
        "schema_version": 1,
        "evidence_contracts": [
            {
                "file": "live.json",
                "kind": "disposable_live",
                "requires_live": True,
                "offline_replay_sufficient": False,
                "owner_action": "run live probe",
            }
        ],
        "phases": [{"id": "9", "name": "deployment", "required_live_files": ["live.json"]}],
    }


def test_checklist_keeps_missing_live_evidence_pending(tmp_path):
    report = build_checklist(_manifest(), tmp_path, {"checks": []})

    assert report["ok"] is False
    assert report["deployment_status"] == "deployment_pending"
    assert report["live_evidence_not_fabricated"] is True
    assert report["required_live_files"][0]["status"] == "missing"
    assert report["required_live_files"][0]["offline_replay_sufficient"] is False


def test_checklist_requires_invariants_and_success(tmp_path):
    (tmp_path / "live.json").write_text("{}", encoding="utf-8")
    report = build_checklist(
        _manifest(),
        tmp_path,
        {
            "checks": [
                {"name": "live.json:json", "ok": True},
                {"name": "live.json:safe", "ok": True},
            ]
        },
    )

    assert report["ok"] is False
    assert report["required_live_files"][0]["status"] == "requires_live_verification"


def test_checklist_accepts_only_a_complete_safe_live_contract(tmp_path):
    (tmp_path / "live.json").write_text("{}", encoding="utf-8")
    report = build_checklist(
        _manifest(),
        tmp_path,
        {
            "checks": [
                {"name": "live.json:json", "ok": True},
                {"name": "live.json:safe", "ok": True},
                {"name": "live.json:invariants", "ok": True},
                {"name": "live.json:success", "ok": True},
            ]
        },
    )

    assert report["ok"] is True
    assert report["deployment_status"] == "ready_for_review"
    assert report["pending_files"] == []
