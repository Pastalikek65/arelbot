"""CAS and effect-retry checks for Casus."""
import asyncio
import time
from copy import deepcopy

import pytest

from src.services.spy_service import SpyService


class Repository:
    def __init__(self):
        self.rows = {}

    async def get_game_state(self, chat_id, key):
        return deepcopy(self.rows.get((chat_id, key)))

    async def create_game_state_if_absent(self, chat_id, key, state):
        if (chat_id, key) in self.rows:
            return False
        self.rows[(chat_id, key)] = deepcopy(state)
        return True

    async def compare_and_save_game_state(self, chat_id, key, state, *, expected_version):
        row = self.rows.get((chat_id, key))
        if row is None or row["version"] != expected_version:
            return False
        self.rows[(chat_id, key)] = deepcopy(state)
        return True


class Scores:
    def __init__(self):
        self.events = set()
        self.fail_once = True

    async def record_score(self, *, event_id, **_):
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("store unavailable")
        self.events.add(event_id)


@pytest.mark.asyncio
async def test_two_instances_cas_duplicate_and_topic_isolation():
    repo = Repository()
    a, b = SpyService(repo), SpyService(repo)
    await a.open(chat_id=-100, thread_id=7, user_id=1, user_name="Bir")
    round_id = (await a.current(-100, 7))["round_id"]
    await asyncio.wait_for(asyncio.gather(
        a.act(chat_id=-100, thread_id=7, user_id=2, user_name="İki",
              action_id="join:2", action="join", round_id=round_id),
        b.act(chat_id=-100, thread_id=7, user_id=3, user_name="Üç",
              action_id="join:3", action="join", round_id=round_id)), 2)
    state = await a.current(-100, 7)
    assert {p["id"] for p in state["players"]} == {1, 2, 3}
    status, _ = await b.act(chat_id=-100, thread_id=7, user_id=2,
                            action_id="join:2", action="join", round_id=round_id)
    assert status == "duplicate"
    assert await a.current(-100, None) is None
    await a.open(chat_id=-100, thread_id=None, user_id=4, user_name="Dört")
    assert (await a.current(-100, None))["owner_id"] == 4


@pytest.mark.asyncio
async def test_user_bound_hash_invite_and_ready_gate():
    repo = Repository()
    svc = SpyService(repo)
    await svc.open(chat_id=-100, user_id=1, user_name="Bir")
    round_id = (await svc.current(-100))["round_id"]
    for user in (2, 3, 4):
        await svc.act(chat_id=-100, user_id=user, user_name=str(user),
                      action_id=f"join:{user}", action="join", round_id=round_id)
    with pytest.raises(ValueError, match="spy_dm_not_ready"):
        await svc.act(chat_id=-100, user_id=1, action_id="start:early",
                      action="start", round_id=round_id)
    for user in (1, 2, 3, 4):
        payload = await svc.mint_invite(chat_id=-100, user_id=user,
                                        round_id=round_id, request_id=f"ready:{user}")
        assert payload.startswith("sp_")
        assert payload not in repr(await svc.current(-100))
        with pytest.raises(ValueError, match="spy_invalid_invite"):
            await svc.accept_invite(payload, user_id=100 + user)
        await svc.accept_invite(payload, user_id=user)
    await svc.act(chat_id=-100, user_id=1, action_id="start", action="start", round_id=round_id)
    state = await svc.current(-100)
    assert state["phase"] == "delivery"
    with pytest.raises(ValueError, match="spy_not_delivered"):
        await svc.act(chat_id=-100, user_id=1, action_id="ack:1", action="confirm", round_id=round_id)
    for user in (1, 2, 3, 4):
        claimed = await svc.claim_delivery(chat_id=-100, kind="role", user_id=user, now=state["last_activity_at"])
        assert claimed is not None
        assert await svc.claim_delivery(chat_id=-100, kind="role", user_id=user,
                                        now=state["last_activity_at"]) is None
        await svc.act(chat_id=-100, user_id=user, action_id=f"delivered:{user}",
                      action="delivered", round_id=round_id, message_id=100 + user)
        await svc.act(chat_id=-100, user_id=user, action_id=f"ack:{user}",
                      action="confirm", round_id=round_id)
    assert (await svc.current(-100))["phase"] == "question"


@pytest.mark.asyncio
async def test_failed_delivery_retries_and_panel_lease():
    svc = SpyService(Repository())
    await svc.open(chat_id=-100, user_id=1, user_name="Bir")
    state = await svc.current(-100)
    first = await svc.claim_delivery(chat_id=-100, kind="panel", now=state["created_at"])
    assert first is not None
    assert await svc.claim_delivery(chat_id=-100, kind="panel", now=state["created_at"] + 1) is None
    second = await svc.claim_delivery(chat_id=-100, kind="panel", now=state["created_at"] + 31)
    assert second is not None
    await svc.acknowledge_panel(chat_id=-100, round_id=state["round_id"],
                                expected_version=second["version"], message_id=42)
    assert (await svc.current(-100))["panel_dirty"] is False


@pytest.mark.asyncio
async def test_reward_retry_is_event_idempotent():
    repo, scores = Repository(), Scores()
    svc = SpyService(repo, leaderboard=scores)
    await svc.open(chat_id=-100, user_id=1, user_name="Bir")
    round_id = (await svc.current(-100))["round_id"]
    for user in (2, 3, 4):
        await svc.act(chat_id=-100, user_id=user, user_name=str(user),
                      action_id=f"j:{user}", action="join", round_id=round_id)
    # A stored terminal state models a process restart before effects finish.
    state = await svc.current(-100)
    state.update(phase="finished", winner="group", spy_id=1, score_pending=True,
                 location="Fırın", deadline=None)
    state["version"] += 1
    assert await repo.compare_and_save_game_state(-100, "casus", state,
                                                  expected_version=state["version"] - 1)
    with pytest.raises(RuntimeError, match="store unavailable"):
        await svc.settle_scores(chat_id=-100)
    assert (await svc.current(-100))["score_pending"]
    await svc.settle_scores(chat_id=-100)
    await svc.settle_scores(chat_id=-100)
    assert len(scores.events) == 4
    assert not (await svc.current(-100))["score_pending"]


@pytest.mark.asyncio
async def test_two_instances_cannot_double_vote_or_replay_expired_phase():
    repo = Repository()
    first, second = SpyService(repo), SpyService(repo)
    await first.open(chat_id=-100, user_id=1, user_name="Bir")
    round_id = (await first.current(-100))["round_id"]
    for user in (2, 3, 4):
        await first.act(chat_id=-100, user_id=user, user_name=str(user),
                        action_id=f"j:{user}", action="join", round_id=round_id)
    state = await first.current(-100)
    import time
    state.update(phase="vote", deadline=time.time() + 60,
                 spy_id=1, location="Fırın", vote_round=1,
                 candidates=[1, 2, 3, 4], votes={})
    state["version"] += 1
    assert await repo.compare_and_save_game_state(-100, "casus", state,
                                                  expected_version=state["version"] - 1)
    results = await asyncio.wait_for(asyncio.gather(
        first.act(chat_id=-100, user_id=1, action_id="vote:a", action="vote",
                  target_id=2, round_id=round_id),
        second.act(chat_id=-100, user_id=1, action_id="vote:b", action="vote",
                   target_id=3, round_id=round_id), return_exceptions=True), 2)
    assert sum(isinstance(result, ValueError) for result in results) == 1
    assert len((await first.current(-100))["votes"]) == 1
    with pytest.raises(ValueError, match="spy_expired"):
        await first.act(chat_id=-100, user_id=2, action_id="late", action="vote",
                        target_id=1, round_id=round_id, now=time.time() + 100)


@pytest.mark.asyncio
async def test_open_rereads_and_settles_result_that_became_pending_during_cas():
    class RacingRepository(Repository):
        inject_pending = False

        async def compare_and_save_game_state(self, chat_id, key, state, *, expected_version):
            if self.inject_pending and state["phase"] == "lobby":
                self.inject_pending = False
                current = self.rows[(chat_id, key)]
                current["version"] += 1
                current["score_pending"] = True
                return False
            return await super().compare_and_save_game_state(
                chat_id, key, state, expected_version=expected_version)

    repo = RacingRepository()
    scores = Scores()
    scores.fail_once = False
    svc = SpyService(repo, leaderboard=scores)
    await svc.open(chat_id=-100, user_id=1, user_name="Bir")
    state = await svc.current(-100)
    old_round = state["round_id"]
    state.update(phase="finished", winner="group", spy_id=1,
                 location="Fırın", score_pending=False, deadline=None)
    state["version"] += 1
    assert await repo.compare_and_save_game_state(-100, "casus", state,
                                                  expected_version=state["version"] - 1)
    repo.inject_pending = True
    status, fresh = await svc.open(chat_id=-100, user_id=2, user_name="İki")
    assert status == "created" and fresh["round_id"] != old_round
    assert len(scores.events) == 1
    assert any(old_round in event for event in scores.events)


@pytest.mark.asyncio
async def test_same_clock_in_two_phases_advances_each_without_duplicate_or_skip():
    repo = Repository()
    svc = SpyService(repo)
    await svc.open(chat_id=-100, user_id=1, user_name="Bir")
    state = await svc.current(-100)
    round_id = state["round_id"]
    stamp = time.time() + 5
    state.update(phase="question", deadline=stamp, question_order=[1],
                 turn_index=0, spy_id=1, location="Fırın")
    state["version"] += 1
    assert await repo.compare_and_save_game_state(-100, "casus", state,
                                                  expected_version=state["version"] - 1)
    await svc.advance(chat_id=-100, round_id=round_id, now=stamp)
    state = await svc.current(-100)
    assert state["phase"] == "discussion" and state["deadline"] is None
    # Simulate a persisted due deadline with the identical wall-clock value.
    state["deadline"] = stamp
    state["version"] += 1
    assert await repo.compare_and_save_game_state(-100, "casus", state,
                                                  expected_version=state["version"] - 1)
    status, _ = await svc.advance(chat_id=-100, round_id=round_id, now=stamp)
    assert status == "applied"
    state = await svc.current(-100)
    assert state["phase"] == "vote" and state["deadline"] is None


@pytest.mark.asyncio
async def test_late_panel_ack_cannot_resurrect_expired_waiting_phase():
    repo = Repository()
    svc = SpyService(repo)
    await svc.open(chat_id=-100, user_id=1, user_name="Bir")
    state = await svc.current(-100)
    round_id = state["round_id"]
    state.update(phase="question", deadline=None, panel_dirty=True,
                 panel_wait_until=time.time() - 1, question_order=[1],
                 turn_index=0, spy_id=1, location="Fırın")
    state["version"] += 1
    assert await repo.compare_and_save_game_state(-100, "casus", state,
                                                  expected_version=state["version"] - 1)
    claimed = await svc.claim_delivery(chat_id=-100, kind="panel")
    assert claimed is not None
    assert await svc.acknowledge_panel(chat_id=-100, round_id=round_id,
                                       expected_version=claimed["version"], message_id=201)
    state = await svc.current(-100)
    assert state["phase"] == "cancelled"
    assert state["deadline"] is None and not state["score_pending"]
    assert state["panel_dirty"]


@pytest.mark.asyncio
async def test_due_worker_ignores_phase_that_changed_after_its_read():
    repo = Repository()
    svc = SpyService(repo)
    await svc.open(chat_id=-100, user_id=1, user_name="Bir")
    state = await svc.current(-100)
    round_id = state["round_id"]
    stamp = time.time() + 5
    state.update(phase="question", deadline=stamp, question_order=[1],
                 turn_index=0, spy_id=1, location="Fırın")
    state["version"] += 1
    assert await repo.compare_and_save_game_state(-100, "casus", state,
                                                  expected_version=state["version"] - 1)
    original_apply = svc._apply

    async def racing_apply(chat_id, thread_id, action_id, transition, **kwargs):
        current = await svc.current(-100)
        current.update(phase="discussion", deadline=stamp)
        current["version"] += 1
        assert await repo.compare_and_save_game_state(-100, "casus", current,
                                                      expected_version=current["version"] - 1)
        return await original_apply(chat_id, thread_id, action_id, transition, **kwargs)

    svc._apply = racing_apply
    status, _ = await svc.advance(chat_id=-100, round_id=round_id, now=stamp)
    assert status == "ignored"
    assert (await svc.current(-100))["phase"] == "discussion"
