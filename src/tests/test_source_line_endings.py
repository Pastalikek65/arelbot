from __future__ import annotations

import sys
from pathlib import Path

import scripts.architecture_check as architecture_check
from scripts.architecture_check import check_line_endings

REPO_ROOT = Path(__file__).resolve().parents[2]


def _python_sources() -> list[Path]:
    """Every Python source in the repository, matching the gate's scan.

    The architecture gate walks ``ROOT.rglob("*.py")``, so the test has to
    walk the same set.  Scoping the test to ``src/`` and ``scripts/`` alone
    would leave the root modules guarded by the gate but untested.
    """

    return sorted(p for p in REPO_ROOT.rglob("*.py") if "__pycache__" not in p.parts)


def test_check_line_endings_flags_crlf(tmp_path):
    target = tmp_path / "sample.py"
    target.write_bytes(b"x = 1\r\ny = 2\n")
    assert check_line_endings(target)


def test_check_line_endings_flags_lone_cr(tmp_path):
    target = tmp_path / "sample.py"
    target.write_bytes(b"x = 1\ry = 2\n")
    assert check_line_endings(target)


def test_check_line_endings_accepts_lf(tmp_path):
    target = tmp_path / "sample.py"
    target.write_bytes(b"x = 1\ny = 2\n")
    assert not check_line_endings(target)


def test_check_line_endings_skips_generated_paths(tmp_path):
    for directory in ("__pycache__", "dist"):
        generated = tmp_path / directory
        generated.mkdir()
        target = generated / "sample.py"
        target.write_bytes(b"x = 1\r\ny = 2\n")
        assert not check_line_endings(target)


def test_check_source_line_endings_reports_crlf_source(tmp_path, monkeypatch):
    (tmp_path / "sample.py").write_bytes(b"x = 1\r\ny = 2\n")
    monkeypatch.setattr(architecture_check, "ROOT", tmp_path)

    errors = architecture_check.check_source_line_endings()

    assert errors == ["non LF line endings: sample.py"]


def test_architecture_check_strict_reports_line_ending_violation(tmp_path, monkeypatch, capsys):
    (tmp_path / "sample.py").write_bytes(b"x = 1\r\ny = 2\n")
    monkeypatch.setattr(architecture_check, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["architecture_check.py", "--strict"])

    exit_code = architecture_check.main()

    assert exit_code == 1
    assert "non LF line endings: sample.py" in capsys.readouterr().out


def test_architecture_check_strict_reports_no_violation_for_lf_source(tmp_path, monkeypatch, capsys):
    (tmp_path / "sample.py").write_bytes(b"x = 1\ny = 2\n")
    monkeypatch.setattr(architecture_check, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["architecture_check.py", "--strict"])

    architecture_check.main()

    assert "non LF line endings" not in capsys.readouterr().out


def test_repository_sources_use_lf_only():
    offenders = [str(p.relative_to(REPO_ROOT)) for p in _python_sources() if check_line_endings(p)]
    assert offenders == []


def test_repository_source_scope_matches_the_gate_scope():
    scanned = {p.resolve() for p in _python_sources()}
    gated = {p.resolve() for p in architecture_check.ROOT.rglob("*.py") if "__pycache__" not in p.parts}
    assert scanned == gated
