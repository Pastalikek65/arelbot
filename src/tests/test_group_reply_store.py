from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.storage.group_reply_store import (
    PENDING_REF_TTL_SECONDS,
    RecentMessageIndex,
    ReplyDeliveryLedger,
)


@pytest.mark.asyncio
async def test_recent_index_filters_thread_window_and_replied_messages():
    index = RecentMessageIndex()
    now = datetime.now(timezone.utc)
    await index.add_message({
        "chat_id": -10, "thread_id": 4, "telegram_message_id": 1,
        "author_user_id": 101, "display_name": "X", "text": "eski",
        "created_at": now - timedelta(seconds=301),
    })
    await index.add_message({
        "chat_id": -10, "thread_id": 4, "telegram_message_id": 2,
        "author_user_id": 101, "display_name": "X", "text": "yeni",
        "created_at": now - timedelta(seconds=20),
    })
    await index.add_message({
        "chat_id": -10, "thread_id": 4, "telegram_message_id": 3,
        "author_user_id": 202, "display_name": "Y", "text": "başka",
        "created_at": now - timedelta(seconds=10),
    })
    await index.add_message({
        "chat_id": -10, "thread_id": 9, "telegram_message_id": 4,
        "author_user_id": 303, "display_name": "Z", "text": "başlık dışı",
        "created_at": now - timedelta(seconds=5),
    })
    await index.mark_replied(-10, 4, 3)

    rows = await index.list_candidates(
        -10,
        thread_id=4,
        since=now - timedelta(seconds=300),
        limit=8,
    )

    assert [row["telegram_message_id"] for row in rows] == [2]
    assert rows[0]["schema_version"] == 1
    assert rows[0]["author_user_id"] == 101


@pytest.mark.asyncio
async def test_recent_index_keeps_latest_message_per_user_and_orders_chronologically():
    index = RecentMessageIndex()
    now = datetime.now(timezone.utc)
    for message_id, user_id, seconds in ((1, 7, 30), (2, 7, 20), (3, 8, 10)):
        await index.add_message({
            "chat_id": 11, "thread_id": None, "telegram_message_id": message_id,
            "author_user_id": user_id, "display_name": str(user_id), "text": str(message_id),
            "created_at": now - timedelta(seconds=seconds),
        })

    rows = await index.list_candidates(11, limit=8, latest_per_user=True)
    assert [row["telegram_message_id"] for row in rows] == [2, 3]


@pytest.mark.asyncio
async def test_recent_index_limit_keeps_the_newest_messages_in_the_window():
    index = RecentMessageIndex()
    now = datetime.now(timezone.utc)
    for message_id in range(1, 12):
        await index.add_message({
            "chat_id": 12,
            "thread_id": None,
            "telegram_message_id": message_id,
            "author_user_id": message_id,
            "display_name": str(message_id),
            "text": str(message_id),
            "created_at": now - timedelta(seconds=message_id),
        })

    rows = await index.list_candidates(12, limit=8)

    assert [row["telegram_message_id"] for row in rows] == list(range(8, 0, -1))


@pytest.mark.asyncio
async def test_reply_delivery_ledger_is_idempotent_and_allows_failed_retry():
    ledger = ReplyDeliveryLedger()
    key = ledger.make_key(-10, 4, 2, feature="group_targeted_reply", generation_id="turn-1")

    assert await ledger.claim(key, chat_id=-10, target_message_id=2, generation_id="turn-1") is True
    assert await ledger.claim(key, chat_id=-10, target_message_id=2, generation_id="turn-1") is False
    assert await ledger.begin_send(key) is True
    assert await ledger.mark_sent(key, telegram_message_id=700) is True
    assert await ledger.claim(key, chat_id=-10, target_message_id=2, generation_id="turn-1") is False

    retry_key = ledger.make_key(-10, 4, 3, feature="group_targeted_reply", generation_id="turn-1")
    assert await ledger.claim(retry_key, chat_id=-10, target_message_id=3, generation_id="turn-1") is True
    await ledger.release(retry_key, "telegram_timeout")
    assert await ledger.claim(retry_key, chat_id=-10, target_message_id=3, generation_id="turn-1") is True


@pytest.mark.asyncio
async def test_reply_delivery_ledger_unknown_send_is_not_replayed():
    ledger = ReplyDeliveryLedger()
    key = ledger.make_key(1, None, 9, feature="group_targeted_reply", generation_id="turn-2")
    assert await ledger.claim(key, chat_id=1, target_message_id=9, generation_id="turn-2")
    assert await ledger.begin_send(key)
    await ledger.mark_uncertain(key, "network_lost")
    assert await ledger.claim(key, chat_id=1, target_message_id=9, generation_id="turn-2") is False


def test_delivery_key_is_stable_and_bounded():
    key = ReplyDeliveryLedger.make_key(1, None, 2, feature="group_targeted_reply", generation_id="turn")
    assert key == "1::2:group_targeted_reply:turn"
    assert len(key) <= 256
    assert PENDING_REF_TTL_SECONDS == 24 * 60 * 60


@pytest.mark.asyncio
async def test_reply_delivery_claim_does_not_set_created_at_twice():
    """Mongo rejects a $set/$setOnInsert collision on created_at."""

    class Collection:
        async def find_one(self, *_args, **_kwargs):
            return None

        async def update_one(self, _query, update, **_kwargs):
            if "created_at" in update.get("$set", {}) and "created_at" in update.get("$setOnInsert", {}):
                raise AssertionError("created_at must not be present in both update operators")
            return SimpleNamespace(modified_count=1, upserted_id=None)

    class Database:
        group_reply_deliveries = Collection()

    ledger = ReplyDeliveryLedger(database=Database(), required=True)
    key = ledger.make_key(-10, None, 7, generation_id="turn-1")

    assert await ledger.claim(
        key,
        chat_id=-10,
        target_message_id=7,
        generation_id="turn-1",
    ) is True

@pytest.mark.asyncio
async def test_database_add_message_preserves_legacy_args_and_writes_attribution():
    from src.database import Database

    class Collection:
        def __init__(self):
            self.rows = []

        async def insert_one(self, row):
            self.rows.append(row)

    class Mongo:
        def __init__(self):
            self.collections = {"messages": Collection()}

        def __getitem__(self, name):
            return self.collections.setdefault(name, Collection())

    previous = Database._db
    mongo = Mongo()
    Database._db = mongo
    try:
        db = Database.__new__(Database)
        await db.add_message(
            -10,
            101,
            "user",
            "merhaba",
            telegram_message_id=55,
            reply_to_message_id=44,
            thread_id=7,
            turn_id="turn-1",
            trigger_kind="mention",
            chat_type="supergroup",
        )
        row = mongo["messages"].rows[0]
        assert row["chat_id"] == -10
        assert row["schema_version"] == 1
        assert row["created_at"] == row["timestamp"]
        assert row["telegram_message_id"] == 55
        assert row["reply_to_message_id"] == 44
        assert row["thread_id"] == 7
        assert row["author_user_id"] == 101
        assert row["turn_id"] == "turn-1"
    finally:
        Database._db = previous


def test_database_registers_reversible_group_reply_migration_v15():
    from src.database import Database

    class Mongo:
        def __getitem__(self, name):
            return object()

    previous = Database._db
    Database._db = Mongo()
    try:
        runner = Database.__new__(Database).migration_runner()
        migration = next(item for item in runner.migrations if item.name == "group-targeted-reply-persistence")
        assert migration.version == 15
        assert migration.name == "group-targeted-reply-persistence"
        assert migration.down is not None
    finally:
        Database._db = previous


def test_database_registers_unique_attribution_upgrade_migration_v18():
    from src.database import Database

    class Mongo:
        def __getitem__(self, name):
            return object()

    previous = Database._db
    Database._db = Mongo()
    try:
        runner = Database.__new__(Database).migration_runner()
        migration = next(item for item in runner.migrations if item.version == 18)
        assert migration.name == "group-reply-unique-attribution-index"
        assert migration.down is not None
    finally:
        Database._db = previous


def test_database_registers_username_target_lookup_migration_v19():
    from src.database import Database

    class Mongo:
        def __getitem__(self, name):
            return object()

    previous = Database._db
    Database._db = Mongo()
    try:
        runner = Database.__new__(Database).migration_runner()
        migration = next(item for item in runner.migrations if item.version == 19)
        assert migration.name == "telegram-username-target-lookup"
        assert migration.down is not None
    finally:
        Database._db = previous
