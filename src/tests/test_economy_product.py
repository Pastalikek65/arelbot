"""Public economy rules must hold across retries, scopes and price confirmation."""
import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from src.games import economy_policy as policy
from src.services.economy_service import EconomyService


@pytest.mark.asyncio
async def test_daily_series_uses_turkish_midnight_and_resets_after_missing_day():
    service = EconomyService()
    first = datetime(2026, 10, 21, 20, 59, tzinfo=UTC)
    before = await service.claim_daily(1, -100, now=first)
    after = await service.claim_daily(1, -100, now=first + timedelta(minutes=2))
    repeat = await service.claim_daily(1, -100, now=first + timedelta(hours=3))
    skipped = await service.claim_daily(1, -100, now=first + timedelta(days=3))
    assert (before['amount'], after['amount'], repeat['amount'], skipped['amount']) == (25, 30, 0, 25)
    assert before['day'] == '2026-10-21' and after['day'] == '2026-10-22'
    assert after['streak'] == 2 and skipped['streak'] == 1


@pytest.mark.asyncio
async def test_shop_purchase_is_one_charge_even_for_concurrent_distinct_requests():
    service = EconomyService(intent_signing_key='synthetic-test-signing-key')
    await service.credit(1, -100, 500, reason='fixture_seed', event_id='seed')
    quote = await service.prepare_purchase(1, -100, 'merakli')
    results = await asyncio.gather(*(service.purchase(1, -100, 'merakli',
        confirmation_token=quote['token'], event_id=f'purchase:{i}') for i in range(2)))
    assert sum(result['accepted'] for result in results) == 1
    assert await service.balance(1, -100) == 425
    await service.equip(1, -100, 'merakli')
    snapshot = await service.snapshot(1, -100)
    assert snapshot['equipped'] == {'title': 'merakli'}
    assert (await service.snapshot(1, -101))['owned'] == []


@pytest.mark.parametrize('uid,cid,item', [(2, -100, 'merakli'), (1, -101, 'merakli'), (1, -100, 'strateji')])
@pytest.mark.asyncio
async def test_quote_cannot_authorize_another_account_or_product(uid, cid, item):
    service = EconomyService(intent_signing_key='synthetic-test-signing-key')
    quote = await service.prepare_purchase(1, -100, 'merakli')
    with pytest.raises(ValueError, match='economy_confirmation_invalid'):
        await service.purchase(uid, cid, item, confirmation_token=quote['token'], event_id='forged')


@pytest.mark.asyncio
async def test_expired_or_edited_price_quote_and_unowned_equipment_are_rejected():
    service = EconomyService(intent_signing_key='synthetic-test-signing-key')
    now = datetime(2026, 10, 22, tzinfo=UTC)
    quote = await service.prepare_purchase(1, -100, 'merakli', now=now)
    with pytest.raises(ValueError, match='economy_confirmation_invalid'):
        await service.purchase(1, -100, 'merakli', event_id='expired', confirmation_token=quote['token'],
                               now=now + timedelta(seconds=policy.CONFIRM_SECONDS))
    edited = quote['token'].split('.')
    edited[1] = '1'
    with pytest.raises(ValueError, match='economy_confirmation_invalid'):
        await service.purchase(1, -100, 'merakli', event_id='edited', confirmation_token='.'.join(edited), now=now)
    with pytest.raises(ValueError, match='economy_item_not_owned'):
        await service.equip(1, -100, 'tac')


@pytest.mark.asyncio
async def test_shared_game_budget_excludes_daily_and_stake_refunds():
    service = EconomyService()
    for i in range(8):
        assert await service.credit(1, -100, 25, reason='quiz_reward', event_id=f'quiz:{i}')
    assert await service.balance(1, -100) == 150
    snap = await service.snapshot(1, -100)
    assert snap['game_earned_today'] == snap['game_daily_limit'] == 150
    assert await service.credit(1, -100, 40_000, reason='blackjack_settlement', event_id='settle:test')
    assert await service.balance(1, -100) == 40_150
    with pytest.raises(ValueError, match='amount_out_of_range'):
        await service.credit(1, -100, 10_001, reason='fixture_seed', event_id='oversize')
    assert (await service.claim_daily(1, -100))['amount'] == 25


@pytest.mark.parametrize('bad', [True, 1.5, '12', -1])
@pytest.mark.asyncio
async def test_non_integer_or_negative_currency_is_never_rounded_into_money(bad):
    with pytest.raises(ValueError, match='amount_out_of_range'):
        await EconomyService().credit(1, -100, bad, reason='fixture_seed', event_id='bad')


def test_catalog_prices_fit_confirmation_callbacks_and_only_decorate_profile():
    assert len(policy.CATALOG) == 10
    assert all(0 < item.price <= 1500 and len(item.key) <= 8
               and item.slot in {'title', 'badge'} for item in policy.CATALOG.values())
    assert policy.next_midnight(datetime(2026, 10, 21, 22, tzinfo=UTC)).hour == 0
