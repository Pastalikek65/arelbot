"""Audit the private proxy snapshot without opening any operator credential."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def audit(root: Path = ROOT) -> dict:
    vendor = root / "vendor" / "d2api"
    metadata = json.loads((vendor / "SOURCE.json").read_text(encoding="utf-8"))
    expected = set(metadata["files"]) | {"SOURCE.json", "arel-start.js"}
    actual = {p.relative_to(vendor).as_posix() for p in vendor.rglob("*") if p.is_file()}
    errors = []
    if actual != expected:
        errors.append("unexpected_or_missing_snapshot_file")
    for name, record in metadata["files"].items():
        path = vendor / name
        if path.is_symlink() or not path.is_file() or vendor.resolve() not in path.resolve().parents:
            errors.append("invalid_snapshot_path")
            continue
        if hashlib.sha256(path.read_bytes()).hexdigest() != record["vendored_sha256"]:
            errors.append("snapshot_hash_mismatch")
    return {"ok": not errors, "files": len(actual), "credential_files_in_snapshot": False if actual == expected else "UNKNOWN",
            "errors": sorted(set(errors)), "wasm_redistributed": "assets/deepseek_hash.wasm" in actual}


if __name__ == "__main__":
    result = audit()
    print(json.dumps(result))
    raise SystemExit(0 if result["ok"] else 1)
