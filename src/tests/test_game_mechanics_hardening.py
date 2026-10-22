"""Regression coverage for concurrent and time-bounded game mechanics."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from src.games.batak_round import BatakRound
from src.games.action_ledger import GameActionLedger
from src.games.guess_age import AgeAsset, new_guess_age_state, round_expired, submit_guess
from src.games.state_store import persist_game
from src.games.uno import UnoError, draw_card, new_uno_state, play_card, start_round
from src.services.batak_service import BatakService
from src.services.blackjack_service import BlackjackService
from src.services.guess_age_service import GuessAgeService, GuessAgeServiceError
from src.services.roulette_service import RouletteService
from src.services.uno_service import UnoService


class SlowGameDatabase:
    def __init__(self) -> None:
        self.states: dict[tuple[int, str], dict] = {}

    async def get_game_state(self, chat_id: int, game_type: str):
        await asyncio.sleep(0)
        value = self.states.get((int(chat_id), str(game_type)))
        return None if value is None else dict(value)

    async def save_game_state(self, chat_id: int, game_type: str, state: dict):
        await asyncio.sleep(0)
        self.states[(int(chat_id), str(game_type))] = dict(state)

    async def delete_game_state(self, chat_id: int, game_type: str):
        await asyncio.sleep(0)
        self.states.pop((int(chat_id), str(game_type)), None)


class ProductionUnavailableDatabase:
    """Production-shaped port used to prove game state fails closed."""

    __module__ = "src.database"
    available = False


class ProductionSaveFailureDatabase:
    """A reachable production-shaped port whose write path is broken."""

    __module__ = "src.database"
    available = True

    async def get_game_state(self, chat_id: int, game_type: str):
        return None

    async def save_game_state(self, chat_id: int, game_type: str, state: dict):
        raise OSError("mongo write failed")


class DegradedSaveFailureDatabase:
    """Non-production adapter that is allowed to keep a local test state."""

    available = True

    async def get_game_state(self, chat_id: int, game_type: str):
        return None

    async def save_game_state(self, chat_id: int, game_type: str, state: dict):
        raise OSError("temporary local adapter failure")


class RecordingEconomy:
    def __init__(self) -> None:
        self.debits: list[tuple] = []
        self._events: set[str] = set()

    async def debit(self, user_id: int, chat_id: int, amount: int, **kwargs):
        event_id = str(kwargs.get("event_id") or "")
        if event_id in self._events:
            return True
        self._events.add(event_id)
        self.debits.append((user_id, chat_id, amount, kwargs))
        return True


class FailingFinalBlackjackWriteDatabase:
    __module__ = "src.database"
    available = True

    def __init__(self) -> None:
        self.states: dict[tuple[int, str], dict] = {}
        self.save_count = 0

    async def get_game_state(self, chat_id: int, game_type: str):
        value = self.states.get((int(chat_id), str(game_type)))
        return None if value is None else dict(value)

    async def save_game_state(self, chat_id: int, game_type: str, state: dict):
        self.save_count += 1
        if self.save_count == 2:
            raise OSError("transient final write failure")
        self.states[(int(chat_id), str(game_type))] = dict(state)

    async def delete_game_state(self, chat_id: int, game_type: str):
        self.states.pop((int(chat_id), str(game_type)), None)


def test_batak_rejects_negative_card_indexes_and_preserves_three_player_talon():
    state = BatakRound.lobby(1, "bir")
    BatakRound.join(state, 2, "iki")
    BatakRound.join(state, 3, "uc")
    BatakRound.start(state, rng=__import__("random").Random(7))

    assert sum(len(hand) for hand in state["hands"].values()) + len(state["talon"]) == 52
    with pytest.raises(ValueError, match="invalid_card"):
        BatakRound.play(state, state["current_turn"], -1)


@pytest.mark.asyncio
async def test_batak_concurrent_joins_do_not_lose_a_player():
    database = SlowGameDatabase()
    service = BatakService(database)
    await service.create_lobby(chat_id=-100, owner_id=1, owner_name="kurucu")

    await asyncio.gather(
        service.join(chat_id=-100, user_id=2, user_name="iki", action_id="join-2"),
        service.join(chat_id=-100, user_id=3, user_name="uc", action_id="join-3"),
        service.join(chat_id=-100, user_id=4, user_name="dort", action_id="join-4"),
    )
    state = database.states[(-100, "batak")]
    assert [player["id"] for player in state["players"]] == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_roulette_concurrent_joins_are_serialized():
    database = SlowGameDatabase()
    service = RouletteService(database)
    await service.create_lobby(chat_id=-100, owner_id=1, owner_name="kurucu")

    await asyncio.gather(
        service.join(chat_id=-100, user_id=2, user_name="iki", action_id="join-2"),
        service.join(chat_id=-100, user_id=3, user_name="uc", action_id="join-3"),
    )
    state = database.states[(-100, "roulette")]
    assert state["players"] == [1, 2, 3]


@pytest.mark.asyncio
async def test_roulette_does_not_fallback_to_memory_when_production_db_is_unavailable():
    service = RouletteService(
        ProductionUnavailableDatabase(),
        action_ledger=GameActionLedger(None),
    )

    with pytest.raises(RuntimeError, match="game_persistence_unavailable"):
        await service.create_lobby(chat_id=-100, owner_id=1, owner_name="kurucu")

    assert -100 not in service._memory


@pytest.mark.asyncio
async def test_roulette_does_not_acknowledge_a_round_after_production_write_failure():
    service = RouletteService(
        ProductionSaveFailureDatabase(),
        action_ledger=GameActionLedger(None),
    )

    with pytest.raises(RuntimeError, match="game_persistence_unavailable"):
        await service.create_lobby(chat_id=-100, owner_id=1, owner_name="kurucu")

    assert -100 not in service._memory


@pytest.mark.asyncio
async def test_roulette_keeps_explicit_degraded_adapter_fallback_bounded():
    service = RouletteService(
        DegradedSaveFailureDatabase(),
        action_ledger=GameActionLedger(None),
    )

    result = await service.create_lobby(chat_id=-100, owner_id=1, owner_name="kurucu")

    assert result["status"] == "created"
    assert service._memory[-100]["phase"] == "lobby"


@pytest.mark.asyncio
async def test_handler_game_persistence_fails_closed_for_production_db():
    with pytest.raises(RuntimeError, match="game_persistence_unavailable"):
        await persist_game(
            ProductionSaveFailureDatabase(),
            -100,
            "hangman",
            {"status": "playing"},
        )


@pytest.mark.asyncio
async def test_batak_does_not_acknowledge_lobby_without_production_state_store():
    service = BatakService(
        ProductionUnavailableDatabase(),
        action_ledger=GameActionLedger(None),
    )

    with pytest.raises(RuntimeError, match="game_persistence_unavailable"):
        await service.create_lobby(chat_id=-100, owner_id=1, owner_name="kurucu")


@pytest.mark.asyncio
async def test_blackjack_checks_production_state_before_debiting_bet():
    economy = RecordingEconomy()
    service = BlackjackService(
        ProductionUnavailableDatabase(),
        economy_service=economy,
        action_ledger=GameActionLedger(None),
    )

    with pytest.raises(RuntimeError, match="game_persistence_unavailable"):
        await service.start(
            chat_id=-100,
            user_id=1,
            user_name="kurucu",
            bet=100,
            event_id="start-unavailable",
        )

    assert economy.debits == []


@pytest.mark.asyncio
async def test_blackjack_does_not_debit_when_initial_state_write_fails():
    economy = RecordingEconomy()
    service = BlackjackService(
        ProductionSaveFailureDatabase(),
        economy_service=economy,
        action_ledger=GameActionLedger(None),
    )

    with pytest.raises(RuntimeError, match="game_persistence_unavailable"):
        await service.start(
            chat_id=-100,
            user_id=1,
            user_name="kurucu",
            bet=100,
            event_id="start-write-failure",
        )

    assert economy.debits == []


@pytest.mark.asyncio
async def test_blackjack_pending_bet_recovers_without_a_second_debit_after_final_write_failure():
    from src.games.blackjack import BlackjackGame
    class NonTerminalHand(BlackjackGame):
        def _create_deck(self):
            return [("7", "♠️"), ("8", "♥️"), ("6", "♣️"), ("5", "♦️"), ("9", "♠️")]

    database = FailingFinalBlackjackWriteDatabase()
    economy = RecordingEconomy()
    service = BlackjackService(
        database,
        economy_service=economy,
        action_ledger=GameActionLedger(None),
        game_factory=NonTerminalHand,
    )

    with pytest.raises(RuntimeError, match="game_persistence_unavailable"):
        await service.start(
            chat_id=-100,
            user_id=1,
            user_name="kurucu",
            bet=100,
            event_id="recoverable-bet",
        )

    pending = database.states[(-100, "blackjack:1")]
    assert pending["status"] == "pending_bet"
    assert len(economy.debits) == 1

    recovered = await BlackjackService(
        database,
        economy_service=economy,
        action_ledger=GameActionLedger(None),
        game_factory=NonTerminalHand,
    ).start(
        chat_id=-100,
        user_id=1,
        user_name="kurucu",
        bet=100,
        event_id="different-command-retry",
    )

    assert recovered["terminal"] is False
    assert len(economy.debits) == 1
    assert database.states[(-100, "blackjack:1")]["bet_status"] == "charged"


@pytest.mark.asyncio
async def test_uno_rejects_production_state_access_when_database_is_unavailable():
    service = UnoService(ProductionUnavailableDatabase())

    with pytest.raises(UnoError, match="UNO_STORAGE_UNAVAILABLE"):
        await service.start(chat_id=-100, owner_id=1, owner_name="kurucu")


@pytest.mark.asyncio
async def test_guess_age_rejects_production_state_access_when_database_is_unavailable():
    service = GuessAgeService(ProductionUnavailableDatabase())

    with pytest.raises(GuessAgeServiceError, match="GUESS_AGE_STORAGE_UNAVAILABLE"):
        await service.start(chat_id=-100, owner_id=1, owner_name="kurucu")


def test_uno_setup_keeps_all_108_cards_and_enforces_drawn_card_choice():
    state = new_uno_state(chat_id=-100, owner_id=1, owner_name="bir")
    state["players"].append({"user_id": 2, "name": "iki", "hand": [], "ready": False, "uno_called": False})
    state = start_round(state, rng=__import__("random").Random(4))
    total = sum(len(player["hand"]) for player in state["players"]) + len(state["deck"]) + len(state["discard"])
    assert total == 108

    state["current_player_id"] = 1
    state["current_color"] = "red"
    state["discard"] = [{"id": "top", "color": "red", "kind": "5"}]
    state["players"][0]["hand"] = [
        {"id": "red-one", "color": "red", "kind": "1"},
        {"id": "blue-two", "color": "blue", "kind": "2"},
    ]
    state["deck"] = [{"id": "drawn", "color": "red", "kind": "9"}]
    drawn = draw_card(state, user_id=1, action_id="draw")
    with pytest.raises(UnoError, match="UNO_MUST_PLAY_DRAWN_CARD"):
        play_card(drawn, user_id=1, card_id="red-one", action_id="wrong-card")


def test_uno_turn_deadline_rejects_late_actions():
    state = new_uno_state(chat_id=-100, owner_id=1, owner_name="bir")
    state["players"].append({"user_id": 2, "name": "iki", "hand": [], "ready": False, "uno_called": False})
    state = start_round(state)
    state["turn_expires_at"] = 0
    with pytest.raises(UnoError, match="UNO_TURN_EXPIRED"):
        draw_card(state, user_id=1, action_id="late")


def test_guess_age_round_deadline_is_enforced_without_answer_leak():
    state = new_guess_age_state(
        owner_id=1,
        owner_name="kurucu",
        chat_id=-100,
        round_seconds=15,
        asset=AgeAsset("age", "age.png", 42),
    )
    state["round_started_at"] = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    state["initial_delivery_pending"] = False
    assert round_expired(state)
    before = dict(state["answers"])
    outcome = submit_guess(state, user_id=2, display_name="oyuncu", guess=42)
    assert outcome.code == "guess_age_round_expired"
    assert state["answers"] == before
