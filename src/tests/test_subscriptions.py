from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.services.subscription_service import SubscriptionRequest, SubscriptionService
from src.storage.subscription_store import SubscriptionStore
from src.features.contracts import FeatureResult
from src.orchestrator import Orchestrator
from types import SimpleNamespace


def test_subscription_parser_validates_feature_city_and_interval():
    service = SubscriptionService(SubscriptionStore())
    request = service.parse(["hava", "İstanbul", "1sa"])
    assert request.feature_key == "weather"
    assert request.params == {"city": "İstanbul"}
    assert request.interval_seconds == 3600

    air_quality = service.parse(["hava", "kalite", "İstanbul", "3sa"])
    assert air_quality.feature_key == "air_quality"
    assert air_quality.params == {"city": "İstanbul"}
    assert air_quality.interval_seconds == 3 * 3600

    earthquake = service.parse(["deprem", "30dk"])
    assert earthquake.feature_key == "earthquakes"
    assert earthquake.params == {}

    crypto = service.parse(["kripto", "btc", "15dk"])
    assert crypto.feature_key == "crypto"
    assert crypto.params == {"symbol": "btc"}
    assert crypto.interval_seconds == 900

    with pytest.raises(ValueError, match="15dk"):
        service.parse(["hava", "Ankara", "10dk"])
    with pytest.raises(ValueError, match="desteklenmiyor"):
        service.parse(["ucak", "1sa"])


def test_subscription_parser_accepts_turkish_daily_and_weekly_words():
    service = SubscriptionService(SubscriptionStore())

    daily = service.parse(["hava", "istanbul", "1g\u00fcn"])
    assert daily.interval_seconds == 24 * 60 * 60

    daily_label = service.parse(["hava", "istanbul", "1g\u00fcnl\u00fck"])
    assert daily_label.interval_seconds == 24 * 60 * 60

    weekly = service.parse(["hava", "istanbul", "1haftal\u0131k"])
    assert weekly.interval_seconds == 7 * 24 * 60 * 60


def test_threshold_parser_accepts_turkish_currency_and_fuel_alerts():
    service = SubscriptionService(SubscriptionStore())
    currency = service.parse_threshold(["doviz", "usd", "40", "üstü"])
    fuel = service.parse_threshold(["akaryakit", "benzin", "50", "altı"])

    assert currency.feature_key == "currency"
    assert currency.condition == {"kind": "threshold", "metric": "usd", "operator": "above", "value": 40.0}
    assert fuel.condition["metric"] == "gasoline"
    assert fuel.condition["operator"] == "below"


def test_threshold_parser_accepts_crypto_and_normalizes_symbol():
    service = SubscriptionService(SubscriptionStore())
    crypto = service.parse_threshold(["kripto", "btc", "100000", "\u00fcst\u00fc"])
    assert crypto.feature_key == "crypto"
    assert crypto.condition == {
        "kind": "threshold",
        "metric": "bitcoin",
        "operator": "above",
        "value": 100000.0,
    }


def test_subscription_deduplication_fingerprint_is_stable_and_feature_scoped():
    service = SubscriptionService(SubscriptionStore())
    item = {"feature_key": "earthquakes", "dedupe_policy": "content"}
    first = {"earthquakes": [{"location": "A"}], "content": "same"}
    second = {"content": "same", "earthquakes": [{"location": "A"}]}
    deliver, fingerprint = service.should_deliver(item, first)
    assert deliver is True
    assert fingerprint
    item["last_fingerprint"] = fingerprint
    deliver_again, same = service.should_deliver(item, second)
    assert deliver_again is False
    assert same == fingerprint
    changed, changed_fingerprint = service.should_deliver(
        item, {"earthquakes": [{"location": "B"}], "content": "new"}
    )
    assert changed is True
    assert changed_fingerprint != fingerprint


@pytest.mark.asyncio
async def test_subscription_store_persists_last_fingerprint_on_completion():
    store = SubscriptionStore()
    item = await store.create(
        user_id=1,
        chat_id=1,
        feature_key="earthquakes",
        params={},
        interval_seconds=900,
        dedupe_policy="content",
    )
    await store.complete(
        item["subscription_id"],
        next_run_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        success=True,
        fingerprint="abc123",
    )
    assert store._items[item["subscription_id"]]["last_fingerprint"] == "abc123"


@pytest.mark.asyncio
async def test_existing_subscription_is_backfilled_with_dedupe_policy():
    store = SubscriptionStore()
    service = SubscriptionService(store)
    old = await store.create(
        user_id=2,
        chat_id=2,
        feature_key="earthquakes",
        params={},
        interval_seconds=900,
    )
    assert old["dedupe_policy"] == "none"
    refreshed = await service.create(2, 2, service.parse(["deprem", "30dk"]))
    assert refreshed["subscription_id"] == old["subscription_id"]
    assert refreshed["dedupe_policy"] == "content"


@pytest.mark.asyncio
async def test_threshold_alert_creation_deduplicates_same_condition():
    service = SubscriptionService(SubscriptionStore())
    request = service.parse_threshold(["doviz", "usd", "40", "üstü"])
    first = await service.create_threshold(7, -100, request)
    second = await service.create_threshold(7, -100, request)
    assert first["subscription_id"] == second["subscription_id"]
    assert second["condition"]["value"] == 40.0


@pytest.mark.asyncio
async def test_subscription_store_claim_is_idempotent_and_disable_is_scoped():
    store = SubscriptionStore()
    item = await store.create(
        user_id=42,
        chat_id=-100,
        feature_key="earthquakes",
        params={},
        interval_seconds=1800,
    )
    assert await store.claim(item["subscription_id"]) is True
    assert await store.claim(item["subscription_id"]) is False
    await store.complete(
        item["subscription_id"],
        next_run_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        success=True,
    )
    due = await store.due()
    assert len(due) == 1
    assert await store.disable(999, item["subscription_id"]) is False
    assert await store.disable(42, item["subscription_id"]) is True
    assert await store.due() == []


@pytest.mark.asyncio
async def test_subscription_store_rejects_invalid_interval_invariant():
    store = SubscriptionStore()

    with pytest.raises(ValueError, match="subscription_interval_out_of_range"):
        await store.create(
            user_id=1,
            chat_id=1,
            feature_key="weather",
            params={},
            interval_seconds=60,
        )


@pytest.mark.asyncio
async def test_expired_claim_is_recoverable_after_worker_restart():
    store = SubscriptionStore()
    item = await store.create(
        user_id=42,
        chat_id=-100,
        feature_key="earthquakes",
        params={},
        interval_seconds=900,
        next_run_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    assert await store.claim(item["subscription_id"]) is True
    assert await store.due() == []

    store._items[item["subscription_id"]]["claimed_at"] = datetime.now(timezone.utc) - timedelta(minutes=6)
    due = await store.due(claim_timeout_seconds=300)
    assert [row["subscription_id"] for row in due] == [item["subscription_id"]]
    assert await store.claim(item["subscription_id"], claim_timeout_seconds=300) is True


@pytest.mark.asyncio
async def test_delivery_ledger_is_idempotent_and_survives_send_boundary():
    store = SubscriptionStore()
    assert await store.claim_delivery(
        "sub-1:slot-1", subscription_id="sub-1", chat_id=-100
    ) is True
    assert await store.claim_delivery(
        "sub-1:slot-1", subscription_id="sub-1", chat_id=-100
    ) is False
    assert await store.delivery_was_sent("sub-1:slot-1") is False
    assert await store.mark_delivery_sent("sub-1:slot-1") is True
    assert await store.delivery_was_sent("sub-1:slot-1") is True
    # A restarted worker must not send a second Telegram message for the same
    # scheduled slot, even if the subscription row was never completed.
    assert await store.claim_delivery(
        "sub-1:slot-1", subscription_id="sub-1", chat_id=-100
    ) is False


@pytest.mark.asyncio
async def test_failed_delivery_key_can_be_reclaimed_for_retry():
    store = SubscriptionStore()
    assert await store.claim_delivery(
        "sub-2:slot-1", subscription_id="sub-2", chat_id=-100
    ) is True
    await store.release_delivery("sub-2:slot-1", "timeout")
    assert await store.claim_delivery(
        "sub-2:slot-1", subscription_id="sub-2", chat_id=-100
    ) is True


@pytest.mark.asyncio
async def test_same_weather_tracking_is_updated_instead_of_duplicated():
    service = SubscriptionService(SubscriptionStore())
    first = await service.create(7, -7, service.parse(["hava", "İst"] ))
    second_request = service.parse(["hava", "İst", "1sa"])
    second = await service.create(7, -7, second_request)

    assert first["subscription_id"] == second["subscription_id"]
    assert second["interval_seconds"] == 3600
    assert len(await service.store.list_for_user(7)) == 1


@pytest.mark.asyncio
async def test_subscription_service_rejects_unsupported_or_unsafe_intervals():
    service = SubscriptionService(SubscriptionStore())

    with pytest.raises(ValueError, match="15 dakika"):
        await service.create(
            7,
            7,
            SubscriptionRequest(
                feature_key="weather",
                params={"city": "istanbul"},
                interval_seconds=60,
                human_interval="1 dakika",
            ),
        )
    with pytest.raises(ValueError, match="desteklenmiyor"):
        await service.create(
            7,
            7,
            SubscriptionRequest(
                feature_key="unknown",
                params={},
                interval_seconds=900,
                human_interval="15 dakika",
            ),
        )


@pytest.mark.asyncio
async def test_subscription_defer_moves_due_item_without_claiming():
    store = SubscriptionStore()
    item = await store.create(
        user_id=7,
        chat_id=7,
        feature_key="weather",
        params={"city": "İstanbul"},
        interval_seconds=3600,
        next_run_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    next_run = datetime.now(timezone.utc) + timedelta(hours=6)
    assert await store.defer(item["subscription_id"], next_run) is True
    assert await store.due() == []
    saved = store._items[item["subscription_id"]]
    assert saved["next_run_at"] == next_run
    assert saved["delivery_claimed"] is False


@pytest.mark.asyncio
async def test_subscription_tick_sends_changed_feed_only_once():
    store = SubscriptionStore()
    item = await store.create(
        user_id=7,
        chat_id=-7,
        feature_key="earthquakes",
        params={},
        interval_seconds=900,
        scope="group",
        dedupe_policy="content",
    )

    class Executor:
        async def execute(self, feature_key, params):
            return FeatureResult.success(
                {"earthquakes": [{"location": "A", "magnitude": "2.0"}]},
                display_blocks=("Deprem A",),
                metadata={"fresh": True},
            )

    class Bot:
        def __init__(self):
            self.sent = []

        async def send_message(self, **kwargs):
            self.sent.append(kwargs)

    bot = Bot()
    orchestrator = object.__new__(Orchestrator)
    orchestrator.subscription_service = SubscriptionService(store)
    orchestrator.notification_preference_store = SimpleNamespace()
    orchestrator.chat_engine = SimpleNamespace(feature_executor=Executor())

    async def due_always(limit=100):
        return [dict(store._items[item["subscription_id"]])]

    store.due = due_always
    context = SimpleNamespace(bot=bot)
    await orchestrator._subscription_tick(context)
    await orchestrator._subscription_tick(context)

    assert len(bot.sent) == 1
    assert store._items[item["subscription_id"]]["last_fingerprint"]


@pytest.mark.asyncio
async def test_subscription_tick_recovers_after_send_before_row_completion():
    store = SubscriptionStore()
    item = await store.create(
        user_id=8,
        chat_id=-8,
        feature_key="earthquakes",
        params={},
        interval_seconds=900,
        scope="group",
        next_run_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    service = SubscriptionService(store)
    key = service.delivery_key(item)
    assert await store.claim_delivery(key, subscription_id=item["subscription_id"], chat_id=-8)
    assert await store.mark_delivery_sent(key)

    class Bot:
        def __init__(self):
            self.sent = []

        async def send_message(self, **kwargs):
            self.sent.append(kwargs)

    class Executor:
        async def execute(self, feature_key, params):
            raise AssertionError("sent ledger should bypass provider and Telegram")

    orchestrator = object.__new__(Orchestrator)
    orchestrator.subscription_service = service
    orchestrator.notification_preference_store = SimpleNamespace()
    orchestrator.chat_engine = SimpleNamespace(feature_executor=Executor())
    bot = Bot()
    await orchestrator._subscription_tick(SimpleNamespace(bot=bot))

    assert bot.sent == []
    assert await store.due() == []
