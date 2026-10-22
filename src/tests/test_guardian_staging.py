from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.guardian_staging import (
    GuardianStagingError,
    build_check_report,
    build_failure_report,
    build_validation_report,
    validate_live_gate,
)


def test_guardian_staging_requires_explicit_three_part_live_gate():
    with pytest.raises(GuardianStagingError, match="live_gate_required"):
        validate_live_gate(live=True, confirm_live=False, env_value="1")
    with pytest.raises(GuardianStagingError, match="live_gate_required"):
        validate_live_gate(live=True, confirm_live=True, env_value="0")


def test_validation_report_is_bounded_and_secret_free():
    report = build_validation_report()

    assert report["scope"] == "guardian-staging"
    assert report["mode"] == "validation-only"
    assert report["secret_free"] is True
    assert "token" not in json.dumps(report).casefold()


def test_failure_report_does_not_include_exception_text():
    report = build_failure_report(ValueError("Bearer super-secret https://private.invalid/x"))

    rendered = json.dumps(report)
    assert report["ok"] is False
    assert "super-secret" not in rendered
    assert "private.invalid" not in rendered
    assert report["secret_free"] is True


def test_default_cli_does_not_contact_socket(tmp_path: Path):
    report_path = tmp_path / "guardian.json"
    result = subprocess.run(
        [sys.executable, "scripts/guardian_staging.py", "--socket", str(tmp_path / "missing.sock"), "--report", str(report_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["mode"] == "validation-only"
    assert payload["secret_free"] is True
    assert report_path.exists()


def test_check_bridge_is_non_live_and_reports_only_socket_presence(tmp_path: Path):
    result = build_check_report(str(tmp_path / "missing.sock"))

    assert result["ok"] is True
    assert result["mode"] == "check-only"
    assert result["socket_present"] is False
    assert result["live_probe_required"] is True
