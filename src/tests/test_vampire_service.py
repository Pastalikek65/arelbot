"""Persistent CAS, private ownership and reward replay tests."""

import asyncio
from copy import deepcopy

import pytest

from src.services.vampire_service import VampireService
from src.games import vampire


class Repository:
    def __init__(self):
        self.rows = {}

    async def get_game_state(self, chat, key):
        return deepcopy(self.rows.get((chat, key)))

    async def create_game_state_if_absent(self, chat, key, state):
        if (chat, key) in self.rows:
            return False
        self.rows[chat, key] = deepcopy(state)
        return True

    async def compare_and_save_game_state(self, chat, key, state, *, expected_version):
        old = self.rows.get((chat, key))
        if not old or old["version"] != expected_version:
            return False
        assert state["version"] == expected_version + 1
        self.rows[chat, key] = deepcopy(state)
        return True


class Leaderboard:
    def __init__(self):
        self.events = set()

    async def record_score(self, **kwargs):
        self.events.add(kwargs["event_id"])


@pytest.mark.asyncio
async def test_two_instances_open_and_join_once():
    repo = Repository()
    first, second = VampireService(repo), VampireService(repo)
    opened = await asyncio.gather(
        first.open(chat_id=-100, thread_id=7, user_id=1, user_name="Bir"),
        second.open(chat_id=-100, thread_id=7, user_id=2, user_name="İki"))
    assert {r[1]["round_id"] for r in opened} == {opened[0][1]["round_id"]}
    state = opened[0][1]
    results = await asyncio.gather(
        first.act(chat_id=-100, thread_id=7, user_id=2, action_id="join:2", action="join", round_id=state["round_id"]),
        second.act(chat_id=-100, thread_id=7, user_id=2, action_id="join:2", action="join", round_id=state["round_id"]),
        return_exceptions=True)
    assert len((await first.current(-100, 7))["players"]) == 2
    assert any(isinstance(r, ValueError) or r[0] == "duplicate" for r in results)


@pytest.mark.asyncio
async def test_invite_is_user_bound_hashed_and_role_ack_needs_delivery():
    service = VampireService(Repository())
    _, state = await service.open(chat_id=-100, user_id=1, user_name="Bir")
    payload = await service.mint_invite(chat_id=-100, user_id=1, round_id=state["round_id"])
    assert len(payload) <= 64
    assert payload.rsplit("_", 1)[-1] not in str(await service.current(-100))
    with pytest.raises(ValueError, match="invalid_invite"):
        await service.accept_invite(payload, user_id=2)
    await service.accept_invite(payload, user_id=1)
    with pytest.raises(ValueError, match="invalid_invite"):
        await service.accept_invite(payload, user_id=1)


@pytest.mark.asyncio
async def test_panel_lease_preserves_newer_dirty_and_topic_scope():
    service = VampireService(Repository())
    _, state = await service.open(chat_id=-100, thread_id=8, user_id=1, user_name="Bir")
    claim = await service.claim_delivery(chat_id=-100, thread_id=8, kind="panel", now=100)
    assert claim and await service.claim_delivery(chat_id=-100, thread_id=8, kind="panel", now=101) is None
    await service.act(chat_id=-100, thread_id=8, user_id=2, user_name="İki", action_id="join", action="join")
    await service.acknowledge_panel(chat_id=-100, thread_id=8, round_id=state["round_id"],
                                    expected_version=claim["version"], message_id=12)
    assert (await service.current(-100, 8))["panel_dirty"]
    assert await service.current(-100, 9) is None
    with pytest.raises(ValueError, match="stale_round"):
        await service.act(chat_id=-100, thread_id=8, user_id=3, action_id="old", action="join", round_id="old")


@pytest.mark.asyncio
async def test_reward_pending_blocks_new_match_and_event_id_is_stable():
    leaderboard = Leaderboard()
    service = VampireService(Repository(), leaderboard=leaderboard)
    _, state = await service.open(chat_id=-100, user_id=1, user_name="Bir")
    # Synthetic terminal state tests persistence protocol, not game rules.
    terminal = deepcopy(state)
    terminal.update(phase="finished", winner="villagers", score_pending=True,
                    players=[dict(state["players"][0], role="villager")])
    service.repository.rows[(-100, service.key(None))] = terminal
    with pytest.raises(ValueError, match="rewards_pending"):
        await service.open(chat_id=-100, user_id=2, user_name="İki")
    await service.settle_scores(chat_id=-100)
    assert leaderboard.events == {f"vampir_koylu:-100:{state['round_id']}:1"}
    await service.settle_scores(chat_id=-100)
    assert len(leaderboard.events) == 1
    _, new = await service.open(chat_id=-100, user_id=2, user_name="İki")
    assert new["round_id"] != state["round_id"]


@pytest.mark.asyncio
async def test_two_instances_simultaneous_secret_ballot_only_one_is_saved():
    repo = Repository()
    one, two = VampireService(repo), VampireService(repo)
    state = vampire.new_lobby(chat_id=-100, thread_id=None, owner_id=1,
                              owner_name="Bir", round_id="r", now=1)
    for uid in range(2, 8):
        state = vampire.join(state, user_id=uid, user_name=str(uid), now=2)
    for uid in range(1, 8):
        state = vampire.ready(state, user_id=uid, now=3)
    state = vampire.start(state, user_id=1,
        roles=["vampire", "seer", "healer", "villager", "villager", "villager", "villager"], now=4)
    for uid in range(1, 8):
        state = vampire.role_ack(state, user_id=uid, now=5)
    state = vampire.activate(state, now=10)
    state = vampire.advance(state, now=100)
    state = vampire.activate(state, now=101)
    state = vampire.advance(state, now=191)
    state = vampire.activate(state, now=192)
    repo.rows[(-100, one.key(None))] = deepcopy(state)
    results = await asyncio.gather(
        one.act(chat_id=-100, user_id=2, action_id="vote:one", action="vote",
                round_id="r", phase_seq=state["phase_seq"], target_id=4, now=193),
        two.act(chat_id=-100, user_id=2, action_id="vote:two", action="vote",
                round_id="r", phase_seq=state["phase_seq"], target_id=5, now=193),
        return_exceptions=True)
    assert sum(isinstance(r, ValueError) and "already_voted" in str(r) for r in results) == 1
    persisted = await one.current(-100)
    assert persisted["votes"]["2"] in {4, 5} and len(persisted["votes"]) == 1


@pytest.mark.asyncio
async def test_late_private_receipt_cannot_reopen_closed_phase():
    repo = Repository()
    service = VampireService(repo)
    state = vampire.new_lobby(chat_id=-100, thread_id=None, owner_id=1,
                              owner_name="Bir", round_id="r", now=1)
    for uid in range(2, 8):
        state = vampire.join(state, user_id=uid, user_name=str(uid), now=2)
    for uid in range(1, 8):
        state = vampire.ready(state, user_id=uid, now=3)
    state = vampire.start(state, user_id=1,
        roles=["vampire", "seer", "healer", "villager", "villager", "villager", "villager"], now=4)
    for uid in range(1, 8):
        state = vampire.role_ack(state, user_id=uid, now=5)
    state = vampire.activate(state, now=10)
    phase_seq = state["phase_seq"]
    state["private_delivery"] = {}
    repo.rows[(-100, service.key(None))] = deepcopy(state)
    claimed = await service.claim_delivery(chat_id=-100, kind="private", user_id=1, now=11)
    assert claimed
    await service.advance(chat_id=-100, round_id="r", now=100)
    with pytest.raises(ValueError, match="stale_phase"):
        await service.acknowledge_private(chat_id=-100, round_id="r",
                                          phase_seq=phase_seq, user_id=1, message_id=77)
    current = await service.current(-100)
    assert current["phase"] == "discussion" and "1" not in current.get("private_delivery", {})


@pytest.mark.asyncio
async def test_panel_and_all_private_receipts_start_clock():
    repo = Repository()
    service = VampireService(repo)
    state = vampire.new_lobby(chat_id=-100, thread_id=None, owner_id=1,
                              owner_name="Bir", round_id="r", now=1)
    for uid in range(2, 8):
        state = vampire.join(state, user_id=uid, user_name=str(uid), now=2)
    for uid in range(1, 8):
        state = vampire.ready(state, user_id=uid, now=3)
    state = vampire.start(state, user_id=1,
        roles=["vampire", "seer", "healer", "villager", "villager", "villager", "villager"], now=4)
    for uid in range(1, 8):
        state = vampire.role_ack(state, user_id=uid, now=5)
    assert state["phase"] == "night" and state["panel_wait_until"] == 305
    state["panel_dirty"] = True
    repo.rows[(-100, service.key(None))] = deepcopy(state)
    leased = await service.claim_delivery(chat_id=-100, kind="panel", now=10)
    await service.acknowledge_panel(chat_id=-100, round_id="r", expected_version=leased["version"],
                                    message_id=42, now=11)
    current = await service.current(-100)
    assert current["panel_ready"] and not current["phase_ready"]
    assert current["panel_wait_until"] == 305 and current["deadline"] is None
    for uid, clock in ((1, 12), (2, 13), (3, 14)):
        claim = await service.claim_delivery(chat_id=-100, kind="private", user_id=uid, now=clock)
        assert claim
        await service.acknowledge_private(chat_id=-100, round_id="r", phase_seq=state["phase_seq"],
                                          user_id=uid, message_id=100 + uid, now=clock)
        current = await service.current(-100)
        if uid < 3:
            assert current["deadline"] is None and not current["phase_ready"]
    assert current["panel_wait_until"] is None and current["deadline"] == 104
    assert current["phase_ready"]


@pytest.mark.asyncio
async def test_late_panel_ack_does_not_resurrect_timed_out_phase():
    repo = Repository()
    service = VampireService(repo)
    state = vampire.new_lobby(chat_id=-100, thread_id=None, owner_id=1,
                              owner_name="Bir", round_id="r", now=1)
    state.update(phase="night", day=1, phase_seq=2, panel_wait_until=300,
                 phase_ready=False, deadline=None, panel_dirty=True)
    repo.rows[(-100, service.key(None))] = deepcopy(state)
    leased = await service.claim_delivery(chat_id=-100, kind="panel", now=299)
    assert leased
    await service.acknowledge_panel(chat_id=-100, round_id="r", expected_version=leased["version"],
                                    message_id=42, now=300)
    current = await service.current(-100)
    assert not current["phase_ready"] and current["deadline"] is None
    await service.advance(chat_id=-100, round_id="r", now=300)
    assert (await service.current(-100))["phase"] == "cancelled"


@pytest.mark.asyncio
async def test_identical_clock_timestamp_advances_distinct_phase_generations():
    repo = Repository()
    service = VampireService(repo)
    state = vampire.new_lobby(chat_id=-100, thread_id=None, owner_id=1,
                              owner_name="Bir", round_id="r", now=1)
    for uid in range(2, 8):
        state = vampire.join(state, user_id=uid, user_name=str(uid), now=2)
    for uid in range(1, 8):
        state = vampire.ready(state, user_id=uid, now=3)
    state = vampire.start(state, user_id=1,
        roles=["vampire", "seer", "healer", "villager", "villager", "villager", "villager"], now=4)
    for uid in range(1, 8):
        state = vampire.role_ack(state, user_id=uid, now=5)
    state = vampire.activate(state, now=10)
    night_seq = state["phase_seq"]
    repo.rows[(-100, service.key(None))] = deepcopy(state)
    _, morning = await service.advance(chat_id=-100, round_id="r", now=100)
    assert morning["phase"] == "discussion"
    discussion_seq = morning["phase_seq"]
    # Reproduce equal persisted deadlines without sleeping or relying on host timer precision.
    morning = vampire.activate(morning, now=10)
    repo.rows[(-100, service.key(None))] = deepcopy(morning)
    _, voting = await service.advance(chat_id=-100, round_id="r", now=100)
    assert voting["phase"] == "vote"
    assert f"clock:r:{night_seq}:100.0" in voting["processed_actions"]
    assert f"clock:r:{discussion_seq}:100.0" in voting["processed_actions"]
