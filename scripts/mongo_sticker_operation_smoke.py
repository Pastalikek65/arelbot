"""Exercise the durable sticker-add claim against the Compose Mongo service."""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.database import Database


async def main() -> None:
    database = Database()
    if not await database.ping():
        raise SystemExit("MongoDB ping failed")
    await database.ensure_indexes()

    pack_id = f"smoke-pack-{uuid.uuid4().hex}"
    file_id = f"smoke-file-{uuid.uuid4().hex}"
    try:
        first = await database.claim_sticker_add(pack_id, file_id, 910002)
        second = await database.claim_sticker_add(pack_id, file_id, 910002)
        assert first.get("status") == "claimed"
        assert second.get("status") == "pending"

        await database.complete_sticker_add(pack_id, file_id, catalog_registered=True)
        third = await database.claim_sticker_add(pack_id, file_id, 910002)
        assert third.get("status") == "completed"
        print("mongo_sticker_operation=ok")
    finally:
        await database.sticker_operations.delete_many({"pack_id": pack_id, "file_id": file_id})
        await database.close_async()


if __name__ == "__main__":
    asyncio.run(main())
