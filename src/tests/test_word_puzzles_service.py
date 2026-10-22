"""Persistent CAS, replay and panel timing contracts for word puzzles."""
import asyncio
import time
from copy import deepcopy

import pytest

from src.services.word_puzzles_service import WordPuzzlesService


class Repository:
    def __init__(self):
        self.rows = {}

    async def get_game_state(self, chat, key):
        return deepcopy(self.rows.get((chat, key)))

    async def create_game_state_if_absent(self, chat, key, state):
        if (chat, key) in self.rows:
            return False
        self.rows[(chat, key)] = deepcopy(state)
        return True

    async def compare_and_save_game_state(self, chat, key, state, *, expected_version):
        previous = self.rows.get((chat, key))
        if previous is None or previous["version"] != expected_version:
            return False
        self.rows[(chat, key)] = deepcopy(state)
        return True


class Scores:
    def __init__(self):
        self.events = set()
        self.fail_once = True

    async def record_score(self, *, event_id, **_):
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("temporary")
        self.events.add(event_id)


@pytest.mark.asyncio
async def test_topic_key_and_two_instance_cas_join_replay():
    repo = Repository()
    first, second = WordPuzzlesService(repo), WordPuzzlesService(repo)
    await first.open(game_key="karsi_kelime", chat_id=-100, thread_id=7, user_id=1, user_name="Bir")
    state = await first.current(game_key="karsi_kelime", chat_id=-100, thread_id=7)
    round_id = state["round_id"]
    await asyncio.wait_for(asyncio.gather(
        first.act(game_key="karsi_kelime", chat_id=-100, thread_id=7, user_id=2,
                  user_name="İki", action="join", action_id="join:2", round_id=round_id),
        second.act(game_key="karsi_kelime", chat_id=-100, thread_id=7, user_id=3,
                   user_name="Üç", action="join", action_id="join:3", round_id=round_id)), 2)
    state = await first.current(game_key="karsi_kelime", chat_id=-100, thread_id=7)
    assert {p["id"] for p in state["players"]} == {1, 2, 3}
    status, _ = await second.act(game_key="karsi_kelime", chat_id=-100, thread_id=7,
                                 user_id=2, action="join", action_id="join:2", round_id=round_id)
    assert status == "duplicate"
    assert await first.current(game_key="karsi_kelime", chat_id=-100, thread_id=None) is None
    assert await first.current(game_key="anagram", chat_id=-100, thread_id=7) is None


@pytest.mark.asyncio
async def test_panel_failure_freezes_round_and_late_ack_cancels():
    repo = Repository()
    svc = WordPuzzlesService(repo)
    await svc.open(game_key="wordle_tr", chat_id=-100, user_id=1, user_name="Bir")
    state = await svc.current(game_key="wordle_tr", chat_id=-100)
    await svc.act(game_key="wordle_tr", chat_id=-100, user_id=1,
                  action="start", action_id="start", round_id=state["round_id"], target="kalem")
    state = await svc.current(game_key="wordle_tr", chat_id=-100)
    assert state["deadline"] is None and state["panel_wait_until"] is not None
    with pytest.raises(ValueError, match="word_puzzles_waiting_panel"):
        await svc.act(game_key="wordle_tr", chat_id=-100, user_id=1,
                      action="text", action_id="msg:500", round_id=state["round_id"],
                      phase_seq=state["phase_seq"], text="kalem", message_id=500)
    claimed = await svc.claim_panel(game_key="wordle_tr", chat_id=-100)
    assert claimed is not None
    assert await svc.claim_panel(game_key="wordle_tr", chat_id=-100) is None
    assert await svc.acknowledge_panel(game_key="wordle_tr", chat_id=-100,
        round_id=state["round_id"], expected_version=claimed["version"], message_id=101)
    state = await svc.current(game_key="wordle_tr", chat_id=-100)
    assert state["deadline"] is not None
    assert state["input_floor"] == 101
    with pytest.raises(ValueError, match="word_puzzles_stale_message"):
        await svc.act(game_key="wordle_tr", chat_id=-100, user_id=1,
                      action="text", action_id="msg:100", round_id=state["round_id"],
                      phase_seq=state["phase_seq"], text="kalem", message_id=100)


@pytest.mark.asyncio
async def test_round_first_correct_replay_and_reward_retry():
    repo, scores = Repository(), Scores()
    svc = WordPuzzlesService(repo, leaderboard=scores)
    await svc.open(game_key="karsi_kelime", chat_id=-100, user_id=1, user_name="Bir")
    state = await svc.current(game_key="karsi_kelime", chat_id=-100)
    await svc.act(game_key="karsi_kelime", chat_id=-100, user_id=2,
                  user_name="İki", action="join", action_id="join:2", round_id=state["round_id"])
    await svc.act(game_key="karsi_kelime", chat_id=-100, user_id=1,
                  action="start", action_id="start", round_id=state["round_id"],
                  card_order=list(range(5)))
    state = await svc.current(game_key="karsi_kelime", chat_id=-100)
    lease = await svc.claim_panel(game_key="karsi_kelime", chat_id=-100)
    await svc.acknowledge_panel(game_key="karsi_kelime", chat_id=-100,
        round_id=state["round_id"], expected_version=lease["version"], message_id=101)
    state = await svc.current(game_key="karsi_kelime", chat_id=-100)
    status, result = await svc.act(game_key="karsi_kelime", chat_id=-100, user_id=2,
                                   action="text", action_id="msg:501", round_id=state["round_id"],
                                   phase_seq=state["phase_seq"], text=state["answers"][0],
                                   message_id=501)
    assert status == "applied" and result["phase"] == "reveal" and result["scores"]["2"] == 10
    with pytest.raises(ValueError, match="word_puzzles_stale_phase"):
        await svc.act(game_key="karsi_kelime", chat_id=-100, user_id=1,
                      action="text", action_id="msg:502", round_id=state["round_id"],
                      phase_seq=state["phase_seq"], text=state["answers"][0], message_id=502)
    # Emulate a restart after the fifth reveal and before the scoreboard write.
    result.update(phase="finished", score_pending=True, version=result["version"] + 1)
    assert await repo.compare_and_save_game_state(-100, "karsi_kelime", result,
                                                  expected_version=result["version"] - 1)
    with pytest.raises(RuntimeError, match="temporary"):
        await svc.settle_scores(game_key="karsi_kelime", chat_id=-100)
    await svc.settle_scores(game_key="karsi_kelime", chat_id=-100)
    await svc.settle_scores(game_key="karsi_kelime", chat_id=-100)
    assert len(scores.events) == 1
    assert not (await svc.current(game_key="karsi_kelime", chat_id=-100))["score_pending"]


@pytest.mark.asyncio
async def test_300_second_panel_wait_cannot_be_resurrected():
    repo = Repository()
    svc = WordPuzzlesService(repo)
    await svc.open(game_key="anagram", chat_id=-100, user_id=1, user_name="Bir")
    state = await svc.current(game_key="anagram", chat_id=-100)
    await svc.act(game_key="anagram", chat_id=-100, user_id=1,
                  action="start", action_id="start", round_id=state["round_id"],
                  word_order=list(range(5)))
    state = await svc.current(game_key="anagram", chat_id=-100)
    state["panel_wait_until"] = time.time() - 1
    state["version"] += 1
    assert await repo.compare_and_save_game_state(-100, "anagram", state,
                                                  expected_version=state["version"] - 1)
    claim = await svc.claim_panel(game_key="anagram", chat_id=-100)
    await svc.acknowledge_panel(game_key="anagram", chat_id=-100,
        round_id=state["round_id"], expected_version=claim["version"], message_id=102)
    state = await svc.current(game_key="anagram", chat_id=-100)
    assert state["phase"] == "cancelled" and state["deadline"] is None


@pytest.mark.asyncio
async def test_full_five_round_match_with_same_timestamp_clock_and_replay():
    repo, scores = Repository(), Scores()
    scores.fail_once = False
    svc = WordPuzzlesService(repo, leaderboard=scores)
    key, chat = "karsi_kelime", -100
    await svc.open(game_key=key, chat_id=chat, user_id=1, user_name="Bir")
    state = await svc.current(game_key=key, chat_id=chat)
    await svc.act(game_key=key, chat_id=chat, user_id=2, user_name="İki",
                  action="join", action_id="join:2", round_id=state["round_id"])
    await svc.act(game_key=key, chat_id=chat, user_id=1, action="start",
                  action_id="start", round_id=state["round_id"],
                  card_order=list(range(5)))
    for turn in range(5):
        state = await svc.current(game_key=key, chat_id=chat)
        assert state["phase"] == "playing" and state["round_number"] == turn + 1
        lease = await svc.claim_panel(game_key=key, chat_id=chat)
        await svc.acknowledge_panel(game_key=key, chat_id=chat,
            round_id=state["round_id"], expected_version=lease["version"],
            message_id=100)
        state = await svc.current(game_key=key, chat_id=chat)
        status, _ = await svc.act(game_key=key, chat_id=chat, user_id=2,
            action="text", action_id=f"answer:{turn}", round_id=state["round_id"],
            phase_seq=state["phase_seq"], text=state["answers"][0], message_id=200 + turn)
        assert status == "applied"
        status, _ = await svc.act(game_key=key, chat_id=chat, user_id=2,
            action="text", action_id=f"answer:{turn}", round_id=state["round_id"],
            text=state["answers"][0], message_id=200 + turn)
        assert status == "duplicate"
        state = await svc.current(game_key=key, chat_id=chat)
        assert state["phase"] == "reveal"
        lease = await svc.claim_panel(game_key=key, chat_id=chat)
        await svc.acknowledge_panel(game_key=key, chat_id=chat,
            round_id=state["round_id"], expected_version=lease["version"], message_id=100)
        state = await svc.current(game_key=key, chat_id=chat)
        # No sleep: each phase may advance at the same exact clock instant.
        await svc.advance(game_key=key, chat_id=chat, round_id=state["round_id"],
                          now=state["deadline"])
    state = await svc.current(game_key=key, chat_id=chat)
    assert state["phase"] == "finished" and state["scores"]["2"] == 50
    await svc.settle_scores(game_key=key, chat_id=chat)
    await svc.settle_scores(game_key=key, chat_id=chat)
    assert len(scores.events) == 1


@pytest.mark.asyncio
async def test_old_delivery_ack_keeps_new_canonical_panel_and_repairs_it():
    repo, svc = Repository(), None
    svc = WordPuzzlesService(repo)
    key, chat = "anagram", -100
    await svc.open(game_key=key, chat_id=chat, user_id=1, user_name="Bir")
    state = await svc.current(game_key=key, chat_id=chat)
    old = await svc.claim_panel(game_key=key, chat_id=chat)
    # Concurrent delivery has become canonical before the old ack arrives.
    concurrent = dict(old, panel_message_id=500, panel_dirty=False,
                      version=old["version"] + 1)
    assert await repo.compare_and_save_game_state(chat, key, concurrent,
                                                  expected_version=old["version"])
    assert await svc.acknowledge_panel(game_key=key, chat_id=chat,
        round_id=state["round_id"], expected_version=old["version"], message_id=499)
    state = await svc.current(game_key=key, chat_id=chat)
    assert state["panel_message_id"] == 500 and state["panel_dirty"]
    assert state["delivery_retry_at"] == 0


@pytest.mark.asyncio
async def test_wrong_answer_is_consumed_once_and_cannot_cross_round():
    svc = WordPuzzlesService(Repository())
    key, chat = "karsi_kelime", -100
    await svc.open(game_key=key, chat_id=chat, user_id=1, user_name="Bir")
    state = await svc.current(game_key=key, chat_id=chat)
    await svc.act(game_key=key, chat_id=chat, user_id=1, action="start",
                  action_id="start", round_id=state["round_id"], card_order=list(range(5)))
    lease = await svc.claim_panel(game_key=key, chat_id=chat)
    await svc.acknowledge_panel(game_key=key, chat_id=chat,
        round_id=lease["round_id"], expected_version=lease["version"], message_id=100)
    state = await svc.current(game_key=key, chat_id=chat)
    status, state = await svc.act(game_key=key, chat_id=chat, user_id=1,
        action="text", action_id="wrong", round_id=state["round_id"],
        phase_seq=state["phase_seq"], text="armut", message_id=101)
    assert status == "applied" and state["phase"] == "playing"
    status, _ = await svc.act(game_key=key, chat_id=chat, user_id=1,
        action="text", action_id="wrong", round_id=state["round_id"],
        text="armut", message_id=101)
    assert status == "duplicate"
    with pytest.raises(ValueError, match="word_puzzles_stale_message"):
        await svc.act(game_key=key, chat_id=chat, user_id=1, action="text",
            action_id="different_id", round_id=state["round_id"],
            text="armut", message_id=101)
