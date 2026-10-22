"""Storage races and private invitation ownership for team Tabu."""

import asyncio
from copy import deepcopy

import pytest

from src.services.tabu_service import TabuService


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
        previous = self.rows.get((chat, key))
        if not previous or previous["version"] != expected_version:
            return False
        assert state["version"] == expected_version + 1
        self.rows[chat, key] = deepcopy(state)
        return True


async def prepared():
    service = TabuService(Repository())
    _, state = await service.open(chat_id=-100, thread_id=7, user_id=1, user_name="A")
    for uid, team in [(2, "blue"), (3, "red"), (4, "blue")]:
        await service.act(chat_id=-100, thread_id=7, user_id=uid, user_name=str(uid),
                          action_id=f"join:{uid}", action="join", team=team, round_id=state["round_id"])
    for uid in range(1, 5):
        payload = await service.mint_invite(chat_id=-100, thread_id=7, user_id=uid, round_id=state["round_id"])
        await service.accept_invite(payload, user_id=uid)
    return service, state["round_id"]


@pytest.mark.asyncio
async def test_private_invitation_is_hashed_owned_and_idempotent():
    service = TabuService(Repository())
    _, state = await service.open(chat_id=-100, thread_id=None, user_id=1, user_name="A")
    payload = await service.mint_invite(chat_id=-100, user_id=1, round_id=state["round_id"])
    assert len(payload) <= 64
    token = payload.split("_", 4)[-1]
    assert token not in str(await service.current(-100))
    with pytest.raises(ValueError, match="invalid_invite"):
        await service.accept_invite(payload, user_id=2)
    chat, topic, ready = await service.accept_invite(payload, user_id=1)
    assert (chat, topic) == (-100, None)
    assert ready["players"][0]["ready"]
    _, _, repeated = await service.accept_invite(payload, user_id=1)
    assert ready["version"] == repeated["version"]


@pytest.mark.asyncio
async def test_duplicate_join_is_one_write_and_old_match_cannot_mutate():
    service = TabuService(Repository())
    _, state = await service.open(chat_id=-100, user_id=1, user_name="A")
    kwargs = dict(chat_id=-100, user_id=2, action_id="join:2", action="join", team="blue", round_id=state["round_id"])
    await service.act(**kwargs)
    outcome, repeated = await service.act(**kwargs)
    assert outcome == "duplicate" and len(repeated["players"]) == 2
    with pytest.raises(ValueError, match="stale_round"):
        await service.act(**dict(kwargs, round_id="old", action_id="join:3"))


@pytest.mark.asyncio
async def test_two_instances_open_one_lobby():
    repository = Repository()
    one, two = TabuService(repository), TabuService(repository)
    results = await asyncio.gather(
        one.open(chat_id=-100, user_id=1, user_name="A"),
        two.open(chat_id=-100, user_id=2, user_name="B"),
    )
    assert {result[1]["round_id"] for result in results} == {results[0][1]["round_id"]}


@pytest.mark.asyncio
async def test_timer_keeps_unexpired_lobby_and_advances_closed_card_once():
    service, round_id = await prepared()
    current = await service.current(-100, 7)
    status, same = await service.advance(chat_id=-100, thread_id=7, round_id=round_id, now=current["created_at"] + 1)
    assert status == "ignored" and same["phase"] == "lobby"
    _, state = await service.act(chat_id=-100, thread_id=7, user_id=1, action_id="start", action="start", now=100)
    card_id = state["card_id"]
    await service.act(chat_id=-100, thread_id=7, user_id=1, action_id="delivery", action="delivered", card_id=card_id, now=101)
    await service.claim_delivery(chat_id=-100, thread_id=7, kind="start", now=101.5)
    await service.act(chat_id=-100, thread_id=7, user_id=1, action_id="ack", action="ack", card_id=card_id, input_floor=1, now=102)
    _, ended = await service.act(chat_id=-100, thread_id=7, user_id=1, action_id="pass", action="pass", card_id=card_id, now=103)
    assert ended["phase"] == "appeal"
    _, advanced = await service.advance(chat_id=-100, thread_id=7, round_id=round_id, now=119)
    assert advanced["card_id"] != card_id and advanced["phase"] == "card_delivery"
    assert advanced["active_message_floor"] is None and advanced["ack_retry_at"] == 0


@pytest.mark.asyncio
async def test_panel_delivery_lease_retains_newer_dirty_state_and_message_identity():
    service = TabuService(Repository())
    _, state = await service.open(chat_id=-100, user_id=1, user_name="A")
    leased = await service.claim_delivery(chat_id=-100, kind="panel", now=100)
    assert leased and await service.claim_delivery(chat_id=-100, kind="panel", now=101) is None
    await service.act(chat_id=-100, user_id=2, user_name="B", action_id="join", action="join", team="blue")
    await service.acknowledge_panel(chat_id=-100, round_id=state["round_id"], expected_version=leased["version"], message_id=123)
    current = await service.current(-100)
    assert current["panel_dirty"] is True and current["panel_message_id"] == 123


@pytest.mark.asyncio
async def test_normal_clue_does_not_flood_group_panels():
    service, round_id = await prepared()
    _, state = await service.act(chat_id=-100, thread_id=7, user_id=1, action_id="start", action="start", now=100)
    card_id = state["card_id"]
    await service.act(chat_id=-100, thread_id=7, user_id=1, action_id="delivery", action="delivered", card_id=card_id, now=101)
    _, active = await service.act(chat_id=-100, thread_id=7, user_id=1, action_id="ack", action="ack", card_id=card_id, input_floor=1, now=102)
    await service.acknowledge_panel(chat_id=-100, thread_id=7, round_id=round_id, expected_version=active["version"], message_id=123)
    await service.act(chat_id=-100, thread_id=7, user_id=1, action_id="clue", action="text", card_id=card_id, text="Bunu düşün", now=103)
    assert not (await service.current(-100, 7))["panel_dirty"]


@pytest.mark.asyncio
async def test_ack_requires_group_floor_and_rejects_pre_ack_messages():
    service, _ = await prepared()
    base = int((await service.current(-100, 7))["created_at"]) + 2
    _, state = await service.act(chat_id=-100, thread_id=7, user_id=1, action_id="start", action="start", now=base)
    card_id = state["card_id"]
    await service.act(chat_id=-100, thread_id=7, user_id=1, action_id="delivery", action="delivered", card_id=card_id, now=base + 0.1)
    with pytest.raises(ValueError, match="tabu_input_floor_required"):
        await service.act(chat_id=-100, thread_id=7, user_id=1, action_id="ack:missing", action="ack", card_id=card_id, now=base + 0.8)
    _, active = await service.act(chat_id=-100, thread_id=7, user_id=1, action_id="ack", action="ack", card_id=card_id, input_floor=77, now=base + 0.8)
    assert active["active_message_floor"] == 77
    for action_id in ("msg:77", "cmd:76"):
        with pytest.raises(ValueError, match="tabu_stale_card"):
            await service.act(chat_id=-100, thread_id=7, user_id=3, action_id=action_id,
                              action="text", card_id=card_id, text="deneme", submitted_at=base + 0.1, now=base + 0.9)
    status, current = await service.act(chat_id=-100, thread_id=7, user_id=3,
                                        action_id="msg:78", action="text", card_id=card_id,
                                        text="deneme", now=base + 0.9)
    assert status == "applied" and current["phase"] == "active"


@pytest.mark.asyncio
async def test_delivery_claims_expire_and_start_claim_has_lease():
    service, _ = await prepared()
    _, state = await service.act(chat_id=-100, thread_id=7, user_id=1,
                                 action_id="start", action="start", now=100)
    assert await service.claim_delivery(chat_id=-100, thread_id=7, kind="card", now=220) is None
    assert await service.claim_delivery(chat_id=-100, thread_id=7, kind="start", now=101) is None
    await service.act(chat_id=-100, thread_id=7, user_id=1, action_id="delivery",
                      action="delivered", card_id=state["card_id"], now=101)
    claimed = await service.claim_delivery(chat_id=-100, thread_id=7, kind="start", now=102)
    assert claimed and claimed["ack_retry_at"] == 132
    assert await service.claim_delivery(chat_id=-100, thread_id=7, kind="start", now=103) is None
    assert await service.claim_delivery(chat_id=-100, thread_id=7, kind="start", now=221) is None


@pytest.mark.asyncio
async def test_invite_duplicate_and_cooldown_do_not_replace_token(monkeypatch):
    service = TabuService(Repository())
    _, state = await service.open(chat_id=-100, user_id=1, user_name="A")
    monkeypatch.setattr("src.services.tabu_service.time.time", lambda: 1000)
    payload = await service.mint_invite(chat_id=-100, user_id=1,
                                        round_id=state["round_id"], request_id="q1")
    first = await service.current(-100)
    with pytest.raises(ValueError, match="tabu_pending_invite"):
        await service.mint_invite(chat_id=-100, user_id=1,
                                  round_id=state["round_id"], request_id="q1")
    with pytest.raises(ValueError, match="tabu_recent_invite"):
        await service.mint_invite(chat_id=-100, user_id=1,
                                  round_id=state["round_id"], request_id="q2")
    assert (await service.current(-100))["dm_invites"] == first["dm_invites"]
    monkeypatch.setattr("src.services.tabu_service.time.time", lambda: 1030)
    renewed = await service.mint_invite(chat_id=-100, user_id=1,
                                         round_id=state["round_id"], request_id="q2")
    assert renewed != payload
    monkeypatch.setattr("src.services.tabu_service.time.time", lambda: 1060)
    with pytest.raises(ValueError, match="tabu_pending_invite"):
        await service.mint_invite(chat_id=-100, user_id=1,
                                  round_id=state["round_id"], request_id="q1")


@pytest.mark.asyncio
async def test_settlement_marks_result_panel_dirty_again():
    class Leaderboard:
        async def record_score(self, **kwargs):
            return None

    repository = Repository()
    service = TabuService(repository, leaderboard=Leaderboard())
    _, state = await service.open(chat_id=-100, user_id=1, user_name="A")
    state = deepcopy(state)
    state.update(phase="finished", score_pending=True, panel_dirty=False,
                 panel_retry_at=9999, team_scores={"red": 1, "blue": 0})
    repository.rows[-100, "tabu"] = state
    await service.settle_scores(chat_id=-100)
    saved = await service.current(-100)
    assert saved["score_pending"] is False
    assert saved["panel_dirty"] is True
    assert saved["panel_retry_at"] == 0


@pytest.mark.asyncio
async def test_two_instances_close_one_card_only_once():
    service, _ = await prepared()
    second = TabuService(service.repository)
    _, state = await service.act(chat_id=-100, thread_id=7, user_id=1,
                                 action_id="start", action="start")
    card_id = state["card_id"]
    await service.act(chat_id=-100, thread_id=7, user_id=1,
                      action_id="delivered", action="delivered", card_id=card_id)
    await service.act(chat_id=-100, thread_id=7, user_id=1,
                      action_id="ack", action="ack", card_id=card_id, input_floor=100)
    calls = [instance.act(chat_id=-100, thread_id=7, user_id=1,
                          action_id="pass:one", action="pass", card_id=card_id)
             for instance in (service, second)]
    first, repeated = await asyncio.gather(*calls)
    assert {first[0], repeated[0]} == {"applied", "duplicate"}
    closed = await service.current(-100, 7)
    assert closed["phase"] == "appeal" and closed["passes_left"]["red"] == 1


@pytest.mark.asyncio
async def test_failed_rewards_keep_finished_match_until_retry_succeeds():
    class Leaderboard:
        def __init__(self):
            self.fail = True
            self.events = set()

        async def record_score(self, **kwargs):
            if self.fail:
                raise RuntimeError("store_down")
            self.events.add(kwargs["event_id"])

    repository, leaderboard = Repository(), Leaderboard()
    service = TabuService(repository, leaderboard=leaderboard)
    _, state = await service.open(chat_id=-100, user_id=1, user_name="A")
    state.update(phase="finished", score_pending=True)
    repository.rows[-100, "tabu"] = deepcopy(state)
    with pytest.raises(RuntimeError, match="store_down"):
        await service.open(chat_id=-100, user_id=2, user_name="B")
    assert (await service.current(-100))["round_id"] == state["round_id"]
    leaderboard.fail = False
    _, new_state = await service.open(chat_id=-100, user_id=2, user_name="B")
    assert new_state["round_id"] != state["round_id"]
    assert len(leaderboard.events) == 1
