"""Full service-play reviews for both grid games and durable rewards."""

from copy import deepcopy

from bson import BSON
import pytest

from src.games import grid_puzzles as rules
from src.services.grid_puzzles_service import GridPuzzlesService


class BsonRepository:
    def __init__(self):
        self.rows = {}

    async def get_game_state(self, chat_id, key):
        return deepcopy(self.rows.get((chat_id, key)))

    async def create_game_state_if_absent(self, chat_id, key, state):
        if (chat_id, key) in self.rows:
            return False
        self.rows[chat_id, key] = BSON.encode(state).decode()
        return True

    async def compare_and_save_game_state(self, chat_id, key, state, *, expected_version):
        prior = self.rows.get((chat_id, key))
        if prior is None or prior["version"] != expected_version:
            return False
        assert state["version"] == expected_version + 1
        self.rows[chat_id, key] = BSON.encode(state).decode()
        return True


class Leaderboard:
    def __init__(self, fail_user=None):
        self.fail_user = fail_user
        self.attempts = []
        self.awards = {}

    async def record_score(self, *, user_id, event_id, score, **_):
        self.attempts.append(event_id)
        if user_id == self.fail_user:
            self.fail_user = None
            raise RuntimeError("temporary ledger outage")
        self.awards.setdefault(event_id, score)


class Economy:
    def __init__(self):
        self.awards = {}

    async def credit(self, user_id, chat_id, amount, *, reason, event_id):
        self.awards.setdefault(event_id, (user_id, chat_id, amount, reason))


async def _state(service, game):
    return await service.current(game_key=game, chat_id=-100, thread_id=7)


async def _publish(service, game, round_id, now):
    """Deliver the preparation panel and, if activated, its live revision."""
    for _ in range(2):
        claim = await service.claim_delivery(game_key=game, chat_id=-100,
                                             thread_id=7, now=now)
        assert claim is not None
        assert await service.acknowledge_panel(
            game_key=game, chat_id=-100, thread_id=7, round_id=round_id,
            expected_version=claim["version"], message_id=55, now=now)
        current = await _state(service, game)
        if not current["panel_dirty"]:
            return current
    assert not current["panel_dirty"]
    return current


async def _pick(service, round_id, actor, index, action_id, now):
    current = await _state(service, rules.MEMORY_KEY)
    status, played = await service.act(
        game_key=rules.MEMORY_KEY, chat_id=-100, thread_id=7,
        user_id=actor, action="pick", action_id=action_id, index=index,
        round_id=round_id, phase_seq=current["phase_seq"],
        turn_seq=current["turn_seq"], now=now)
    assert status == "applied"
    return played


@pytest.mark.asyncio
async def test_memory_eight_pairs_two_players_and_restart_safe_reward_ledger():
    repository = BsonRepository()
    ledger, economy = Leaderboard(fail_user=2), Economy()
    service = GridPuzzlesService(repository, leaderboard=ledger, economy=economy)
    _, created = await service.open(game_key=rules.MEMORY_KEY, chat_id=-100,
                                    thread_id=7, user_id=1, user_name="Bir")
    round_id = created["round_id"]
    base = created["created_at"]
    await service.act(game_key=rules.MEMORY_KEY, chat_id=-100, thread_id=7,
                      user_id=2, user_name="İki", action="join", action_id="join:2",
                      round_id=round_id, now=base + 1)
    await _publish(service, rules.MEMORY_KEY, round_id, base + 1)
    await service.act(game_key=rules.MEMORY_KEY, chat_id=-100, thread_id=7,
                      user_id=1, action="start", action_id="start",
                      round_id=round_id, now=base + 2)
    preview = await _publish(service, rules.MEMORY_KEY, round_id, base + 3)
    assert preview["phase"] == "preview" and preview["deadline"] == base + 11
    await service.advance(game_key=rules.MEMORY_KEY, chat_id=-100,
                          thread_id=7, round_id=round_id, now=base + 11)
    await _publish(service, rules.MEMORY_KEY, round_id, base + 12)

    # The test driver knows the stored layout; real players see it only during
    # preview or when a card is opened. Play every pair via the public service.
    layout = (await _state(service, rules.MEMORY_KEY))["cards"]
    pairs = [[index for index, card in enumerate(layout) if card == symbol]
             for symbol in rules.CARD_SYMBOLS]
    tick = base + 13
    await _pick(service, round_id, 1, pairs[0][0], "owner:first", tick)
    await _pick(service, round_id, 1, pairs[0][1], "owner:match", tick + .1)
    after_first = await _state(service, rules.MEMORY_KEY)
    assert after_first["players"][0]["score"] == 1
    await _publish(service, rules.MEMORY_KEY, round_id, tick + 1)

    # A mismatch reveals two different cards for three seconds, then rotates
    # the turn. Both players therefore score during the genuine match.
    await _pick(service, round_id, 1, pairs[1][0], "owner:mismatch:1", tick + 2)
    await _pick(service, round_id, 1, pairs[2][0], "owner:mismatch:2", tick + 2.1)
    mismatch = await _publish(service, rules.MEMORY_KEY, round_id, tick + 3)
    assert mismatch["phase"] == "mismatch" and mismatch["deadline"] == tick + 6
    await service.advance(game_key=rules.MEMORY_KEY, chat_id=-100,
                          thread_id=7, round_id=round_id, now=tick + 6)
    rotated = await _publish(service, rules.MEMORY_KEY, round_id, tick + 7)
    assert rotated["current_actor"] == 2

    for ordinal, pair in enumerate(pairs[1:], start=1):
        moment = tick + 8 + ordinal * 2
        await _pick(service, round_id, 2, pair[0], f"guest:{ordinal}:a", moment)
        await _pick(service, round_id, 2, pair[1], f"guest:{ordinal}:b", moment + .1)
        current = await _state(service, rules.MEMORY_KEY)
        if ordinal < 7:
            assert current["phase"] == "turn"
            await _publish(service, rules.MEMORY_KEY, round_id, moment + 1)

    finished = await _state(service, rules.MEMORY_KEY)
    assert finished["phase"] == "finished"
    assert finished["matched"] == list(range(16))
    assert [p["score"] for p in finished["players"]] == [1, 7]
    assert finished["winner_ids"] == [2] and finished["score_pending"]
    with pytest.raises(ValueError, match="grid_rewards_pending"):
        await service.open(game_key=rules.MEMORY_KEY, chat_id=-100,
                           thread_id=7, user_id=3, user_name="Üç")

    with pytest.raises(RuntimeError, match="temporary ledger outage"):
        await service.settle_scores(game_key=rules.MEMORY_KEY,
                                    chat_id=-100, thread_id=7)
    assert (await _state(service, rules.MEMORY_KEY))["score_pending"]
    restarted = GridPuzzlesService(repository, leaderboard=ledger, economy=economy)
    await restarted.settle_scores(game_key=rules.MEMORY_KEY,
                                  chat_id=-100, thread_id=7)
    attempts_after_settle = len(ledger.attempts)
    await restarted.settle_scores(game_key=rules.MEMORY_KEY,
                                  chat_id=-100, thread_id=7)
    assert len(ledger.attempts) == attempts_after_settle
    assert not (await _state(restarted, rules.MEMORY_KEY))["score_pending"]
    assert ledger.awards == {
        f"{rules.MEMORY_KEY}:-100:{round_id}:1": 1,
        f"{rules.MEMORY_KEY}:-100:{round_id}:2": 7,
    }
    assert economy.awards == {
        f"coin:{rules.MEMORY_KEY}:-100:{round_id}:2":
            (2, -100, 5, f"{rules.MEMORY_KEY}_reward")
    }


@pytest.mark.asyncio
async def test_mines_full_safe_clear_credits_once_after_restart():
    repository = BsonRepository()
    ledger, economy = Leaderboard(), Economy()
    service = GridPuzzlesService(repository, leaderboard=ledger, economy=economy)
    _, created = await service.open(game_key=rules.MINES_KEY, chat_id=-100,
                                    thread_id=7, user_id=1, user_name="Bir")
    round_id, base = created["round_id"], created["created_at"]
    playing = await _publish(service, rules.MINES_KEY, round_id, base + 1)
    assert playing["phase_ready"] and playing["deadline"] == base + 601
    await service.act(game_key=rules.MINES_KEY, chat_id=-100, thread_id=7,
                      user_id=1, action="open", action_id="first:0", index=0,
                      round_id=round_id, phase_seq=playing["phase_seq"], now=base + 2)
    revealed = await _state(service, rules.MINES_KEY)
    assert 0 not in revealed["mines"]
    assert not (set(rules._neighbors(0)) & set(revealed["mines"]))
    for index in range(36):
        state = await _state(service, rules.MINES_KEY)
        if index in state["mines"] or index in state["opened"]:
            continue
        await service.act(game_key=rules.MINES_KEY, chat_id=-100, thread_id=7,
                          user_id=1, action="open", action_id=f"safe:{index}",
                          index=index, round_id=round_id,
                          phase_seq=state["phase_seq"], now=base + 3 + index)
    finished = await _state(service, rules.MINES_KEY)
    assert finished["phase"] == "finished" and finished["outcome"] == "cleared"
    assert len(finished["opened"]) == 30 and finished["winner_ids"] == [1]
    assert finished["score_pending"]
    restarted = GridPuzzlesService(repository, leaderboard=ledger, economy=economy)
    await restarted.settle_scores(game_key=rules.MINES_KEY, chat_id=-100, thread_id=7)
    await restarted.settle_scores(game_key=rules.MINES_KEY, chat_id=-100, thread_id=7)
    assert ledger.awards == {f"{rules.MINES_KEY}:-100:{round_id}:1": 10}
    assert economy.awards == {f"coin:{rules.MINES_KEY}:-100:{round_id}:1":
                             (1, -100, 5, f"{rules.MINES_KEY}_reward")}
