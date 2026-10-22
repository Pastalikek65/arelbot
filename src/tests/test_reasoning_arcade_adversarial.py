"""Adversarial CAS, stale delivery, private answer and scope checks."""

import asyncio

import pytest

from src.games import reasoning_arcade as rules
from src.services import reasoning_arcade_ui as ui
from src.services.reasoning_arcade_service import ReasoningArcadeService
from src.tests.test_reasoning_arcade_service import Ledger, Repository, ack, act, current


@pytest.mark.asyncio
async def test_cross_instance_rank_race_only_one_irreversible_slot_wins():
    repo = Repository()
    first, second = ReasoningArcadeService(repo), ReasoningArcadeService(repo)
    _, opened = await first.open(game_key=rules.RANK, chat_id=-100, thread_id=7,
                                 user_id=1, user_name="Bir", now=100,
                                 values=[11, 40, 23, 90, 7])
    live = await ack(first, rules.RANK, opened["round_id"], 101)
    assert await first.current(game_key=rules.RANK, chat_id=-100, thread_id=None) is None
    with pytest.raises(ValueError, match="ra_not_owner"):
        await act(first, rules.RANK, opened["round_id"], "rank", "wrong-actor",
                  102, user_id=2, value=1)
    results = await asyncio.wait_for(asyncio.gather(
        first.act(game_key=rules.RANK, chat_id=-100, thread_id=7,
            user_id=1, action="rank", action_id="race:first", round_id=opened["round_id"],
            phase_seq=live["phase_seq"], value=1, now=102),
        second.act(game_key=rules.RANK, chat_id=-100, thread_id=7,
            user_id=1, action="rank", action_id="race:second", round_id=opened["round_id"],
            phase_seq=live["phase_seq"], value=2, now=102),
        return_exceptions=True), 2)
    assert sum(isinstance(item, ValueError) for item in results) == 1
    current_state = await current(first, rules.RANK)
    assert current_state["step"] == 1 and sum(v is not None for v in current_state["assigned"]) == 1
    with pytest.raises(ValueError, match="ra_stale_phase"):
        await first.act(game_key=rules.RANK, chat_id=-100, thread_id=7,
            user_id=1, action="rank", action_id="old-phase", round_id=opened["round_id"],
            phase_seq=live["phase_seq"], value=3, now=103)


@pytest.mark.asyncio
async def test_stale_ack_repairs_newer_panel_and_late_ack_cancels_without_reward():
    service = ReasoningArcadeService(Repository())
    _, opened = await service.open(game_key=rules.RANK, chat_id=-100, thread_id=7,
                                   user_id=1, user_name="Bir", now=100)
    first = await service.claim_panel(game_key=rules.RANK, chat_id=-100,
                                      thread_id=7, now=100)
    second = await service.claim_panel(game_key=rules.RANK, chat_id=-100,
                                       thread_id=7, now=131)
    assert second
    assert await service.acknowledge_panel(game_key=rules.RANK, chat_id=-100,
        thread_id=7, round_id=opened["round_id"], expected_version=second["version"],
        message_id=50, now=132)
    assert not await service.acknowledge_panel(game_key=rules.RANK, chat_id=-100,
        thread_id=7, round_id=opened["round_id"], expected_version=first["version"],
        message_id=51, now=133)
    repaired = await current(service, rules.RANK)
    assert repaired["panel_message_id"] == 50 and repaired["panel_dirty"]
    await service.claim_panel(game_key=rules.RANK, chat_id=-100, thread_id=7, now=134)

    other = ReasoningArcadeService(Repository())
    _, waiting = await other.open(game_key=rules.RANK, chat_id=-100, thread_id=7,
                                  user_id=1, user_name="Bir", now=100)
    claim = await other.claim_panel(game_key=rules.RANK, chat_id=-100,
                                    thread_id=7, now=101)
    assert not await other.acknowledge_panel(game_key=rules.RANK, chat_id=-100,
        thread_id=7, round_id=waiting["round_id"], expected_version=claim["version"],
        message_id=60, now=waiting["panel_wait_until"])
    closed = await current(other, rules.RANK)
    assert closed["phase"] == "cancelled" and closed["deadline"] is None
    assert closed["panel_wait_until"] is None and not closed["score_pending"]


@pytest.mark.asyncio
async def test_delayed_clock_from_older_stage_cannot_skip_next_calendar_round():
    service = ReasoningArcadeService(Repository())
    _, opened = await service.open(game_key=rules.CALENDAR, chat_id=-100,
                                   thread_id=7, user_id=1, user_name="Bir", now=100)
    rid = opened["round_id"]
    await act(service, rules.CALENDAR, rid, "start", "start", 101)
    first = await ack(service, rules.CALENDAR, rid, 102)
    await service.advance(game_key=rules.CALENDAR, chat_id=-100,
                          thread_id=7, round_id=rid, now=first["deadline"])
    second = await current(service, rules.CALENDAR)
    assert second["round_no"] == 2 and second["deadline"] is None
    await service.advance(game_key=rules.CALENDAR, chat_id=-100,
                          thread_id=7, round_id=rid, now=first["deadline"])
    assert (await current(service, rules.CALENDAR))["round_no"] == 2


def test_hidden_future_rank_answer_vote_and_detective_solution_are_not_public():
    ranking = rules.new(game_key=rules.RANK, chat_id=-100, thread_id=7,
                        owner_id=1, owner_name="Bir", round_id="r", now=100,
                        values=[11, 40, 23, 90, 7])
    assert rules.public_state(ranking)["values"] == [11]
    assert "40" not in ui.render(ranking)
    assert all("40" not in str(button.callback_data)
               for row in ui.keyboard(ranking).inline_keyboard for button in row)
    vote = rules.new(game_key=rules.TRUE_FALSE, chat_id=-100, thread_id=7,
                     owner_id=1, owner_name="Bir", round_id="r", now=100)
    vote = rules.start(vote, user_id=1, now=101)
    public = rules.public_state(vote)
    assert "answer" not in public["proposition"]
    assert "explanation" not in public["proposition"]
    case = rules.new(game_key=rules.DETECTIVE, chat_id=-100, thread_id=7,
                     owner_id=1, owner_name="Bir", round_id="r", now=100)
    case = rules.start(case, user_id=1, now=101)
    assert "solution" not in rules.public_state(case)["case"]


@pytest.mark.asyncio
async def test_true_false_vote_does_not_reveal_answer_through_score_or_lives():
    service = ReasoningArcadeService(Repository())
    _, opened = await service.open(game_key=rules.TRUE_FALSE, chat_id=-100,
                                   thread_id=7, user_id=1, user_name="Bir", now=100)
    rid = opened["round_id"]
    await act(service, rules.TRUE_FALSE, rid, "join", "join", 101,
              user_id=2, user_name="İki")
    await act(service, rules.TRUE_FALSE, rid, "start", "start", 102)
    before = await ack(service, rules.TRUE_FALSE, rid, 103)
    answer = before["proposition"]["answer"]
    await act(service, rules.TRUE_FALSE, rid, "vote", "first-vote", 104,
              answer=answer, risk=True)
    interim = await current(service, rules.TRUE_FALSE)
    assert interim["players"][0]["score"] == 2
    public = rules.public_state(interim)
    assert public["players"][0]["score"] == 0
    assert public["players"][0]["lives"] == 3
    assert "answer" not in public["proposition"]
    assert "explanation" not in public["proposition"]
    assert "2p" not in ui.render(interim)


@pytest.mark.asyncio
async def test_detective_old_plaintext_cannot_be_applied_to_new_case():
    service = ReasoningArcadeService(Repository())
    _, opened = await service.open(game_key=rules.DETECTIVE, chat_id=-100,
                                   thread_id=7, user_id=1, user_name="Bir", now=100)
    rid = opened["round_id"]
    await act(service, rules.DETECTIVE, rid, "start", "start", 101)
    first = await ack(service, rules.DETECTIVE, rid, 102)
    case = first["case"]
    person, place, item = case["solution"]
    phrase = f"{case['suspects'][person]} / {case['places'][place]} / {case['objects'][item]}"
    await act(service, rules.DETECTIVE, rid, "accuse", "first-case", 103,
              text=phrase, message_id=100, submitted_at=103)
    second = await ack(service, rules.DETECTIVE, rid, 104)
    assert second["round_no"] == 2
    with pytest.raises(ValueError, match="ra_stale_message"):
        await act(service, rules.DETECTIVE, rid, "accuse", "late-first-case", 105,
                  text=phrase, message_id=101, submitted_at=103)
    assert (await current(service, rules.DETECTIVE))["players"][0]["accusations"] == 0


@pytest.mark.asyncio
async def test_detective_telegram_second_precision_accepts_live_same_second_message():
    service = ReasoningArcadeService(Repository())
    _, opened = await service.open(game_key=rules.DETECTIVE, chat_id=-100,
                                   thread_id=7, user_id=1, user_name="Bir", now=100)
    rid = opened["round_id"]
    await act(service, rules.DETECTIVE, rid, "start", "start", 101)
    live = await ack(service, rules.DETECTIVE, rid, 102.25)
    person, place, item = live["case"]["solution"]
    phrase = (f"{live['case']['suspects'][person]} / "
              f"{live['case']['places'][place]} / {live['case']['objects'][item]}")
    status, _ = await act(service, rules.DETECTIVE, rid, "accuse", "same-second", 102.5,
                          text=phrase, message_id=100, submitted_at=102)
    assert status == "applied"


@pytest.mark.asyncio
async def test_reward_retry_then_replacement_preserves_event_identity():
    class PartialLedger(Ledger):
        def __init__(self):
            super().__init__()
            self.fail_coin = True

        async def credit(self, user_id, chat_id, amount, *, event_id, reason):
            if self.fail_coin:
                self.fail_coin = False
                raise RuntimeError("temporary coin outage")
            await super().credit(user_id, chat_id, amount,
                                 event_id=event_id, reason=reason)

    repo, ledger = Repository(), PartialLedger()
    service = ReasoningArcadeService(repo, leaderboard=ledger, economy=ledger)
    _, opened = await service.open(game_key=rules.RANK, chat_id=-100, thread_id=7,
        user_id=1, user_name="Bir", now=100, values=[11, 40, 23, 90, 7])
    rid = opened["round_id"]
    for index, rank in enumerate([2, 4, 3, 5, 1]):
        await ack(service, rules.RANK, rid, 101 + index * 2)
        await act(service, rules.RANK, rid, "rank", f"choice:{index}",
                  102 + index * 2, value=rank)
    with pytest.raises(RuntimeError, match="temporary coin outage"):
        await service.settle_scores(game_key=rules.RANK, chat_id=-100, thread_id=7)
    assert (await current(service, rules.RANK))["score_pending"]
    restarted = ReasoningArcadeService(repo, leaderboard=ledger, economy=ledger)
    await restarted.settle_scores(game_key=rules.RANK, chat_id=-100, thread_id=7)
    await restarted.settle_scores(game_key=rules.RANK, chat_id=-100, thread_id=7)
    assert ledger.scores == {f"{rules.RANK}:-100:{rid}:1": 100}
    assert set(ledger.coins) == {f"coin:{rules.RANK}:-100:{rid}:1"}
    status, replacement = await restarted.open(game_key=rules.RANK, chat_id=-100,
        thread_id=7, user_id=2, user_name="İki", now=120)
    assert status == "created" and replacement["round_id"] != rid
