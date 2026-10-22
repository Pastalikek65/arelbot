"""CAS, timer-ack and effect retry checks for the logic puzzle arcade."""
import asyncio
from copy import deepcopy

import pytest

from src.services.logic_puzzles_service import LogicPuzzlesService


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
        prior = self.rows.get((chat_id, key))
        if prior is None or prior["version"] != expected_version:
            return False
        self.rows[(chat_id, key)] = deepcopy(state)
        return True


class Scores:
    def __init__(self, fail_once=False):
        self.events = set()
        self.fail_once = fail_once

    async def record_score(self, *, event_id, **_):
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("temporary")
        self.events.add(event_id)


async def open_game(service, game, *, now=100):
    _, state = await service.open(game_key=game, chat_id=-100, thread_id=7,
                                  user_id=1, user_name="Bir", now=now)
    return state


async def ack(service, game, state, *, now, message_id=40):
    claimed = await service.claim_panel(game_key=game, chat_id=-100, thread_id=7, now=now)
    assert claimed is not None, (now, await service.current(game_key=game, chat_id=-100, thread_id=7))
    assert await service.acknowledge_panel(game_key=game, chat_id=-100, thread_id=7,
        round_id=state["round_id"], expected_version=claimed["version"],
        message_id=message_id, now=now)
    return await service.current(game_key=game, chat_id=-100, thread_id=7)


@pytest.mark.asyncio
async def test_two_instances_join_once_and_topic_isolated():
    repository = Repository()
    a, b = LogicPuzzlesService(repository), LogicPuzzlesService(repository)
    state = await open_game(a, "sayisal_tahmin")
    await asyncio.wait_for(asyncio.gather(
        a.act(game_key="sayisal_tahmin", chat_id=-100, thread_id=7, user_id=2,
              user_name="İki", action_id="join2", action="join", round_id=state["round_id"], now=101),
        b.act(game_key="sayisal_tahmin", chat_id=-100, thread_id=7, user_id=3,
              user_name="Üç", action_id="join3", action="join", round_id=state["round_id"], now=101)), 2)
    current = await a.current(game_key="sayisal_tahmin", chat_id=-100, thread_id=7)
    assert {p["id"] for p in current["players"]} == {1, 2, 3}
    status, _ = await b.act(game_key="sayisal_tahmin", chat_id=-100, thread_id=7,
                             user_id=2, action_id="join2", action="join",
                             round_id=state["round_id"], now=101)
    assert status == "duplicate"
    assert await a.current(game_key="sayisal_tahmin", chat_id=-100, thread_id=None) is None


@pytest.mark.asyncio
async def test_number_turn_clock_starts_after_panel_ack_and_rejected_guess_keeps_deadline():
    service = LogicPuzzlesService(Repository())
    state = await open_game(service, "sayisal_tahmin")
    await service.act(game_key="sayisal_tahmin", chat_id=-100, thread_id=7,
        user_id=1, action_id="start", action="start", round_id=state["round_id"],
        secret=700, now=101)
    pending = await service.current(game_key="sayisal_tahmin", chat_id=-100, thread_id=7)
    assert pending["phase"] == "turn_pending" and pending["deadline"] is None
    assert (await service.advance(game_key="sayisal_tahmin", chat_id=-100, thread_id=7,
        round_id=state["round_id"], now=102))[0] == "ignored"
    prepared = await ack(service, "sayisal_tahmin", state, now=103)
    assert prepared["phase"] == "turn" and prepared["deadline"] is None
    live = await ack(service, "sayisal_tahmin", state, now=103)
    assert live["phase"] == "turn" and live["deadline"] == 133
    with pytest.raises(ValueError, match="lp_guess_out_of_range"):
        await service.act(game_key="sayisal_tahmin", chat_id=-100, thread_id=7,
            user_id=1, action_id="bad", action="guess", round_id=state["round_id"],
            round_no=1, value=1001, now=104, submitted_at=104, message_id=41)
    still = await service.current(game_key="sayisal_tahmin", chat_id=-100, thread_id=7)
    assert still["deadline"] == 133 and still["version"] == live["version"]
    with pytest.raises(ValueError, match="lp_stale_input"):
        await service.act(game_key="sayisal_tahmin", chat_id=-100, thread_id=7,
            user_id=1, action_id="old", action="guess", round_id=state["round_id"],
            round_no=1, value=700, now=104, submitted_at=102, message_id=39)
    await service.act(game_key="sayisal_tahmin", chat_id=-100, thread_id=7,
        user_id=1, action_id="good", action="guess", round_id=state["round_id"],
        round_no=1, value=700, now=105, submitted_at=105, message_id=41)
    result = await service.current(game_key="sayisal_tahmin", chat_id=-100, thread_id=7)
    assert result["phase"] == "round_result"
    status, _ = await service.advance(game_key="sayisal_tahmin", chat_id=-100,
        thread_id=7, round_id=state["round_id"], now=140,
        expected_phase="turn", expected_deadline=133, expected_round_no=1)
    assert status == "ignored"
    shown = await ack(service, "sayisal_tahmin", state, now=141)
    assert shown["phase"] == "round_result" and shown["deadline"] == 146
    await service.advance(game_key="sayisal_tahmin", chat_id=-100,
                          thread_id=7, round_id=state["round_id"], now=146)
    next_round = await service.current(game_key="sayisal_tahmin", chat_id=-100, thread_id=7)
    assert next_round["phase"] == "turn_pending" and next_round["round_no"] == 2


@pytest.mark.asyncio
async def test_reflex_delay_and_go_clock_start_only_on_actual_delivery():
    service = LogicPuzzlesService(Repository())
    state = await open_game(service, "hizli_buton")
    await service.act(game_key="hizli_buton", chat_id=-100, thread_id=7,
        user_id=2, user_name="İki", action_id="join", action="join",
        round_id=state["round_id"], now=101)
    await service.act(game_key="hizli_buton", chat_id=-100, thread_id=7,
        user_id=1, action_id="start", action="start", round_id=state["round_id"],
        delay=4, symbol="◆", now=102)
    waiting = await service.current(game_key="hizli_buton", chat_id=-100, thread_id=7)
    assert waiting["go_at"] is None and waiting["deadline"] is None
    waiting = await ack(service, "hizli_buton", state, now=103)
    assert waiting["go_at"] == 107 and waiting["deadline"] == 107
    await service.advance(game_key="hizli_buton", chat_id=-100, thread_id=7,
                          round_id=state["round_id"], now=107)
    pending = await service.current(game_key="hizli_buton", chat_id=-100, thread_id=7)
    assert pending["phase"] == "go_pending" and pending["deadline"] is None
    with pytest.raises(ValueError, match="lp_wrong_phase"):
        await service.act(game_key="hizli_buton", chat_id=-100, thread_id=7,
            user_id=1, action_id="earlygo", action="press", round_id=state["round_id"],
            round_no=1, symbol="◆", now=108)
    prepared = await ack(service, "hizli_buton", state, now=110)
    assert prepared["phase"] == "go" and prepared["deadline"] is None
    live = await ack(service, "hizli_buton", state, now=110)
    assert live["phase"] == "go" and live["deadline"] == 125
    with pytest.raises(ValueError, match="lp_stale_round"):
        await service.act(game_key="hizli_buton", chat_id=-100, thread_id=7,
            user_id=1, action_id="old", action="press", round_id=state["round_id"],
            round_no=0, symbol="◆", now=111)


@pytest.mark.asyncio
async def test_sudoku_completion_reward_retry_and_no_replacement_before_settlement():
    repository, scores = Repository(), Scores(fail_once=True)
    service = LogicPuzzlesService(repository, leaderboard=scores)
    state = await open_game(service, "sudoku")
    state = await ack(service, "sudoku", state, now=101)
    for row in range(4):
        for col in range(4):
            if state["givens"][row][col]:
                continue
            await service.act(game_key="sudoku", chat_id=-100, thread_id=7,
                user_id=1, action_id=f"cell:{row}:{col}", action="select",
                round_id=state["round_id"], row=row, col=col, now=102)
            await service.act(game_key="sudoku", chat_id=-100, thread_id=7,
                user_id=1, action_id=f"digit:{row}:{col}", action="digit",
                round_id=state["round_id"], value=state["solution"][row][col], now=102)
    finished = await service.current(game_key="sudoku", chat_id=-100, thread_id=7)
    assert finished["phase"] == "finished" and finished["score_pending"]
    with pytest.raises(RuntimeError, match="temporary"):
        await service.open(game_key="sudoku", chat_id=-100, thread_id=7,
                           user_id=2, user_name="İki", now=103)
    assert (await service.current(game_key="sudoku", chat_id=-100, thread_id=7))["round_id"] == state["round_id"]
    result, fresh = await service.open(game_key="sudoku", chat_id=-100, thread_id=7,
                                       user_id=2, user_name="İki", now=104)
    assert result == "created" and fresh["round_id"] != state["round_id"]
    assert scores.events == {f"sudoku:-100:{state['round_id']}:1"}


@pytest.mark.asyncio
async def test_sudoku_expiry_and_stale_delivery_repairs_panel():
    service = LogicPuzzlesService(Repository())
    state = await open_game(service, "sudoku")
    live = await ack(service, "sudoku", state, now=101)
    assert live["deadline"] == 1901
    claimed = await service.claim_panel(game_key="sudoku", chat_id=-100,
                                        thread_id=7, force=True, now=102)
    assert claimed
    await service.act(game_key="sudoku", chat_id=-100, thread_id=7,
        user_id=1, action_id="focus", action="focus_row", round_id=state["round_id"],
        row=1, now=103)
    assert not await service.acknowledge_panel(game_key="sudoku", chat_id=-100,
        thread_id=7, round_id=state["round_id"],
        expected_version=claimed["version"], message_id=40, now=104)
    current = await service.current(game_key="sudoku", chat_id=-100, thread_id=7)
    assert current["panel_dirty"] and current["deadline"] == 1901
    with pytest.raises(ValueError, match="lp_expired"):
        await service.act(game_key="sudoku", chat_id=-100, thread_id=7,
            user_id=1, action_id="late", action="hint", round_id=state["round_id"],
            now=1902)
    await service.advance(game_key="sudoku", chat_id=-100, thread_id=7,
                          round_id=state["round_id"], now=1902)
    ended = await service.current(game_key="sudoku", chat_id=-100, thread_id=7)
    assert ended["phase"] == "cancelled" and not ended["score_pending"]


@pytest.mark.asyncio
async def test_stale_first_send_preserves_canonical_panel_for_repair():
    service = LogicPuzzlesService(Repository())
    state = await open_game(service, "sudoku")
    first = await service.claim_panel(game_key="sudoku", chat_id=-100,
                                      thread_id=7, now=101)
    assert first["panel_message_id"] is None
    await service.act(game_key="sudoku", chat_id=-100, thread_id=7,
        user_id=1, action_id="select", action="select", round_id=state["round_id"],
        row=0, col=0, now=102)
    assert not await service.acknowledge_panel(game_key="sudoku", chat_id=-100,
        thread_id=7, round_id=state["round_id"],
        expected_version=first["version"], message_id=77, now=103)
    repaired = await service.current(game_key="sudoku", chat_id=-100, thread_id=7)
    assert repaired["panel_message_id"] == 77 and repaired["panel_dirty"]


@pytest.mark.asyncio
async def test_sudoku_late_select_digit_and_hint_cannot_win_before_worker_tick():
    service = LogicPuzzlesService(Repository(), leaderboard=Scores())
    created = await open_game(service, "sudoku")
    state = await ack(service, "sudoku", created, now=101)
    empty = next((r, c) for r in range(4) for c in range(4) if not state["givens"][r][c])
    await service.act(game_key="sudoku", chat_id=-100, thread_id=7,
        user_id=1, action_id="select:valid", action="select", round_id=created["round_id"],
        row=empty[0], col=empty[1], now=102)
    before = await service.current(game_key="sudoku", chat_id=-100, thread_id=7)
    expired_at = before["deadline"] + .001
    for action, kwargs in (
        ("select", {"row": empty[0], "col": empty[1]}),
        ("digit", {"row": empty[0], "col": empty[1],
                   "value": before["solution"][empty[0]][empty[1]]}),
        ("hint", {}),
        ("undo", {}),
    ):
        with pytest.raises(ValueError, match="lp_expired"):
            await service.act(game_key="sudoku", chat_id=-100, thread_id=7,
                user_id=1, action_id=f"late:{action}", action=action,
                round_id=created["round_id"], now=expired_at, **kwargs)
    unchanged = await service.current(game_key="sudoku", chat_id=-100, thread_id=7)
    assert unchanged["version"] == before["version"]
    assert unchanged["board"] == before["board"]
    assert unchanged["phase"] == "active" and not unchanged["score_pending"]
    await service.act(game_key="sudoku", chat_id=-100, thread_id=7,
        user_id=1, action_id="late:cancel", action="stop", round_id=created["round_id"],
        now=expired_at)
    cancelled = await service.current(game_key="sudoku", chat_id=-100, thread_id=7)
    assert cancelled["phase"] == "cancelled" and not cancelled["score_pending"]


@pytest.mark.asyncio
async def test_five_number_rounds_auto_progress_and_reward_once():
    scores = Scores()
    service = LogicPuzzlesService(Repository(), leaderboard=scores)
    state = await open_game(service, "sayisal_tahmin")
    await service.act(game_key="sayisal_tahmin", chat_id=-100, thread_id=7,
        user_id=1, action_id="start", action="start", round_id=state["round_id"],
        secret=1, now=101)
    for round_no in range(1, 6):
        moment = 100 + round_no * 10
        await ack(service, "sayisal_tahmin", state, now=moment)
        live = await ack(service, "sayisal_tahmin", state, now=moment + 1)
        assert live["round_no"] == round_no and live["phase"] == "turn"
        await service.act(game_key="sayisal_tahmin", chat_id=-100, thread_id=7,
            user_id=1, action_id=f"guess:{round_no}", action="guess",
            round_id=state["round_id"], round_no=round_no, value=live["secret"],
            message_id=41, submitted_at=moment + 2, now=moment + 2)
        await ack(service, "sayisal_tahmin", state, now=moment + 3)
        step, _ = await service.advance(game_key="sayisal_tahmin", chat_id=-100, thread_id=7,
                                        round_id=state["round_id"], now=moment + 8)
        current = await service.current(game_key="sayisal_tahmin", chat_id=-100, thread_id=7)
        assert current["phase"] in {"turn_pending", "finished"}, (round_no, step, current["phase"], current["deadline"])
    finished = await service.current(game_key="sayisal_tahmin", chat_id=-100, thread_id=7)
    assert finished["phase"] == "finished" and finished["score_pending"]
    await service.settle_scores(game_key="sayisal_tahmin", chat_id=-100, thread_id=7)
    await service.settle_scores(game_key="sayisal_tahmin", chat_id=-100, thread_id=7)
    assert scores.events == {f"sayisal_tahmin:-100:{state['round_id']}:1"}
