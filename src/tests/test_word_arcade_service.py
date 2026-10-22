"""CAS, delivery, private actor and reward boundaries for the word arcade."""
import asyncio
import time
from copy import deepcopy

import pytest

from src.services.word_arcade_service import WordArcadeService


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
async def test_two_instance_cas_topic_and_replay():
    repo = Repository()
    first, second = WordArcadeService(repo), WordArcadeService(repo)
    await first.open(game_key="harf_maratonu", chat_id=-100, thread_id=7,
                     user_id=1, user_name="Bir")
    state = await first.current(game_key="harf_maratonu", chat_id=-100, thread_id=7)
    await asyncio.wait_for(asyncio.gather(
        first.act(game_key="harf_maratonu", chat_id=-100, thread_id=7,
                  user_id=2, action_id="join2", action="join", user_name="İki",
                  round_id=state["round_id"]),
        second.act(game_key="harf_maratonu", chat_id=-100, thread_id=7,
                   user_id=3, action_id="join3", action="join", user_name="Üç",
                   round_id=state["round_id"])), 2)
    state = await first.current(game_key="harf_maratonu", chat_id=-100, thread_id=7)
    assert len(state["players"]) == 3
    status, _ = await second.act(game_key="harf_maratonu", chat_id=-100, thread_id=7,
                                 user_id=2, action_id="join2", action="join",
                                 round_id=state["round_id"])
    assert status == "duplicate"
    assert await first.current(game_key="harf_maratonu", chat_id=-100) is None


@pytest.mark.asyncio
async def test_timer_only_after_panel_and_late_ack_cancels():
    svc = WordArcadeService(Repository())
    await svc.open(game_key="sozcuk_avi", chat_id=-100, user_id=1, user_name="Bir")
    state = await svc.current(game_key="sozcuk_avi", chat_id=-100)
    await svc.act(game_key="sozcuk_avi", chat_id=-100, user_id=1,
                  action_id="start", action="start", round_id=state["round_id"])
    state = await svc.current(game_key="sozcuk_avi", chat_id=-100)
    assert state["phase"] == "hunt" and state["deadline"] is None
    with pytest.raises(ValueError, match="waiting_panel"):
        await svc.act(game_key="sozcuk_avi", chat_id=-100, user_id=1,
            action_id="guess", action="text", round_id=state["round_id"],
            text="kedi", message_id=200)
    lease = await svc.claim_panel(game_key="sozcuk_avi", chat_id=-100)
    await svc.acknowledge_panel(game_key="sozcuk_avi", chat_id=-100,
        round_id=state["round_id"], expected_version=lease["version"], message_id=100)
    state = await svc.current(game_key="sozcuk_avi", chat_id=-100)
    assert state["deadline"] is not None and state["input_floor"] == 100


@pytest.mark.asyncio
async def test_panel_delivery_failure_lease_and_late_ack_never_revive_clock():
    svc = WordArcadeService(Repository())
    await svc.open(game_key="sozcuk_avi", chat_id=-100, user_id=1, user_name="Bir")
    state = await svc.current(game_key="sozcuk_avi", chat_id=-100)
    await svc.act(game_key="sozcuk_avi", chat_id=-100, user_id=1,
                  action_id="start", action="start", round_id=state["round_id"], now=100)
    state = await svc.current(game_key="sozcuk_avi", chat_id=-100)
    claim = await svc.claim_panel(game_key="sozcuk_avi", chat_id=-100, now=101)
    assert claim is not None
    assert await svc.claim_panel(game_key="sozcuk_avi", chat_id=-100, now=102) is None
    assert await svc.claim_panel(game_key="sozcuk_avi", chat_id=-100, now=131)
    state = await svc.current(game_key="sozcuk_avi", chat_id=-100)
    assert state["deadline"] is None and state["panel_wait_until"] == 400
    await svc.acknowledge_panel(game_key="sozcuk_avi", chat_id=-100,
        round_id=state["round_id"], expected_version=state["version"],
        message_id=1000, now=400)
    state = await svc.current(game_key="sozcuk_avi", chat_id=-100)
    assert state["phase"] == "cancelled" and state["deadline"] is None
    assert not state["score_pending"]


@pytest.mark.asyncio
async def test_private_delivery_claim_retries_and_ack_requires_current_version():
    svc = WordArcadeService(Repository())
    await svc.open(game_key="yasak_harf", chat_id=-100, user_id=1, user_name="Bir")
    state = await svc.current(game_key="yasak_harf", chat_id=-100)
    await svc.act(game_key="yasak_harf", chat_id=-100, user_id=2,
                  action="join", action_id="join", user_name="İki", round_id=state["round_id"])
    await svc.act(game_key="yasak_harf", chat_id=-100, user_id=1,
                  action="start", action_id="start", round_id=state["round_id"],
                  card_order=[0, 1, 2, 3], now=100)
    state = await svc.current(game_key="yasak_harf", chat_id=-100)
    assert not state["private_dirty"]
    token = await svc.mint_invite(game_key="yasak_harf", chat_id=-100,
                                  user_id=1, round_id=state["round_id"], now=101)
    await svc.accept_invite(token, user_id=1, now=102)
    await svc.register_private(game_key="yasak_harf", chat_id=-100,
                               user_id=1, round_id=state["round_id"], message_id=501)
    claim = await svc.claim_private(game_key="yasak_harf", chat_id=-100, now=103)
    assert claim and claim["private_retry_at"] == 133
    assert await svc.claim_private(game_key="yasak_harf", chat_id=-100, now=104) is None
    retry = await svc.claim_private(game_key="yasak_harf", chat_id=-100, now=133)
    assert retry and retry["version"] > claim["version"]
    assert not await svc.acknowledge_private(game_key="yasak_harf", chat_id=-100,
        round_id=state["round_id"], expected_version=claim["version"], message_id=600)
    state = await svc.current(game_key="yasak_harf", chat_id=-100)
    assert state["private_dirty"] and state["private_retry_at"] == 0


@pytest.mark.asyncio
async def test_private_token_is_actor_bound_and_sealed_submission_not_in_public_view():
    svc = WordArcadeService(Repository())
    await svc.open(game_key="harf_maratonu", chat_id=-100, user_id=1, user_name="Bir")
    state = await svc.current(game_key="harf_maratonu", chat_id=-100)
    await svc.act(game_key="harf_maratonu", chat_id=-100, user_id=2,
                  action="join", action_id="join", user_name="İki", round_id=state["round_id"])
    await svc.act(game_key="harf_maratonu", chat_id=-100, user_id=1,
                  action="start", action_id="start", round_id=state["round_id"],
                  card_order=[0, 1, 2, 3, 4])
    state = await svc.current(game_key="harf_maratonu", chat_id=-100)
    token = await svc.mint_invite(game_key="harf_maratonu", chat_id=-100,
                                  user_id=2, round_id=state["round_id"])
    with pytest.raises(ValueError, match="invite_forbidden"):
        await svc.accept_invite(token, user_id=3)
    scope = await svc.accept_invite(token, user_id=2)
    assert scope == (-100, None, "harf_maratonu")
    await svc.register_private(game_key="harf_maratonu", chat_id=-100,
                               user_id=2, round_id=state["round_id"], message_id=400)
    await svc.register_private(game_key="harf_maratonu", chat_id=-100,
                               user_id=1, round_id=state["round_id"], message_id=410)
    lease = await svc.claim_panel(game_key="harf_maratonu", chat_id=-100)
    await svc.acknowledge_panel(game_key="harf_maratonu", chat_id=-100,
        round_id=state["round_id"], expected_version=lease["version"], message_id=100)
    state = await svc.current(game_key="harf_maratonu", chat_id=-100)
    await svc.act(game_key="harf_maratonu", chat_id=-100, user_id=2,
                  action="text", action_id="sealed", round_id=state["round_id"],
                  phase_seq=state["phase_seq"], text="kedi", private=True,
                  message_id=401, reply_to_message_id=400)
    state = await svc.current(game_key="harf_maratonu", chat_id=-100)
    assert state["submissions"]["2"] == "kedi"
    assert "kedi" not in str(svc.public_state(state))


@pytest.mark.asyncio
async def test_marathon_waits_for_all_private_routes_before_starting_clock():
    svc = WordArcadeService(Repository())
    await svc.open(game_key="harf_maratonu", chat_id=-100, user_id=1, user_name="Bir")
    state = await svc.current(game_key="harf_maratonu", chat_id=-100)
    await svc.act(game_key="harf_maratonu", chat_id=-100, user_id=2,
                  action="join", action_id="join", user_name="İki", round_id=state["round_id"])
    await svc.act(game_key="harf_maratonu", chat_id=-100, user_id=1,
                  action="start", action_id="start", round_id=state["round_id"],
                  card_order=[0, 1, 2, 3, 4], now=100)
    state = await svc.current(game_key="harf_maratonu", chat_id=-100)
    claim = await svc.claim_panel(game_key="harf_maratonu", chat_id=-100, now=101)
    await svc.acknowledge_panel(game_key="harf_maratonu", chat_id=-100,
        round_id=state["round_id"], expected_version=claim["version"],
        message_id=1000, now=102)
    state = await svc.current(game_key="harf_maratonu", chat_id=-100)
    assert state["deadline"] is None and state["panel_wait_until"] == 400
    await svc.register_private(game_key="harf_maratonu", chat_id=-100,
        user_id=1, round_id=state["round_id"], message_id=501)
    await svc.register_private(game_key="harf_maratonu", chat_id=-100,
        user_id=2, round_id=state["round_id"], message_id=502)
    claim = await svc.claim_panel(game_key="harf_maratonu", chat_id=-100, now=103)
    await svc.acknowledge_panel(game_key="harf_maratonu", chat_id=-100,
        round_id=state["round_id"], expected_version=claim["version"],
        message_id=1000, now=104)
    state = await svc.current(game_key="harf_maratonu", chat_id=-100)
    assert state["deadline"] == 164


@pytest.mark.asyncio
async def test_marathon_old_private_message_cannot_score_in_next_round():
    svc = WordArcadeService(Repository())
    await svc.open(game_key="harf_maratonu", chat_id=-100, user_id=1, user_name="Bir")
    state = await svc.current(game_key="harf_maratonu", chat_id=-100)
    base = state["created_at"]
    await svc.act(game_key="harf_maratonu", chat_id=-100, user_id=2,
                  action="join", action_id="join", user_name="İki", round_id=state["round_id"])
    await svc.act(game_key="harf_maratonu", chat_id=-100, user_id=1,
                  action="start", action_id="start", round_id=state["round_id"],
                  card_order=[0, 1, 2, 3, 4], now=base + 1)
    state = await svc.current(game_key="harf_maratonu", chat_id=-100)
    for uid in (1, 2):
        await svc.register_private(game_key="harf_maratonu", chat_id=-100,
            user_id=uid, round_id=state["round_id"], message_id=400 + uid)
    lease = await svc.claim_panel(game_key="harf_maratonu", chat_id=-100, now=base + 2)
    await svc.acknowledge_panel(game_key="harf_maratonu", chat_id=-100,
        round_id=state["round_id"], expected_version=lease["version"],
        message_id=100, now=base + 3)
    state = await svc.current(game_key="harf_maratonu", chat_id=-100)
    assert state["deadline"] == base + 63
    await svc.advance(game_key="harf_maratonu", chat_id=-100,
                      round_id=state["round_id"], now=state["deadline"])
    lease = await svc.claim_panel(game_key="harf_maratonu", chat_id=-100, now=base + 63)
    await svc.acknowledge_panel(game_key="harf_maratonu", chat_id=-100,
        round_id=state["round_id"], expected_version=lease["version"],
        message_id=100, now=base + 64)
    state = await svc.current(game_key="harf_maratonu", chat_id=-100)
    await svc.advance(game_key="harf_maratonu", chat_id=-100,
                      round_id=state["round_id"], now=state["deadline"])
    state = await svc.current(game_key="harf_maratonu", chat_id=-100)
    assert state["round_no"] == 2 and state["phase"] == "collect"
    lease = await svc.claim_panel(game_key="harf_maratonu", chat_id=-100, now=base + 70)
    await svc.acknowledge_panel(game_key="harf_maratonu", chat_id=-100,
        round_id=state["round_id"], expected_version=lease["version"],
        message_id=100, now=base + 71.8)
    state = await svc.current(game_key="harf_maratonu", chat_id=-100)
    with pytest.raises(ValueError, match="wa_stale_message"):
        await svc.act(game_key="harf_maratonu", chat_id=-100, user_id=2,
            action="text", action_id="delayed-old-dm", round_id=state["round_id"],
            phase_seq=state["phase_seq"], text="saat", private=True,
            message_id=600, reply_to_message_id=402,
            submitted_at=base + 50, now=base + 72)
    status, _ = await svc.act(game_key="harf_maratonu", chat_id=-100, user_id=2,
        action="text", action_id="live-new-dm", round_id=state["round_id"],
        phase_seq=state["phase_seq"], text="saat", private=True,
        message_id=601, reply_to_message_id=402,
        submitted_at=int(base + 71.8), now=base + 72)
    assert status == "applied"


@pytest.mark.asyncio
async def test_forbidden_private_delivery_ack_precedes_clue_clock():
    svc = WordArcadeService(Repository())
    await svc.open(game_key="yasak_harf", chat_id=-100, user_id=1, user_name="Bir")
    state = await svc.current(game_key="yasak_harf", chat_id=-100)
    await svc.act(game_key="yasak_harf", chat_id=-100, user_id=2,
                  action="join", action_id="join", user_name="İki", round_id=state["round_id"])
    await svc.act(game_key="yasak_harf", chat_id=-100, user_id=1,
                  action="start", action_id="start", round_id=state["round_id"],
                  card_order=[0, 1, 2, 3])
    state = await svc.current(game_key="yasak_harf", chat_id=-100)
    assert state["phase"] == "secret_delivery" and state["deadline"] is None
    token = await svc.mint_invite(game_key="yasak_harf", chat_id=-100,
                                  user_id=1, round_id=state["round_id"])
    assert await svc.accept_invite(token, user_id=1) == (-100, None, "yasak_harf")
    await svc.register_private(game_key="yasak_harf", chat_id=-100,
                               user_id=1, round_id=state["round_id"], message_id=400)
    delivery = await svc.claim_private(game_key="yasak_harf", chat_id=-100)
    assert delivery and delivery["target"] == "kalem"
    await svc.acknowledge_private(game_key="yasak_harf", chat_id=-100,
        round_id=state["round_id"], expected_version=delivery["version"], message_id=401)
    state = await svc.current(game_key="yasak_harf", chat_id=-100)
    await svc.act(game_key="yasak_harf", chat_id=-100, user_id=1,
        action="secret_ack", action_id="ack", round_id=state["round_id"],
        message_id=401)
    state = await svc.current(game_key="yasak_harf", chat_id=-100)
    assert state["phase"] == "clue" and state["deadline"] is None


@pytest.mark.asyncio
async def test_editing_a_valid_narrator_clue_into_target_ends_unscored_turn():
    svc = WordArcadeService(Repository())
    await svc.open(game_key="yasak_harf", chat_id=-100, user_id=1, user_name="Bir")
    state = await svc.current(game_key="yasak_harf", chat_id=-100)
    await svc.act(game_key="yasak_harf", chat_id=-100, user_id=2,
                  action="join", action_id="join", user_name="İki",
                  round_id=state["round_id"])
    await svc.act(game_key="yasak_harf", chat_id=-100, user_id=1,
                  action="start", action_id="start", round_id=state["round_id"],
                  card_order=[0, 1, 2, 3])
    state = await svc.current(game_key="yasak_harf", chat_id=-100)
    state.update(phase="clue", phase_seq=state["phase_seq"] + 1,
                 private_message_id=200, private_ack=True, deadline=time.time() + 60,
                 input_floor=100)
    state["version"] += 1
    assert await svc.repository.compare_and_save_game_state(-100, "yasak_harf", state,
                                                             expected_version=state["version"] - 1)
    await svc.act(game_key="yasak_harf", chat_id=-100, user_id=1,
        action="text", action_id="clue", round_id=state["round_id"],
        phase_seq=state["phase_seq"], message_id=501,
        text="Bu bir yazı aracı.")
    state = await svc.current(game_key="yasak_harf", chat_id=-100)
    assert state["clue_count"] == 1 and 501 in state["clue_message_ids"]
    await svc.act(game_key="yasak_harf", chat_id=-100, user_id=1,
        action="edit_clue", action_id="edit:501:1", round_id=state["round_id"],
        phase_seq=state["phase_seq"], message_id=501, text="kalem")
    state = await svc.current(game_key="yasak_harf", chat_id=-100)
    assert state["phase"] == "reveal" and state["scores"] == {"1": 0, "2": 0}


@pytest.mark.asyncio
async def test_reward_retry_preserves_pending_and_new_lobby_settles_it():
    repo, scores = Repository(), Scores()
    svc = WordArcadeService(repo, leaderboard=scores)
    await svc.open(game_key="sozcuk_avi", chat_id=-100, user_id=1, user_name="Bir")
    state = await svc.current(game_key="sozcuk_avi", chat_id=-100)
    await svc.act(game_key="sozcuk_avi", chat_id=-100, user_id=1,
                  action_id="start", action="start", round_id=state["round_id"])
    lease = await svc.claim_panel(game_key="sozcuk_avi", chat_id=-100)
    await svc.acknowledge_panel(game_key="sozcuk_avi", chat_id=-100,
        round_id=state["round_id"], expected_version=lease["version"], message_id=100)
    state = await svc.current(game_key="sozcuk_avi", chat_id=-100)
    await svc.act(game_key="sozcuk_avi", chat_id=-100, user_id=1,
        action_id="kedi", action="text", round_id=state["round_id"],
        text="kedi", message_id=101)
    state = await svc.current(game_key="sozcuk_avi", chat_id=-100)
    await svc.advance(game_key="sozcuk_avi", chat_id=-100, round_id=state["round_id"],
                      now=state["deadline"])
    state = await svc.current(game_key="sozcuk_avi", chat_id=-100)
    assert state["phase"] == "finished" and state["score_pending"]
    with pytest.raises(RuntimeError, match="temporary"):
        await svc.open(game_key="sozcuk_avi", chat_id=-100, user_id=1, user_name="Bir")
    assert (await svc.current(game_key="sozcuk_avi", chat_id=-100))["round_id"] == state["round_id"]
    _, newer = await svc.open(game_key="sozcuk_avi", chat_id=-100, user_id=1, user_name="Bir")
    assert newer["round_id"] != state["round_id"] and len(scores.events) == 1
