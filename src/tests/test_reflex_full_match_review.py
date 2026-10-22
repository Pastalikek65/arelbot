"""Full durable Reflex match through actual delivery and score transitions."""
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
        self.rows[chat_id, key] = deepcopy(state)
        return True

    async def compare_and_save_game_state(self, chat_id, key, state, *, expected_version):
        previous = self.rows.get((chat_id, key))
        if previous is None or previous["version"] != expected_version:
            return False
        self.rows[chat_id, key] = deepcopy(state)
        return True


class Leaderboard:
    def __init__(self):
        self.events = {}
        self.fail_once = True

    async def record_score(self, *, event_id, user_id, score, **_):
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("temporary_score_outage")
        self.events[event_id] = (user_id, score)


class Economy:
    def __init__(self):
        self.events = {}

    async def credit(self, user_id, chat_id, amount, *, event_id, **_):
        self.events[event_id] = (user_id, chat_id, amount)


async def delivered(service, round_id, now):
    claim = await service.claim_panel(game_key="hizli_buton", chat_id=-100,
                                      thread_id=7, now=now)
    assert claim is not None
    assert await service.acknowledge_panel(game_key="hizli_buton", chat_id=-100,
        thread_id=7, round_id=round_id, expected_version=claim["version"],
        message_id=77, now=now)
    return await service.current(game_key="hizli_buton", chat_id=-100, thread_id=7)


@pytest.mark.asyncio
async def test_reflex_five_real_rounds_then_retryable_unique_reward():
    leaderboard, economy = Leaderboard(), Economy()
    service = LogicPuzzlesService(Repository(), leaderboard=leaderboard, economy=economy)
    _, created = await service.open(game_key="hizli_buton", chat_id=-100,
        thread_id=7, user_id=1, user_name="Bir", now=100)
    round_id = created["round_id"]
    await service.act(game_key="hizli_buton", chat_id=-100, thread_id=7,
        user_id=2, user_name="İki", action_id="join:2", action="join",
        round_id=round_id, now=101)
    await service.act(game_key="hizli_buton", chat_id=-100, thread_id=7,
        user_id=1, action_id="start", action="start", round_id=round_id,
        delay=2, symbol="◆", now=102)

    clock = 103.0
    for round_no in range(1, 6):
        waiting = await delivered(service, round_id, clock)
        assert waiting["phase"] == "waiting" and waiting["round_no"] == round_no
        assert waiting["deadline"] == waiting["go_at"] > clock

        await service.advance(game_key="hizli_buton", chat_id=-100,
                              thread_id=7, round_id=round_id, now=waiting["go_at"])
        pending = await service.current(game_key="hizli_buton", chat_id=-100, thread_id=7)
        assert pending["phase"] == "go_pending" and pending["deadline"] is None

        prepared = await delivered(service, round_id, waiting["go_at"] + 1)
        assert prepared["phase"] == "go" and prepared["deadline"] is None
        go = await delivered(service, round_id, waiting["go_at"] + 2)
        assert go["phase"] == "go" and go["deadline"] == waiting["go_at"] + 17

        await service.act(game_key="hizli_buton", chat_id=-100, thread_id=7,
            user_id=2, action_id=f"press:{round_no}", action="press",
            round_id=round_id, round_no=round_no, symbol=go["target"],
            now=waiting["go_at"] + 3)
        result = await delivered(service, round_id, waiting["go_at"] + 4)
        assert result["phase"] == "round_result"
        assert result["deadline"] == waiting["go_at"] + 9
        assert result["scores"]["2"] == 2 * round_no

        await service.advance(game_key="hizli_buton", chat_id=-100,
                              thread_id=7, round_id=round_id, now=result["deadline"])
        clock = result["deadline"] + 1

    finished = await service.current(game_key="hizli_buton", chat_id=-100, thread_id=7)
    assert finished["phase"] == "finished"
    assert finished["round_no"] == 5 and finished["winner_id"] == 2
    assert finished["score_pending"] is True

    with pytest.raises(RuntimeError, match="temporary_score_outage"):
        await service.open(game_key="hizli_buton", chat_id=-100, thread_id=7,
                           user_id=3, user_name="Üç", now=clock + 1)
    still = await service.current(game_key="hizli_buton", chat_id=-100, thread_id=7)
    assert still["round_id"] == round_id and still["score_pending"]

    await service.settle_scores(game_key="hizli_buton", chat_id=-100, thread_id=7)
    await service.settle_scores(game_key="hizli_buton", chat_id=-100, thread_id=7)
    assert leaderboard.events == {
        f"hizli_buton:-100:{round_id}:1": (1, 0),
        f"hizli_buton:-100:{round_id}:2": (2, 100),
    }
    assert economy.events == {
        f"coin:hizli_buton:-100:{round_id}:2": (2, -100, 5),
    }
