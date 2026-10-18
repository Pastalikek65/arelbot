"""Run the notification delivery ledger against the Compose Mongo service."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.database import Database
from src.storage.subscription_store import SubscriptionStore


async def main() -> None:
    database = Database()
    if not await database.ping():
        raise SystemExit("MongoDB ping failed")
    await database.ensure_indexes()
    store = SubscriptionStore(database)
    item = await store.create(
        user_id=910001,
        chat_id=-910001,
        feature_key="earthquakes",
        params={},
        interval_seconds=900,
    )
    key = f"smoke:{item['subscription_id']}"
    try:
        assert await store.claim_delivery(
            key, subscription_id=item["subscription_id"], chat_id=item["chat_id"]
        )
        assert await store.begin_delivery_send(key)
        assert await store.mark_delivery_sent(key)
        assert await store.delivery_was_sent(key)
        assert not await store.claim_delivery(
            key, subscription_id=item["subscription_id"], chat_id=item["chat_id"]
        )
        print("mongo_notification_ledger=ok")
    finally:
        await database.subscriptions.delete_one({"subscription_id": item["subscription_id"]})
        await database.notification_deliveries.delete_one({"delivery_key": key})
        await database.close_async()


if __name__ == "__main__":
    asyncio.run(main())
