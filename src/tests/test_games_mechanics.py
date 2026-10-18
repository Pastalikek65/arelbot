"""Gameplay and restart contracts for blackjack and clue-driven games."""

from copy import deepcopy
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.games.blackjack import BlackjackGame
from src.games.hangman import apply_guess, available_keys, reveal_hint
from src.games.word_game import reveal_word_hint
from src.services.blackjack_service import BlackjackService


def _hand(player, dealer, *, deck=(), status="playing"):
    return {
        "player_hand": [(rank, "S") for rank in player],
        "dealer_hand": [(rank, "H") for rank in dealer],
        "deck": [(rank, "C") for rank in deck],
        "player_id": 7,
        "player_name": "Oyuncu",
        "bet": 100,
        "status": status,
    }


def test_two_natural_blackjacks_push_instead_of_paying_a_bonus():
    state = _hand(["A", "K"], ["A", "Q"], status="blackjack")
    assert BlackjackGame().get_result(state)["result"] == "push"
    assert BlackjackGame().get_result(state)["winnings"] == 0


def test_dealer_natural_beats_three_card_twenty_one():
    state = _hand(["7", "7", "7"], ["A", "K"], status="finished")
    assert BlackjackGame().get_result(state)["result"] == "lose"
    assert BlackjackGame().get_result(state)["winnings"] == -100


def test_hitting_twenty_one_completes_dealer_play_without_an_extra_callback():
    state = _hand(["10", "5"], ["10", "5"], deck=["2", "6"])
    assert BlackjackGame().hit(state) is state
    assert state["status"] == "finished"
    assert len(state["player_hand"]) == 3
    assert BlackjackGame().calculate_score(state["dealer_hand"]) == 17
    assert BlackjackGame().get_result(state)["result"] == "win"


@pytest.mark.parametrize("action", ["hit", "stand", "double_down"])
def test_finished_blackjack_actions_are_rejected_without_changing_the_hand(action):
    state = _hand(["10", "8"], ["10", "7"], deck=["2"], status="finished")
    before = deepcopy(state)
    with pytest.raises(ValueError, match="game_finished"):
        getattr(BlackjackGame(), action)(state)
    assert state == before


def test_double_is_only_available_before_the_first_hit():
    state = _hand(["2", "3", "4"], ["10", "7"], deck=["2"])
    before = deepcopy(state)
    with pytest.raises(ValueError, match="double_only_initial_hand"):
        BlackjackGame().double_down(state)
    assert state == before


@pytest.mark.parametrize("action", ["hit", "stand", "double_down"])
def test_deck_exhaustion_does_not_partially_mutate_or_double_the_stake(action):
    state = _hand(["10", "5"], ["2", "3"], deck=["6"])
    if action == "stand":
        state["deck"] = []
    before = deepcopy(state)
    with pytest.raises(ValueError, match="deck_exhausted"):
        getattr(BlackjackGame(), action)(state)
    assert state == before


class _Database:
    def __init__(self):
        self.states = {}

    async def get_game_state(self, chat_id, game_type):
        return deepcopy(self.states.get((chat_id, game_type)))

    async def save_game_state(self, chat_id, game_type, state):
        self.states[chat_id, game_type] = deepcopy(state)

    async def delete_game_state(self, chat_id, game_type):
        self.states.pop((chat_id, game_type), None)


class _Economy:
    def __init__(self):
        self.balance = 1000
        self.debits = []
        self.credits = []

    async def debit(self, _user_id, _chat_id, amount, **_kwargs):
        self.debits.append(amount)
        self.balance -= amount
        return True

    async def credit(self, _user_id, _chat_id, amount, **_kwargs):
        self.credits.append(amount)
        self.balance += amount
        return True


def _factory(player, dealer, *, deck=()):
    class FixedDeck(BlackjackGame):
        def _create_deck(self):
            # create_game pops the player pair, then the dealer pair.
            return ([(r, "C") for r in deck]
                    + [(r, "H") for r in reversed(dealer)]
                    + [(r, "S") for r in reversed(player)])
    return FixedDeck


@pytest.mark.parametrize(
    "player,dealer,expected_balance,expected_credit,expected_result",
    [
        (["A", "K"], ["A", "Q"], 1000, [100], "push"),
        (["10", "9"], ["A", "K"], 900, [], "lose"),
        (["A", "K"], ["10", "9"], 1150, [250], "blackjack"),
    ],
)
def test_initial_naturals_settle_the_charged_stake_once(
    player, dealer, expected_balance, expected_credit, expected_result,
):
    async def scenario():
        database, economy = _Database(), _Economy()
        service = BlackjackService(database, economy, game_factory=_factory(player, dealer))
        result = await service.start(chat_id=-10, user_id=7, user_name="Oyuncu", bet=100, event_id="initial")
        assert result["terminal"] is True
        assert result["result"]["result"] == expected_result
        assert economy.balance == expected_balance
        assert economy.debits == [100]
        assert economy.credits == expected_credit
        assert database.states == {}
        with pytest.raises(ValueError, match="game_expired"):
            await service.action(chat_id=-10, user_id=7, action="stand", action_id="late")
        assert economy.balance == expected_balance
    asyncio.run(scenario())


def test_double_down_debits_two_stakes_and_returns_the_doubled_winning_payout():
    async def scenario():
        database, economy = _Database(), _Economy()
        service = BlackjackService(database, economy, game_factory=_factory(["5", "6"], ["10", "7"], deck=["10"]))
        await service.start(chat_id=-10, user_id=7, user_name="Oyuncu", bet=100, event_id="double")
        result = await service.action(chat_id=-10, user_id=7, action="double", action_id="double-action")
        assert result["terminal"] is True
        assert result["winnings"] == 200
        assert economy.debits == [100, 100]
        assert economy.credits == [400]
        assert economy.balance == 1200
        assert database.states == {}
        with pytest.raises(ValueError, match="duplicate_action"):
            await service.action(chat_id=-10, user_id=7, action="double", action_id="double-action")
        assert economy.balance == 1200
    asyncio.run(scenario())


def test_hit_to_twenty_one_settles_once_and_removes_the_persisted_hand():
    async def scenario():
        database, economy = _Database(), _Economy()
        service = BlackjackService(database, economy, game_factory=_factory(["10", "5"], ["10", "5"], deck=["2", "6"]))
        await service.start(chat_id=-10, user_id=7, user_name="Oyuncu", bet=100, event_id="hit-21")
        result = await service.action(chat_id=-10, user_id=7, action="hit", action_id="last-card")
        assert result["terminal"] is True
        assert result["winnings"] == 100
        assert economy.balance == 1100
        assert economy.credits == [200]
        assert database.states == {}
        with pytest.raises(ValueError, match="game_expired"):
            await service.action(chat_id=-10, user_id=7, action="stand", action_id="unneeded-stand")
        assert economy.credits == [200]
    asyncio.run(scenario())


def test_unplayable_double_does_not_charge_a_second_stake_and_can_be_cancelled():
    async def scenario():
        database, economy = _Database(), _Economy()
        service = BlackjackService(database, economy, game_factory=_factory(["5", "6"], ["2", "3"], deck=["10"]))
        await service.start(chat_id=-10, user_id=7, user_name="Oyuncu", bet=100, event_id="short-deck")
        before = deepcopy(database.states)
        for _attempt in range(2):
            with pytest.raises(ValueError, match="deck_exhausted"):
                await service.action(chat_id=-10, user_id=7, action="double", action_id="retry")
            assert economy.balance == 900
            assert economy.debits == [100]
            assert database.states == before
        await service.cancel(chat_id=-10, user_id=7)
        assert economy.balance == 1000
        assert economy.credits == [100]
    asyncio.run(scenario())


@pytest.mark.parametrize(
    "player,draw,expected_balance,credits,result_name",
    [
        (["5", "6"], "9", 1000, [200], "push"),
        (["5", "6"], "8", 800, [], "lose"),
        (["9", "8"], "10", 800, [], "bust"),
    ],
)
def test_double_down_push_loss_and_bust_payouts(player, draw, expected_balance, credits, result_name):
    async def scenario():
        database, economy = _Database(), _Economy()
        service = BlackjackService(database, economy, game_factory=_factory(player, ["10", "Q"], deck=[draw]))
        await service.start(chat_id=-10, user_id=7, user_name="Oyuncu", bet=100, event_id="double-results")
        result = await service.action(chat_id=-10, user_id=7, action="double", action_id="double")
        assert result["terminal"] is True
        assert result["result"]["result"] == result_name
        assert economy.debits == [100, 100]
        assert economy.credits == credits
        assert economy.balance == expected_balance
        assert database.states == {}
    asyncio.run(scenario())


def test_word_clues_rotate_without_repeat_charges_after_a_restart():
    state = {"valid_words": ["kelime", "KELİME", "keder", "kiler", "at"], "found_words": [], "hints_used": 0}
    first = reveal_word_hint(state)
    assert first["hint"] == {"length": 6, "first": "K", "last": "E"}
    restored = json.loads(json.dumps(state))
    second = reveal_word_hint(restored)
    assert second["hint"] == {"length": 5, "first": "K", "last": "R"}
    before = deepcopy(restored)
    assert reveal_word_hint(restored)["status"] == "no_hint"
    assert restored == before
    assert restored["hints_used"] == 2


def test_word_clues_never_disclose_a_whole_two_letter_answer_or_a_found_word():
    state = {"valid_words": ["at", "kelime"], "found_words": ["kelime"], "hints_used": 0}
    before = deepcopy(state)
    assert reveal_word_hint(state)["status"] == "no_hint"
    assert state == before


@pytest.mark.parametrize("won", [False, True])
def test_hangman_terminal_rounds_cannot_be_resurrected_or_charged_another_hint(won):
    state = {
        "word": "kedi", "hidden": list("KEDI") if won else ["K", "E", "D", "_"],
        "guessed_letters": list("kedi") if won else list("ked"),
        "lives": 4 if won else 0, "hints_used": 0,
    }
    before = deepcopy(state)
    assert reveal_hint(state)["status"] == "no_hint"
    assert apply_guess(state, "i")["status"] == "invalid"
    assert available_keys(state) == []
    assert state == before


@pytest.mark.parametrize("terminal", [False, True])
def test_hangman_text_and_button_guesses_share_normalization_and_terminal_rules(terminal):
    from src.handlers.message_handler import MessageHandler

    async def scenario():
        handler = MessageHandler.__new__(MessageHandler)
        handler.data_port = _Database()
        handler._record_game_result = AsyncMock()
        state = {"word": "KEDI", "hidden": ["K", "E", "D", "_"] if terminal else ["K", "_", "_", "_"],
                 "guessed_letters": list("ked") if terminal else ["k"], "lives": 0 if terminal else 6}
        before = deepcopy(state)
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(message=message, effective_chat=SimpleNamespace(id=-10), effective_user=SimpleNamespace(id=7))
        context = SimpleNamespace(chat_data={"hangman": state}, job_queue=None)
        assert await handler._check_game_inputs(update, context, "i" if terminal else "K") is True
        handler._record_game_result.assert_not_awaited()
        assert state == before
        if terminal:
            assert "hangman" not in context.chat_data
        else:
            assert handler.data_port.states == {}
    asyncio.run(scenario())


def test_hangman_text_finish_charges_the_same_hint_penalty_as_buttons(monkeypatch):
    from src.handlers.message_handler import MessageHandler
    from src.games.engine import calculate_hangman_score
    monkeypatch.setattr("src.handlers.message_handler.time.time", lambda: 1005.0)

    async def scenario():
        handler = MessageHandler.__new__(MessageHandler)
        handler.data_port = _Database()
        handler._record_game_result = AsyncMock()
        state = {"word": "KEDI", "hidden": ["K", "E", "D", "_"], "guessed_letters": list("ked"),
                 "lives": 4, "max_lives": 6, "start_time": 1000.0, "hints_used": 2}
        update = SimpleNamespace(message=SimpleNamespace(reply_text=AsyncMock()), effective_chat=SimpleNamespace(id=-10))
        context = SimpleNamespace(chat_data={"hangman": state}, job_queue=None)
        await handler._check_game_inputs(update, context, "i")
        expected = calculate_hangman_score(5.0, lives_remaining=4, max_lives=6, hints_used=2)
        handler._record_game_result.assert_awaited_once_with(update, "hangman", expected, "win")
        assert "hangman" not in context.chat_data
    asyncio.run(scenario())
