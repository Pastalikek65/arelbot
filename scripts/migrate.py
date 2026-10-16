#!/usr/bin/env python3
"""Apply or explicitly roll back Arelbot Mongo schema migrations."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.database import Database


async def run(args: argparse.Namespace) -> int:
    if args.rollback_to is not None and not args.confirm:
        print("rollback requires --confirm", file=sys.stderr)
        return 2
    database = Database()
    if not await database.ping():
        print("migration failed: MongoDB is unreachable", file=sys.stderr)
        return 1
    try:
        runner = database.migration_runner()
        if args.rollback_to is not None:
            version = await runner.rollback(args.rollback_to)
            print(f"migration rolled back; current_version={version}")
        else:
            version = await runner.migrate(args.target)
            print(f"migration applied; current_version={version}")
        return 0
    except Exception as exc:
        print(f"migration failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    finally:
        await database.close_async()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=int, help="apply migrations through this version")
    parser.add_argument("--rollback-to", type=int, help="rollback to this version")
    parser.add_argument("--confirm", action="store_true", help="confirm a potentially destructive rollback")
    args = parser.parse_args()
    if args.target is not None and args.rollback_to is not None:
        parser.error("--target and --rollback-to are mutually exclusive")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
