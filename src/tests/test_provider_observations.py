from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.database import Database


class _Collection:
    def __init__(self) -> None:
        self.updates: list[tuple[dict, dict]] = []

    async def update_one(self, query, update, *, upsert=False):
        assert upsert is True
        self.updates.append((dict(query), dict(update)))


class _Mongo:
    def __init__(self) -> None:
        self.collections: dict[str, _Collection] = {}

    def __getitem__(self, name: str) -> _Collection:
        return self.collections.setdefault(name, _Collection())


@pytest.mark.asyncio
async def test_provider_health_persists_allowlisted_source_observation():
    previous_db = Database._db
    previous_client = Database._client
    fake_db = _Mongo()
    Database._db = fake_db
    Database._client = False
    try:
        fetched_at = datetime.now(timezone.utc)
        database = Database.__new__(Database)
        await database.save_provider_health(
            {
                "name": "weather_provider",
                "healthy": True,
                "source": "wttr.in",
                "source_url": "https://wttr.in/istanbul",
                "last_fetched_at": fetched_at,
                "last_cache_hit": False,
                "last_stale": False,
                "last_metadata": {
                    "records": 1,
                    "freshness": "fresh",
                    "request_query": "private value",
                    "api_token": "must-not-persist",
                },
                "detail": "internal runtime detail",
                "secret": "must-not-persist",
            }
        )

        latest = fake_db["provider_health"].updates
        observations = fake_db["provider_observations"].updates
        assert len(latest) == 1
        assert len(observations) == 1
        latest_document = latest[0][1]["$set"]
        assert "api_token" not in latest_document["last_metadata"]
        assert "request_query" not in latest_document["last_metadata"]
        query, update = observations[0]
        assert query["provider"] == "weather_provider"
        assert query["fetched_at"] == fetched_at
        document = update["$set"]
        assert document["source_url"] == "https://wttr.in/istanbul"
        assert document["last_metadata"]["records"] == 1
        assert "request_query" not in document["last_metadata"]
        assert "api_token" not in document["last_metadata"]
        assert "secret" not in document
        assert "detail" not in document
        assert document["observed_at"].tzinfo is not None
    finally:
        Database._db = previous_db
        Database._client = previous_client
