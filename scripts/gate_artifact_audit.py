#!/usr/bin/env python3
"""Report the drift between the committed gate artifact and the gate.

The committed artifact (``deploy/evidence/release-gate.json``) is the
evidence record of the last live gate run, and the phase contract audit
reads it.  When a check joins the gate, the artifact has to be regenerated
with a live Compose run; otherwise the new check stays invisible to the
phase contract and the evidence of a remediation never reaches the record.

The audit is **not** a check of the gate.  The gate has to stay green, and
the process that repairs the drift is the gate run itself: a check that
fails until an operator commits a regenerated artifact cannot live inside
the gate that regenerates it.  The audit therefore reports the drift and
exits 0 by default; ``--strict`` turns the drift into a failure and is used
by the release path, where a release must not ship with a stale evidence
artifact.

The expected check list comes from the gate itself and never from the
artifact: an artifact that claims the offline scope while the gate runs the
Compose scope has to be reported as missing the container checks.

The audit compares the check *names* only.  It never reads, prints or
rewrites a recorded output, so no value of a check can reach the log.  The
artifact is not repaired here either: a regeneration needs a live run, and
an artifact that a script writes without a run would be a fabricated
evidence record.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.release_gate import commands

DEFAULT_ARTIFACT = PROJECT_ROOT / "deploy" / "evidence" / "release-gate.json"

# The scope the expected check list is taken from.  The artifact of record
# documents a full gate run, so it has to carry the container checks as well.
SCOPES = {
    "offline": False,
    "docker": True,
}


def expected_check_names(scope: str) -> list[str]:
    """Return the check names the gate runs today for the given scope."""

    if scope not in SCOPES:
        raise ValueError(f"unknown_scope:{scope}")
    return [name for name, _command in commands(include_docker=SCOPES[scope], requests=8, concurrency=2)]


def recorded_check_names(payload: dict) -> list[str]:
    """Return the check names the artifact records."""

    rows = payload.get("checks")
    if not isinstance(rows, list):
        return []
    return [row["name"] for row in rows if isinstance(row, dict) and isinstance(row.get("name"), str)]


def audit(artifact: Path, scope: str = "docker") -> tuple[list[str], list[str]]:
    """Return the checks the artifact misses and the checks it no longer has."""

    payload = json.loads(artifact.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("artifact_root_not_object")
    expected = expected_check_names(scope)
    recorded = recorded_check_names(payload)
    missing = [name for name in expected if name not in recorded]
    unexpected = [name for name in recorded if name not in expected]
    return missing, unexpected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifact",
        type=Path,
        default=DEFAULT_ARTIFACT,
        help="the committed gate artifact the audit compares with the gate",
    )
    parser.add_argument(
        "--scope",
        choices=sorted(SCOPES),
        default="docker",
        help="the gate scope the artifact has to describe",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit with a failure when the artifact does not describe the gate",
    )
    args = parser.parse_args(argv)

    if not args.artifact.is_file():
        print(f"gate_artifact: missing_artifact={args.artifact.name}")
        return 1 if args.strict else 0
    try:
        missing, unexpected = audit(args.artifact, args.scope)
    except (OSError, ValueError) as exc:
        print(f"gate_artifact: unreadable ({type(exc).__name__})")
        return 1 if args.strict else 0

    print(f"gate_artifact: scope={args.scope} missing={','.join(missing) or '-'} unexpected={','.join(unexpected) or '-'}")
    if not missing and not unexpected:
        return 0
    print(
        "gate_artifact: regenerate the artifact with one live run: "
        "python scripts/release_gate.py --docker --requests 8 --concurrency 2 "
        "--report deploy/evidence/release-gate.json"
    )
    return 1 if args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
