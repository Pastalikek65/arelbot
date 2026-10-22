"""Run the registered Mongo migration twice against a real Compose server."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Config
from src.database import Database


def _smoke_uri() -> str | None:
    """Return the ambient connection string with the disposable database.

    The release gate runs this smoke against a throwaway database so the
    migration rollback never touches a persistent volume.  The database name
    travels in its own environment variable because the connection string
    carries the credential and must never reach a command line.
    """

    database_name = os.getenv("MONGODB_SMOKE_DATABASE", "").strip()
    if not database_name:
        return None
    base = Config.MONGODB_URI or os.getenv("MONGODB_URI", "")
    try:
        parsed = urlsplit(base)
    except ValueError as exc:
        raise SystemExit("MONGODB_SMOKE_DATABASE requires a valid MONGODB_URI") from exc
    if parsed.scheme not in {"mongodb", "mongodb+srv"} or not parsed.netloc:
        raise SystemExit("MONGODB_SMOKE_DATABASE requires a valid MONGODB_URI")
    return urlunsplit((parsed.scheme, parsed.netloc, f"/{database_name}", parsed.query, ""))


async def main() -> None:
    database = Database(_smoke_uri())
    if not await database.ping():
        raise SystemExit("MongoDB ping failed")
    try:
        runner = database.migration_runner()
        latest_version = max(migration.version for migration in runner.migrations)
        first = await runner.migrate()
        second = await runner.migrate()
        applied = await runner.applied()
        # The second call must be a no-op while still returning the latest
        # registered version. Derive this from the runner so adding a
        # migration cannot silently leave the smoke test stale.
        assert first == second == latest_version
        assert sorted(applied) == list(range(1, latest_version + 1))
        index_info = await database._db["provider_observations"].index_information()
        assert "provider_fetched_at_unique" in index_info
        assert "provider_observed_at_ttl" in index_info
        sticker_indexes = await database._db["stickers"].index_information()
        assert "sticker_pack_position" in sticker_indexes
        group_message_indexes = await database._db["messages"].index_information()
        assert {
            "group_reply_recent_messages",
            "group_reply_telegram_message_unique",
        }.issubset(group_message_indexes)
        delivery_indexes = await database._db["group_reply_deliveries"].index_information()
        assert {
            "group_reply_delivery_key_unique",
            "group_reply_delivery_created_ttl",
        }.issubset(delivery_indexes)

        # Validate the latest migration rollback and forward path, rather than
        # only asserting that its version row was written.
        assert await runner.rollback(3) == 3
        assert 4 not in await runner.applied()
        moderation_indexes = await database._db["moderation_violations"].index_information()
        assert not {
            "moderation_event_unique",
            "moderation_actor_time",
            "moderation_created_at_ttl",
        }.intersection(moderation_indexes)
        assert await runner.migrate() == latest_version
        moderation_indexes = await database._db["moderation_violations"].index_information()
        assert {
            "moderation_event_unique",
            "moderation_actor_time",
            "moderation_created_at_ttl",
        }.issubset(moderation_indexes)
        rag_indexes = await database._db["rag_documents"].index_information()
        assert {
            "rag_document_id_unique",
            "rag_document_user_time",
            "rag_document_user_sha_unique",
        }.issubset(rag_indexes)
        document_job_indexes = await database._db["document_jobs"].index_information()
        assert {
            "document_job_status_time",
            "document_job_user_status",
            "document_job_id_unique",
            "document_job_source_key",
        }.issubset(document_job_indexes)
        attachment_job_indexes = await database._db["attachment_jobs"].index_information()
        assert {
            "attachment_job_status_time",
            "attachment_job_user_status",
            "attachment_job_id_unique",
            "attachment_job_source_key",
        }.issubset(attachment_job_indexes)
        quote_job_indexes = await database._db["quote_jobs"].index_information()
        assert {
            "quote_job_status_time", "quote_job_user_status", "quote_job_id_unique", "quote_job_source_key",
        }.issubset(quote_job_indexes)
        speech_job_indexes = await database._db["speech_jobs"].index_information()
        assert {
            "speech_job_status_time", "speech_job_user_status", "speech_job_id_unique", "speech_job_source_key",
        }.issubset(speech_job_indexes)
        sticker_tag_job_indexes = await database._db["sticker_tag_jobs"].index_information()
        assert {
            "sticker_tag_job_status_time", "sticker_tag_job_user_status",
            "sticker_tag_job_id_unique", "sticker_tag_job_source_key",
        }.issubset(sticker_tag_job_indexes)
        market_chart_job_indexes = await database._db["market_chart_jobs"].index_information()
        assert {
            "market_chart_job_status_time", "market_chart_job_user_status",
            "market_chart_job_id_unique", "market_chart_job_source_key",
        }.issubset(market_chart_job_indexes)
        delivery_indexes = await database._db["job_deliveries"].index_information()
        assert {"job_delivery_key_unique", "job_delivery_created_ttl"}.issubset(delivery_indexes)
        subscription_indexes = await database._db["subscriptions"].index_information()
        assert "subscription_topic_schedule" in subscription_indexes
        image_job_indexes = await database._db["image_jobs"].index_information()
        assert {
            "image_job_status_time", "image_job_user_status", "image_job_id_unique", "image_job_source_key",
        }.issubset(image_job_indexes)
        print("mongo_migration=ok")
    finally:
        # The smoke database is disposable and must not pollute a persistent
        # test volume. Production migration tooling never executes this path.
        db = Database._db
        if db is not None:
            await db.client.drop_database(db.name)
        await database.close_async()


if __name__ == "__main__":
    asyncio.run(main())
