#!/usr/bin/env python3
"""Produce a safe typed-provider inventory without calling live sources."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import Config
from src.chat_engine import ChatEngine
from src.features.provider_audit import audit_errors, audit_registry
from src.key_manager import KeyManager
from src.mcp_manager import McpManager
from src.security.log_redaction import redact


async def collect(*, include_health: bool = False) -> dict:
    engine = ChatEngine(KeyManager(), McpManager(Config.MCP_SERVERS))
    try:
        rows = audit_registry(engine.feature_registry)
        result = {
            "ok": not audit_errors(engine.feature_registry),
            "features": [row.as_dict() for row in rows],
            "feature_count": len(rows),
            "provider_count": len(engine.feature_registry.providers()),
            "health": [],
        }
        if include_health:
            health = await engine.feature_registry.health_snapshot()
            result["health"] = [
                {
                    "name": item.name,
                    "healthy": item.healthy,
                    "latency_ms": item.latency_ms,
                    "error_rate": item.error_rate,
                    "detail": redact(item.detail or "")[:160],
                }
                for item in health
            ]
        return result
    finally:
        await engine.close()


def main() -> int:
    # Keep stdout machine-readable; application diagnostics must not corrupt
    # the JSON consumed by CI and operators.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.disable(logging.CRITICAL)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--health", action="store_true", help="include lazy provider health snapshot")
    args = parser.parse_args()
    result = asyncio.run(collect(include_health=args.health))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
