from __future__ import annotations

import json

import pytest

from scripts.vds_readiness import (
    VdsReadinessError,
    build_report,
    parse_port_list,
    parse_free_space_kb,
    parse_release_count,
    validate_live_gate,
)
from scripts.evidence_audit import _audit_live_invariants


def test_live_readiness_requires_two_flags_and_environment_gate():
    with pytest.raises(VdsReadinessError, match="vds_readiness_live_gate_required"):
        validate_live_gate(live=True, confirm_live=False, env_value="1")

    validate_live_gate(live=True, confirm_live=True, env_value="1")


def test_release_and_port_parsers_are_bounded_and_deterministic():
    assert parse_release_count("0\n") == 0
    assert parse_release_count("8\nnoise\n") is None
    assert parse_free_space_kb("1048576\n") == 1048576
    assert parse_free_space_kb("-1\n") is None
    assert parse_port_list("LISTEN 0 4096 *:80 *:*\nLISTEN 0 4096 *:443 *:*\n") == [80, 443]
    assert parse_port_list("garbage\n") == []


def test_report_is_safe_and_marks_missing_shared_env_as_blocker():
    report = build_report(
        {
            "ssh_authenticated": True,
            "host_key_verified": True,
            "linux_host": True,
            "docker_available": True,
            "compose_available": True,
            "app_root_present": True,
            "app_root_writable": False,
            "shared_env_present": False,
            "release_count": 0,
            "free_space_kb": 1024,
            "target_container_count": 0,
            "nginx_active": False,
            "listening_ports": [80, 443],
            "existing_container_count": 8,
        }
    )

    assert report["scope"] == "vds-readiness"
    assert report["ok"] is False
    assert "shared_env_missing" in report["blockers"]
    assert "app_root_not_writable" in report["blockers"]
    assert "disk_space_low" in report["blockers"]
    assert report["secret_free"] is True
    serialized = json.dumps(report).casefold()
    assert "78.135" not in serialized
    assert "password" not in serialized


def test_readiness_evidence_requires_read_only_contract_and_truthful_checks():
    report = build_report(
        {
            "ssh_authenticated": True,
            "host_key_verified": True,
            "linux_host": True,
            "docker_available": True,
            "compose_available": True,
            "app_root_present": True,
            "shared_env_present": False,
            "release_count": 0,
            "nginx_active": False,
            "listening_ports": [80, 443],
            "existing_container_count": 8,
        }
    )
    assert _audit_live_invariants("vds-readiness.json", report).ok is True

    forged = dict(report)
    forged["ok"] = True
    assert _audit_live_invariants("vds-readiness.json", forged).ok is False

    mutable = dict(report)
    mutable["mutation"] = True
    assert _audit_live_invariants("vds-readiness.json", mutable).ok is False
