"""Durable roulette outcome, partial reward retry and terminal panel delivery."""
from copy import deepcopy

import pytest

from src.services.roulette_service import RouletteService


class Database:
    available = True

    def __init__(self):
        self.rows = {}

    async def get_game_state(self, chat_id, game_type):
        return deepcopy(self.rows.get((chat_id, game_type)))

    async def save_game_state(self, chat_id, game_type, state):
        self.rows[(chat_id, game_type)] = deepcopy(state)

    async def delete_game_state(self, chat_id, game_type):
        self.rows.pop((chat_id, game_type), None)


class Ledger:
    def __init__(self):
        self.events = set()

    async def record_score(self, *, event_id, **_):
        if event_id in self.events:
            return False
        self.events.add(event_id)
        return True


class Coins:
    def __init__(self, *, fail_once=False, capped=False):
        self.events = set()
        self.fail_once = fail_once
        self.capped = capped

    async def credit(self, user_id, chat_id, amount, *, reason, event_id):
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("temporary economy outage")
        if self.capped:
            return False
        if event_id in self.events:
            return False
        self.events.add(event_id)
        return True


async def finish(monkeypatch, db, ledger, coins):
    svc = RouletteService(db, leaderboard_service=ledger, economy_service=coins)
    await svc.create_lobby(chat_id=-100, owner_id=1, owner_name="<Bir>&")
    await svc.join(chat_id=-100, user_id=2, user_name="İki", action_id="join-2")
    await svc.start(chat_id=-100, user_id=1, action_id="start-1")
    monkeypatch.setattr("src.services.roulette_service.RouletteGame.pull_trigger",
                        lambda self, state, player_name: {
                            "hit": True, "message": f"💥 {player_name} elendi", "player": player_name})
    return svc, await svc.pull(chat_id=-100, user_id=1, action_id="pull-1")


@pytest.mark.asyncio
async def test_partial_coin_outage_persists_result_and_restart_replays_once(monkeypatch):
    db, ledger, coins = Database(), Ledger(), Coins(fail_once=True)
    svc, result = await finish(monkeypatch, db, ledger, coins)
    assert result["status"] == "finished" and result["winner_id"] == 2
    assert "&lt;Bir&gt;&amp;" in result["text"]
    row = await db.get_game_state(-100, "roulette")
    assert row["phase"] == "finished" and row["score_pending"]
    assert row["panel_dirty"] and row["turn_expires_at"] is None
    assert len(ledger.events) == 1 and not coins.events
    with pytest.raises(ValueError, match="game_not_playing"):
        await svc.cancel(chat_id=-100, user_id=1)
    assert (await db.get_game_state(-100, "roulette"))["score_pending"]
    with pytest.raises(ValueError, match="result_pending"):
        # The new instance first retries the coin, then protects the undelivered result.
        await RouletteService(db, leaderboard_service=ledger, economy_service=coins).create_lobby(
            chat_id=-100, owner_id=3, owner_name="Üç")
    row = await db.get_game_state(-100, "roulette")
    assert not row["score_pending"] and len(ledger.events) == len(coins.events) == 1
    restarted = RouletteService(db, leaderboard_service=ledger, economy_service=coins)
    claim = await restarted.claim_result_panel(chat_id=-100, now=1000)
    assert claim and restarted.result_text(claim) == result["text"]
    assert await restarted.claim_result_panel(chat_id=-100, now=1001) is None
    assert await restarted.acknowledge_result_panel(chat_id=-100,
        game_id=row["game_id"], message_id=91)
    assert not (await db.get_game_state(-100, "roulette"))["panel_dirty"]
    assert await restarted.settle_pending(chat_id=-100)
    fresh = await restarted.create_lobby(chat_id=-100, owner_id=3, owner_name="Üç")
    assert fresh["state"]["game_id"] != row["game_id"]
    assert len(ledger.events) == len(coins.events) == 1


@pytest.mark.asyncio
async def test_reward_outage_blocks_new_lobby_until_recovered(monkeypatch):
    db, ledger, coins = Database(), Ledger(), Coins(fail_once=True)
    _, result = await finish(monkeypatch, db, ledger, coins)
    coins.fail_once = True
    restarted = RouletteService(db, leaderboard_service=ledger, economy_service=coins)
    with pytest.raises(ValueError, match="reward_pending"):
        await restarted.create_lobby(chat_id=-100, owner_id=3, owner_name="Üç")
    assert (await db.get_game_state(-100, "roulette"))["score_pending"]
    assert len(ledger.events) == 1 and not coins.events
    await restarted.settle_pending(chat_id=-100)
    assert len(ledger.events) == len(coins.events) == 1


@pytest.mark.asyncio
async def test_restart_without_economy_does_not_clear_persisted_coin_obligation(monkeypatch):
    db, ledger, coins = Database(), Ledger(), Coins(fail_once=True)
    await finish(monkeypatch, db, ledger, coins)
    worker = RouletteService(db, leaderboard_service=ledger, economy_service=None)
    with pytest.raises(RuntimeError, match="game_reward_store_unavailable"):
        await worker.settle_pending(chat_id=-100)
    assert (await db.get_game_state(-100, "roulette"))["score_pending"]
    await RouletteService(db, leaderboard_service=ledger,
                          economy_service=coins).settle_pending(chat_id=-100)
    assert len(coins.events) == 1


@pytest.mark.asyncio
async def test_policy_cap_false_marks_reward_settled_without_replay(monkeypatch):
    db, ledger, coins = Database(), Ledger(), Coins(capped=True)
    _, result = await finish(monkeypatch, db, ledger, coins)
    assert result["status"] == "finished"
    row = await db.get_game_state(-100, "roulette")
    assert not row["score_pending"] and row["panel_dirty"]
    assert len(ledger.events) == 1 and not coins.events
    assert not await RouletteService(db, leaderboard_service=ledger,
        economy_service=coins).acknowledge_result_panel(
            chat_id=-100, game_id="wrong", message_id=91)


@pytest.mark.asyncio
async def test_old_game_or_panel_callback_cannot_mutate_new_table():
    db = Database()
    service = RouletteService(db)
    first = await service.create_lobby(chat_id=-100, owner_id=1, owner_name="Bir")
    old_game = first["state"]["game_id"]
    assert await service.acknowledge_panel(chat_id=-100, game_id=old_game, message_id=111)
    await service.cancel(chat_id=-100, user_id=1,
                         game_id=old_game, panel_message_id=111)
    second = await service.create_lobby(chat_id=-100, owner_id=1, owner_name="Bir")
    new_game = second["state"]["game_id"]
    assert not await service.acknowledge_panel(chat_id=-100, game_id=old_game, message_id=111)
    assert await service.acknowledge_panel(chat_id=-100, game_id=new_game, message_id=222)
    for game_id, panel_id in ((old_game, 111), (new_game, 111), (None, 222)):
        with pytest.raises(ValueError, match="stale_game"):
            await service.join(chat_id=-100, user_id=2, user_name="İki",
                               action_id=f"stale-{game_id}-{panel_id}",
                               game_id=game_id, panel_message_id=panel_id)
    row = await db.get_game_state(-100, "roulette")
    assert row["players"] == [1] and row["panel_message_id"] == 222
    joined = await service.join(chat_id=-100, user_id=2, user_name="İki",
                                action_id="current-join", game_id=new_game,
                                panel_message_id=222)
    assert joined["status"] == "joined"
    await service.start(chat_id=-100, user_id=1, action_id="current-start",
                        game_id=new_game, panel_message_id=222)
    before = await db.get_game_state(-100, "roulette")
    with pytest.raises(ValueError, match="stale_game"):
        await service.pull(chat_id=-100, user_id=1, action_id="old-pull",
                           game_id=old_game, panel_message_id=111)
    assert await db.get_game_state(-100, "roulette") == before
