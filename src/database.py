import os
import json
import logging
import re
import hashlib
from urllib.parse import unquote, urlsplit, urlunsplit
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional
try:
    import motor.motor_asyncio
except ImportError:  # Allows lightweight unit-test collection without optional DB extras.
    motor = None
try:
    from pymongo import UpdateOne
except ImportError:  # pragma: no cover - only used by migration helpers.
    UpdateOne = None

from config import Config
from src.security.log_redaction import summarize_exception

# Configure logging
logger = logging.getLogger(__name__)

# How many account groups the economy ledger seed processes between two
# progress records.  The seed is a one-time data migration, so its progress
# has to be visible to the operator without flooding the log.
ECONOMY_SEED_PROGRESS_INTERVAL = 500


def _safe_db_url(value: str) -> str:
    try:
        parts = urlsplit(value)
        host = parts.hostname or "localhost"
        if parts.port:
            host += f":{parts.port}"
        return urlunsplit((parts.scheme, host, parts.path, "", ""))
    except Exception:
        return "<mongodb>"


def _database_name_from_url(value: str) -> str:
    """Return the configured Mongo database name without exposing credentials."""
    try:
        path = unquote(urlsplit(str(value)).path or "").strip("/")
        if path and "/" not in path and "$" not in path:
            return path
    except Exception:
        pass
    return "arel_bot"

class Database:
    _instance = None
    _client = None
    _db = None
    _last_ping_ok = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(Database, cls).__new__(cls)
        return cls._instance

    def __init__(self, db_url=None):
        # Initialize only once
        if Database._client:
            return

        if motor is None:
            logger.warning("Motor is not installed; database operations are unavailable")
            Database._client = False
            Database._db = None
            if Config.MONGODB_REQUIRED:
                raise RuntimeError("MONGODB_REQUIRED=true but motor is not installed")
            return

        if db_url is None:
            # Use formalized config first, with environment fallback
            db_url = Config.MONGODB_URI or os.getenv("MONGODB_URI", "mongodb://localhost:27017")
        
        try:
            Database._client = motor.motor_asyncio.AsyncIOMotorClient(db_url, serverSelectionTimeoutMS=2000, connectTimeoutMS=2000)
            Database._db = Database._client[_database_name_from_url(db_url)]
            logger.info("MongoDB client initialized: %s", _safe_db_url(db_url))
        except Exception as e:
            logger.error("MongoDB connection failed: %s", summarize_exception(e, label="mongodb_init"))

    # Helper for collections
    @property
    def users(self): return Database._db["users"]
    @property
    def messages(self): return Database._db["messages"]
    @property
    def chats(self): return Database._db["chats"]
    @property
    def facts(self): return Database._db["facts"]
    @property
    def quotes(self): return Database._db["quotes"]
    @property
    def quote_templates(self): return Database._db["quote_templates"]
    @property
    def sticker_packs(self): return Database._db["sticker_packs"]
    @property
    def stickers(self): return Database._db["stickers"]
    @property
    def sticker_operations(self): return Database._db["sticker_operations"]
    @property
    def sudo_users(self): return Database._db["sudo_users"]
    @property
    def blacklist(self): return Database._db["blacklist"]
    @property
    def game_scores(self): return Database._db["game_scores"]
    @property
    def player_stats(self): return Database._db["player_stats"]
    @property
    def game_progress_events(self): return Database._db["game_progress_events"] if Database._db is not None else None
    @property
    def game_action_ledger(self): return Database._db["game_action_ledger"] if Database._db is not None else None
    @property
    def game_states(self): return Database._db["game_states"] if Database._db is not None else None
    @property
    def media_jobs(self): return Database._db["media_jobs"] if Database._db is not None else None
    @property
    def media_cache(self): return Database._db["media_cache"] if Database._db is not None else None
    @property
    def media_assets(self): return Database._db["media_assets"] if Database._db is not None else None
    @property
    def provider_health(self): return Database._db["provider_health"] if Database._db is not None else None
    @property
    def group_reply_deliveries(self):
        return Database._db["group_reply_deliveries"] if Database._db is not None else None
    @property
    def moderation_actions(self):
        return Database._db["moderation_actions"] if Database._db is not None else None
    @property
    def moderation_provider_events(self):
        return Database._db["moderation_provider_events"] if Database._db is not None else None
    @property
    def moderation_policies(self):
        return Database._db["moderation_policies"] if Database._db is not None else None
    @property
    def provider_observations(self):
        return Database._db["provider_observations"] if Database._db is not None else None
    @property
    def audit_logs(self): return Database._db["audit_logs"] if Database._db is not None else None
    @property
    def moderation_violations(self):
        return Database._db["moderation_violations"] if Database._db is not None else None
    @property
    def dead_letters(self): return Database._db["dead_letters"] if Database._db is not None else None
    @property
    def states(self): return Database._db["states"] if Database._db is not None else None
    @property
    def notes(self): return Database._db["notes"] if Database._db is not None else None
    @property
    def tasks(self): return Database._db["tasks"] if Database._db is not None else None
    @property
    def reminders(self): return Database._db["reminders"] if Database._db is not None else None
    @property
    def subscriptions(self): return Database._db["subscriptions"] if Database._db is not None else None
    @property
    def notification_preferences(self): return Database._db["notification_preferences"] if Database._db is not None else None
    @property
    def notification_deliveries(self): return Database._db["notification_deliveries"] if Database._db is not None else None
    @property
    def economy_accounts(self): return Database._db["economy_accounts"] if Database._db is not None else None
    @property
    def economy_transactions(self): return Database._db["economy_transactions"] if Database._db is not None else None
    @property
    def ai_usage(self): return Database._db["ai_usage"] if Database._db is not None else None
    @property
    def rag_documents(self): return Database._db["rag_documents"] if Database._db is not None else None
    @property
    def document_jobs(self): return Database._db["document_jobs"] if Database._db is not None else None
    @property
    def attachment_jobs(self): return Database._db["attachment_jobs"] if Database._db is not None else None
    @property
    def quote_jobs(self): return Database._db["quote_jobs"] if Database._db is not None else None
    @property
    def speech_jobs(self): return Database._db["speech_jobs"] if Database._db is not None else None
    @property
    def sticker_tag_jobs(self): return Database._db["sticker_tag_jobs"] if Database._db is not None else None
    @property
    def market_chart_jobs(self): return Database._db["market_chart_jobs"] if Database._db is not None else None
    @property
    def image_jobs(self): return Database._db["image_jobs"] if Database._db is not None else None
    @property
    def job_deliveries(self): return Database._db["job_deliveries"] if Database._db is not None else None

    @property
    def available(self) -> bool:
        # Motor database objects intentionally reject truth-value testing.
        return Database._db is not None and Database._last_ping_ok is not False

    @property
    def is_production_database(self) -> bool:
        """Whether this deployment requires durable production persistence.

        The economy ledger and the games' durable state must not fall back to
        process-local memory behind the production database: a restart would
        lose the balance, a paid reward or a running round.  A production or
        staging deployment, a deployment that requires MongoDB, and the
        bot/worker roles therefore hold the database to the durable standard.
        """
        return (
            Config.APP_ENV in {"production", "staging"}
            or Config.MONGODB_REQUIRED
            or Config.APP_ROLE in {"bot", "worker"}
        )

    @property
    def reachable(self) -> bool | None:
        """Last observed server reachability; ``None`` means not probed yet."""
        return Database._last_ping_ok

    async def ping(self) -> bool:
        """Check Mongo without exposing a driver-specific object to handlers."""
        if not Database._client or Database._db is None:
            Database._last_ping_ok = False
            return False
        try:
            await Database._client.admin.command("ping")
            Database._last_ping_ok = True
            return True
        except Exception as exc:
            Database._last_ping_ok = False
            logger.warning("MongoDB health check failed: %s", summarize_exception(exc, label="mongodb_health"))
            return False

    async def close_async(self) -> None:
        client = Database._client
        if client and client is not False:
            client.close()
        Database._client = None
        Database._db = None
        Database._last_ping_ok = None

    async def ensure_indexes(self) -> None:
        """Apply the versioned base schema and its idempotent indexes."""
        if Database._db is None:
            return
        await self.migration_runner().migrate()

    def migration_runner(self):
        """Return the registered migration runner for operator tooling."""
        if Database._db is None:
            raise RuntimeError("MONGODB_UNAVAILABLE")
        from src.storage.migrations import Migration, MigrationRunner

        async def apply_base_indexes(_database: Any) -> None:
            await self._ensure_indexes_v1()

        async def apply_provider_observation_indexes(database: Any) -> None:
            collection = database["provider_observations"]
            await collection.create_index(
                [("provider", 1), ("fetched_at", 1)],
                unique=True,
                name="provider_fetched_at_unique",
            )
            await collection.create_index(
                [("provider", 1), ("observed_at", -1)],
                name="provider_observed_at_desc",
            )
            await collection.create_index(
                "observed_at",
                expireAfterSeconds=30 * 24 * 60 * 60,
                name="provider_observed_at_ttl",
            )

        async def rollback_provider_observation_indexes(database: Any) -> None:
            collection = database["provider_observations"]
            for name in (
                "provider_fetched_at_unique",
                "provider_observed_at_desc",
                "provider_observed_at_ttl",
            ):
                try:
                    await collection.drop_index(name)
                except Exception:
                    continue

        async def apply_sticker_position_index(database: Any) -> None:
            await database["stickers"].create_index(
                [("pack_id", 1), ("position", 1)],
                name="sticker_pack_position",
            )

        async def rollback_sticker_position_index(database: Any) -> None:
            try:
                await database["stickers"].drop_index("sticker_pack_position")
            except Exception:
                return

        async def apply_moderation_violation_indexes(database: Any) -> None:
            collection = database["moderation_violations"]
            await collection.create_index("event_id", unique=True, name="moderation_event_unique")
            await collection.create_index(
                [("chat_id", 1), ("user_id", 1), ("created_at", -1)],
                name="moderation_actor_time",
            )
            await collection.create_index(
                "created_at",
                expireAfterSeconds=30 * 24 * 60 * 60,
                name="moderation_created_at_ttl",
            )

        async def rollback_moderation_violation_indexes(database: Any) -> None:
            collection = database["moderation_violations"]
            for name in ("moderation_event_unique", "moderation_actor_time", "moderation_created_at_ttl"):
                try:
                    await collection.drop_index(name)
                except Exception:
                    continue

        async def apply_rag_document_indexes(database: Any) -> None:
            collection = database["rag_documents"]
            await collection.create_index("document_id", unique=True, name="rag_document_id_unique")
            await collection.create_index(
                [("user_id", 1), ("created_at", -1)],
                name="rag_document_user_time",
            )
            await collection.create_index(
                [("user_id", 1), ("sha256", 1)],
                unique=True,
                name="rag_document_user_sha_unique",
            )

        async def rollback_rag_document_indexes(database: Any) -> None:
            collection = database["rag_documents"]
            for name in ("rag_document_id_unique", "rag_document_user_time", "rag_document_user_sha_unique"):
                try:
                    await collection.drop_index(name)
                except Exception:
                    continue

        async def apply_document_job_indexes(database: Any) -> None:
            collection = database["document_jobs"]
            await collection.create_index([("status", 1), ("created_at", 1)], name="document_job_status_time")
            await collection.create_index([("user_id", 1), ("status", 1)], name="document_job_user_status")
            await collection.create_index("job_id", unique=True, name="document_job_id_unique")
            await collection.create_index("source_key", name="document_job_source_key")

        async def rollback_document_job_indexes(database: Any) -> None:
            collection = database["document_jobs"]
            for name in (
                "document_job_status_time",
                "document_job_user_status",
                "document_job_id_unique",
                "document_job_source_key",
            ):
                try:
                    await collection.drop_index(name)
                except Exception:
                    continue

        async def apply_attachment_job_indexes(database: Any) -> None:
            collection = database["attachment_jobs"]
            await collection.create_index([("status", 1), ("created_at", 1)], name="attachment_job_status_time")
            await collection.create_index([("user_id", 1), ("status", 1)], name="attachment_job_user_status")
            await collection.create_index("job_id", unique=True, name="attachment_job_id_unique")
            await collection.create_index("source_key", name="attachment_job_source_key")

        async def rollback_attachment_job_indexes(database: Any) -> None:
            collection = database["attachment_jobs"]
            for name in (
                "attachment_job_status_time", "attachment_job_user_status",
                "attachment_job_id_unique", "attachment_job_source_key",
            ):
                try:
                    await collection.drop_index(name)
                except Exception:
                    continue

        async def apply_quote_job_indexes(database: Any) -> None:
            collection = database["quote_jobs"]
            await collection.create_index([("status", 1), ("created_at", 1)], name="quote_job_status_time")
            await collection.create_index([("user_id", 1), ("status", 1)], name="quote_job_user_status")
            await collection.create_index("job_id", unique=True, name="quote_job_id_unique")
            await collection.create_index("source_key", name="quote_job_source_key")

        async def rollback_quote_job_indexes(database: Any) -> None:
            collection = database["quote_jobs"]
            for name in ("quote_job_status_time", "quote_job_user_status", "quote_job_id_unique", "quote_job_source_key"):
                try:
                    await collection.drop_index(name)
                except Exception:
                    continue

        async def apply_speech_job_indexes(database: Any) -> None:
            collection = database["speech_jobs"]
            await collection.create_index([("status", 1), ("created_at", 1)], name="speech_job_status_time")
            await collection.create_index([("user_id", 1), ("status", 1)], name="speech_job_user_status")
            await collection.create_index("job_id", unique=True, name="speech_job_id_unique")
            await collection.create_index("source_key", name="speech_job_source_key")

        async def rollback_speech_job_indexes(database: Any) -> None:
            collection = database["speech_jobs"]
            for name in ("speech_job_status_time", "speech_job_user_status", "speech_job_id_unique", "speech_job_source_key"):
                try:
                    await collection.drop_index(name)
                except Exception:
                    continue

        async def apply_sticker_tag_job_indexes(database: Any) -> None:
            collection = database["sticker_tag_jobs"]
            await collection.create_index([("status", 1), ("created_at", 1)], name="sticker_tag_job_status_time")
            await collection.create_index([("user_id", 1), ("status", 1)], name="sticker_tag_job_user_status")
            await collection.create_index("job_id", unique=True, name="sticker_tag_job_id_unique")
            await collection.create_index("source_key", name="sticker_tag_job_source_key")

        async def rollback_sticker_tag_job_indexes(database: Any) -> None:
            collection = database["sticker_tag_jobs"]
            for name in ("sticker_tag_job_status_time", "sticker_tag_job_user_status", "sticker_tag_job_id_unique", "sticker_tag_job_source_key"):
                try:
                    await collection.drop_index(name)
                except Exception:
                    continue

        async def apply_market_chart_job_indexes(database: Any) -> None:
            collection = database["market_chart_jobs"]
            await collection.create_index([("status", 1), ("created_at", 1)], name="market_chart_job_status_time")
            await collection.create_index([("user_id", 1), ("status", 1)], name="market_chart_job_user_status")
            await collection.create_index("job_id", unique=True, name="market_chart_job_id_unique")
            await collection.create_index("source_key", name="market_chart_job_source_key")

        async def rollback_market_chart_job_indexes(database: Any) -> None:
            collection = database["market_chart_jobs"]
            for name in ("market_chart_job_status_time", "market_chart_job_user_status", "market_chart_job_id_unique", "market_chart_job_source_key"):
                try:
                    await collection.drop_index(name)
                except Exception:
                    continue

        async def apply_job_delivery_indexes(database: Any) -> None:
            collection = database["job_deliveries"]
            await collection.create_index("delivery_key", unique=True, name="job_delivery_key_unique")
            await collection.create_index("created_at", expireAfterSeconds=7 * 24 * 60 * 60, name="job_delivery_created_ttl")

        async def rollback_job_delivery_indexes(database: Any) -> None:
            collection = database["job_deliveries"]
            for name in ("job_delivery_key_unique", "job_delivery_created_ttl"):
                try:
                    await collection.drop_index(name)
                except Exception:
                    continue

        async def apply_subscription_topic_index(database: Any) -> None:
            await database["subscriptions"].create_index(
                [
                    ("chat_id", 1),
                    ("scope", 1),
                    ("message_thread_id", 1),
                    ("feature_key", 1),
                    ("enabled", 1),
                    ("next_run_at", 1),
                ],
                name="subscription_topic_schedule",
            )

        async def rollback_subscription_topic_index(database: Any) -> None:
            try:
                await database["subscriptions"].drop_index("subscription_topic_schedule")
            except Exception:
                return

        async def apply_image_job_indexes(database: Any) -> None:
            collection = database["image_jobs"]
            await collection.create_index([("status", 1), ("created_at", 1)], name="image_job_status_time")
            await collection.create_index([("user_id", 1), ("status", 1)], name="image_job_user_status")
            await collection.create_index("job_id", unique=True, name="image_job_id_unique")
            await collection.create_index("source_key", name="image_job_source_key")

        async def rollback_image_job_indexes(database: Any) -> None:
            collection = database["image_jobs"]
            for name in ("image_job_status_time", "image_job_user_status", "image_job_id_unique", "image_job_source_key"):
                try:
                    await collection.drop_index(name)
                except Exception:
                    continue

        async def apply_group_reply_indexes(database: Any) -> None:
            """Add non-destructive indexes for targeted group replies."""
            messages = database["messages"]
            await messages.create_index(
                [("chat_id", 1), ("thread_id", 1), ("created_at", -1)],
                name="group_reply_recent_messages",
            )
            await messages.create_index(
                [("chat_id", 1), ("thread_id", 1), ("telegram_message_id", 1)],
                partialFilterExpression={"telegram_message_id": {"$exists": True}},
                name="group_reply_telegram_message_unique",
            )
            deliveries = database["group_reply_deliveries"]
            await deliveries.create_index(
                "delivery_key", unique=True, name="group_reply_delivery_key_unique"
            )
            await deliveries.create_index(
                "created_at",
                expireAfterSeconds=24 * 60 * 60,
                name="group_reply_delivery_created_ttl",
            )

        async def rollback_group_reply_indexes(database: Any) -> None:
            for collection_name, names in {
                "messages": (
                    "group_reply_recent_messages",
                    "group_reply_telegram_message_unique",
                ),
                "group_reply_deliveries": (
                    "group_reply_delivery_key_unique",
                    "group_reply_delivery_created_ttl",
                ),
            }.items():
                collection = database[collection_name]
                for name in names:
                    try:
                        await collection.drop_index(name)
                    except Exception:
                        continue

        async def apply_group_reply_unique_index(database: Any) -> None:
            """Upgrade the v15 attribution index to a real uniqueness guard.

            v15 created the index with a stable name but without ``unique``.
            Drop-in upgrades therefore need an explicit migration; simply
            changing the v15 declaration would never run on an already
            migrated database.  Duplicate legacy rows are reduced
            deterministically before the unique index is rebuilt.
            """
            messages = database["messages"]
            index_name = "group_reply_telegram_message_unique"
            try:
                await messages.drop_index(index_name)
            except Exception:
                pass
            try:
                # Remove explicit null values from legacy attribution fields.
                # The partial index below also excludes legacy documents that
                # never had Telegram attribution at all.
                await messages.update_many(
                    {"telegram_message_id": {"$type": 10}},
                    {"$unset": {"telegram_message_id": ""}},
                )
                cursor = messages.find(
                    {"telegram_message_id": {"$exists": True}},
                    {"_id": 1, "chat_id": 1, "thread_id": 1, "telegram_message_id": 1, "created_at": 1},
                )
                rows = await cursor.to_list(length=None)
                seen: set[tuple[Any, Any, Any]] = set()
                duplicate_ids = []
                def created_sort_key(item: dict[str, Any]) -> tuple[int, float | str]:
                    value = item.get("created_at")
                    if isinstance(value, datetime):
                        if value.tzinfo is not None:
                            return (0, value.timestamp())
                        return (0, value.replace(tzinfo=timezone.utc).timestamp())
                    if isinstance(value, str):
                        return (1, value)
                    return (2, "")

                for row in sorted(rows, key=created_sort_key):
                    key = (row.get("chat_id"), row.get("thread_id"), row.get("telegram_message_id"))
                    if key in seen:
                        if row.get("_id") is not None:
                            duplicate_ids.append(row["_id"])
                    else:
                        seen.add(key)
                if duplicate_ids:
                    await messages.delete_many({"_id": {"$in": duplicate_ids}})
            except Exception:
                # A minimal test double or a partially unavailable collection
                # may not support the cleanup query.  Index creation remains
                # the authoritative operation and will surface real conflicts.
                pass
            await messages.create_index(
                [("chat_id", 1), ("thread_id", 1), ("telegram_message_id", 1)],
                unique=True,
                partialFilterExpression={"telegram_message_id": {"$exists": True}},
                name=index_name,
            )

        async def rollback_group_reply_unique_index(database: Any) -> None:
            messages = database["messages"]
            try:
                await messages.drop_index("group_reply_telegram_message_unique")
            except Exception:
                pass
            await messages.create_index(
                [("chat_id", 1), ("thread_id", 1), ("telegram_message_id", 1)],
                partialFilterExpression={"telegram_message_id": {"$exists": True}},
                name="group_reply_telegram_message_unique",
            )

        async def apply_user_username_index(database: Any) -> None:
            # Historical profiles may contain display names in this field, so
            # this lookup index is deliberately non-unique and sparse.
            await database["users"].create_index(
                "username",
                name="user_username_lookup",
                sparse=True,
            )

        async def rollback_user_username_index(database: Any) -> None:
            try:
                await database["users"].drop_index("user_username_lookup")
            except Exception:
                pass

        async def apply_moderation_action_indexes(database: Any) -> None:
            """Create durable, idempotent moderation/provider ledgers.

            The collections are deliberately created through indexes only.  No
            existing violation or message data is modified, so the migration
            can be rolled back by removing only the indexes it introduced.
            """
            actions = database["moderation_actions"]
            await actions.create_index(
                "idempotency_key",
                unique=True,
                name="moderation_action_idempotency_unique",
            )
            await actions.create_index(
                [("chat_id", 1), ("created_at", -1)],
                name="moderation_action_chat_time",
            )
            await actions.create_index(
                "created_at",
                expireAfterSeconds=30 * 24 * 60 * 60,
                name="moderation_action_created_ttl",
            )

            provider_events = database["moderation_provider_events"]
            await provider_events.create_index(
                "request_id",
                unique=True,
                name="moderation_provider_request_unique",
            )
            await provider_events.create_index(
                [("provider", 1), ("created_at", -1)],
                name="moderation_provider_time",
            )
            await provider_events.create_index(
                "created_at",
                expireAfterSeconds=7 * 24 * 60 * 60,
                name="moderation_provider_created_ttl",
            )

            policies = database["moderation_policies"]
            await policies.create_index(
                "chat_id",
                unique=True,
                name="moderation_policy_chat_unique",
            )

        async def rollback_moderation_action_indexes(database: Any) -> None:
            for collection_name, names in {
                "moderation_actions": (
                    "moderation_action_idempotency_unique",
                    "moderation_action_chat_time",
                    "moderation_action_created_ttl",
                ),
                "moderation_provider_events": (
                    "moderation_provider_request_unique",
                    "moderation_provider_time",
                    "moderation_provider_created_ttl",
                ),
                "moderation_policies": (
                    "moderation_policy_chat_unique",
                ),
            }.items():
                collection = database[collection_name]
                for name in names:
                    try:
                        await collection.drop_index(name)
                    except Exception:
                        continue

        async def apply_moderation_policy_retention_fix(database: Any) -> None:
            """Group moderation policies are durable configuration, not TTL data."""
            policies = database["moderation_policies"]
            try:
                await policies.drop_index("moderation_policy_updated_ttl")
            except Exception:
                pass

        async def rollback_moderation_policy_retention_fix(database: Any) -> None:
            policies = database["moderation_policies"]
            await policies.create_index(
                "updated_at",
                expireAfterSeconds=180 * 24 * 60 * 60,
                name="moderation_policy_updated_ttl",
            )

        async def apply_economy_atomic_ledger(database: Any) -> None:
            """Give every economy account one document with an atomic ledger.

            The balance and the stamp of each applied event live in the same
            account document, so a ledger event is written with a single
            atomic update instead of a transaction row plus a balance write.
            The unique account identity makes that document the only place a
            balance can live.

            The applied-event map is seeded from the historical transaction
            rows first.  Every reward that was already paid is recorded in
            ``economy_transactions``; without the seed an event id that was
            used before the migration looks new again and a replayed reward -
            the deterministic daily claim of the deploy day, for example -
            would be paid twice.  The seed writes the same values again on a
            repeated run, so a partially completed migration is safe to
            repeat.

            Deploy note: the seed closes the double-claim window of the
            deploy day.  A deployment that cannot read the seeded rows before
            the next ``/gunluk`` claim window must not cross that claim.
            """
            accounts = database["economy_accounts"]
            # The index is declared exactly like the base schema so a
            # database that already carries it is not rebuilt under a second
            # name, and the migration stays idempotent.
            await accounts.create_index([("user_id", 1), ("chat_id", 1)], unique=True)
            # A ledger event is applied with $inc on "balance" and $set on
            # "applied_events.<key>".  Both operators need a numeric balance
            # and an object container, so every legacy account is normalized
            # before the ledger writes to it.  A balance that was stored as
            # a numeric string keeps its value; only a value that cannot be
            # read as a number falls back to zero.
            await accounts.update_many(
                {"balance": {"$not": {"$type": "number"}}},
                {
                    "$set": {
                        "balance": {
                            "$convert": {
                                "input": {"$ifNull": ["$balance", 0]},
                                "to": "long",
                                "onError": 0,
                            }
                        }
                    }
                },
            )
            await accounts.update_many(
                {"applied_events": {"$not": {"$type": "object"}}},
                {"$set": {"applied_events": {}}},
            )
            await seed_economy_ledger_from_transactions(database)

        async def seed_economy_ledger_from_transactions(database: Any) -> None:
            """Seed the applied-event map from the historical transaction rows.

            The rows are grouped by their account.  Each account receives the
            newest ``MAX_APPLIED_EVENTS`` rows of its own history; a row
            whose event was already seeded is skipped, which keeps the seed
            idempotent and resumable.  A group without an account document
            cannot be seeded and is reported instead of being swallowed.

            The grouping runs on disk and is streamed, so a mature database
            does not have to fit its groups in memory.  The work is one
            document update per account, therefore the first run of this
            migration can take minutes on a large database.  A run that is
            interrupted is continued by the next run: the seed skips the
            events it already wrote.
            """
            # The import stays local so the database layer and the storage
            # layer do not import each other at module load time.
            from src.storage.economy_store import MAX_APPLIED_EVENTS, EconomyStore

            accounts = database["economy_accounts"]
            transactions = database["economy_transactions"]
            groups = transactions.aggregate(
                [{"$group": {"_id": {"user_id": "$user_id", "chat_id": "$chat_id"}}}],
                allowDiskUse=True,
            )
            seeded = 0
            skipped = 0
            processed = 0
            async for group_row in groups:
                processed += 1
                if processed % ECONOMY_SEED_PROGRESS_INTERVAL == 0:
                    logger.info(
                        "Economy ledger seed progress groups=%s seeded_events=%s skipped_groups=%s",
                        processed,
                        seeded,
                        skipped,
                    )
                identity = group_row.get("_id") or {}
                user_id = int(identity.get("user_id") or 0)
                chat_id = int(identity.get("chat_id") or 0)
                account = await accounts.find_one(
                    {"user_id": user_id, "chat_id": chat_id}, {"_id": 0, "applied_events": 1}
                )
                if account is None:
                    skipped += 1
                    logger.warning(
                        "Economy ledger seed skipped: no account document for chat_id=%s",
                        chat_id,
                    )
                    continue
                existing = account.get("applied_events") or {}
                history = transactions.find(
                    {"user_id": user_id, "chat_id": chat_id},
                    {
                        "_id": 0,
                        "event_key": 1,
                        "amount": 1,
                        "direction": 1,
                        "delta": 1,
                        "reason": 1,
                        "created_at": 1,
                    },
                ).sort("created_at", -1).limit(MAX_APPLIED_EVENTS)
                rows = await history.to_list(length=MAX_APPLIED_EVENTS)
                update: Dict[str, Any] = {}
                for transaction in rows:
                    event_key = str(transaction.get("event_key") or "")
                    if not event_key:
                        continue
                    safe_key = EconomyStore.safe_event_key(event_key)
                    if safe_key in existing or safe_key in update:
                        continue
                    update[f"applied_events.{safe_key}"] = {
                        "event_key": event_key,
                        "amount": int(transaction.get("amount") or 0),
                        "direction": str(transaction.get("direction") or "credit"),
                        "delta": int(transaction.get("delta") or 0),
                        "reason": str(transaction.get("reason") or ""),
                        "at": transaction.get("created_at"),
                    }
                if update:
                    seeded += len(update)
                # The seed and the prune are one document update per
                # account, so the account is never seen half-seeded.
                merged = {**existing, **update}
                dropped = [
                    key
                    for key in EconomyStore.excess_event_keys(merged)
                    if key not in update
                ]
                document_update: Dict[str, Any] = {}
                if update:
                    document_update["$set"] = update
                if dropped:
                    document_update["$unset"] = {
                        f"applied_events.{key}": "" for key in dropped
                    }
                if document_update:
                    await accounts.update_one(
                        {"user_id": user_id, "chat_id": chat_id}, document_update
                    )
            logger.info(
                "Economy ledger seed complete groups=%s seeded_events=%s skipped_groups=%s",
                processed,
                seeded,
                skipped,
            )

        async def rollback_economy_atomic_ledger(database: Any) -> None:
            """Restore the legacy ledger shape.

            The applied-event map is written back to
            ``economy_transactions`` before it is removed, so the legacy
            history reader keeps the events that only ever existed in the
            account document.  The account identity index is dropped, which
            leaves the database without the index the base schema declares
            until the migrations run forward again; the legacy code path did
            not depend on it for its idempotency, which came from the unique
            ``event_key`` index on ``economy_transactions``.
            """
            accounts = database["economy_accounts"]
            transactions = database["economy_transactions"]
            cursor = accounts.find({}, {"_id": 0, "user_id": 1, "chat_id": 1, "applied_events": 1})
            # The cursor is streamed: a mature database holds one event map
            # of up to 500 entries per account, which must not be
            # materialized in memory during a rollback.
            async for account in cursor:
                events = account.get("applied_events") or {}
                for record in events.values():
                    if not isinstance(record, dict):
                        continue
                    event_key = str(record.get("event_key") or "")
                    if not event_key:
                        continue
                    try:
                        await transactions.update_one(
                            {"event_key": event_key},
                            {
                                "$setOnInsert": {
                                    "event_key": event_key,
                                    "user_id": int(account.get("user_id") or 0),
                                    "chat_id": int(account.get("chat_id") or 0),
                                    "amount": int(record.get("amount") or 0),
                                    "direction": str(record.get("direction") or "credit"),
                                    "delta": int(record.get("delta") or 0),
                                    "reason": str(record.get("reason") or ""),
                                    "created_at": record.get("at") or datetime.now(timezone.utc),
                                }
                            },
                            upsert=True,
                        )
                    except Exception as exc:
                        if getattr(exc, "code", None) != 11000:
                            raise
            await accounts.update_many({}, {"$unset": {"applied_events": ""}})
            try:
                await accounts.drop_index([("user_id", 1), ("chat_id", 1)])
            except Exception:
                pass

        async def apply_economy_receipts(database: Any) -> None:
            # Backfill only known applied stamps. Balances, inventory and
            # daily progression are unchanged; missing historical events
            # cannot be reconstructed from a previously pruned account.
            from src.storage.economy_store import EconomyStore
            accounts = database['economy_accounts']
            async for account in accounts.find({}, {'user_id': 1, 'chat_id': 1}):
                await EconomyStore.prune_account(accounts, int(account['user_id']), int(account['chat_id']))
            await database['game_states'].update_many(
                {"$or": [
                    {"game_type": {"$regex": r"^blackjack:[0-9]+$"},
                     "state.economy_required": {"$ne": False}},
                    {"state.score_pending": True}, {"state.settlement_pending": True},
                    {"state.refund_pending": True}, {"state.terminal_panel_pending": True},
                    {"game_type": "roulette", "state.panel_dirty": True},
                ]}, {"$unset": {"expires_at": ""}},
            )

        async def rollback_economy_receipts(database: Any) -> None:
            # Additive safety records remain through a code rollback. Never
            # erase paid-event evidence, ownership or real wallet balances.
            return None

        async def apply_board_panel_recovery(database: Any) -> None:
            games = database["game_states"]
            keys = ["connect4", "tictactoe", "gomoku", "chess"]
            async for document in games.find({"game_type": {"$in": keys}},
                                             {"chat_id": 1, "game_type": 1, "state": 1}):
                state = document.get("state") or {}
                if "board_panel_revision" in state:
                    continue
                fields = {"state.board_panel_revision": 0, "state.board_panel_dirty": True,
                          "state.board_panel_retry_at": 0,
                          "state.board_panel_delivery_unknown": not bool(state.get("prompt_message_id")),
                          "state.board_score_pending": False,
                          "state.board_score_round_id": state.get("round_id"),
                          "state.leaderboard_required": True, "state.economy_required": True}
                if "version" not in state:
                    fields["state.version"] = 0
                await games.update_one(
                    {"chat_id": document["chat_id"], "game_type": document["game_type"],
                     "state.board_panel_revision": {"$exists": False}},
                    {"$set": fields, "$unset": {"expires_at": ""}})
            for fields, name in (
                ([("game_type", 1), ("state.board_panel_dirty", 1), ("state.board_panel_retry_at", 1)], "board_panel_due"),
                ([("game_type", 1), ("state.board_score_pending", 1), ("board_recovery_after", 1)], "board_score_due"),
                ([("game_type", 1), ("state.status", 1), ("state.turn_expires_at", 1)], "board_clock_due"),
            ):
                await games.create_index(fields, name=name)

        async def rollback_board_panel_recovery(database: Any) -> None:
            # Delivery state and score evidence survive code rollback.
            for name in ("board_panel_due", "board_score_due", "board_clock_due"):
                try:
                    await database["game_states"].drop_index(name)
                except Exception:
                    pass

        return MigrationRunner(
            Database._db,
            [
                Migration(1, "base-indexes-and-retention", apply_base_indexes),
                Migration(
                    2,
                    "provider-observation-history",
                    apply_provider_observation_indexes,
                    rollback_provider_observation_indexes,
                ),
                Migration(
                    3,
                    "sticker-position-index",
                    apply_sticker_position_index,
                    rollback_sticker_position_index,
                ),
                Migration(
                    4,
                    "moderation-violation-history",
                    apply_moderation_violation_indexes,
                    rollback_moderation_violation_indexes,
                ),
                Migration(
                    5,
                    "rag-document-metadata",
                    apply_rag_document_indexes,
                    rollback_rag_document_indexes,
                ),
                Migration(
                    6,
                    "document-ingestion-jobs",
                    apply_document_job_indexes,
                    rollback_document_job_indexes,
                ),
                Migration(
                    7,
                    "attachment-processing-jobs",
                    apply_attachment_job_indexes,
                    rollback_attachment_job_indexes,
                ),
                Migration(
                    8,
                    "quote-render-jobs",
                    apply_quote_job_indexes,
                    rollback_quote_job_indexes,
                ),
                Migration(
                    9,
                    "speech-synthesis-jobs",
                    apply_speech_job_indexes,
                    rollback_speech_job_indexes,
                ),
                Migration(
                    10,
                    "sticker-tag-jobs",
                    apply_sticker_tag_job_indexes,
                    rollback_sticker_tag_job_indexes,
                ),
                Migration(
                    11,
                    "market-chart-jobs",
                    apply_market_chart_job_indexes,
                    rollback_market_chart_job_indexes,
                ),
                Migration(
                    12,
                    "worker-job-delivery-ledger",
                    apply_job_delivery_indexes,
                    rollback_job_delivery_indexes,
                ),
                Migration(
                    13,
                    "forum-topic-subscription-scope",
                    apply_subscription_topic_index,
                    rollback_subscription_topic_index,
                ),
                Migration(
                    14,
                    "image-generation-jobs",
                    apply_image_job_indexes,
                    rollback_image_job_indexes,
                ),
                Migration(
                    15,
                    "group-targeted-reply-persistence",
                    apply_group_reply_indexes,
                    rollback_group_reply_indexes,
                ),
                Migration(
                    16,
                    "moderation-action-ledger",
                    apply_moderation_action_indexes,
                    rollback_moderation_action_indexes,
                ),
                Migration(
                    17,
                    "moderation-policy-retention-fix",
                    apply_moderation_policy_retention_fix,
                    rollback_moderation_policy_retention_fix,
                ),
                Migration(
                    18,
                    "group-reply-unique-attribution-index",
                    apply_group_reply_unique_index,
                    rollback_group_reply_unique_index,
                ),
                Migration(
                    19,
                    "telegram-username-target-lookup",
                    apply_user_username_index,
                    rollback_user_username_index,
                ),
                Migration(
                    20,
                    "economy-atomic-account-ledger",
                    apply_economy_atomic_ledger,
                    rollback_economy_atomic_ledger,
                ),
                Migration(21, 'economy-durable-receipts-and-cosmetics',
                          apply_economy_receipts, rollback_economy_receipts),
                Migration(22, 'board-panel-recovery', apply_board_panel_recovery, rollback_board_panel_recovery),
            ],
        )

    async def _ensure_indexes_v1(self) -> None:
        """Create the indexes required by retention, search and job recovery."""
        if Database._db is None:
            return
        await self.messages.create_index([("chat_id", 1), ("timestamp", -1)])
        await self.messages.create_index("timestamp", expireAfterSeconds=30 * 24 * 60 * 60)
        await self.quotes.create_index([("chat_id", 1), ("rating", -1)])
        await Database._db["quote_templates"].create_index([("user_id", 1), ("name", 1)], unique=True)
        await self.sticker_packs.create_index([("user_id", 1), ("created_at", -1)])
        await Database._db["stickers"].create_index("tags")
        await Database._db["stickers"].create_index([("pack_id", 1), ("file_id", 1)], unique=True)
        await Database._db["stickers"].create_index(
            [("pack_id", 1), ("position", 1)], name="sticker_pack_position"
        )
        await Database._db["sticker_operations"].create_index("operation_key", unique=True)
        await Database._db["sticker_operations"].create_index(
            "created_at", expireAfterSeconds=30 * 24 * 60 * 60
        )
        await Database._db["media_jobs"].create_index([("status", 1), ("created_at", 1)])
        await Database._db["media_jobs"].create_index([("user_id", 1), ("status", 1)])
        await Database._db["media_jobs"].create_index("job_id", unique=True)
        await Database._db["media_jobs"].create_index("url_hash")
        await Database._db["media_cache"].create_index("url_hash", unique=True)
        await Database._db["media_cache"].create_index("created_at", expireAfterSeconds=24 * 60 * 60)
        await Database._db["audit_logs"].create_index([("actor_id", 1), ("created_at", -1)])
        await Database._db["dead_letters"].create_index([("created_at", -1)])
        await Database._db["game_scores"].create_index([("chat_id", 1), ("game_type", 1), ("score", -1)])
        await Database._db["game_scores"].create_index([("user_id", 1), ("chat_id", 1)])
        await Database._db["game_scores"].create_index("event_id", unique=True, sparse=True)
        await Database._db["game_progress_events"].create_index("event_id", unique=True)
        await Database._db["game_progress_events"].create_index([("chat_id", 1), ("season_id", 1), ("created_at", -1)])
        await Database._db["game_progress_events"].create_index([("user_id", 1), ("chat_id", 1), ("season_id", 1)])
        await Database._db["game_action_ledger"].create_index("action_key", unique=True)
        await Database._db["game_action_ledger"].create_index([("chat_id", 1), ("game_type", 1), ("created_at", -1)])
        await Database._db["game_action_ledger"].create_index("created_at", expireAfterSeconds=30 * 24 * 60 * 60)
        await Database._db["reminders"].create_index([("user_id", 1), ("due_at", 1)])
        await Database._db["subscriptions"].create_index([("enabled", 1), ("next_run_at", 1)])
        await Database._db["subscriptions"].create_index([("user_id", 1), ("created_at", -1)])
        await Database._db["subscriptions"].create_index("subscription_id", unique=True)
        await Database._db["notification_preferences"].create_index("user_id", unique=True)
        await Database._db["notification_deliveries"].create_index("delivery_key", unique=True)
        await Database._db["notification_deliveries"].create_index(
            "created_at", expireAfterSeconds=30 * 24 * 60 * 60
        )
        await Database._db["economy_accounts"].create_index([("user_id", 1), ("chat_id", 1)], unique=True)
        await Database._db["economy_transactions"].create_index("event_key", unique=True)
        await Database._db["economy_transactions"].create_index([("user_id", 1), ("chat_id", 1), ("created_at", -1)])
        await Database._db["ai_usage"].create_index([("provider", 1), ("created_at", -1)])
        await Database._db["ai_usage"].create_index("created_at", expireAfterSeconds=90 * 24 * 60 * 60)
        await Database._db["notes"].create_index([("user_id", 1), ("updated_at", -1)])
        await Database._db["notes"].create_index([("user_id", 1), ("note_id", 1)], unique=True)
        await Database._db["tasks"].create_index([("user_id", 1), ("completed", 1), ("created_at", -1)])
        await Database._db["tasks"].create_index([("user_id", 1), ("task_id", 1)], unique=True)
        await Database._db["facts"].create_index([("user_id", 1), ("key", 1)], unique=True)
        await Database._db["facts"].create_index([("user_id", 1), ("timestamp", -1)])
        await Database._db["states"].create_index("expires_at", expireAfterSeconds=0)
        await Database._db["game_states"].create_index([("chat_id", 1), ("game_type", 1)], unique=True)
        await Database._db["game_states"].create_index("expires_at", expireAfterSeconds=0)
        await Database._db["game_states"].create_index([("game_type", 1), ("state.turn_expires_at", 1)])
        await Database._db["game_states"].create_index([("game_type", 1), ("state.round_deadline", 1)])

    async def delete_chat_history(self, chat_id: int, user_id: Optional[int] = None) -> int:
        """Delete conversation records while preserving account and audit data."""
        if Database._db is None:
            return 0
        query: Dict[str, Any] = {"chat_id": chat_id}
        if user_id is not None:
            query["user_id"] = user_id
        result = await self.messages.delete_many(query)
        return int(result.deleted_count)

    async def delete_user_facts(self, user_id: int) -> int:
        if Database._db is None:
            return 0
        result = await self.facts.delete_many({"user_id": user_id})
        return int(result.deleted_count)

    @staticmethod
    def _fact_id(user_id: int, key: str) -> str:
        raw = f"{int(user_id)}:{str(key).strip().casefold()}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:16]

    async def list_user_facts(self, user_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        """Return bounded, user-scoped memory records with stable public IDs."""
        if Database._db is None:
            return []
        cursor = self.facts.find(
            {"user_id": int(user_id)},
            {"_id": 0, "user_id": 0},
        ).sort("timestamp", -1).limit(max(1, min(int(limit), 100)))
        rows = await cursor.to_list(length=max(1, min(int(limit), 100)))
        result = []
        for row in rows:
            key = str(row.get("key") or "")[:80]
            result.append({
                "fact_id": str(row.get("fact_id") or self._fact_id(user_id, key)),
                "key": key,
                "value": str(row.get("value") or "")[:500],
                "timestamp": row.get("timestamp"),
            })
        return result

    async def delete_user_fact(self, user_id: int, fact_id: str) -> bool:
        """Delete exactly one fact after resolving its owner-scoped public ID."""
        if Database._db is None:
            return False
        requested = str(fact_id or "").strip().lower()[:32]
        if not requested:
            return False
        rows = await self.list_user_facts(user_id, limit=100)
        match = next((row for row in rows if row["fact_id"] == requested), None)
        if match is None:
            return False
        result = await self.facts.delete_one({"user_id": int(user_id), "key": match["key"]})
        return bool(result.deleted_count)

    async def save_rag_document(self, document: Dict[str, Any]) -> None:
        """Persist bounded metadata for one owner-scoped Chroma document."""
        if Database._db is None or self.rag_documents is None:
            return
        allowed = {
            "document_id", "user_id", "filename", "extension", "mime_type", "size",
            "sha256", "chunk_count", "created_at", "embedding_mode",
        }
        safe = {key: value for key, value in dict(document or {}).items() if key in allowed}
        safe["document_id"] = str(safe.get("document_id") or "")[:64]
        safe["user_id"] = int(safe.get("user_id"))
        safe["filename"] = str(safe.get("filename") or "belge")[:255]
        safe["sha256"] = str(safe.get("sha256") or "")[:64]
        safe["chunk_count"] = max(0, min(int(safe.get("chunk_count", 0)), 100_000))
        safe["size"] = max(0, min(int(safe.get("size", 0)), 100 * 1024 * 1024))
        safe["created_at"] = safe.get("created_at") or datetime.now(timezone.utc)
        await self.rag_documents.update_one(
            {"document_id": safe["document_id"], "user_id": safe["user_id"]},
            {"$set": safe},
            upsert=True,
        )

    async def list_rag_documents(self, user_id: int, limit: int = 20) -> List[Dict[str, Any]]:
        if Database._db is None or self.rag_documents is None:
            return []
        safe_limit = max(1, min(int(limit), 100))
        cursor = self.rag_documents.find(
            {"user_id": int(user_id)},
            {"_id": 0, "user_id": 0},
        ).sort("created_at", -1).limit(safe_limit)
        return await cursor.to_list(length=safe_limit)

    async def delete_rag_document(self, user_id: int, document_id: str) -> bool:
        if Database._db is None or self.rag_documents is None:
            return False
        result = await self.rag_documents.delete_one({
            "user_id": int(user_id),
            "document_id": str(document_id or "")[:64],
        })
        return bool(result.deleted_count)

    async def delete_rag_documents(self, user_id: int) -> int:
        if Database._db is None or self.rag_documents is None:
            return 0
        result = await self.rag_documents.delete_many({"user_id": int(user_id)})
        return int(result.deleted_count)

    async def add_audit_log(self, actor_id: int, action: str, **data: Any) -> None:
        if Database._db is None:
            return
        document = {
            "actor_id": actor_id,
            "action": action,
            "created_at": datetime.utcnow(),
            **data,
        }
        await self.audit_logs.insert_one(document)

    async def record_moderation_violation(
        self,
        *,
        event_id: str,
        chat_id: int,
        user_id: int,
        reason: str,
        window_seconds: int = 600,
    ) -> int:
        """Persist one idempotent violation and return the current strike count."""
        if Database._db is None or self.moderation_violations is None:
            return 0
        now = datetime.now(timezone.utc)
        safe_reason = str(reason or "unknown")[:64]
        document = {
            "event_id": str(event_id)[:180],
            "chat_id": int(chat_id),
            "user_id": int(user_id),
            "reason": safe_reason,
            "created_at": now,
        }
        try:
            await self.moderation_violations.insert_one(document)
        except Exception as exc:
            if not (getattr(exc, "code", None) == 11000 or "duplicate" in str(exc).casefold()):
                raise
        cutoff = now - timedelta(seconds=max(60, min(int(window_seconds), 86400)))
        return int(await self.moderation_violations.count_documents({
            "chat_id": int(chat_id),
            "user_id": int(user_id),
            "reason": safe_reason,
            "created_at": {"$gte": cutoff},
        }))

    async def record_moderation_provider_event(
        self,
        *,
        request_id: str,
        provider: str,
        decision: str,
        error_code: str | None = None,
        latency_ms: float | None = None,
        enforced: bool = False,
    ) -> bool:
        """Persist a bounded, content-free Guardian/provider observation."""
        if Database._db is None or self.moderation_provider_events is None:
            return False
        document = {
            "request_id": str(request_id)[:180],
            "provider": str(provider)[:64],
            "decision": str(decision)[:32],
            "error_code": str(error_code)[:96] if error_code else None,
            "latency_ms": max(0.0, float(latency_ms)) if latency_ms is not None else None,
            "enforced": bool(enforced),
            "created_at": datetime.now(timezone.utc),
        }
        try:
            await self.moderation_provider_events.insert_one(document)
            return True
        except Exception as exc:
            if getattr(exc, "code", None) == 11000 or "duplicate" in str(exc).casefold():
                return False
            raise

    async def get_moderation_summary(
        self,
        chat_id: int,
        *,
        since: datetime,
        limit: int = 10,
    ) -> Dict[str, Any]:
        """Return bounded group moderation aggregates without message content."""
        if Database._db is None or self.moderation_violations is None:
            return {"total": 0, "active_users": 0, "by_reason": [], "top_users": []}
        safe_limit = max(1, min(int(limit), 20))
        match = {"chat_id": int(chat_id), "created_at": {"$gte": since}}
        total = int(await self.moderation_violations.count_documents(match))
        reason_rows = await self.moderation_violations.aggregate([
            {"$match": match},
            {"$group": {"_id": "$reason", "count": {"$sum": 1}}},
            {"$sort": {"count": -1, "_id": 1}},
            {"$limit": safe_limit},
        ]).to_list(length=safe_limit)
        user_rows = await self.moderation_violations.aggregate([
            {"$match": match},
            {"$group": {"_id": "$user_id", "count": {"$sum": 1}, "last_at": {"$max": "$created_at"}}},
            {"$sort": {"count": -1, "last_at": -1}},
            {"$limit": safe_limit},
        ]).to_list(length=safe_limit)
        active_rows = await self.moderation_violations.aggregate([
            {"$match": match},
            {"$group": {"_id": "$user_id"}},
            {"$count": "active_users"},
        ]).to_list(length=1)
        top_users: list[Dict[str, Any]] = []
        for row in user_rows:
            user_id = int(row.get("_id"))
            user = await self.get_user(user_id)
            name = (user or {}).get("first_name") or (user or {}).get("username") or f"ID:{user_id}"
            top_users.append({
                "user_id": user_id,
                "name": str(name)[:80],
                "count": int(row.get("count", 0)),
                "last_at": row.get("last_at"),
            })
        return {
            "total": total,
            "active_users": int(active_rows[0]["active_users"]) if active_rows else 0,
            "by_reason": [
                {"reason": str(row.get("_id") or "unknown")[:64], "count": int(row.get("count", 0))}
                for row in reason_rows
            ],
            "top_users": top_users,
        }

    async def clear_moderation_strikes(self, chat_id: int, user_id: int) -> int:
        """Clear one user's retained moderation history and return deleted rows."""
        if Database._db is None or self.moderation_violations is None:
            return 0
        result = await self.moderation_violations.delete_many({
            "chat_id": int(chat_id),
            "user_id": int(user_id),
        })
        return int(result.deleted_count)

    async def get_audit_logs(self, limit: int = 20) -> List[Dict[str, Any]]:
        if Database._db is None:
            return []
        cursor = self.audit_logs.find({}, {"_id": 0}).sort("created_at", -1).limit(limit)
        return await cursor.to_list(length=limit)

    async def add_dead_letter(self, job_id: str, error_code: str, payload: Dict[str, Any]) -> None:
        if Database._db is None:
            return
        safe_payload = {key: value for key, value in payload.items() if key not in {"url", "cookie", "cookie_file"}}
        await self.dead_letters.insert_one({
            "job_id": job_id, "error_code": error_code, "payload": safe_payload,
            "created_at": datetime.utcnow(),
        })

    async def record_ai_usage(self, document: Dict[str, Any]) -> None:
        """Persist bounded AI telemetry without prompt or response content."""
        if Database._db is None:
            return
        allowed = {
            "provider", "success", "latency_ms", "input_tokens", "output_tokens",
            "cost_usd", "task", "created_at",
        }
        safe = {key: value for key, value in dict(document or {}).items() if key in allowed}
        safe["provider"] = str(safe.get("provider", "unknown"))[:80]
        safe["task"] = str(safe.get("task", "chat"))[:80]
        safe["created_at"] = safe.get("created_at") or datetime.utcnow()
        await self.ai_usage.insert_one(safe)

    async def get_media_cache(self, url_hash: str) -> Optional[str]:
        if Database._db is None:
            return None
        document = await self.media_cache.find_one({"url_hash": url_hash})
        return document.get("telegram_file_id") if document else None

    async def save_media_cache(self, url_hash: str, telegram_file_id: str, platform: str) -> None:
        if Database._db is None:
            return
        await self.media_cache.update_one(
            {"url_hash": url_hash},
            {"$set": {"url_hash": url_hash, "telegram_file_id": telegram_file_id,
                       "platform": platform, "created_at": datetime.utcnow()}},
            upsert=True,
        )

    async def delete_media_cache(self, url_hash: str) -> None:
        """Evict a Telegram file reference that Telegram no longer accepts."""
        if Database._db is None:
            return
        await self.media_cache.delete_one({"url_hash": url_hash})

    # --- LEADERBOARD & GAME SCORES ---
    async def save_game_score(self, user_id: int, chat_id: int, game_type: str, score: int,
                              user_name: str = None, event_id: str | None = None) -> bool:
        document = {
            "user_id": user_id,
            "chat_id": chat_id,
            "game_type": game_type,
            "score": score,
            "user_name": user_name,
            "timestamp": datetime.utcnow()
        }
        if event_id:
            document["event_id"] = str(event_id)
            # Existing score rows predate this recovery contract. Only newly
            # inserted rows may replay their player-stats update.
            document["stats_protocol_version"] = 2
        try:
            await self.game_scores.insert_one(document)
            return True
        except Exception as exc:
            # The sparse unique event_id index makes callback retries safe.
            if getattr(exc, "code", None) == 11000 and event_id:
                return False
            raise

    async def score_stats_retryable(
        self, *, event_id: str, user_id: int, chat_id: int, game_type: str, score: int,
    ) -> bool:
        row = await self.game_scores.find_one(
            {"event_id": str(event_id)},
            {"_id": 0, "user_id": 1, "chat_id": 1, "game_type": 1,
             "score": 1, "stats_protocol_version": 1},
        )
        if not row or int(row.get("stats_protocol_version", 0)) != 2:
            return False
        if (int(row["user_id"]), int(row["chat_id"]), row["game_type"], int(row["score"])) != (
            int(user_id), int(chat_id), str(game_type), int(score)
        ):
            raise ValueError("game_score_event_conflict")
        return True

    async def get_leaderboard(self, chat_id: int, game_type: str = None, limit: int = 10) -> List[Dict]:
        match = {"chat_id": chat_id}
        if game_type:
            match["game_type"] = game_type
        pipeline = [
            {"$match": match},
            {"$group": {"_id": "$user_id", "total_score": {"$sum": "$score"}, "games_played": {"$sum": 1}, "user_name": {"$first": "$user_name"}}},
            {"$sort": {"total_score": -1}},
            {"$limit": limit}
        ]
        cursor = self.game_scores.aggregate(pipeline)
        return await cursor.to_list(length=limit)

    async def get_player_stats(self, user_id: int) -> Optional[Dict]:
        return await self.player_stats.find_one({"_id": user_id})

    async def update_player_stats(
        self, user_id: int, score_delta: int, user_name: str = None,
        event_id: str | None = None,
    ) -> bool:
        update = {
            "$inc": {"total_score": score_delta, "games_played": 1},
            "$set": {"last_played": datetime.utcnow()}
        }
        if user_name:
            update["$set"]["user_name"] = user_name
        if event_id is None:
            await self.player_stats.update_one({"_id": user_id}, update, upsert=True)
            return True
        # The event marker and counters change in one document write. A retry
        # after a lost acknowledgement cannot increment the profile twice.
        update["$addToSet"] = {"applied_score_events": str(event_id)}
        for attempt in range(4):
            try:
                result = await self.player_stats.update_one(
                    {"_id": int(user_id), "applied_score_events": {"$ne": str(event_id)}},
                    update, upsert=True,
                )
                return bool(result.modified_count or result.upserted_id is not None)
            except Exception as exc:
                if getattr(exc, "code", None) != 11000:
                    raise
                # A filtered upsert also collides when a different score
                # creates this player's profile concurrently. Retry that
                # case, while treating our own stamped event as complete.
                existing = await self.player_stats.find_one(
                    {"_id": int(user_id), "applied_score_events": str(event_id)},
                    {"_id": 1},
                )
                if existing:
                    return False
                if attempt == 3:
                    raise
        raise RuntimeError("player_stats_conflict")

    async def record_game_progress(
        self,
        *,
        event_id: str,
        user_id: int,
        chat_id: int,
        season_id: str,
        points: int,
        game_type: str,
        achievements: list[str],
        user_name: str | None = None,
    ) -> bool:
        """Append one canonical game event; uniqueness makes retry safe."""
        if Database._db is None:
            return False
        now = datetime.utcnow()
        try:
            await self.game_progress_events.insert_one({
                "event_id": str(event_id),
                "user_id": int(user_id),
                "chat_id": int(chat_id),
                "season_id": str(season_id),
                "points": max(0, int(points)),
                "game_type": str(game_type)[:80],
                "achievements": sorted({str(item)[:80] for item in (achievements or []) if item}),
                "user_name": str(user_name)[:120] if user_name else None,
                "created_at": now,
            })
            return True
        except Exception as exc:
            if getattr(exc, "code", None) == 11000 or "duplicate" in str(exc).casefold() or "11000" in str(exc):
                return False
            raise

    async def claim_game_action(
        self,
        *,
        action_key: str,
        chat_id: int,
        game_type: str,
        action_id: str,
        user_id: int | None = None,
        payload: Dict[str, Any] | None = None,
    ) -> bool:
        """Insert one callback action; the unique key makes retries harmless."""
        if Database._db is None:
            return False
        try:
            await self.game_action_ledger.insert_one({
                "action_key": str(action_key),
                "chat_id": int(chat_id),
                "game_type": str(game_type)[:64],
                "action_id": str(action_id)[:180],
                "user_id": int(user_id) if user_id is not None else None,
                "payload": dict(payload or {}),
                "created_at": datetime.utcnow(),
            })
            return True
        except Exception as exc:
            if getattr(exc, "code", None) == 11000 or "duplicate" in str(exc).casefold() or "11000" in str(exc):
                return False
            raise

    async def release_game_action(self, action_key: str) -> None:
        if Database._db is not None:
            await self.game_action_ledger.delete_one({"action_key": str(action_key)})

    async def claim_game_lease(self, action_key: str, owner_token: str,
                               lease_seconds: int, now: datetime) -> bool:
        """Acquire an expired lease atomically; unique action_key excludes rivals."""
        if Database._db is None:
            raise RuntimeError("game_persistence_unavailable")
        until = now + timedelta(seconds=max(1, min(300, int(lease_seconds))))
        try:
            result = await self.game_action_ledger.update_one(
                {"action_key": action_key, "lease_until": {"$lte": now}},
                {"$set": {"owner_token": owner_token, "lease_until": until,
                          "created_at": now, "kind": "owner_lease"},
                 "$setOnInsert": {"action_key": action_key}}, upsert=True,
            )
            return bool(result.modified_count or result.upserted_id is not None)
        except Exception as exc:
            if getattr(exc, "code", None) == 11000:
                return False
            raise

    async def renew_game_lease(self, action_key: str, owner_token: str,
                               lease_seconds: int, now: datetime) -> bool:
        if Database._db is None:
            raise RuntimeError("game_persistence_unavailable")
        result = await self.game_action_ledger.update_one(
            {"action_key": action_key, "owner_token": owner_token, "lease_until": {"$gt": now}},
            {"$set": {"lease_until": now + timedelta(seconds=max(1, min(300, int(lease_seconds)))),
                      "created_at": now}},
        )
        return bool(result.matched_count)

    async def release_game_lease(self, action_key: str, owner_token: str) -> bool:
        if Database._db is None:
            raise RuntimeError("game_persistence_unavailable")
        result = await self.game_action_ledger.delete_one(
            {"action_key": action_key, "owner_token": owner_token})
        return bool(result.deleted_count)

    async def get_game_achievements(self, user_id: int, chat_id: int, limit: int = 50) -> List[Dict]:
        if Database._db is None:
            return []
        pipeline = [
            {"$match": {"user_id": int(user_id), "chat_id": int(chat_id)}},
            {"$unwind": "$achievements"},
            {"$group": {"_id": "$achievements", "unlocked_at": {"$min": "$created_at"}}},
            {"$sort": {"unlocked_at": 1}},
            {"$limit": max(1, min(int(limit), 100))},
        ]
        rows = await self.game_progress_events.aggregate(pipeline).to_list(length=max(1, min(int(limit), 100)))
        rows = [{"key": row["_id"], "unlocked_at": row.get("unlocked_at")} for row in rows]
        labels = {
            "first_game": "İlk oyun",
            "perfect_round": "Kusursuz tur",
            "quiz_player": "Quiz oyuncusu",
        }
        for row in rows:
            row["label"] = labels.get(row.get("key"), str(row.get("key", "Başarım")).replace("_", " ").title())
        return rows

    async def get_season_leaderboard(self, chat_id: int, season_id: str, limit: int = 10) -> List[Dict]:
        if Database._db is None:
            return []
        pipeline = [
            {"$match": {"chat_id": int(chat_id), "season_id": str(season_id)}},
            {"$group": {
                "_id": "$user_id", "points": {"$sum": "$points"},
                "games_played": {"$sum": 1}, "user_name": {"$last": "$user_name"},
            }},
            {"$sort": {"points": -1}},
            {"$limit": max(1, min(int(limit), 50))},
        ]
        rows = await self.game_progress_events.aggregate(pipeline).to_list(length=max(1, min(int(limit), 50)))
        return [{"user_id": row["_id"], **{key: value for key, value in row.items() if key != "_id"}} for row in rows]

    # --- SUDO OPERATIONS ---
    async def get_sudo_users(self) -> List[int]:
        """Tüm sudo kullanıcılarını döndürür."""
        cursor = self.sudo_users.find({}, {"_id": 1})
        results = await cursor.to_list(length=1000)
        return [r["_id"] for r in results]

    async def add_sudo_user(self, user_id: int):
        """Sudo kullanıcı ekler."""
        await self.sudo_users.update_one(
            {"_id": user_id},
            {"$set": {"added_at": datetime.utcnow()}},
            upsert=True
        )

    async def remove_sudo_user(self, user_id: int):
        """Sudo kullanıcı siler."""
        await self.sudo_users.delete_one({"_id": user_id})

    async def is_sudo_user(self, user_id: int) -> bool:
        """Kullanıcı sudo mu?"""
        return await self.sudo_users.find_one({"_id": user_id}) is not None

    async def get_blacklist_users(self) -> List[int]:
        """Tüm kara listedeki kullanıcıları döndürür."""
        cursor = self.blacklist.find({"type": "user"}, {"_id": 1})
        results = await cursor.to_list(length=1000)
        return [r["_id"] for r in results]

    async def get_blacklist_groups(self) -> List[int]:
        """Tüm kara listedeki grupları döndürür."""
        cursor = self.blacklist.find({"type": "group"}, {"_id": 1})
        results = await cursor.to_list(length=1000)
        return [r["_id"] for r in results]

    async def add_blacklist_user(self, user_id: int):
        """Kullanıcıyı kara listeye ekler."""
        await self.blacklist.update_one(
            {"_id": user_id, "type": "user"},
            {"$set": {"added_at": datetime.utcnow()}},
            upsert=True
        )

    async def add_blacklist_group(self, chat_id: int):
        """Grubu kara listeye ekler."""
        await self.blacklist.update_one(
            {"_id": chat_id, "type": "group"},
            {"$set": {"added_at": datetime.utcnow()}},
            upsert=True
        )

    async def remove_blacklist_user(self, user_id: int):
        """Kullanıcıyı kara listeden çıkarır."""
        await self.blacklist.delete_one({"_id": user_id, "type": "user"})

    async def remove_blacklist_group(self, chat_id: int):
        """Grubu kara listeden çıkarır."""
        await self.blacklist.delete_one({"_id": chat_id, "type": "group"})

    async def is_blacklisted_user(self, user_id: int) -> bool:
        """Kullanıcı kara listede mi?"""
        return await self.blacklist.find_one({"_id": user_id, "type": "user"}) is not None

    async def is_blacklisted_group(self, chat_id: int) -> bool:
        """Grup kara listede mi?"""
        return await self.blacklist.find_one({"_id": chat_id, "type": "group"}) is not None

    # --- USER OPERATIONS ---
    async def get_user(self, user_id: int) -> Optional[Dict]:
        return await self.users.find_one({"_id": user_id})

    async def find_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        """Return the bounded profile for a username observed by Arel.

        Telegram usernames are case-insensitive.  The query is anchored and
        escaped so user supplied input cannot become a Mongo regex operator.
        Membership in a particular chat is verified by the Telegram transport
        service before a moderation action is built.
        """
        normalized = str(username or "").strip().lstrip("@").strip()
        if not normalized or Database._db is None:
            return None
        pattern = f"^{re.escape(normalized)}$"
        return await self.users.find_one(
            {"username": {"$regex": pattern, "$options": "i"}},
            {"_id": 1, "username": 1, "first_name": 1},
        )

    async def update_user(self, user_id: int, data: Dict):
        data["last_seen"] = datetime.utcnow()
        await self.users.update_one(
            {"_id": user_id},
            {"$set": data},
            upsert=True
        )

    @staticmethod
    def _game_has_financial_obligation(game_type: str, state: Dict[str, Any]) -> bool:
        # Paid hands must survive inactivity too: cancellation still owes a refund.
        return (str(game_type).startswith("blackjack:") and state.get("economy_required") is not False
                or any(state.get(key) for key in ("score_pending", "settlement_pending", "refund_pending", "terminal_panel_pending", "board_score_pending", "board_panel_dirty"))
                or str(game_type) == "roulette" and bool(state.get("panel_dirty")))

    async def save_game_state(self, chat_id: int, game_type: str, state: Dict[str, Any]) -> None:
        if Database._db is None:
            raise RuntimeError("game_persistence_unavailable")
        now = datetime.now(timezone.utc)
        fields = {"chat_id": chat_id, "game_type": game_type, "state": state, "updated_at": now}
        update = {"$set": fields}
        if self._game_has_financial_obligation(game_type, state):
            update["$unset"] = {"expires_at": ""}
        else:
            fields["expires_at"] = now + timedelta(hours=24)
        await self.game_states.update_one(
            {"chat_id": chat_id, "game_type": game_type}, update, upsert=True,
        )

    async def create_game_state_if_absent(self, chat_id: int, game_type: str, state: Dict[str, Any]) -> bool:
        """Create one game session without replacing a concurrent lobby."""
        if Database._db is None:
            raise RuntimeError("game_persistence_unavailable")
        now = datetime.now(timezone.utc)
        initial = {"chat_id": int(chat_id), "game_type": str(game_type), "state": state, "updated_at": now}
        if not self._game_has_financial_obligation(game_type, state):
            initial["expires_at"] = now + timedelta(hours=24)
        try:
            result = await self.game_states.update_one(
                {"chat_id": int(chat_id), "game_type": str(game_type)},
                {"$setOnInsert": initial},
                upsert=True,
            )
        except Exception as exc:
            if getattr(exc, "code", None) == 11000:
                return False
            raise
        return result.upserted_id is not None

    async def initialize_game_state_version_if_missing(self, chat_id: int, game_type: str) -> bool:
        """Backfill only the CAS field on a legacy game without replacing its payload."""
        if Database._db is None:
            raise RuntimeError("game_persistence_unavailable")
        result = await self.game_states.update_one(
            {"chat_id": int(chat_id), "game_type": str(game_type), "state.version": {"$exists": False}},
            {"$set": {"state.version": 0}},
        )
        return bool(result.modified_count)

    async def compare_and_save_game_state(
        self, chat_id: int, game_type: str, state: Dict[str, Any], *, expected_version: int,
    ) -> bool:
        """Save a transition only if no other process changed the session."""
        if Database._db is None:
            raise RuntimeError("game_persistence_unavailable")
        if int(state.get("version", -1)) != int(expected_version) + 1:
            raise ValueError("game_state_version_invalid")
        now = datetime.now(timezone.utc)
        update = {"$set": {"state": state, "updated_at": now}}
        if self._game_has_financial_obligation(game_type, state):
            update["$unset"] = {"expires_at": ""}
        else:
            update["$set"]["expires_at"] = now + timedelta(hours=24)
        result = await self.game_states.update_one(
            {"chat_id": int(chat_id), "game_type": str(game_type), "state.version": int(expected_version)},
            update,
        )
        return bool(result.modified_count)

    async def get_game_state(self, chat_id: int, game_type: str) -> Optional[Dict[str, Any]]:
        if Database._db is None:
            raise RuntimeError("game_persistence_unavailable")
        document = await self.game_states.find_one({"chat_id": chat_id, "game_type": game_type})
        return document.get("state") if document else None

    async def delete_game_state(self, chat_id: int, game_type: str) -> None:
        if Database._db is None:
            raise RuntimeError("game_persistence_unavailable")
        await self.game_states.delete_one({"chat_id": chat_id, "game_type": game_type})

    @staticmethod
    def _active_secret_route_query(user_id: int) -> dict:
        import re
        from src.games.puzzle_catalog import SECRET_GAME_KEYS
        pattern = "^(?:" + "|".join(re.escape(key) for key in sorted(SECRET_GAME_KEYS)) + ")(?:_t[0-9]+)?$"
        return {"game_type": {"$regex": pattern},
                "state.phase": {"$nin": ["lobby", "finished", "cancelled", "expired"]},
                "state.players": {"$elemMatch": {"id": int(user_id), "active": True}}}

    async def get_active_secret_game_for_user(self, user_id: int) -> dict | None:
        """Return safe routing metadata, never a role, target, clue or ballot."""
        if Database._db is None:
            raise RuntimeError("game_persistence_unavailable")
        return await self.game_states.find_one(self._active_secret_route_query(user_id),
                                               {"_id": 0, "chat_id": 1, "game_type": 1})

    async def list_active_secret_game_routes(self, user_id: int) -> list[dict]:
        if Database._db is None:
            raise RuntimeError("game_persistence_unavailable")
        return await self.game_states.find(self._active_secret_route_query(user_id),
            {"_id": 0, "chat_id": 1, "game_type": 1}).limit(50).to_list(length=50)

    async def list_active_puzzle_routes(self, chat_id: int, thread_id: int | None, user_id: int) -> list[dict]:
        """One bounded metadata read replaces per-game secret-state fetches."""
        if Database._db is None:
            raise RuntimeError("game_persistence_unavailable")
        from src.games.puzzle_catalog import TEXT_PUZZLE_KEYS
        keys = [key if thread_id is None else f"{key}_t{int(thread_id)}" for key in sorted(TEXT_PUZZLE_KEYS)]
        query = {"chat_id": int(chat_id), "game_type": {"$in": keys},
                 "state.phase": {"$nin": ["lobby", "finished", "cancelled", "expired"]},
                 "$or": [{"state.owner_id": int(user_id)},
                         {"state.players": {"$elemMatch": {"id": int(user_id), "active": {"$ne": False}}}}]}
        projection = {"_id": 0, "game_type": 1, "state.game_key": 1, "state.phase": 1,
                      "state.panel_message_id": 1, "state.owner_id": 1, "state.thread_id": 1,
                      "state.players.id": 1, "state.players.active": 1}
        return await self.game_states.find(query, projection).limit(len(keys)).to_list(length=len(keys))

    async def defer_board_recovery(self, chat_id: int, game_type: str, until: float) -> None:
        if str(game_type) not in {"connect4", "tictactoe", "gomoku", "chess"}:
            raise ValueError("board_unknown_game")
        if Database._db is None:
            raise RuntimeError("game_persistence_unavailable")
        await self.game_states.update_one(
            {"chat_id": int(chat_id), "game_type": str(game_type)},
            {"$set": {"board_recovery_after": float(until), "updated_at": datetime.now(timezone.utc)}},
        )

    async def list_due_game_states(self, *, now: float, limit: int = 100) -> list[dict]:
        """Find durable timers and undelivered panels after any process restart."""
        if Database._db is None:
            raise RuntimeError("game_persistence_unavailable")
        import re
        from src.games.puzzle_catalog import PUZZLE_KEYS
        puzzle_pattern = "^(?:" + "|".join(re.escape(key) for key in sorted(PUZZLE_KEYS)) + ")(?:_t[0-9]+)?$"
        query = {"$or": [
            {"game_type": {"$in": ["connect4", "tictactoe", "gomoku", "chess"]}, "$and": [
                {"$or": [{"board_recovery_after": {"$exists": False}},
                         {"board_recovery_after": {"$lte": now}}]},
                {"$or": [
                {"state.board_score_pending": True},
                {"state.board_panel_dirty": True, "state.board_panel_delivery_unknown": {"$ne": True},
                 "state.board_panel_retry_at": {"$lte": now}},
                {"state.status": "playing", "state.turn_expires_at": {"$lte": now}},
            ]}]},
            {'game_type': 'roulette', 'state.phase': 'playing', 'state.turn_expires_at': {'$lte': now}},
            {'game_type': 'roulette', 'state.phase': {'$in': ['finished', 'expired']}, '$or': [
                {'state.score_pending': True},
                {'state.panel_dirty': True, 'state.panel_retry_at': {'$lte': now}},
            ]},
            {'game_type': {'$regex': r'^blackjack:[0-9]+$'}, '$or': [
                {'state.settlement_pending': True}, {'state.refund_pending': True},
                {'state.terminal_panel_pending': True, 'state.panel_retry_at': {'$lte': now}},
                {'state.status': {'$in': ['pending_bet', 'pending_double']}},
            ]},
            {"game_type": {"$regex": puzzle_pattern}, "$or": [
                {"state.phase": {"$nin": ["finished", "cancelled"]}, "state.deadline": {"$gt": 0, "$lte": now}},
                {"state.phase": {"$nin": ["finished", "cancelled"]}, "state.panel_wait_until": {"$gt": 0, "$lte": now}},
                {"state.lobby_expires_at": {"$gt": 0, "$lte": now}, "state.phase": "lobby"},
                {"state.panel_dirty": True, "$or": [
                    {"state.delivery_retry_at": {"$lte": now}},
                    {"state.delivery_retry_at": {"$exists": False}},
                ]},
                {"state.score_pending": True},
                {"state.private_dirty": True, "$or": [
                    {"state.private_retry_at": {"$exists": False}},
                    {"state.private_retry_at": {"$lte": now}},
                ]},
            ]},
            {"game_type": "uno", "$or": [
                {"state.status": "playing", "state.turn_expires_at": {"$lte": now, "$ne": None}},
                {"state.status": {"$in": ["lobby", "playing"]}, "state.last_activity_at": {"$lte": now - 900}},
                {"state.panel_dirty": True}, {"state.score_pending": True},
            ]},
            {"game_type": "guess_age", "$or": [
                {"state.delivery_pending": True}, {"state.pending_scores.0": {"$exists": True}},
                {"state.status": {"$in": ["playing", "expired"]}, "$or": [
                    {"state.round_deadline": {"$lte": now, "$ne": None}},
                    {"state.round_deadline": {"$exists": False}},
                    {"state.prompt_message_id": {"$in": [0, None]}},
                ]},
            ]},
            {"game_type": {"$regex": r"^kelime_zinciri(?:_t[0-9]+)?$"}, "$or": [
                {"state.phase": "playing", "state.turn_expires_at": {"$lte": now, "$ne": None}},
                {"state.panel_dirty": True},
                {"state.score_pending": True},
            ]},
            {"game_type": {"$regex": r"^tabu(?:_t[0-9]+)?$"}, "$or": [
                {"state.phase": "lobby", "state.last_activity_at": {"$lte": now - 1200}},
                {"state.phase": "card_delivery", "state.last_activity_at": {"$lte": now - 120}},
                {"state.phase": "card_delivery", "state.delivered": False, "$or": [
                    {"state.card_retry_at": {"$exists": False}}, {"state.card_retry_at": {"$lte": now}},
                ]},
                {"state.phase": "active", "state.deadline": {"$lte": now, "$ne": None}},
                {"state.phase": "appeal", "state.appeal_until": {"$lte": now}},
                {"state.phase": "adjudication", "$or": [
                    {"state.judge_deadline": {"$lte": now}},
                    {"state.judge_decision": {"$exists": True, "$ne": None}},
                ]},
                {"state.panel_dirty": True, "$or": [
                    {"state.panel_retry_at": {"$exists": False}}, {"state.panel_retry_at": {"$lte": now}},
                ]},
                {"state.score_pending": True},
            ]},
            {"game_type": {"$regex": r"^(?:casus|vampir_koylu)(?:_t[0-9]+)?$"}, "$or": [
                {"state.phase": {"$nin": ["finished", "cancelled"]}, "state.panel_wait_until": {"$lte": now, "$ne": None}},
                {"state.phase": {"$nin": ["finished", "cancelled"]}, "state.deadline": {"$lte": now, "$ne": None}},
                {"state.phase": "lobby", "state.last_activity_at": {"$lte": now - 1200}},
                {"state.phase": "role_delivery", "state.last_activity_at": {"$lte": now - 600}},
                {"state.phase": "delivery", "state.players": {"$elemMatch": {"active": True, "delivered": False}}, "$or": [
                    {"state.role_retry_at": {"$exists": False}}, {"state.role_retry_at": {"$lte": now, "$ne": None}},
                ]},
                {"state.panel_dirty": True, "$or": [
                    {"state.panel_retry_at": {"$exists": False}}, {"state.panel_retry_at": {"$lte": now}},
                ]},
                {"state.private_dirty": True, "$or": [
                    {"state.private_retry_at": {"$exists": False}}, {"state.private_retry_at": {"$lte": now}},
                ]},
                {"state.score_pending": True},
            ]},
        ]}
        # Puzzle families already guard the public panel branch with its own
        # lease. Other due work (private hand, timer, score) remains independent.
        query = {"$and": [query, {"$or": [
            {"game_type": {"$regex": puzzle_pattern}},
            {"state.delivery_retry_at": {"$exists": False}},
            {"state.delivery_retry_at": {"$lte": now}},
        ]}]}
        return await self.game_states.find(query, {"_id": 0, "chat_id": 1, "game_type": 1, "state": 1}).sort("updated_at", 1).limit(max(1, min(500, limit))).to_list(length=max(1, min(500, limit)))

    async def get_memory_consent(self, user_id: int) -> bool:
        if Database._db is None:
            return False
        user = await self.users.find_one({"_id": user_id}, {"memory_opt_in": 1})
        return bool(user and user.get("memory_opt_in"))

    async def set_memory_consent(self, user_id: int, enabled: bool) -> None:
        if Database._db is None:
            return
        await self.users.update_one({"_id": user_id}, {"$set": {"memory_opt_in": bool(enabled)}}, upsert=True)

    async def increment_user_message_count(self, user_id: int):
        await self.users.update_one(
            {"_id": user_id},
            {"$inc": {"message_count": 1}}
        )

    # --- CHAT OPERATIONS ---
    async def get_chat(self, chat_id: int) -> Optional[Dict]:
        return await self.chats.find_one({"_id": chat_id})

    async def update_chat(self, chat_id: int, data: Dict):
        await self.chats.update_one(
            {"_id": chat_id},
            {"$set": data},
            upsert=True
        )

    async def get_chat_settings(self, chat_id: int) -> Dict:
        chat = await self.get_chat(chat_id)
        return chat.get("settings", {}) if chat else {}

    async def update_chat_settings(self, chat_id: int, settings: Dict):
        await self.chats.update_one(
            {"_id": chat_id},
            {"$set": {"settings": settings}},
            upsert=True
        )

    async def update_chat_setting_key(self, chat_id: int, key: str, value: Any):
        """Updates a specific setting for a chat."""
        await self.chats.update_one(
            {"_id": chat_id},
            {"$set": {f"settings.{key}": value}},
            upsert=True
        )

    # --- MESSAGE OPERATIONS ---
    async def add_message(
        self,
        chat_id: int,
        user_id: Optional[int],
        role: str,
        content: str,
        function_name: str = None,
        function_args: str = None,
        *,
        schema_version: int = 1,
        turn_id: str | None = None,
        telegram_message_id: int | None = None,
        reply_to_message_id: int | None = None,
        thread_id: int | None = None,
        author_user_id: int | None = None,
        target_user_id: int | None = None,
        trigger_kind: str | None = None,
        chat_type: str | None = None,
        created_at: datetime | None = None,
    ):
        """Persist a conversation message with optional Telegram attribution.

        The original positional signature remains valid. Attribution fields
        are sparse so legacy records and readers continue to work unchanged.
        """
        timestamp = created_at or datetime.now(timezone.utc)
        msg = {
            "chat_id": int(chat_id),
            "user_id": user_id,
            "role": role,
            "content": content,
            "function_name": function_name,
            "function_args": function_args,
            "timestamp": timestamp,
            "created_at": timestamp,
            "schema_version": int(schema_version),
        }
        optional = {
            "turn_id": turn_id,
            "telegram_message_id": telegram_message_id,
            "reply_to_message_id": reply_to_message_id,
            "thread_id": thread_id,
            "author_user_id": author_user_id if author_user_id is not None else user_id,
            "target_user_id": target_user_id,
            "trigger_kind": trigger_kind,
            "chat_type": chat_type,
        }
        msg.update({key: value for key, value in optional.items() if value is not None})
        await self.messages.insert_one(msg)
    # --- QUOTE OPERATIONS ---
    async def save_quote(self, quote_data: Dict):
        quote_data["created_at"] = datetime.utcnow()
        await self.quotes.insert_one(quote_data)

    async def save_quote_template(self, user_id: int, name: str, options: Dict[str, Any]) -> None:
        from src.quotes.options import normalize_quote_options, normalize_template_name

        name = normalize_template_name(name)
        options = normalize_quote_options(options)
        if Database._db is None:
            return
        await self.quote_templates.update_one(
            {"user_id": user_id, "name": name},
            {"$set": {"user_id": user_id, "name": name, "options": options,
                       "updated_at": datetime.utcnow()}},
            upsert=True,
        )

    async def save_provider_health(self, health: Dict[str, Any]) -> None:
        if Database._db is None or not health.get("name"):
            return
        safe_health = dict(health)
        safe_health["last_metadata"] = self._safe_provider_metadata(health.get("last_metadata"))
        await self.provider_health.update_one(
            {"name": health["name"]},
            {"$set": {**safe_health, "updated_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
        # Keep a bounded, source-level history separate from the latest health
        # snapshot. Cache hits reuse ``last_fetched_at`` and therefore upsert
        # the same observation instead of multiplying identical records.
        fetched_at = health.get("last_fetched_at")
        if fetched_at is None or self.provider_observations is None:
            return
        allowed = {
            "provider", "source", "source_url", "last_fetched_at", "last_cache_hit",
            "last_stale", "last_metadata", "healthy", "latency_ms", "error_rate",
            "score", "last_error_code",
        }
        observation = {key: value for key, value in health.items() if key in allowed}
        observation["last_metadata"] = safe_health["last_metadata"]
        observation["provider"] = str(health["name"])
        observation["fetched_at"] = fetched_at
        observation["observed_at"] = datetime.now(timezone.utc)
        await self.provider_observations.update_one(
            {"provider": observation["provider"], "fetched_at": fetched_at},
            {"$set": observation},
            upsert=True,
        )

    @staticmethod
    def _safe_provider_metadata(value: Any) -> Dict[str, Any]:
        """Keep provider observation metadata scalar and free of user payloads."""

        if not isinstance(value, dict):
            return {}
        denied_fragments = (
            "secret", "token", "cookie", "password", "prompt", "content",
            "message", "query", "user", "chat", "raw", "body", "payload",
        )
        safe: Dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key).strip().lower()[:64]
            if not key or any(fragment in key for fragment in denied_fragments):
                continue
            if isinstance(raw_value, (str, int, float, bool)):
                safe[key] = raw_value if not isinstance(raw_value, str) else raw_value[:256]
            elif isinstance(raw_value, list):
                values = [item for item in raw_value[:16] if isinstance(item, (str, int, float, bool))]
                safe[key] = [item[:128] if isinstance(item, str) else item for item in values]
        return safe

    async def get_quote_templates(self, user_id: int) -> List[Dict]:
        if Database._db is None:
            return []
        cursor = self.quote_templates.find({"user_id": user_id}).sort("updated_at", -1).limit(50)
        return await cursor.to_list(length=50)

    async def get_quote_template(self, user_id: int, name: str) -> Optional[Dict]:
        from src.quotes.options import normalize_template_name

        try:
            name = normalize_template_name(name)
        except ValueError:
            return None
        if Database._db is None:
            return None
        return await self.quote_templates.find_one({"user_id": user_id, "name": name})

    async def delete_quote_template(self, user_id: int, name: str) -> int:
        from src.quotes.options import normalize_template_name

        try:
            name = normalize_template_name(name)
        except ValueError:
            return 0
        if Database._db is None:
            return 0
        result = await self.quote_templates.delete_one({"user_id": user_id, "name": name})
        return int(result.deleted_count)

    async def delete_quotes(self, chat_id: int, limit: Optional[int] = None) -> int:
        """Deletes quotes from a chat. If limit is None, deletes all."""
        if limit is None:
            result = await self.quotes.delete_many({"chat_id": chat_id})
            return result.deleted_count
        else:
            # Delete last N quotes
            cursor = self.quotes.find({"chat_id": chat_id}).sort("created_at", -1).limit(limit)
            docs = await cursor.to_list(length=limit)
            ids = [doc["_id"] for doc in docs]
            result = await self.quotes.delete_many({"_id": {"$in": ids}})
            return result.deleted_count

    async def get_quotes(self, chat_id: int, limit: int = 5) -> List[Dict]:
        cursor = self.quotes.find({"chat_id": chat_id}).sort("created_at", -1).limit(limit)
        return await cursor.to_list(length=limit)

    async def get_top_quotes(self, chat_id: int, offset: int = 0, limit: int = 50) -> List[Dict]:
        """Fetches top quotes for a chat sorted by rating."""
        cursor = self.quotes.find({"chat_id": chat_id, "sticker_file_id": {"$ne": None}})\
                           .sort("rating", -1)\
                           .skip(offset)\
                           .limit(limit)
        return await cursor.to_list(length=limit)

    async def get_random_quote(self, chat_id: int) -> Optional[Dict]:
        pipeline = [
            {"$match": {"chat_id": chat_id, "sticker_file_id": {"$ne": None}}},
            {"$sample": {"size": 1}}
        ]
        cursor = self.quotes.aggregate(pipeline)
        results = await cursor.to_list(length=1)
        return results[0] if results else None

    async def update_quote_rating(self, sticker_file_id: str, field: str, inc: int = 1):
        await self.quotes.update_one(
            {"sticker_file_id": sticker_file_id},
            {"$inc": {field: inc}}
        )

    async def get_latest_quote_by_user(self, chat_id: int, user_id: int) -> Optional[Dict]:
        """Finds the most recent quote for a specific user in a chat."""
        return await self.quotes.find_one(
            {"chat_id": chat_id, "user_id": user_id},
            sort=[("created_at", -1)]
        )

    # --- STICKER PACK OPERATIONS ---
    async def create_sticker_pack(self, pack_data: Dict):
        """Persist a Telegram sticker pack and return its durable id.

        Telegram may already have created the remote set when this method is
        called.  Silently returning while MongoDB is unavailable therefore
        leaves an orphaned pack that can never be found through ``/packs``.
        Persistence is an explicit success boundary now: callers receive a
        stable error instead of claiming that the operation completed.
        """
        if Database._db is None:
            raise RuntimeError("MONGODB_UNAVAILABLE")
        document = dict(pack_data or {})
        document.setdefault("created_at", datetime.now(timezone.utc))
        document.setdefault("is_public", True)
        document["user_id"] = int(document["user_id"])
        result = await self.sticker_packs.insert_one(document)
        if not getattr(result, "inserted_id", None):
            raise RuntimeError("STICKER_PACK_PERSISTENCE_UNAVAILABLE")
        return result.inserted_id

    async def get_user_packs(self, user_id: int, pack_type: str = None) -> List[Dict]:
        if Database._db is None:
            raise RuntimeError("MONGODB_UNAVAILABLE")
        normalized_user_id = int(user_id)
        # The string alternative keeps packs written by older releases
        # discoverable while all new writes use Telegram's numeric id.
        query = {"user_id": {"$in": [normalized_user_id, str(normalized_user_id)]}}
        if pack_type: query["pack_type"] = pack_type
        cursor = self.sticker_packs.find(query).sort("created_at", -1)
        return await cursor.to_list(length=50)

    async def get_top_packs(self, limit: int = 5) -> List[Dict]:
        """Fetches top public sticker packs by rating."""
        cursor = self.sticker_packs.find({"is_public": True})\
                                   .sort([("rating", -1), ("created_at", -1)])\
                                   .limit(limit)
        return await cursor.to_list(length=limit)

    async def get_pack_by_name(self, pack_name: str, user_id: Optional[int] = None) -> Optional[Dict]:
        query = {"pack_name": pack_name}
        if user_id: query["user_id"] = user_id
        return await self.sticker_packs.find_one(query)

    async def get_pack_by_id(self, pack_id: Any, user_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Return a sticker pack by its repository identifier."""
        query: dict[str, Any] = {"_id": pack_id}
        if user_id is not None:
            query["user_id"] = int(user_id)
        return await self.sticker_packs.find_one(query)

    async def search_stickers(self, query_text: str, limit: int = 10, offset: int = 0) -> List[Dict]:
        """Searches stickers by tags or associated pack title."""
        safe_query = re.escape(str(query_text)[:96])
        cursor = Database._db["stickers"].find({
            "$or": [
                {"tags": {"$regex": safe_query, "$options": "i"}},
                {"emojis": {"$regex": safe_query, "$options": "i"}}
            ]
        }).skip(max(0, int(offset))).limit(max(1, min(int(limit), 50)))
        return await cursor.to_list(length=limit)

    async def delete_sticker_from_db(self, pack_id: Any, file_id: str):
        """Removes a sticker from the stickers collection."""
        await Database._db["stickers"].delete_one({"pack_id": pack_id, "file_id": file_id})

    async def update_sticker_pack(self, pack_name: str, user_id: int, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update only owner-editable pack metadata and return the new record."""
        if Database._db is None:
            return None
        allowed = {"pack_title", "default_emojis", "thumbnail_file_id", "is_public"}
        safe_updates = {key: value for key, value in dict(updates or {}).items() if key in allowed}
        if not safe_updates:
            return await self.sticker_packs.find_one({"pack_name": pack_name, "user_id": int(user_id)})
        await self.sticker_packs.update_one(
            {"pack_name": str(pack_name), "user_id": int(user_id)},
            {"$set": safe_updates},
        )
        return await self.sticker_packs.find_one({"pack_name": str(pack_name), "user_id": int(user_id)})

    async def update_sticker_emojis(self, pack_name: str, user_id: int, file_id: str, emojis: list[str]) -> None:
        if Database._db is None:
            return
        pack = await self.get_pack_by_name(pack_name, user_id=int(user_id))
        if pack:
            await Database._db["stickers"].update_one(
                {"pack_id": pack.get("_id"), "file_id": str(file_id)},
                {"$set": {"emojis": list(emojis), "updated_at": datetime.utcnow()}},
            )

    async def update_sticker_position(
        self, pack_name: str, user_id: int, file_id: str, position: int
    ) -> None:
        """Persist Telegram's successful sticker order change."""
        if Database._db is None:
            return
        pack = await self.get_pack_by_name(pack_name, user_id=int(user_id))
        if pack:
            await Database._db["stickers"].update_one(
                {"pack_id": pack.get("_id"), "file_id": str(file_id)},
                {"$set": {"position": int(position), "updated_at": datetime.utcnow()}},
            )

    async def delete_sticker_pack(self, pack_name: str, user_id: int) -> bool:
        if Database._db is None:
            return False
        pack = await self.get_pack_by_name(pack_name, user_id=int(user_id))
        if not pack:
            return False
        result = await self.sticker_packs.delete_one({"_id": pack.get("_id"), "user_id": int(user_id)})
        await Database._db["stickers"].delete_many({"pack_id": pack.get("_id")})
        return bool(result.deleted_count)

    async def register_sticker(self, pack_id: Any, file_id: str, emojis: str, tags: Optional[list[str]] = None) -> None:
        if Database._db is None:
            return
        await Database._db["stickers"].update_one(
            {"pack_id": pack_id, "file_id": file_id},
            {"$set": {"pack_id": pack_id, "file_id": file_id, "emojis": emojis,
                       "tags": tags or [], "timestamp": datetime.utcnow()}},
            upsert=True,
        )

    async def get_sticker_in_pack(self, pack_id: Any, file_id: str) -> Optional[Dict[str, Any]]:
        """Find a catalog entry used as the idempotency boundary for pack adds."""
        if Database._db is None:
            return None
        return await self.stickers.find_one({"pack_id": pack_id, "file_id": str(file_id)})

    @staticmethod
    def _sticker_operation_key(pack_id: Any, file_id: str) -> str:
        raw = f"{pack_id}:{str(file_id)}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    async def claim_sticker_add(self, pack_id: Any, file_id: str, user_id: int) -> Dict[str, Any]:
        """Atomically reserve a remote sticker add across workers."""
        if Database._db is None:
            return {"status": "claimed"}
        key = self._sticker_operation_key(pack_id, file_id)
        now = datetime.utcnow()
        document = {
            "operation_key": key,
            "pack_id": pack_id,
            "file_id": str(file_id),
            "user_id": int(user_id),
            "status": "pending",
            "created_at": now,
            "updated_at": now,
        }
        try:
            result = await self.sticker_operations.update_one(
                {"operation_key": key}, {"$setOnInsert": document}, upsert=True
            )
            if getattr(result, "upserted_id", None) is not None:
                return {**document, "status": "claimed"}
        except Exception:
            # A concurrent upsert can surface as a duplicate-key exception;
            # reading the durable row gives both workers the same decision.
            logger.warning("Sticker add claim race or Mongo error", exc_info=True)
        existing = await self.sticker_operations.find_one({"operation_key": key})
        if existing:
            return dict(existing)
        # Fail closed if Mongo returned neither an insert result nor a row.
        return {"status": "pending", "operation_key": key}

    async def complete_sticker_add(self, pack_id: Any, file_id: str, *, catalog_registered: bool) -> None:
        if Database._db is None:
            return
        key = self._sticker_operation_key(pack_id, file_id)
        await self.sticker_operations.update_one(
            {"operation_key": key},
            {
                "$set": {
                    "status": "completed",
                    "catalog_registered": bool(catalog_registered),
                    "updated_at": datetime.utcnow(),
                }
            },
            upsert=True,
        )

    async def update_sticker_tags(self, file_id: str, tags: Any) -> None:
        if Database._db is None:
            return
        await Database._db["stickers"].update_one({"file_id": file_id}, {"$set": {"tags": tags}})

    async def get_sticker_by_file_id(self, file_id: str) -> Optional[Dict[str, Any]]:
        """Return one sticker catalog record without exposing Mongo internals."""
        if Database._db is None:
            return None
        return await Database._db["stickers"].find_one({"file_id": str(file_id)}, {"_id": 0})

    # --- MESSAGE & USER UTILITIES ---
    async def get_recent_active_user_names(self, chat_id: int, limit: int = 20) -> List[str]:
        """Returns names of users who recently sent messages in a chat."""
        pipeline = [
            {"$match": {"chat_id": chat_id, "role": "user"}},
            {"$sort": {"timestamp": -1}},
            {"$limit": limit * 2}, # Get more to account for duplicates
            {
                "$lookup": {
                    "from": "users",
                    "localField": "user_id",
                    "foreignField": "_id",
                    "as": "user_info"
                }
            },
            {"$unwind": "$user_info"},
            {"$group": {"_id": "$user_info.first_name"}},
            {"$limit": limit}
        ]
        results = await self.messages.aggregate(pipeline).to_list(length=limit)
        return [r["_id"] for r in results]

    async def get_first_message(self, chat_id: int, user_id: int | None = None) -> Optional[Dict[str, Any]]:
        """Return the first retained user message for a chat/member.

        Guardian's member-intelligence commands are intentionally backed by
        Arel's 30-day message retention.  The query returns metadata and a
        bounded content preview; callers must still escape it for Telegram.
        """
        if Database._db is None:
            return None
        query: Dict[str, Any] = {"chat_id": int(chat_id), "role": "user"}
        if user_id is not None:
            query["user_id"] = int(user_id)
        return await self.messages.find_one(query, {"_id": 0}, sort=[("timestamp", 1), ("_id", 1)])

    async def get_member_message_log(self, chat_id: int, user_id: int, limit: int = 20) -> List[Dict[str, Any]]:
        if Database._db is None:
            return []
        bounded = max(1, min(int(limit), 100))
        cursor = self.messages.find(
            {"chat_id": int(chat_id), "user_id": int(user_id), "role": "user"},
            {"_id": 0, "content": 1, "timestamp": 1, "telegram_message_id": 1, "thread_id": 1},
        ).sort([("timestamp", -1), ("_id", -1)]).limit(bounded)
        rows = await cursor.to_list(length=bounded)
        return list(reversed(rows))

    async def get_inactive_users(self, chat_id: int, since: datetime, limit: int = 50) -> List[Dict[str, Any]]:
        """Return users whose last retained message predates ``since``.

        Telegram Bot API does not expose a complete member list, so the
        reliable bounded alternative is the activity index Arel already owns.
        """
        if Database._db is None:
            return []
        bounded = max(1, min(int(limit), 100))
        pipeline = [
            {"$match": {"chat_id": int(chat_id), "role": "user"}},
            {"$group": {"_id": "$user_id", "last_seen": {"$max": "$timestamp"}}},
            {"$match": {"last_seen": {"$lt": since}}},
            {"$sort": {"last_seen": 1}},
            {"$limit": bounded},
        ]
        return await self.messages.aggregate(pipeline).to_list(length=bounded)

    # --- CACHE OPERATIONS ---
    async def save_creation_cache(self, unique_id: str, data: Any):
        await Database._db["creation_cache"].update_one(
            {"_id": unique_id},
            {"$set": {"data": data, "created_at": datetime.utcnow()}},
            upsert=True
        )

    async def get_creation_cache(self, unique_id: str) -> Optional[Any]:
        doc = await Database._db["creation_cache"].find_one({"_id": unique_id})
        return doc["data"] if doc else None

    async def get_history(
        self,
        chat_id: int,
        limit: int = 15,
        *,
        user_id: int | None = None,
        thread_id: int | None = None,
        since: datetime | None = None,
        roles: list[str] | tuple[str, ...] | None = None,
    ) -> List[Dict]:
        """Return bounded, deterministic chronological history.

        Optional filters support targeted group context without changing the
        legacy ``get_history(chat_id, limit)`` call contract.
        """
        query: Dict[str, Any] = {"chat_id": int(chat_id)}
        if user_id is not None:
            query["user_id"] = int(user_id)
        if thread_id is not None:
            query["thread_id"] = int(thread_id)
        if since is not None:
            query["timestamp"] = {"$gte": since}
        if roles:
            query["role"] = {"$in": [str(role) for role in roles]}
        bounded_limit = max(1, min(int(limit), 100))
        cursor = self.messages.find(query).sort([("timestamp", -1), ("_id", -1)]).limit(bounded_limit)
        msgs = await cursor.to_list(length=bounded_limit)
        return list(reversed(msgs))
    async def get_recent_messages_with_users(self, chat_id: int, limit: int = 150) -> List[Dict]:
        """Returns recent messages joined with user data (simulating a join)."""
        pipeline = [
            {"$match": {"chat_id": chat_id, "role": "user"}},
            {"$sort": {"timestamp": -1}},
            {"$limit": limit},
            {
                "$lookup": {
                    "from": "users",
                    "localField": "user_id",
                    "foreignField": "_id",
                    "as": "user_info"
                }
            },
            {"$unwind": {"path": "$user_info", "preserveNullAndEmptyArrays": True}}
        ]
        cursor = self.messages.aggregate(pipeline)
        return await cursor.to_list(length=limit)

    async def get_messages_by_ids(self, message_ids: List[str]) -> List[Dict]:
        """Fetch multiple messages by their MongoDB ObjectIds string or int IDs."""
        from bson import ObjectId
        converted_ids = []
        for mid in message_ids:
            try:
                converted_ids.append(ObjectId(mid))
            except:
                # If it's a numeric ID (legacy or custom)
                if isinstance(mid, str) and mid.isdigit():
                    converted_ids.append(int(mid))
                else:
                    converted_ids.append(mid)

        pipeline = [
            {"$match": {"_id": {"$in": converted_ids}}},
            {
                "$lookup": {
                    "from": "users",
                    "localField": "user_id",
                    "foreignField": "_id",
                    "as": "user_info"
                }
            },
            {"$unwind": {"path": "$user_info", "preserveNullAndEmptyArrays": True}},
            {"$sort": {"timestamp": 1}}
        ]
        cursor = self.messages.aggregate(pipeline)
        return await cursor.to_list(length=len(message_ids))

    async def get_sequential_messages(self, chat_id: int, start_timestamp: datetime, limit: int) -> List[Dict]:
        """Fetches messages following a specific timestamp in a chat."""
        pipeline = [
            {"$match": {"chat_id": chat_id, "timestamp": {"$gte": start_timestamp}, "role": "user"}},
            {"$sort": {"timestamp": 1}},
            {"$limit": limit},
            {
                "$lookup": {
                    "from": "users",
                    "localField": "user_id",
                    "foreignField": "_id",
                    "as": "user_info"
                }
            },
            {"$unwind": {"path": "$user_info", "preserveNullAndEmptyArrays": True}}
        ]
        cursor = self.messages.aggregate(pipeline)
        return await cursor.to_list(length=limit)

    async def find_latest_message_for_quote(self, chat_id: int, user_id: int, content: str) -> Optional[Dict[str, Any]]:
        """Find the latest stored user message matching a quote source."""
        if Database._db is None:
            return None
        return await self.messages.find_one(
            {"chat_id": int(chat_id), "user_id": int(user_id), "content": str(content)},
            sort=[("timestamp", -1)],
        )

    # --- FACT OPERATIONS ---
    async def save_fact(self, user_id: int, key: str, value: str):
        key = str(key).strip()[:80]
        if not key:
            raise ValueError("fact_key_required")
        if not str(value or "").strip():
            raise ValueError("fact_value_required")
        await self.facts.update_one(
            {"user_id": user_id, "key": key},
            {"$set": {
                "fact_id": self._fact_id(user_id, key),
                "value": str(value)[:500],
                "timestamp": datetime.utcnow(),
            }},
            upsert=True
        )

    async def get_facts(self, user_id: int, key_query: str = None) -> List[Dict]:
        query = {"user_id": user_id}
        if key_query and key_query != "all":
            query["key"] = {"$regex": key_query, "$options": "i"}
        cursor = self.facts.find(query)
        return await cursor.to_list(length=100)

    # --- ANALYTICS ---
    async def get_top_users(self, chat_id: int, limit: int = 5) -> List[Dict]:
        pipeline = [
            {"$match": {"chat_id": chat_id, "role": "user"}},
            {"$group": {"_id": "$user_id", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": limit}
        ]
        results = await self.messages.aggregate(pipeline).to_list(length=limit)
        
        top_users = []
        for r in results:
            user = await self.get_user(r["_id"])
            name = user.get("first_name") if user else f"ID:{r['_id']}"
            top_users.append({"name": name, "count": r["count"]})
        return top_users

    async def get_total_messages(self, chat_id: int) -> int:
        return await self.messages.count_documents({"chat_id": chat_id})

    async def get_group_analytics(self, chat_id: int, *, since: datetime, limit: int = 5) -> Dict[str, Any]:
        """Return bounded, repository-owned group activity aggregates."""
        if Database._db is None:
            return {"total_messages": 0, "active_users": 0, "peak_hour": None, "top_users": []}
        match = {"chat_id": int(chat_id), "role": "user", "timestamp": {"$gte": since}}
        total_messages = int(await self.messages.count_documents(match))
        user_pipeline = [
            {"$match": {**match, "user_id": {"$ne": None}}},
            {"$group": {"_id": "$user_id", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": max(1, min(int(limit), 10))},
        ]
        user_rows = await self.messages.aggregate(user_pipeline).to_list(length=max(1, min(int(limit), 10)))
        active_pipeline = [
            {"$match": {**match, "user_id": {"$ne": None}}},
            {"$group": {"_id": "$user_id"}},
            {"$count": "active_users"},
        ]
        active_rows = await self.messages.aggregate(active_pipeline).to_list(length=1)
        top_users: list[Dict[str, Any]] = []
        for row in user_rows:
            user = await self.get_user(row.get("_id"))
            name = (user or {}).get("first_name") or (user or {}).get("username") or f"ID:{row.get('_id')}"
            top_users.append({"user_id": row.get("_id"), "name": str(name), "count": int(row.get("count", 0))})

        peak_pipeline = [
            {"$match": match},
            {"$project": {"hour": {"$hour": {"date": "$timestamp", "timezone": "Europe/Istanbul"}}}},
            {"$group": {"_id": "$hour", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 1},
        ]
        peak_rows = await self.messages.aggregate(peak_pipeline).to_list(length=1)
        return {
            "total_messages": total_messages,
            "active_users": int(active_rows[0]["active_users"]) if active_rows else 0,
            "peak_hour": peak_rows[0].get("_id") if peak_rows else None,
            "top_users": top_users,
        }

    async def get_active_chat_ids(self) -> List[int]:
        """Returns a list of all chat IDs present in the chats collection."""
        cursor = self.chats.find({}, {"_id": 1})
        results = await cursor.to_list(length=1000)
        return [r["_id"] for r in results]

    # Backward compatibility stub (to avoid crashes while refactoring other files)
    def get_session(self):
        logger.warning("get_session() called on MongoDB-backed Database. returning self for dummy context.")
        return self
    
    # Context manager stubs for SQLAlchemy-style 'with session' usage
    def __enter__(self): return self
    def __exit__(self, exc_type, exc_val, exc_tb): pass
    def close(self): pass
    def commit(self): pass
    def rollback(self): pass
    def query(self, *args, **kwargs): 
        logger.error("SQLAlchemy query() called on MongoDatabase! This needs refactoring.")
        return self
    def filter_by(self, *args, **kwargs): return self
    def first(self): return None
    def all(self): return []
