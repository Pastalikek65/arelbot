"""Cross-family timing and durable reward-sink regressions."""

from copy import deepcopy

import pytest

from src.services.clue_arcade_service import ClueArcadeService
from src.services.grid_puzzles_service import GridPuzzlesService
from src.services.logic_puzzles_service import LogicPuzzlesService
from src.services.reasoning_arcade_service import ReasoningArcadeService
from src.services.social_arcade_service import SocialArcadeService
from src.services.tile2048_service import Tile2048Service
from src.services.word_arcade_service import WordArcadeService
from src.services.word_puzzles_service import WordPuzzlesService
from src.tests.test_clue_arcade import Repository, Ledger


@pytest.mark.asyncio
async def test_clue_telegram_second_and_canonical_panel_message_order():
    repo = Repository()
    service = ClueArcadeService(repo, leaderboard=Ledger())
    _, opened = await service.open(game_key="emoji_bilmece", chat_id=123,
                                   user_id=123, user_name="Bir", solo=True, now=100)
    claim = await service.claim_panel(game_key="emoji_bilmece", chat_id=123, now=100.9)
    assert await service.acknowledge_panel(game_key="emoji_bilmece", chat_id=123,
        round_id=opened["round_id"], expected_version=claim["version"],
        message_id=50, now=100.9)
    before = await service.current(game_key="emoji_bilmece", chat_id=123)
    for action_id, message_id, submitted_at in (
        ("prior-second", 51, 99),
        ("panel-message", 50, 100),
    ):
        with pytest.raises(ValueError, match="ca_stale_message"):
            await service.act(game_key="emoji_bilmece", chat_id=123, user_id=123,
                action_id=action_id, action="text", round_id=opened["round_id"],
                phase_seq=before["phase_seq"], text=before["card"]["answer"],
                message_id=message_id, submitted_at=submitted_at, now=100.95)
    assert await service.current(game_key="emoji_bilmece", chat_id=123) == before
    status, after = await service.act(game_key="emoji_bilmece", chat_id=123,
        user_id=123, action_id="after-panel", action="text",
        round_id=opened["round_id"], phase_seq=before["phase_seq"],
        text=before["card"]["answer"], message_id=51, submitted_at=100,
        now=100.95)
    assert status == "applied" and after["phase"] == "reveal"


@pytest.mark.asyncio
async def test_detective_telegram_second_and_canonical_panel_message_order():
    repo = Repository()
    service = ReasoningArcadeService(repo, leaderboard=Ledger())
    _, opened = await service.open(game_key="dedektif", chat_id=-100,
                                   user_id=1, user_name="Bir", now=100)
    await service.act(game_key="dedektif", chat_id=-100, user_id=1,
                      action_id="start", action="start", round_id=opened["round_id"],
                      now=100.2)
    claim = await service.claim_panel(game_key="dedektif", chat_id=-100, now=100.9)
    assert await service.acknowledge_panel(game_key="dedektif", chat_id=-100,
        round_id=opened["round_id"], expected_version=claim["version"],
        message_id=50, now=100.9)
    before = await service.current(game_key="dedektif", chat_id=-100)
    for action_id, message_id, submitted_at in (
        ("prior-second", 51, 99),
        ("panel-message", 50, 100),
    ):
        with pytest.raises(ValueError, match="ra_stale_message"):
            await service.act(game_key="dedektif", chat_id=-100, user_id=1,
                action_id=action_id, action="accuse", round_id=opened["round_id"],
                phase_seq=before["phase_seq"], text="yanlış / eski / mesaj",
                message_id=message_id, submitted_at=submitted_at, now=100.95)
    assert await service.current(game_key="dedektif", chat_id=-100) == before
    case = before["case"]
    person, place, item = case["solution"]
    accusation = (f"{case['suspects'][person]} / {case['places'][place]} / "
                  f"{case['objects'][item]}")
    status, after = await service.act(game_key="dedektif", chat_id=-100,
        user_id=1, action_id="after-panel", action="accuse",
        round_id=opened["round_id"], phase_seq=before["phase_seq"],
        text=accusation, message_id=51, submitted_at=100,
        now=100.95)
    assert status == "applied" and after != before


FAMILIES = (
    (ClueArcadeService, "emoji_bilmece", {"solo": True, "now": 100}),
    (Tile2048Service, "2048", {"now": 100}),
    (GridPuzzlesService, "mayin_tarlasi", {}),
    (LogicPuzzlesService, "sudoku", {"now": 100}),
    (ReasoningArcadeService, "kor_siralama", {"now": 100}),
    (WordArcadeService, "sozcuk_avi", {}),
    (WordPuzzlesService, "wordle_tr", {}),
    (SocialArcadeService, "amiral_batti", {"now": 100}),
)


@pytest.mark.asyncio
@pytest.mark.parametrize("service_class,game_key,options", FAMILIES)
async def test_reward_sink_requirement_survives_restart(service_class, game_key, options):
    repo, ledger = Repository(), Ledger()
    started = service_class(repo, leaderboard=ledger, economy=ledger)
    _, opened = await started.open(game_key=game_key, chat_id=-100,
                                   user_id=1, user_name="Bir", **options)
    assert opened["leaderboard_required"] is True
    assert opened["economy_required"] is True
    terminal = deepcopy(opened)
    terminal.update(phase="finished", score_pending=True,
                    version=opened["version"] + 1)
    assert await repo.compare_and_save_game_state(-100, game_key, terminal,
                                                   expected_version=opened["version"])
    restarted = service_class(repo, leaderboard=ledger, economy=None)
    with pytest.raises(RuntimeError, match="game_score_store_unavailable"):
        await restarted.settle_scores(game_key=game_key, chat_id=-100)
    pending = await restarted.current(game_key=game_key, chat_id=-100)
    assert pending["score_pending"] is True
    assert ledger.scores == {} and ledger.coins == {}


@pytest.mark.asyncio
async def test_legacy_reward_state_without_sink_marker_fails_closed():
    repo, ledger = Repository(), Ledger()
    service = Tile2048Service(repo, leaderboard=ledger, economy=ledger)
    _, opened = await service.open(game_key="2048", chat_id=-100,
                                   user_id=1, user_name="Bir", now=100)
    terminal = deepcopy(opened)
    terminal.pop("economy_required")
    terminal.update(phase="finished", score_pending=True, score=100,
                    won=True, version=opened["version"] + 1)
    assert await repo.compare_and_save_game_state(-100, "2048", terminal,
                                                   expected_version=opened["version"])
    without_coins = Tile2048Service(repo, leaderboard=ledger, economy=None)
    with pytest.raises(RuntimeError, match="game_score_store_unavailable"):
        await without_coins.settle_scores(game_key="2048", chat_id=-100)
    assert (await without_coins.current(game_key="2048", chat_id=-100))["score_pending"]
    await service.settle_scores(game_key="2048", chat_id=-100)
    assert ledger.scores and ledger.coins
    assert not (await service.current(game_key="2048", chat_id=-100))["score_pending"]


@pytest.mark.asyncio
async def test_explicitly_unfunded_match_cannot_mint_rewards_after_restart():
    repo, ledger = Repository(), Ledger()
    unfunded = Tile2048Service(repo, leaderboard=None, economy=None)
    _, opened = await unfunded.open(game_key="2048", chat_id=-100,
                                    user_id=1, user_name="Bir", now=100)
    assert opened["leaderboard_required"] is False
    assert opened["economy_required"] is False
    terminal = deepcopy(opened)
    terminal.update(phase="finished", score_pending=True, score=1000,
                    won=True, version=opened["version"] + 1)
    assert await repo.compare_and_save_game_state(-100, "2048", terminal,
                                                   expected_version=opened["version"])
    funded_restart = Tile2048Service(repo, leaderboard=ledger, economy=ledger)
    await funded_restart.settle_scores(game_key="2048", chat_id=-100)
    assert not (await funded_restart.current(game_key="2048", chat_id=-100))["score_pending"]
    assert ledger.scores == {} and ledger.coins == {}


@pytest.mark.asyncio
async def test_missing_leaderboard_does_not_credit_coin_first():
    repo, ledger = Repository(), Ledger()
    both_sinks = Tile2048Service(repo, leaderboard=ledger, economy=ledger)
    _, opened = await both_sinks.open(game_key="2048", chat_id=-100,
                                      user_id=1, user_name="Bir", now=100)
    terminal = deepcopy(opened)
    terminal.update(phase="finished", score_pending=True, score=1000,
                    won=True, version=opened["version"] + 1)
    assert await repo.compare_and_save_game_state(-100, "2048", terminal,
                                                   expected_version=opened["version"])
    missing_scores = Tile2048Service(repo, leaderboard=None, economy=ledger)
    with pytest.raises(RuntimeError, match="game_score_store_unavailable"):
        await missing_scores.settle_scores(game_key="2048", chat_id=-100)
    assert (await missing_scores.current(game_key="2048", chat_id=-100))["score_pending"]
    assert ledger.scores == {} and ledger.coins == {}
