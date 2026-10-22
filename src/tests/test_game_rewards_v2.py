"""Reward eligibility and retry boundaries across durable game services."""
from copy import deepcopy

import pytest

from src.games import card_table, social_arcade, logic_puzzles
from src.services.card_table_service import CardTableService
from src.services.social_arcade_service import SocialArcadeService
from src.services.spy_service import SpyService
from src.services.logic_puzzles_service import LogicPuzzlesService
from src.ui import card_table_ui, clue_arcade_ui, tile2048_ui


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


class Ledger:
    def __init__(self):
        self.rows = {}

    async def record_score(self, *, event_id, score, **_):
        self.rows.setdefault(event_id, score)
        return False if event_id in self.rows else True


class Economy:
    def __init__(self, *, fail_once=False):
        self.fail_once = fail_once
        self.rows = {}

    async def credit(self, user_id, chat_id, amount, *, reason, event_id):
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("temporary coin outage")
        if event_id in self.rows:
            return False
        self.rows[event_id] = (user_id, chat_id, amount, reason)
        return True


@pytest.mark.asyncio
async def test_completed_card_partial_coin_failure_retries_after_restart_once():
    repo, scores, coins = Repository(), Ledger(), Economy(fail_once=True)
    row = card_table.new(game_key="pisti", chat_id=-100, owner_id=1,
                         owner_name="Bir", now=100)
    row = card_table.join(row, user_id=2, user_name="İki", now=100)
    row.update(phase="finished", reason="cards_exhausted", winner_ids=[1],
               score_pending=True, scores={"1": 18, "2": 0})
    repo.rows[(-100, "pisti")] = row
    first = CardTableService(repo, leaderboard=scores, economy=coins)
    with pytest.raises(RuntimeError, match="coin outage"):
        await first.settle_scores(game_key="pisti", chat_id=-100)
    assert (await first.current(game_key="pisti", chat_id=-100))["score_pending"]
    second = CardTableService(repo, leaderboard=scores, economy=coins)
    await second.settle_scores(game_key="pisti", chat_id=-100)
    await first.settle_scores(game_key="pisti", chat_id=-100)
    assert list(coins.rows) == [f"coin:pisti:-100:{row['round_id']}:1"]
    assert list(scores.rows.values()) == [18, 0]
    assert not (await second.current(game_key="pisti", chat_id=-100))["score_pending"]


@pytest.mark.asyncio
async def test_zero_progress_winner_cannot_gain_artificial_ten_points_or_coin():
    repo, scores, coins = Repository(), Ledger(), Economy()
    row = card_table.new(game_key="pisti", chat_id=-100, owner_id=1,
                         owner_name="Bir", now=100)
    row.update(phase="finished", reason="cards_exhausted", winner_ids=[1],
               score_pending=True)
    repo.rows[(-100, "pisti")] = row
    await CardTableService(repo, leaderboard=scores, economy=coins).settle_scores(
        game_key="pisti", chat_id=-100)
    assert list(scores.rows.values()) == [0]
    assert not coins.rows


@pytest.mark.asyncio
async def test_social_zero_point_winner_does_not_gain_artificial_reward():
    repo, scores, coins = Repository(), Ledger(), Economy()
    row = social_arcade.new(game_key="amiral_batti", chat_id=-100,
                            thread_id=None, owner_id=1, owner_name="Bir",
                            round_id="zero", now=100)
    row.update(phase="finished", winner_ids=[1], score_pending=True)
    repo.rows[(-100, "amiral_batti")] = row
    await SocialArcadeService(repo, leaderboard=scores, economy=coins).settle_scores(
        game_key="amiral_batti", chat_id=-100)
    assert list(scores.rows.values()) == [0]
    assert not coins.rows


@pytest.mark.asyncio
async def test_spy_departure_has_public_outcome_but_no_reward():
    repo, scores, coins = Repository(), Ledger(), Economy()
    svc = SpyService(repo, leaderboard=scores, economy=coins)
    await svc.open(chat_id=-100, user_id=1, user_name="Bir")
    row = await svc.current(-100)
    row.update(phase="finished", reason="spy_left", winner="group",
               score_pending=True)
    repo.rows[(-100, "casus")] = row
    await SpyService(repo, leaderboard=scores, economy=coins).settle_scores(chat_id=-100)
    assert not scores.rows and not coins.rows
    assert not (await svc.current(-100))["score_pending"]


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["sayisal_tahmin", "hizli_buton"])
async def test_five_idle_rounds_record_no_coin(key):
    repo, scores, coins = Repository(), Ledger(), Economy()
    row = logic_puzzles.new(game_key=key, chat_id=-100, thread_id=None,
                            owner_id=1, owner_name="Bir", round_id="idle", now=100)
    row.update(phase="finished", round_no=5, score_pending=True, winner_id=None,
               scores={"1": 0})
    repo.rows[(-100, key)] = row
    await LogicPuzzlesService(repo, leaderboard=scores, economy=coins).settle_scores(
        game_key=key, chat_id=-100)
    assert list(scores.rows.values()) == [0]
    assert not coins.rows


def test_card_panel_distinguishes_delivery_clock_and_unrewarded_forfeit():
    row = card_table.new(game_key="pisti", chat_id=-100, owner_id=1,
                         owner_name="Bir", now=100)
    row = card_table.join(row, user_id=2, user_name="İki", now=100)
    for uid in (1, 2):
        row = card_table.ready(row, user_id=uid)
    row = card_table.start(row, user_id=1, now=101, seed=2)
    assert "Süre başlamadı" in card_table_ui.group_text(row)
    row["deadline"], row["phase_ready"] = 161, True
    assert "Sıra bitişi:" in card_table_ui.group_text(row)
    row = card_table.act(row, user_id=2, action="leave", now=102)
    assert "Hükmen sonuç" in card_table_ui.group_text(row)


def test_clue_and_2048_panels_show_absolute_deadline_and_waiting_state():
    clue = {"game_key": "bes_ipucu", "phase": "lobby", "players": [],
            "lobby_expires_at": 200}
    assert "Katılım bitişi:" in clue_arcade_ui.render(clue)
    tile = {"game_key": "2048", "phase": "playing", "players": [{"name": "Bir"}],
            "board": [[0] * 4 for _ in range(4)], "score": 0, "moves": 0,
            "best_tile": 2, "won": False, "deadline": None}
    assert "Süre başlamadı" in tile2048_ui.render(tile)
    tile["deadline"] = 200
    assert "Hamle bitişi:" in tile2048_ui.render(tile)
    tile.update(phase="finished", won=False, reason="move_limit", score_pending=False)
    assert "Hamle sınırına" in tile2048_ui.render(tile)


def test_pisti_equal_result_is_labeled_draw_instead_of_two_winners():
    row = card_table.new(game_key="pisti", chat_id=-100, owner_id=1,
                         owner_name="Bir", now=100)
    row = card_table.join(row, user_id=2, user_name="İki", now=100)
    for uid in (1, 2):
        row = card_table.ready(row, user_id=uid)
    row = card_table.start(row, user_id=1, now=101, seed=2)
    row.update(phase="finished", reason="cards_exhausted", team_scores=[16, 16],
               winner_ids=[1, 2], score_pending=True)
    panel = card_table_ui.group_text(row)
    assert "Berabere" in panel and "Kazanan:" not in panel
