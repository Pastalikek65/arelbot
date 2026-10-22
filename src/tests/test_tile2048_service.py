"""Persistent 2048 match, delivery, replay and score contracts."""

import asyncio
import random

import pytest
from bson import BSON

from src.games import tile2048 as rules
from src.services.tile2048_service import Tile2048Service


class Repository:
    def __init__(self):
        self.rows = {}

    async def get_game_state(self, chat, key):
        row = self.rows.get((chat, key))
        return BSON(row).decode() if row is not None else None

    async def create_game_state_if_absent(self, chat, key, state):
        if (chat, key) in self.rows:
            return False
        self.rows[(chat, key)] = BSON.encode(state)
        return True

    async def compare_and_save_game_state(self, chat, key, state, *, expected_version):
        prior = await self.get_game_state(chat, key)
        if prior is None or prior["version"] != expected_version:
            return False
        self.rows[(chat, key)] = BSON.encode(state)
        return True


class Scores:
    def __init__(self):
        self.calls = []
        self.events = set()
        self.fail_once = False

    async def record_score(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("temporary")
        self.events.add(kwargs["event_id"])


class Coins:
    def __init__(self):
        self.events = set()

    async def credit(self, *args, **kwargs):
        self.events.add(kwargs["event_id"])


async def ready(service, *, topic=None, now=100):
    _, state = await service.open(game_key="2048", chat_id=-100,
        thread_id=topic, user_id=1, user_name="Oyuncu", now=now, rng=random.Random(3))
    claim = await service.claim_panel(game_key="2048", chat_id=-100,
                                      thread_id=topic, now=now)
    assert claim and await service.acknowledge_panel(game_key="2048", chat_id=-100,
        thread_id=topic, round_id=state["round_id"], expected_version=claim["version"],
        message_id=11, now=now)
    return await service.current(game_key="2048", chat_id=-100, thread_id=topic)


@pytest.mark.asyncio
async def test_two_instances_one_move_cas_and_replay_with_bson():
    repo = Repository()
    first, second = Tile2048Service(repo), Tile2048Service(repo)
    state = await ready(first, topic=7)
    direction = next(move for move in rules.DIRECTIONS if rules.slide(state["board"], move)[2])
    kwargs = {"game_key": "2048", "chat_id": -100, "thread_id": 7,
              "user_id": 1, "action_id": "callback-1", "action": "move",
              "round_id": state["round_id"], "phase_seq": state["phase_seq"],
              "direction": direction, "now": 101, "rng": random.Random(8)}
    results = await asyncio.gather(first.act(**kwargs), second.act(**kwargs))
    assert sorted(item[0] for item in results) == ["applied", "duplicate"]
    updated = await second.current(game_key="2048", chat_id=-100, thread_id=7)
    assert updated["moves"] == 1 and updated["deadline"] is None
    assert updated["panel_wait_until"] == 401
    assert len([cell for row in updated["board"] for cell in row if cell]) >= 2
    assert await first.current(game_key="2048", chat_id=-100) is None
    with pytest.raises(ValueError, match="t2_stale_phase"):
        await second.act(**dict(kwargs, action_id="callback-2"))
    with pytest.raises(ValueError, match="t2_not_owner"):
        await second.act(**dict(kwargs, action_id="intruder", user_id=2,
                                phase_seq=updated["phase_seq"]))


@pytest.mark.asyncio
async def test_failed_delivery_freezes_clock_late_ack_cancels_and_stale_ack_repairs():
    repo = Repository()
    service = Tile2048Service(repo)
    _, opened = await service.open(game_key="2048", chat_id=-100, user_id=1,
                                   user_name="X", now=100, rng=random.Random(4))
    claimed = await service.claim_panel(game_key="2048", chat_id=-100, now=100)
    assert await service.claim_panel(game_key="2048", chat_id=-100, now=101) is None
    state = await service.current(game_key="2048", chat_id=-100)
    assert state["deadline"] is None and state["panel_wait_until"] == 400
    assert not await service.acknowledge_panel(game_key="2048", chat_id=-100,
        round_id=opened["round_id"], expected_version=claimed["version"] - 1,
        message_id=99, now=102)
    state = await service.current(game_key="2048", chat_id=-100)
    assert state["panel_message_id"] is None and state["panel_dirty"]
    repaired = await service.claim_panel(game_key="2048", chat_id=-100, now=103)
    assert repaired
    assert not await service.acknowledge_panel(game_key="2048", chat_id=-100,
        round_id=opened["round_id"], expected_version=repaired["version"],
        message_id=11, now=400)
    expired = await service.current(game_key="2048", chat_id=-100)
    assert expired["phase"] == "cancelled" and expired["deadline"] is None
    assert not expired["score_pending"] and expired["panel_dirty"]


@pytest.mark.asyncio
async def test_full_win_restarts_reward_retry_and_new_match_keeps_event():
    repo, scores, coins = Repository(), Scores(), Coins()
    first = Tile2048Service(repo, leaderboard=scores, economy=coins)
    state = await ready(first)
    # A persisted late-game board exercises the real move, panel receipt, claim and ledger.
    state["board"] = [[1024, 1024, 0, 0], [0] * 4, [0] * 4, [0] * 4]
    state["best_tile"] = 1024
    state["version"] += 1
    assert await repo.compare_and_save_game_state(-100, "2048", state,
                                                  expected_version=state["version"] - 1)
    status, won = await first.act(game_key="2048", chat_id=-100, user_id=1,
        action_id="win", action="move", direction="left", round_id=state["round_id"],
        phase_seq=state["phase_seq"], now=101, rng=random.Random(5))
    assert status == "applied" and won["won"] and won["phase"] == "playing"
    claim = await first.claim_panel(game_key="2048", chat_id=-100, now=101)
    assert claim and await first.acknowledge_panel(game_key="2048", chat_id=-100,
        round_id=state["round_id"], expected_version=claim["version"],
        message_id=11, now=102)
    active = await first.current(game_key="2048", chat_id=-100)
    _, finished = await first.act(game_key="2048", chat_id=-100, user_id=1,
        action_id="claim", action="cashout", round_id=state["round_id"],
        phase_seq=active["phase_seq"], now=103)
    assert finished["phase"] == "finished" and finished["score_pending"]
    scores.fail_once = True
    with pytest.raises(RuntimeError, match="temporary"):
        await first.settle_scores(game_key="2048", chat_id=-100)
    pending = await first.current(game_key="2048", chat_id=-100)
    assert pending["score_pending"]
    restarted = Tile2048Service(repo, leaderboard=scores, economy=coins)
    await restarted.settle_scores(game_key="2048", chat_id=-100)
    await restarted.settle_scores(game_key="2048", chat_id=-100)
    assert len(scores.events) == len(coins.events) == 1
    assert len(scores.calls) == 2
    result, fresh = await restarted.open(game_key="2048", chat_id=-100,
        user_id=1, user_name="Oyuncu", now=110, rng=random.Random(2))
    assert result == "created" and fresh["round_id"] != state["round_id"]
    assert len(scores.events) == len(coins.events) == 1
    with pytest.raises(ValueError, match="t2_stale_round"):
        await restarted.act(game_key="2048", chat_id=-100, user_id=1,
            action_id="old", action="stop", round_id=state["round_id"], now=111)


@pytest.mark.asyncio
async def test_loss_has_score_but_no_victory_reward_and_timeout_cannot_payout():
    repo, scores, coins = Repository(), Scores(), Coins()
    service = Tile2048Service(repo, leaderboard=scores, economy=coins)
    state = await ready(service)
    state["board"] = [[2, 2, 8, 16], [64, 128, 256, 512],
                      [512, 1024, 64, 128], [128, 256, 512, 1024]]
    state["best_tile"] = 1024
    state["version"] += 1
    assert await repo.compare_and_save_game_state(-100, "2048", state,
                                                  expected_version=state["version"] - 1)
    _, lost = await service.act(game_key="2048", chat_id=-100, user_id=1,
        action_id="loss", action="move", direction="left", round_id=state["round_id"],
        phase_seq=state["phase_seq"], now=101, rng=random.Random(0))
    assert lost["phase"] == "finished" and not lost["won"]
    await service.settle_scores(game_key="2048", chat_id=-100)
    assert len(scores.events) == 1 and not coins.events
    _, new = await service.open(game_key="2048", chat_id=-100, user_id=1,
                                user_name="X", now=120, rng=random.Random(1))
    await service.advance(game_key="2048", chat_id=-100,
                          round_id=new["round_id"], now=421)
    expired = await service.current(game_key="2048", chat_id=-100)
    assert expired["phase"] == "cancelled" and not expired["score_pending"]


@pytest.mark.asyncio
async def test_real_match_from_two_tiles_to_no_moves_settles_once():
    repo, scores, coins = Repository(), Scores(), Coins()
    service = Tile2048Service(repo, leaderboard=scores, economy=coins)
    rng = random.Random(2)
    _, opened = await service.open(game_key="2048", chat_id=-100, user_id=1,
                                   user_name="Oyuncu", now=0, rng=rng)
    claim = await service.claim_panel(game_key="2048", chat_id=-100, now=0)
    assert await service.acknowledge_panel(game_key="2048", chat_id=-100,
        round_id=opened["round_id"], expected_version=claim["version"],
        message_id=88, now=0)
    for number in range(1, 500):
        state = await service.current(game_key="2048", chat_id=-100)
        if state["phase"] == "finished":
            break
        direction = next(direction for direction in ("left", "down", "up", "right")
                         if rules.slide(state["board"], direction)[2])
        _, state = await service.act(game_key="2048", chat_id=-100, user_id=1,
            action_id=f"move:{number}", action="move", direction=direction,
            round_id=opened["round_id"], phase_seq=state["phase_seq"], now=number,
            rng=rng)
        if state["phase"] == "playing":
            claim = await service.claim_panel(game_key="2048", chat_id=-100,
                                              now=number)
            assert await service.acknowledge_panel(game_key="2048", chat_id=-100,
                round_id=opened["round_id"], expected_version=claim["version"],
                message_id=88, now=number)
    else:
        pytest.fail("deterministic full match did not terminate")
    assert state["phase"] == "finished" and state["reason"] == "board_full"
    assert state["moves"] > 10 and state["score"] > 0 and not state["won"]
    assert not rules.legal_moves(state["board"])
    await service.settle_scores(game_key="2048", chat_id=-100)
    await Tile2048Service(repo, leaderboard=scores, economy=coins).settle_scores(
        game_key="2048", chat_id=-100)
    assert len(scores.events) == 1 and not coins.events
