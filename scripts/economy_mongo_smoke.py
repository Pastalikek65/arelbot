"""Real Mongo economy acceptance, restricted to a disposable synthetic collection."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from motor.motor_asyncio import AsyncIOMotorClient

from config import Config
from src.database import Database
from src.services.economy_service import EconomyService


async def main():
    uri = urlsplit(Config.MONGODB_URI)
    if os.getenv('APP_ENV') != 'test' or uri.path != '/arel_bot_test' or uri.username != 'arel_test':
        raise RuntimeError('synthetic_test_database_required')
    client = AsyncIOMotorClient(Config.MONGODB_URI, serverSelectionTimeoutMS=5000)
    accounts = client['arel_bot_test']['economy_acceptance_' + uuid4().hex]
    games = client['arel_bot_test']['economy_games_acceptance_' + uuid4().hex]
    leases = client['arel_bot_test']['economy_leases_acceptance_' + uuid4().hex]

    class Scope:
        available = True
        economy_accounts = accounts

        def __getitem__(self, key):
            if key == 'schema_migrations':
                # The runner is inspected only; its tracking collection is
                # never used when calling this one migration directly.
                return None
            collections = {'economy_accounts': accounts, 'game_states': games,
                           'game_action_ledger': leases}
            if key not in collections:
                raise RuntimeError('unexpected_collection')
            return collections[key]

    previous = Database._db
    try:
        service = EconomyService(Scope(), allow_memory_fallback=False,
                                 intent_signing_key='synthetic-economy-acceptance')
        other = EconomyService(Scope(), allow_memory_fallback=False,
                               intent_signing_key='synthetic-economy-acceptance')
        await service._store.ensure_indexes()
        await service.credit(7, -100, 1000, reason='fixture_seed', event_id='seed')
        quote = await service.prepare_purchase(7, -100, 'merakli')
        bought = await asyncio.gather(*(item.purchase(7, -100, 'merakli', event_id=f'buy:{i}',
            confirmation_token=quote['token']) for i, item in enumerate((service, other))))
        assert sum(result['accepted'] for result in bought) == 1
        assert await service.balance(7, -100) == 925
        await service.equip(7, -100, 'merakli')
        assert (await other.snapshot(7, -100))['equipped'] == {'title': 'merakli'}
        moment = datetime(2026, 10, 22, 10, tzinfo=UTC)
        daily = await asyncio.gather(*(item.claim_daily(7, -100, now=moment) for item in (service, other)))
        assert sum(result['accepted'] for result in daily) == 1
        for index in range(8):
            await service.credit(7, -100, 25, reason='quiz_reward', event_id=f'quiz:{index}')
        assert await service.balance(7, -100) == 1100
        for index in range(505):
            await service.credit(8, -100, 1, reason='fixture_history', event_id=f'history:{index}')
        restarted = EconomyService(Scope(), allow_memory_fallback=False,
                                   intent_signing_key='synthetic-economy-acceptance')
        assert await restarted.was_applied(8, -100, 'history:0')
        assert not await restarted.credit(8, -100, 1, reason='fixture_history', event_id='history:0')
        assert await restarted.balance(8, -100) == 505
        assert len(await restarted.history(8, -100, 100)) == 100
        legacy = {'user_id': 9, 'chat_id': -100, 'balance': 543, 'inventory': ['pusula'],
                  'equipped': {'badge': 'pusula'}, 'applied_events': {
                      f'9:-100:legacy:{i}': {'event_key': f'9:-100:legacy:{i}', 'direction': 'credit',
                          'delta': 1, 'amount': 1, 'reason': 'fixture_legacy', 'at': moment}
                      for i in range(503)}}
        await accounts.insert_one(legacy)
        await games.insert_many([
            {'chat_id': -120, 'game_type': 'blackjack:7',
             'state': {'status': 'pending_bet'}, 'expires_at': moment},
            {'chat_id': -121, 'game_type': 'roulette',
             'state': {'phase': 'finished', 'panel_dirty': True}, 'expires_at': moment},
        ])
        Database._db = Scope()
        migration = next(row for row in Database.__new__(Database).migration_runner().migrations if row.version == 21)
        await migration.up(Scope())
        await migration.up(Scope())
        await migration.down(Scope())
        saved = await accounts.find_one({'user_id': 9, 'chat_id': -100})
        assert saved['balance'] == 543 and saved['inventory'] == ['pusula']
        assert saved['equipped'] == {'badge': 'pusula'} and len(saved['applied_events']) == 500
        assert len(saved['receipts']) == 3
        assert 'expires_at' not in await games.find_one({'chat_id': -120, 'game_type': 'blackjack:7'})
        assert 'expires_at' not in await games.find_one({'chat_id': -121, 'game_type': 'roulette'})
        database = Database.__new__(Database)
        clock = datetime(2026, 10, 22, 10, tzinfo=UTC)
        lease_key = '-100:blackjack_owner_v2:owner:7'
        await leases.create_index('action_key', unique=True)
        first_claim, rival_claim = await asyncio.gather(
            database.claim_game_lease(lease_key, 'owner-a', 60, clock),
            database.claim_game_lease(lease_key, 'owner-b', 60, clock))
        assert sorted((first_claim, rival_claim)) == [False, True]
        holder, rival = ('owner-a', 'owner-b') if first_claim else ('owner-b', 'owner-a')
        assert not await database.renew_game_lease(lease_key, rival, 60, clock)
        assert await database.renew_game_lease(lease_key, holder, 60,
                                               clock + timedelta(seconds=30))
        assert not await database.claim_game_lease(lease_key, rival, 60,
                                                    clock + timedelta(seconds=61))
        takeover = clock + timedelta(seconds=91)
        assert await database.claim_game_lease(lease_key, rival, 60, takeover)
        assert not await database.release_game_lease(lease_key, holder)
        assert not await database.renew_game_lease(lease_key, holder, 60, takeover)
        assert await database.release_game_lease(lease_key, rival)
        assert not await database.renew_game_lease(lease_key, rival, 60, takeover)
        assert await database.claim_game_lease(lease_key, holder, 60, takeover)
        await database.release_game_lease(lease_key, holder)

        await games.insert_one({'chat_id': -100, 'game_type': 'blackjack:7',
            'state': {'version': 1, 'status': 'pending_bet', 'economy_required': True},
            'expires_at': clock})
        await database.save_game_state(-100, 'blackjack:7',
            {'version': 1, 'status': 'pending_bet', 'economy_required': True})
        assert 'expires_at' not in await games.find_one({'chat_id': -100, 'game_type': 'blackjack:7'})
        assert await database.compare_and_save_game_state(-100, 'blackjack:7',
            {'version': 2, 'status': 'finished', 'settlement_pending': True}, expected_version=1)
        assert 'expires_at' not in await games.find_one({'chat_id': -100, 'game_type': 'blackjack:7'})
        await database.create_game_state_if_absent(-101, 'roulette',
            {'phase': 'finished', 'score_pending': True, 'panel_dirty': True})
        assert 'expires_at' not in await games.find_one({'chat_id': -101, 'game_type': 'roulette'})
        due = await database.list_due_game_states(now=clock.timestamp(), limit=100)
        assert {row['game_type'] for row in due} >= {'blackjack:7', 'roulette'}
        # Prove writes in different game services share one atomic budget.
        earned = await asyncio.gather(*(item.credit(10, -100, 25, reason='quiz_reward', event_id=f'parallel:{i}')
            for i, item in enumerate((service, other) * 4)))
        assert all(earned) and await service.balance(10, -100) == 150
        assert await service.balance(7, -101) == 0
        print(json.dumps({'ok': True, 'scope': 'isolated_synthetic_mongo_collection',
            'concurrent_purchase_single_charge': True, 'daily_single_claim': True,
            'shared_game_budget': 150, 'post_prune_restart_replay_rejected': True,
            'history_events': 505, 'migration21_balance_and_inventory_preserved': True,
            'cross_group_scope_preserved': True, 'lease_contention_and_takeover': True,
            'stale_lease_release_and_renew_rejected': True, 'financial_states_no_ttl': True}))
    finally:
        Database._db = previous
        await accounts.drop()
        await games.drop()
        await leases.drop()
        client.close()


if __name__ == '__main__':
    asyncio.run(main())
