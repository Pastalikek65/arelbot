"""BSON-backed full matches, delivery gates, and restart-safe ledgers."""

import re
from copy import deepcopy
from html import unescape

import pytest
from bson import BSON

from src.games import reasoning_arcade as rules
from src.services import reasoning_arcade_ui as ui
from src.services.reasoning_arcade_service import ReasoningArcadeService


class Repository:
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
        previous = self.rows.get((chat_id, key))
        if not previous or previous["version"] != expected_version:
            return False
        assert state["version"] == previous["version"] + 1
        self.rows[chat_id, key] = BSON.encode(state).decode()
        return True


class Ledger:
    def __init__(self):
        self.scores = {}
        self.coins = {}

    async def record_score(self, *, event_id, score, **_):
        self.scores.setdefault(event_id, score)

    async def credit(self, user_id, chat_id, amount, *, event_id, reason):
        self.coins.setdefault(event_id, (user_id, chat_id, amount, reason))


async def current(service, game):
    return await service.current(game_key=game, chat_id=-100, thread_id=7)


async def ack(service, game, round_id, now):
    claim = await service.claim_panel(game_key=game, chat_id=-100,
                                      thread_id=7, now=now)
    assert claim is not None
    assert await service.acknowledge_panel(game_key=game, chat_id=-100,
        thread_id=7, round_id=round_id, expected_version=claim["version"],
        message_id=50, now=now)
    return await current(service, game)


async def act(service, game, round_id, action, action_id, now,
              user_id=1, **kwargs):
    state = await current(service, game)
    return await service.act(game_key=game, chat_id=-100, thread_id=7,
        user_id=user_id, action=action, action_id=action_id,
        round_id=round_id, phase_seq=state["phase_seq"], now=now, **kwargs)


@pytest.mark.asyncio
async def test_rank_five_irreversible_choices_finish_and_settle_once():
    repo, ledger = Repository(), Ledger()
    service = ReasoningArcadeService(repo, leaderboard=ledger, economy=ledger)
    values = [11, 40, 23, 90, 7]
    _, opened = await service.open(game_key=rules.RANK, chat_id=-100,
        thread_id=7, user_id=1, user_name="Bir", now=100, values=values)
    round_id = opened["round_id"]
    assert rules.public_state(opened)["values"] == [11]
    with pytest.raises(ValueError, match="ra_wrong_phase"):
        await act(service, rules.RANK, round_id, "rank", "before-panel", 101, value=2)
    for step, rank in enumerate([2, 4, 3, 5, 1]):
        live = await ack(service, rules.RANK, round_id, 102 + step * 2)
        assert live["deadline"] == 192 + step * 2
        old_seq = live["phase_seq"]
        await act(service, rules.RANK, round_id, "rank", f"rank:{step}",
                  103 + step * 2, value=rank)
        if step < 4:
            with pytest.raises(ValueError, match="ra_stale_phase"):
                await service.act(game_key=rules.RANK, chat_id=-100, thread_id=7,
                    user_id=1, action="rank", action_id=f"stale:{step}",
                    round_id=round_id, phase_seq=old_seq, value=1, now=104 + step * 2)
    ended = await current(service, rules.RANK)
    assert ended["phase"] == "finished" and ended["players"][0]["score"] == 100
    assert ended["score_pending"] and ended["deadline"] is None
    restarted = ReasoningArcadeService(repo, leaderboard=ledger, economy=ledger)
    await restarted.settle_scores(game_key=rules.RANK, chat_id=-100, thread_id=7)
    await restarted.settle_scores(game_key=rules.RANK, chat_id=-100, thread_id=7)
    assert ledger.scores == {f"{rules.RANK}:-100:{round_id}:1": 100}
    assert set(ledger.coins) == {f"coin:{rules.RANK}:-100:{round_id}:1"}


@pytest.mark.asyncio
async def test_calendar_five_fictional_permutations_require_confirmation():
    service = ReasoningArcadeService(Repository(), leaderboard=Ledger())
    _, opened = await service.open(game_key=rules.CALENDAR, chat_id=-100,
        thread_id=7, user_id=1, user_name="Bir", now=100)
    rid = opened["round_id"]
    await act(service, rules.CALENDAR, rid, "start", "start", 101)
    for turn in range(5):
        live = await ack(service, rules.CALENDAR, rid, 102 + turn * 3)
        assert live["round_no"] == turn + 1
        assert "solution" not in rules.public_state(live)["puzzle"]
        assert "Kurgu Takvimi" in ui.render(live)
        with pytest.raises(ValueError, match="ra_incomplete_order"):
            await act(service, rules.CALENDAR, rid, "confirm", f"premature:{turn}",
                      102.1 + turn * 3)
        for index, event in enumerate(live["puzzle"]["solution"]):
            await act(service, rules.CALENDAR, rid, "choose",
                      f"choose:{turn}:{index}", 102.2 + turn * 3,
                      value=event)
        await act(service, rules.CALENDAR, rid, "confirm", f"confirm:{turn}",
                  103 + turn * 3)
    finished = await current(service, rules.CALENDAR)
    assert finished["phase"] == "finished" and finished["players"][0]["score"] == 25
    assert finished["deadline"] is None and finished["panel_wait_until"] is None


@pytest.mark.asyncio
async def test_true_false_ten_rounds_shield_risk_and_hidden_answer():
    service = ReasoningArcadeService(Repository(), leaderboard=Ledger())
    _, opened = await service.open(game_key=rules.TRUE_FALSE, chat_id=-100,
        thread_id=7, user_id=1, user_name="Bir", now=100)
    rid = opened["round_id"]
    await act(service, rules.TRUE_FALSE, rid, "join", "join", 101,
              user_id=2, user_name="İki")
    await act(service, rules.TRUE_FALSE, rid, "start", "start", 102)
    for turn in range(10):
        moment = 103 + turn * 10
        live = await ack(service, rules.TRUE_FALSE, rid, moment)
        assert live["round_no"] == turn + 1
        assert "answer" not in rules.public_state(live)["proposition"]
        answer = live["proposition"]["answer"]
        await act(service, rules.TRUE_FALSE, rid, "vote", f"owner:{turn}",
                  moment + 1, answer=answer, risk=False)
        after_owner = await current(service, rules.TRUE_FALSE)
        if turn < 2:
            await act(service, rules.TRUE_FALSE, rid, "vote", f"guest:{turn}",
                      moment + 2, user_id=2, answer=not answer, risk=turn == 0)
        else:
            assert after_owner["phase"] == "reveal"
        reveal = await current(service, rules.TRUE_FALSE)
        assert reveal["phase"] == "reveal"
        assert "answer" in rules.public_state(reveal)["proposition"]
        await ack(service, rules.TRUE_FALSE, rid, moment + 3)
        await service.advance(game_key=rules.TRUE_FALSE, chat_id=-100,
                              thread_id=7, round_id=rid, now=moment + 9)
    ended = await current(service, rules.TRUE_FALSE)
    assert ended["phase"] == "finished" and ended["players"][0]["score"] == 10
    assert ended["players"][1]["lives"] == 0 and ended["winner_ids"] == [1]


@pytest.mark.asyncio
async def test_detective_three_cases_plaintext_and_costed_clues():
    service = ReasoningArcadeService(Repository(), leaderboard=Ledger())
    _, opened = await service.open(game_key=rules.DETECTIVE, chat_id=-100,
        thread_id=7, user_id=1, user_name="Bir", now=100)
    rid = opened["round_id"]
    await act(service, rules.DETECTIVE, rid, "start", "start", 101)
    for turn in range(3):
        moment = 102 + turn * 4
        live = await ack(service, rules.DETECTIVE, rid, moment)
        assert "solution" not in rules.public_state(live)["case"]
        if turn == 0:
            await act(service, rules.DETECTIVE, rid, "hint", "hint:first", moment + .1)
            assert (await current(service, rules.DETECTIVE))["players"][0]["score"] == -1
        case = live["case"]
        person, place, item = case["solution"]
        accusation = (f"{case['suspects'][person]} / {case['places'][place]} / "
                      f"{case['objects'][item]}")
        await act(service, rules.DETECTIVE, rid, "accuse", f"accuse:{turn}",
                  moment + 1, text=accusation, message_id=100 + turn,
                  submitted_at=moment + 1)
    ended = await current(service, rules.DETECTIVE)
    assert ended["phase"] == "finished" and ended["players"][0]["score"] == 8


@pytest.mark.parametrize("game", rules.GAME_KEYS)
def test_callback_size_and_panel_text_bound(game):
    state = rules.new(game_key=game, chat_id=-(2**63 - 1), thread_id=2**63 - 1,
        owner_id=1, owner_name="😀" * 32, round_id="f" * 12, now=100)
    if game != rules.RANK:
        for user_id in range(2, (30 if game == rules.TRUE_FALSE else 12) + 1):
            state = rules.join(state, user_id=user_id, user_name="😀" * 32, now=101)
        state = rules.start(state, user_id=1, now=102)
    plain = unescape(re.sub(r"<[^>]*>", "", ui.render(state)))
    assert len(plain.encode("utf-16-le")) // 2 <= 4096
    assert all(len(button.callback_data.encode()) <= 64
               for row in ui.keyboard(state).inline_keyboard for button in row)
