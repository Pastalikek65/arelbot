#!/usr/bin/env python3
"""Evaluate Arelbot phases against immutable release/evidence contracts.

The report is deliberately conservative: deterministic checks can verify
repository behavior, but they cannot promote a VDS, Telegram mutation, live
media, monitoring, or legal provider approval to production evidence.  The
first unmet phase is printed as the next engineering/deployment action.

Two kinds of unmet phase are reported separately:

``needs_attention``
    A deterministic check or a required artifact is missing.  This is a real
    contract violation and blocks the gate.

``deployment_pending``
    The phase only waits for live deployment evidence, which is produced by a
    real deployment and can never be satisfied inside CI.  This is a
    documented, legitimate state and does not block the gate.

``ok`` keeps its original meaning -- every phase is ``verified`` -- so the
report stays honest.  The exit code follows the blocking phases only.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = PROJECT_ROOT / "docs" / "PHASE_STATUS.json"
DEFAULT_GATE = PROJECT_ROOT / "deploy" / "evidence" / "release-gate.json"
DEFAULT_EVIDENCE = PROJECT_ROOT / "deploy" / "evidence"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.evidence_audit import audit as audit_evidence


def _read_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name}:root_not_object")
    return payload


def _check_map(gate: dict[str, Any]) -> dict[str, bool]:
    rows = gate.get("checks")
    if not isinstance(rows, list):
        return {}
    result: dict[str, bool] = {}
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("name"), str):
            result[row["name"]] = row.get("ok") is True
    return result


def _git_commit() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.strip()[:64]
    except (OSError, subprocess.CalledProcessError):
        return None


def evaluate(manifest: dict[str, Any], gate: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    """Return a bounded phase report; all inputs are already parsed objects."""

    phases = manifest.get("phases")
    if manifest.get("schema_version") != 1 or not isinstance(phases, list) or not phases:
        raise ValueError("phase_manifest_invalid")
    check_map = _check_map(gate)
    evidence_checks = {
        str(row.get("name")): row.get("ok") is True
        for row in evidence.get("checks", [])
        if isinstance(row, dict) and isinstance(row.get("name"), str)
    }
    phase_rows: list[dict[str, Any]] = []
    for phase in phases:
        if not isinstance(phase, dict) or not isinstance(phase.get("id"), str) or not isinstance(phase.get("name"), str):
            raise ValueError("phase_manifest_entry_invalid")
        required_checks = phase.get("required_checks", [])
        required_evidence = phase.get("required_evidence", [])
        required_live = phase.get("required_live_files", [])
        if not all(isinstance(item, str) for item in (*required_checks, *required_evidence, *required_live)):
            raise ValueError(f"phase_manifest_requirements_invalid:{phase['id']}")

        missing_checks = [name for name in required_checks if not check_map.get(name, False)]
        missing_evidence = [name for name in required_evidence if not evidence_checks.get(f"{name}:json", False)]
        missing_live = [name for name in required_live if not evidence_checks.get(f"{name}:success", False)]
        if missing_checks or missing_evidence:
            status = "needs_attention"
        elif required_live and missing_live:
            status = "deployment_pending"
        else:
            status = "verified"
        phase_rows.append(
            {
                "id": phase["id"],
                "name": phase["name"],
                "status": status,
                "missing_checks": missing_checks,
                "missing_evidence": missing_evidence,
                "missing_live": missing_live,
            }
        )

    next_phase = next((row["id"] for row in phase_rows if row["status"] != "verified"), None)
    blocking_phases = [row["id"] for row in phase_rows if row["status"] == "needs_attention"]
    awaiting_live_evidence = [row["id"] for row in phase_rows if row["status"] == "deployment_pending"]
    return {
        "ok": next_phase is None,
        "commit": _git_commit(),
        "phase_count": len(phase_rows),
        "verified_count": sum(row["status"] == "verified" for row in phase_rows),
        "next_phase": next_phase,
        "blocking_phases": blocking_phases,
        "awaiting_live_evidence": awaiting_live_evidence,
        "phases": phase_rows,
    }


def _write_report(path: Path, report: dict[str, Any]) -> None:
    if path.exists() and path.is_symlink():
        raise OSError("phase report target cannot be symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with open(fd, "w", encoding="utf-8", newline="\n", closefd=True) as handle:
            json.dump(report, handle, ensure_ascii=True, indent=2)
            handle.write("\n")
        Path(name).replace(path)
    finally:
        temporary = Path(name)
        if temporary.exists():
            temporary.unlink()


def audit(manifest_path: Path = DEFAULT_MANIFEST, gate_path: Path = DEFAULT_GATE, evidence_dir: Path = DEFAULT_EVIDENCE) -> dict[str, Any]:
    manifest = _read_object(manifest_path)
    gate = _read_object(gate_path)
    evidence = audit_evidence(evidence_dir, require_live=True)
    return evaluate(manifest, gate, evidence)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--gate", type=Path, default=DEFAULT_GATE)
    parser.add_argument("--evidence-dir", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = audit(args.manifest, args.gate, args.evidence_dir)
    if args.report:
        _write_report(args.report, report)
    print(json.dumps(report, ensure_ascii=True, indent=2))
    # Only a real contract violation blocks.  A phase that merely waits for
    # live deployment evidence is a documented state and stays green, so the
    # audit can run inside CI without turning permanently red.
    return 0 if not report["blocking_phases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
