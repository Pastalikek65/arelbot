#!/usr/bin/env python3
"""Produce a secret-free, conservative Phase 9 evidence checklist.

This command is a planning/release gate aid. It never creates evidence, calls
Telegram, contacts a VDS, or treats fixture/replay output as live deployment
proof. A missing or invalid live artifact remains an explicit operator action.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = PROJECT_ROOT / "docs" / "PHASE_STATUS.json"
DEFAULT_EVIDENCE = PROJECT_ROOT / "deploy" / "evidence"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.evidence_audit import audit as audit_evidence


_INVARIANT_CHECKS = {
    "vds-readiness.json": "vds_readiness:invariants",
    "telegram-mutation-live.json": "telegram_mutation:invariants",
    "media-staging-full-matrix.json": "live_media_matrix:invariants",
    "vds-backup-restore.json": "vds_backup_restore:invariants",
    "monitoring-alert-delivery.json": "monitoring_delivery:invariants",
    "provider-governance-approval.json": "provider_approval:invariants",
    "evren-staging.json": "evren_probe:invariants",
}


def _read_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name}:root_not_object")
    return payload


def _contracts(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = manifest.get("evidence_contracts", [])
    if not isinstance(rows, list):
        raise ValueError("evidence_contracts_invalid")
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("file"), str):
            raise ValueError("evidence_contract_invalid")
        name = row["file"]
        if name in result:
            raise ValueError(f"evidence_contract_duplicate:{name}")
        if row.get("requires_live") is not True or row.get("offline_replay_sufficient") is not False:
            raise ValueError(f"evidence_contract_not_conservative:{name}")
        if not isinstance(row.get("kind"), str) or not isinstance(row.get("owner_action"), str):
            raise ValueError(f"evidence_contract_metadata_missing:{name}")
        result[name] = row
    return result


def build_checklist(manifest: dict[str, Any], evidence_dir: Path, evidence_report: dict[str, Any]) -> dict[str, Any]:
    phases = manifest.get("phases")
    if not isinstance(phases, list):
        raise ValueError("phase_manifest_invalid")
    phase9 = next((item for item in phases if isinstance(item, dict) and item.get("id") == "9"), None)
    if not isinstance(phase9, dict):
        raise ValueError("phase_9_missing")
    required = phase9.get("required_live_files", [])
    if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
        raise ValueError("phase_9_live_files_invalid")
    contracts = _contracts(manifest)
    missing_contracts = [name for name in required if name not in contracts]
    if missing_contracts:
        raise ValueError("phase_9_contract_missing:" + ",".join(missing_contracts))

    checks = {
        str(row.get("name")): row.get("ok") is True
        for row in evidence_report.get("checks", [])
        if isinstance(row, dict) and isinstance(row.get("name"), str)
    }
    items: list[dict[str, Any]] = []
    for name in required:
        path = evidence_dir / name
        present = path.is_file()
        invariant = checks.get(_INVARIANT_CHECKS.get(name, f"{name}:invariants"))
        safe = checks.get(f"{name}:safe")
        success = checks.get(f"{name}:success")
        if not present:
            status = "missing"
        elif invariant is False or safe is False:
            status = "invalid"
        elif success is True:
            status = "satisfied"
        else:
            status = "requires_live_verification"
        items.append(
            {
                "file": name,
                "kind": contracts[name]["kind"],
                "status": status,
                "present": present,
                "invariants_pass": invariant is True,
                "secret_free_pass": safe is True,
                "live_success_pass": success is True,
                "offline_replay_sufficient": False,
                "owner_action": contracts[name]["owner_action"] if status != "satisfied" else None,
            }
        )

    pending = [item["file"] for item in items if item["status"] != "satisfied"]
    return {
        "schema_version": 1,
        "scope": "phase9-evidence-checklist",
        "phase": "9",
        "ok": not pending,
        "deployment_status": "ready_for_review" if not pending else "deployment_pending",
        "live_evidence_not_fabricated": True,
        "offline_replay_cannot_promote": True,
        "required_live_files": items,
        "pending_files": pending,
        "safe": True,
        "environment": "production" if os.getenv("APP_ENV") == "production" else "non_production",
    }


def _write_report(path: Path, report: dict[str, Any]) -> None:
    if path.exists() and path.is_symlink():
        raise OSError("report_target_symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(report, handle, ensure_ascii=True, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--evidence-dir", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    manifest = _read_object(args.manifest)
    evidence_report = audit_evidence(args.evidence_dir, require_live=True)
    report = build_checklist(manifest, args.evidence_dir, evidence_report)
    if args.report:
        _write_report(args.report, report)
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
