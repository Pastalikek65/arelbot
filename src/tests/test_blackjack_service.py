from __future__ import annotations

import pytest

from src.services.blackjack_service import BlackjackService


class FakeDatabase:
    def __init__(self):
        self.states = {}

    async def get_game_state(self, chat_id, game_type):
        return self.states.get((int(chat_id), str(game_type)))

    async def save_game_state(self, chat_id, game_type, state):
        self.states[(int(chat_id), str(game_type))] = dict(state)

    async def delete_game_state(self, chat_id, game_type):
        self.states.pop((int(chat_id), str(game_type)), None)


class FakeEconomy:
    def __init__(self, balance=1000):
        self.balance_value = balance
        self.debits = []
        self.credits = []

    async def debit(self, user_id, chat_id, amount, **kwargs):
        if self.balance_value < amount:
            return False
        self.balance_value -= amount
        self.debits.append((user_id, chat_id, amount, kwargs))
        return True

    async def credit(self, user_id, chat_id, amount, **kwargs):
        self.balance_value += amount
        self.credits.append((user_id, chat_id, amount, kwargs))
        return True


class FakeLeaderboard:
    def __init__(self):
        self.scores = []

    async def record_score(self, **kwargs):
        self.scores.append(kwargs)
        return True


class DeterministicGame:
    def create_game(self, player_name, player_id):
        return {
            "deck": [("4", "♠️"), ("10", "♣️")],
            "player_hand": [("10", "♥️"), ("7", "♦️")],
            "dealer_hand": [("5", "♠️"), ("6", "♥️")],
            "player_name": player_name,
            "player_id": player_id,
            "status": "playing",
        }

    def hit(self, state):
        state["player_hand"].append(state["deck"].pop())
        state["status"] = "playing"

    def stand(self, state):
        state["status"] = "finished"

    def double_down(self, state):
        state["bet"] *= 2
        state["player_hand"].append(state["deck"].pop())
        state["status"] = "finished"

    def get_result(self, state):
        if state["status"] == "finished":
            return {"result": "win", "message": "Kazandın", "winnings": state["bet"]}
        return {"result": "push", "message": "Berabere", "winnings": 0}

    def format_game(self, state, reveal_dealer=False):
        return f"Blackjack {state['player_name']} bahis={state['bet']} reveal={reveal_dealer}"


@pytest.mark.asyncio
async def test_blackjack_start_persists_owner_scoped_hand_and_debits_once():
    database = FakeDatabase()
    economy = FakeEconomy()
    service = BlackjackService(database, economy_service=economy, game_factory=DeterministicGame)

    result = await service.start(chat_id=-10, user_id=7, user_name="Arel", bet=100, event_id="start-1")

    assert result["terminal"] is False
    assert economy.balance_value == 900
    assert len(economy.debits) == 1
    state = database.states[(-10, "blackjack:7")]
    assert state["player_id"] == 7
    assert state["base_bet"] == 100


@pytest.mark.asyncio
async def test_blackjack_callback_action_is_idempotent_and_settlement_is_persisted():
    database = FakeDatabase()
    economy = FakeEconomy()
    leaderboard = FakeLeaderboard()
    service = BlackjackService(
        database,
        economy_service=economy,
        leaderboard_service=leaderboard,
        game_factory=DeterministicGame,
    )
    await service.start(chat_id=-10, user_id=7, user_name="Arel", bet=100, event_id="start-2")

    ongoing = await service.action(chat_id=-10, user_id=7, action="hit", action_id="callback-1")
    assert ongoing["terminal"] is False
    with pytest.raises(ValueError, match="duplicate_action"):
        await service.action(chat_id=-10, user_id=7, action="hit", action_id="callback-1")

    finished = await service.action(chat_id=-10, user_id=7, action="stand", action_id="callback-2")
    assert finished["terminal"] is True
    assert database.states[(-10, "blackjack:7")]["terminal_panel_pending"]
    assert await service.acknowledge_terminal_panel(chat_id=-10, user_id=7,
        event_id=finished["state"]["event_id"], message_id=91)
    assert (-10, "blackjack:7") not in database.states
    assert economy.balance_value == 1100  # 100 stake + 200 payout returned
    assert leaderboard.scores[0]["event_id"] == "score:start-2"


@pytest.mark.asyncio
async def test_blackjack_double_requires_a_second_stake_and_owner():
    database = FakeDatabase()
    economy = FakeEconomy(balance=150)
    service = BlackjackService(database, economy_service=economy, game_factory=DeterministicGame)
    await service.start(chat_id=-10, user_id=7, user_name="Arel", bet=100, event_id="start-3")

    with pytest.raises(ValueError, match="insufficient_balance"):
        await service.action(chat_id=-10, user_id=7, action="double", action_id="callback-3")
    with pytest.raises(ValueError, match="game_expired"):
        await service.action(chat_id=-10, user_id=8, action="hit", action_id="callback-4")


@pytest.mark.asyncio
async def test_blackjack_cancel_removes_hand_and_refunds_charged_stake_once():
    database = FakeDatabase()
    economy = FakeEconomy()
    service = BlackjackService(database, economy_service=economy, game_factory=DeterministicGame)

    await service.start(chat_id=-10, user_id=7, user_name="Arel", bet=100, event_id="start-cancel")
    result = await service.cancel(chat_id=-10, user_id=7)

    assert result["status"] == "cancelled"
    assert (-10, "blackjack:7") not in database.states
    assert economy.balance_value == 1000
    assert economy.credits[-1][2] == 100

    with pytest.raises(ValueError, match="game_expired"):
        await service.cancel(chat_id=-10, user_id=7)
