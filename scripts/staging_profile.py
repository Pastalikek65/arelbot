#!/usr/bin/env python3
"""Build an explicit, fail-closed staging profile from a governance worksheet.

This command does not approve a provider and never contacts an external
service. It converts pending feature keys into a bounded ``DISABLED_FEATURES``
value for a staging runtime.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.features.staging_profile import (
    StagingProfileError,
    build_profile,
    load_review_template,
    write_report,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, required=True, help="provider approval review worksheet")
    parser.add_argument("--report", type=Path, help="write the secret-free profile JSON atomically")
    args = parser.parse_args(argv)
    try:
        payload = build_profile(load_review_template(args.template))
        if args.report:
            write_report(args.report, payload)
    except StagingProfileError as exc:
        print(json.dumps({"ok": False, "error": exc.code}, ensure_ascii=False))
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
