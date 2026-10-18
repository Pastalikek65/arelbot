from __future__ import annotations

import json

import pytest

from scripts.persistence_staging_smoke import (
    PersistenceSmokeError,
    _safe_report,
    _validate_name,
)


def test_persistence_smoke_allowlists_database_names():
    assert _validate_name("arel_bot_backup_smoke_abc123", "database") == "arel_bot_backup_smoke_abc123"
    with pytest.raises(PersistenceSmokeError, match="invalid_database"):
        _validate_name("../../unsafe", "database")


def test_persistence_smoke_report_is_bounded_and_atomic(tmp_path):
    report = tmp_path / "evidence" / "persistence.json"
    _safe_report(report, {"ok": True, "secret_free": True})
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload == {"ok": True, "secret_free": True}
    assert not list(report.parent.glob("*.tmp"))


def test_persistence_smoke_report_rejects_symlink_target(tmp_path):
    real = tmp_path / "real.json"
    real.write_text("{}", encoding="utf-8")
    link = tmp_path / "link.json"
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows runner")
    with pytest.raises(PersistenceSmokeError, match="report_target_symlink"):
        _safe_report(link, {"ok": True})

