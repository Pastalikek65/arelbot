"""Independent rules for the cooperative code and visual-difference matches."""

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock
import time

from src.games import logic_puzzles as game
from src.services.logic_puzzles_service import LogicPuzzlesService
from src.services import logic_puzzles_ui as ui
from src.tests.test_logic_puzzles_service import Repository, ack, open_game
from src.handlers.logic_puzzles_handler import LogicPuzzlesHandler
from src.games.puzzle_catalog import PUZZLES
from src.games.help_catalog import GAME_HELP
from src.services.menu_service import MenuService


def lobby(key):
    state = game.new(game_key=key, chat_id=-100, thread_id=7,
                     owner_id=1, owner_name="Bir", round_id="round", now=100, seed=19)
    return game.join(state, user_id=2, user_name="İki", now=101)


def ready_turn(state, now=102):
    state["phase"] = "turn" if state["game_key"] == "sifre_tahmin" else "spot"
    state["deadline"] = now + 90
    state["panel_wait_until"] = None
    return state


def test_codebreaker_feedback_counts_exact_and_misplaced_without_leaking_secret():
    state = game.start(lobby("sifre_tahmin"), user_id=1, now=102, secret="1234")
    assert state["phase"] == "turn_pending" and state["secret"] == "1234"
    assert "secret" not in game.public_state(state)
    ready_turn(state)
    for digit in "1243":
        state = game.code_digit(state, user_id=1, digit=digit, now=103)
    state = game.code_submit(state, user_id=1, now=104)
    assert state["history"][-1]["exact"] == 2
    assert state["history"][-1]["misplaced"] == 2
    assert state["turn_index"] == 1 and state["phase"] == "turn_pending"
    assert "secret" not in game.public_state(state)


def test_codebreaker_rejects_other_players_and_duplicate_digits_then_finishes_three_rounds():
    state = ready_turn(game.start(lobby("sifre_tahmin"), user_id=1, now=102, secret="1234"))
    with pytest.raises(ValueError, match="lp_not_turn"):
        game.code_digit(state, user_id=2, digit="1", now=103)
    state = game.code_digit(state, user_id=1, digit="1", now=103)
    with pytest.raises(ValueError, match="lp_duplicate_digit"):
        game.code_digit(state, user_id=1, digit="1", now=103)
    for digit in "234":
        state = game.code_digit(state, user_id=1, digit=digit, now=103)
    state = game.code_submit(state, user_id=1, now=104)
    assert state["phase"] == "round_result" and state["scores"]["1"] > 0
    for round_no in (2, 3):
        state["deadline"] = 106
        state = game.advance(state, now=107)
        assert state["round_no"] == round_no
        ready_turn(state, 108)
        actor = state["players"][state["turn_index"]]["id"]
        for digit in state["secret"]:
            state = game.code_digit(state, user_id=actor, digit=digit, now=109)
        state = game.code_submit(state, user_id=actor, now=110)
    state["deadline"] = 111
    state = game.advance(state, now=112)
    assert state["phase"] == "finished" and state["winner_id"] in {1, 2}


def test_difference_grid_hides_coordinates_and_scores_each_found_spot_once():
    state = game.start(lobby("fark_bulmaca"), user_id=1, now=102, seed=7)
    assert state["phase"] == "spot_pending" and len(state["left_grid"]) == 25
    assert len(state["diff_positions"]) == 2
    assert "diff_positions" not in game.public_state(state)
    ready_turn(state)
    with pytest.raises(ValueError, match="lp_not_player"):
        game.spot_pick(state, user_id=999, index=state["diff_positions"][0], now=103)
    first = state["diff_positions"][0]
    state = game.spot_pick(state, user_id=2, index=first, now=103)
    assert state["scores"]["2"] == 2
    with pytest.raises(ValueError, match="lp_already_found"):
        game.spot_pick(state, user_id=1, index=first, now=104)
    state = game.spot_pick(state, user_id=1, index=state["diff_positions"][1], now=105)
    assert state["phase"] == "round_result" and state["scores"]["1"] == 2


def test_difference_wrong_click_is_bounded_and_later_rounds_change_pattern():
    state = ready_turn(game.start(lobby("fark_bulmaca"), user_id=1, now=102, seed=7))
    wrong = next(index for index in range(25) if index not in state["diff_positions"])
    state = game.spot_pick(state, user_id=1, index=wrong, now=103)
    assert state["misses"]["1"] == 1 and state["scores"]["1"] == -1
    with pytest.raises(ValueError, match="lp_already_tried"):
        game.spot_pick(state, user_id=1, index=wrong, now=104)
    state["deadline"] = 105
    state = game.advance(state, now=106)
    assert state["phase"] == "round_result"
    state["deadline"] = 107
    state = game.advance(state, now=108)
    assert state["round_no"] == 2 and len(state["diff_positions"]) == 3


@pytest.mark.parametrize("key", ["sifre_tahmin", "fark_bulmaca"])
def test_new_lobbies_reject_a_thirteenth_player(key):
    state = game.new(game_key=key, chat_id=-100, thread_id=7,
                     owner_id=1, owner_name="Bir", round_id="round", now=100)
    for uid in range(2, 13):
        state = game.join(state, user_id=uid, user_name=str(uid), now=101)
    with pytest.raises(ValueError, match="lp_full"):
        game.join(state, user_id=13, user_name="On üç", now=102)


def test_code_hint_reveals_only_one_position_after_three_failed_guesses():
    state = ready_turn(game.start(lobby("sifre_tahmin"), user_id=1, now=102, secret="1234"))
    with pytest.raises(ValueError, match="lp_hint_unavailable"):
        game.code_hint(state, user_id=1, now=103)
    state["history"] = [{"user_id": 1, "guess": "5678", "exact": 0, "misplaced": 0}] * 3
    state = game.code_hint(state, user_id=1, now=104)
    assert state["revealed_digit"] == "1" and "secret" not in game.public_state(state)
    assert "İlk rakam: 1" in ui.render(state)


def test_difference_hint_cannot_be_bought_after_the_timer():
    state = ready_turn(game.start(lobby("fark_bulmaca"), user_id=1, now=102, seed=7))
    with pytest.raises(ValueError, match="lp_expired"):
        game.spot_hint(state, user_id=1, now=state["deadline"])


def test_found_difference_cell_is_visibly_locked():
    state = ready_turn(game.start(lobby("fark_bulmaca"), user_id=1, now=102, seed=7))
    state = game.spot_pick(state, user_id=1, index=state["diff_positions"][0], now=103)
    found = state["found"][0]
    button = ui.keyboard(state).inline_keyboard[found // 5][found % 5]
    assert button.text == "✅"
    assert button.to_dict().get("disabled") == {}
    assert button.callback_data is None


def test_selected_code_digit_is_locked_until_undone():
    state = ready_turn(game.start(lobby("sifre_tahmin"), user_id=1, now=102, secret="1234"))
    state = game.code_digit(state, user_id=1, digit="1", now=103)
    button = next(button for row in ui.keyboard(state).inline_keyboard
                  for button in row if button.text == "1")
    assert button.to_dict().get("disabled") == {}


@pytest.mark.asyncio
async def test_codebreaker_service_starts_clock_after_panel_delivery_and_uses_scoped_controls():
    service = LogicPuzzlesService(Repository())
    lobby = await open_game(service, "sifre_tahmin")
    await service.act(game_key="sifre_tahmin", chat_id=-100, thread_id=7,
        user_id=1, action_id="start", action="start", round_id=lobby["round_id"],
        secret="1234", now=101)
    pending = await service.current(game_key="sifre_tahmin", chat_id=-100, thread_id=7)
    assert pending["phase"] == "turn_pending" and pending["deadline"] is None
    state = await ack(service, "sifre_tahmin", lobby, now=102)
    assert state["phase"] == "turn"
    state = await ack(service, "sifre_tahmin", lobby, now=103)
    assert state["deadline"] == 163
    buttons = [button for row in ui.keyboard(state).inline_keyboard for button in row]
    assert any(button.text == "1" for button in buttons)
    data = next(button.callback_data for button in buttons if button.text == "1")
    assert ui.parse_callback(data)[0] == "sifre_tahmin"
    assert "1234" not in ui.render(state)
    await service.act(game_key="sifre_tahmin", chat_id=-100, thread_id=7,
        user_id=1, action_id="digit", action="code_digit", round_id=lobby["round_id"],
        round_no=1, value="1", now=104)
    current = await service.current(game_key="sifre_tahmin", chat_id=-100, thread_id=7)
    assert current["draft"] == "1"


@pytest.mark.asyncio
async def test_difference_service_buttons_are_scoped_and_no_answer_coordinates_leak():
    service = LogicPuzzlesService(Repository())
    lobby = await open_game(service, "fark_bulmaca")
    await service.act(game_key="fark_bulmaca", chat_id=-100, thread_id=7,
        user_id=1, action_id="start", action="start", round_id=lobby["round_id"], now=101)
    state = await ack(service, "fark_bulmaca", lobby, now=102)
    state = await ack(service, "fark_bulmaca", lobby, now=103)
    assert state["phase"] == "spot" and state["deadline"] == 193
    assert "diff_positions" not in ui.render(state)
    buttons = [button for row in ui.keyboard(state).inline_keyboard for button in row]
    assert len([button for button in buttons if len((button.callback_data or "").split(":")[-1]) == 2
                and (button.callback_data or "").split(":")[-1].startswith("f")]) == 25
    data = next(button.callback_data for button in buttons if (button.callback_data or "").endswith("f0"))
    assert ui.parse_callback(data)[0] == "fark_bulmaca"


def test_both_games_are_discoverable_with_full_turkish_rules():
    entries = {item.key: item for item in PUZZLES}
    for key, command in (("sifre_tahmin", "sifre"), ("fark_bulmaca", "fark")):
        assert entries[key].command == command
        assert key in GAME_HELP
    menu = MenuService()
    choices = [button.callback_data for category in ("guess", "boards")
               for row in menu.games_menu(category=category)[1].inline_keyboard for button in row]
    assert "game_start_sifre_tahmin" in choices
    assert "game_start_fark_bulmaca" in choices


@pytest.mark.asyncio
async def test_three_code_rounds_do_not_exceed_leaderboard_score_contract():
    class Scores:
        async def record_score(self, **kwargs):
            assert 0 <= kwargs["score"] <= 100

    repository = Repository()
    service = LogicPuzzlesService(repository, leaderboard=Scores())
    state = await open_game(service, "sifre_tahmin")
    key = (-100, "sifre_tahmin_t7")
    repository.rows[key].update(phase="finished", scores={"1": 27},
                                score_pending=True, winner_id=1)
    await service.settle_scores(game_key="sifre_tahmin", chat_id=-100, thread_id=7)
    assert repository.rows[key]["score_pending"] is False


@pytest.mark.asyncio
async def test_started_code_round_becomes_actionable_without_waiting_for_worker_tick():
    service = LogicPuzzlesService(Repository())
    moment = time.time()
    lobby = await open_game(service, "sifre_tahmin", now=moment)
    await service.act(game_key="sifre_tahmin", chat_id=-100, thread_id=7,
        user_id=1, action_id="start", action="start", round_id=lobby["round_id"],
        secret="1234", now=moment)
    current = await service.current(game_key="sifre_tahmin", chat_id=-100, thread_id=7)
    bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=40)),
                          edit_message_text=AsyncMock())
    await LogicPuzzlesHandler(service).process(bot, {"chat_id": -100, "state": current})
    current = await service.current(game_key="sifre_tahmin", chat_id=-100, thread_id=7)
    assert current["phase"] == "turn" and current["deadline"] is not None
    assert current["panel_dirty"] is False
    assert bot.send_message.await_count == 1
    assert bot.edit_message_text.await_count >= 1
