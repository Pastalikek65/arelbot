from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.guardian_bridge_staging import (
    GuardianBridgeStagingError,
    build_check_report,
    build_failure_report,
    build_validation_report,
)


def test_bridge_staging_default_report_is_secret_free():
    report = build_validation_report()
    rendered = json.dumps(report)
    assert report["ok"] is True
    assert report["real_guardian_engine_tested"] is False
    assert report["secret_free"] is True
    assert "token" not in rendered.casefold()


def test_bridge_check_never_opens_socket(tmp_path: Path):
    report = build_check_report(str(tmp_path / "missing.sock"))
    assert report["ok"] is True
    assert report["socket_present"] is False
    assert report["secret_free"] is True


def test_bridge_failure_report_drops_exception_details():
    report = build_failure_report(ValueError("Bearer secret https://private.invalid"))
    rendered = json.dumps(report)
    assert report["ok"] is False
    assert "Bearer secret" not in rendered
    assert "private.invalid" not in rendered


def test_bridge_staging_default_cli_is_non_live(tmp_path: Path):
    report_path = tmp_path / "bridge.json"
    result = subprocess.run(
        [sys.executable, "scripts/guardian_bridge_staging.py", "--report", str(report_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["mode"] == "validation-only"
    assert report_path.exists()


@pytest.mark.skipif(os.name == "nt", reason="Unix socket bridge is Linux-only")
def test_bridge_self_test_uses_synthetic_provider_only():
    result = subprocess.run(
        [sys.executable, "scripts/guardian_bridge_staging.py", "--self-test"],
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(result.stdout)
    assert result.returncode == 0
    assert payload["mode"] == "self-test"
    assert payload["real_guardian_engine_tested"] is False
    assert payload["metrics"]["completed"] == 1


def test_bridge_staging_error_is_bounded():
    error = GuardianBridgeStagingError("token=secret https://private.invalid")
    assert "secret" not in str(error)
    assert error.code == "guardian_bridge_staging_failed"
