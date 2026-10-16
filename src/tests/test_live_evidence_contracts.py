from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from scripts.evidence_audit import _audit_live_invariants, audit


def _media_matrix() -> dict:
    platforms = {
        name: {"jobs": 1, "passed": 1, "failed": 0}
        for name in ("instagram", "tiktok", "youtube", "x")
    }
    return {
        "scope": "live-media-matrix",
        "ok": True,
        "mode": "live-download",
        "case_count": 4,
        "passed": 4,
        "failed": 0,
        "platforms": platforms,
        "completion_slo": {"met": True, "p95_ms": 8_900},
        "latency_ms": {"p95": 8_900},
        "chat_count": 100,
        "concurrency_limit": 10,
        "max_active_jobs": 10,
    }


def test_cleanapis_live_evidence_requires_bounded_model_probe():
    payload = {
        "scope": "cleanapis-staging",
        "ok": True,
        "mode": "live-completion",
        "provider": "cleanapis",
        "model": "deepseek-v4-flash-0731",
        "latency_ms": 120.5,
        "response_chars": 1,
        "usage_present": True,
        "secret_free": True,
    }
    result = _audit_live_invariants("cleanapis-staging.json", payload)
    assert result is not None and result.ok is True

    broken = copy.deepcopy(payload)
    broken["model"] = "other-model"
    result = _audit_live_invariants("cleanapis-staging.json", broken)
    assert result is not None and result.ok is False
    assert result.code == "cleanapis_probe_invariant_failed"


def test_live_media_matrix_requires_all_platforms_and_slo():
    payload = _media_matrix()

    result = _audit_live_invariants("media-staging-full-matrix.json", payload)
    assert result is not None and result.ok is True

    broken = copy.deepcopy(payload)
    broken["platforms"].pop("x")
    result = _audit_live_invariants("media-staging-full-matrix.json", broken)
    assert result is not None and result.ok is False
    assert result.code == "live_media_matrix_invariant_failed"


@pytest.mark.parametrize(
    ("filename", "payload"),
    [
        (
            "telegram-mutation-live.json",
            {
                "scope": "telegram-mutation-live",
                "ok": True,
                "passed": 3,
                "failed": 0,
                "operations": {"sendMessage": True, "sendVideo": True, "answerCallbackQuery": True},
            },
        ),
        (
            "vds-backup-restore.json",
            {
                "scope": "vds-backup-restore",
                "ok": True,
                "checks": {"backup": True, "checksum": True, "restore": True, "rollback": True},
                "archive_sha256": "a" * 64,
            },
        ),
        (
            "monitoring-alert-delivery.json",
            {
                "scope": "live-monitoring-alert-delivery",
                "ok": True,
                "alerts": {"bot_down": True, "queue_backlog": True},
                "delivered": 2,
                "failed": 0,
            },
        ),
        (
            "provider-governance-approval.json",
            {
                "scope": "provider-governance-approval",
                "ok": True,
                "features": [{"feature_key": "weather", "status": "approved", "evidence_id": "review:weather:1"}],
                "approved_count": 1,
                "pending_count": 0,
                "rejected_count": 0,
            },
        ),
    ],
)
def test_live_evidence_contracts_require_stable_scope_and_counters(filename, payload):
    result = _audit_live_invariants(filename, payload)
    assert result is not None and result.ok is True

    broken = copy.deepcopy(payload)
    broken["ok"] = False
    result = _audit_live_invariants(filename, broken)
    assert result is not None and result.ok is False


def test_require_live_audit_promotes_only_a_complete_valid_evidence_set(tmp_path: Path):
    deterministic = {
        "media-queue-load.json": {
            "ok": True, "jobs": 100, "completed": 100, "failed": 0,
            "queue_depth": 0, "workers": 10, "max_active_workers": 10,
            "active_chats": 100, "secret_free": True,
        },
        "persistence-staging.json": {"ok": True, "checks": {"restore": True, "cleanup": True}, "secret_free": True},
        "provider-staging.json": {"ok": True, "case_count": 1, "passed": 1, "failed": 0, "repeat_latency_ms": {}, "secret_free": True},
        "media-staging-validation.json": {"ok": True, "disabled_count": 0, "secret_free": True},
        "media-replay-matrix.json": {
            "ok": True, "case_count": 6, "passed": 6, "failed": 0, "network_calls": 0,
            "platforms": {name: {"cases": 1, "passed": 1, "failed": 0} for name in ("instagram", "tiktok", "youtube", "x", "facebook", "reddit")},
            "secret_free": True,
        },
    }
    for name, payload in deterministic.items():
        (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")
    for name in ("telegram-staging.json", "media-staging-concurrency-docker-rerun.json"):
        (tmp_path / name).write_text(json.dumps({"ok": True}), encoding="utf-8")
    (tmp_path / "provider-governance-links.json").write_text(json.dumps({
        "links": {"scope": "live-link-health", "approval_unchanged": True, "link_count": 0, "passed": 0, "failed": 0, "results": []},
        "ok": True,
    }), encoding="utf-8")
    (tmp_path / "vds-readiness.json").write_text(json.dumps({
        "scope": "vds-readiness",
        "ok": True,
        "mode": "live-read-only",
        "mutation": False,
        "release_count": 1,
        "free_space_kb": 10**9,
        "target_container_count": 0,
        "nginx_active": True,
        "listening_ports": [80, 443],
        "existing_container_count": 4,
        "blockers": [],
        "checks": {
            "ssh_authenticated": True,
            "host_key_verified": True,
            "linux_host": True,
            "docker_available": True,
            "compose_available": True,
            "app_root_present": True,
            "app_root_writable": True,
            "shared_env_present": True,
        },
        "secret_free": True,
    }), encoding="utf-8")
    (tmp_path / "cleanapis-staging.json").write_text(json.dumps({
        "scope": "cleanapis-staging", "ok": True, "mode": "live-completion",
        "provider": "cleanapis", "model": "deepseek-v4-flash-0731",
        "latency_ms": 120.5, "response_chars": 1, "usage_present": True,
        "secret_free": True,
    }), encoding="utf-8")
    deployment = {
        "telegram-mutation-live.json": {
            "scope": "telegram-mutation-live", "ok": True, "passed": 1, "failed": 0, "operations": {"sendMessage": True},
        },
        "media-staging-full-matrix.json": _media_matrix(),
        "vds-backup-restore.json": {
            "scope": "vds-backup-restore", "ok": True,
            "checks": {"backup": True, "checksum": True, "restore": True, "rollback": True}, "archive_sha256": "b" * 64,
        },
        "monitoring-alert-delivery.json": {
            "scope": "live-monitoring-alert-delivery", "ok": True,
            "alerts": {"bot_down": True}, "delivered": 1, "failed": 0,
        },
        "provider-governance-approval.json": {
            "scope": "provider-governance-approval", "ok": True,
            "features": [{"feature_key": "weather", "status": "approved", "evidence_id": "review:weather:1"}],
            "approved_count": 1, "pending_count": 0, "rejected_count": 0,
        },
    }
    for name, payload in deployment.items():
        (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")

    report = audit(tmp_path, require_live=True)
    assert report["ok"] is True

    broken = copy.deepcopy(deployment["media-staging-full-matrix.json"])
    broken["completion_slo"]["met"] = False
    (tmp_path / "media-staging-full-matrix.json").write_text(json.dumps(broken), encoding="utf-8")
    report = audit(tmp_path, require_live=True)
    assert report["ok"] is False
    assert any(row["code"] == "live_media_matrix_invariant_failed" for row in report["checks"])
