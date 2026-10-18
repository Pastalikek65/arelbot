#!/usr/bin/env python3
"""Keep the human feature catalog in lockstep with the runtime registry.

The catalog is intentionally checked from the rendered Markdown table rather
than from a second hand-maintained list.  This makes a missing row fail the
release gate while keeping the check deterministic and network-free.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = PROJECT_ROOT / "docs" / "FEATURE_CATALOG.md"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


_FEATURE_ROW = re.compile(r"(?m)^\|\s*`([a-z][a-z0-9_]*)`\s*\|")


def documented_feature_keys(markdown: str) -> tuple[str, ...]:
    """Return feature keys from Markdown table rows in their source order."""

    return tuple(_FEATURE_ROW.findall(markdown))


@dataclass(frozen=True)
class CatalogAudit:
    ok: bool
    runtime_count: int
    documented_count: int
    missing: tuple[str, ...]
    undocumented: tuple[str, ...]
    duplicates: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        for key in ("missing", "undocumented", "duplicates"):
            payload[key] = list(payload[key])
        return payload


def audit_feature_catalog(definitions: Iterable[object], markdown: str) -> CatalogAudit:
    """Compare unique runtime definition keys with the catalog table.

    Runtime keys are taken from typed ``FeatureDefinition`` instances.  A
    duplicate Markdown row is an error even if the set of keys happens to
    match; duplicated rows create contradictory command/provider metadata for
    operators and reviewers.
    """

    runtime = tuple(sorted({str(getattr(item, "key", "")).strip() for item in definitions if str(getattr(item, "key", "")).strip()}))
    documented = documented_feature_keys(markdown)
    documented_set = set(documented)
    duplicate_values = tuple(sorted({key for key in documented if documented.count(key) > 1}))
    missing = tuple(key for key in runtime if key not in documented_set)
    undocumented = tuple(sorted(documented_set - set(runtime)))
    return CatalogAudit(
        ok=not missing and not undocumented and not duplicate_values,
        runtime_count=len(runtime),
        documented_count=len(documented),
        missing=missing,
        undocumented=undocumented,
        duplicates=duplicate_values,
    )


async def _collect_runtime_definitions():
    from config import Config
    from src.chat_engine import ChatEngine
    from src.key_manager import KeyManager
    from src.mcp_manager import McpManager

    engine = ChatEngine(KeyManager(), McpManager(Config.MCP_SERVERS))
    try:
        return engine.feature_registry.definitions()
    finally:
        await engine.close()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.disable(logging.CRITICAL)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    args = parser.parse_args()
    if args.catalog.is_symlink() or not args.catalog.is_file():
        print(json.dumps({"ok": False, "error": "catalog_missing"}, ensure_ascii=False))
        return 1
    definitions = asyncio.run(_collect_runtime_definitions())
    report = audit_feature_catalog(definitions, args.catalog.read_text(encoding="utf-8"))
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
