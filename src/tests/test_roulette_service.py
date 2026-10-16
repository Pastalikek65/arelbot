from types import SimpleNamespace

import pytest

from src.services.roulette_service import RouletteService


class FakeDatabase:
    available = True

    def __init__(self):
        self.states = {}

    async def get_game_state(self, chat_id, game_type):
        item = self.states.get((chat_id, game_type))
        return dict(item) if item else None

    async def save_game_state(self, chat_id, game_type, state):
        self.states[(chat_id, game_type)] = dict(state)

    async def delete_game_state(self, chat_id, game_type):
        self.states.pop((chat_id, game_type), None)


@pytest.mark.asyncio
async def test_roulette_lobby_join_start_and_turn_are_persistent(monkeypatch):
    database = FakeDatabase()
    service = RouletteService(database)

    created = await service.create_lobby(chat_id=-100, owner_id=1, owner_name="Arel")
    assert created["status"] == "created"
    joined = await service.join(chat_id=-100, user_id=2, user_name="Deniz", action_id="join-2")
    assert joined["status"] == "joined"

    started = await service.start(chat_id=-100, user_id=1, action_id="start-1")
    assert started["status"] == "started"
    assert started["state"]["phase"] == "playing"
    assert started["state"]["current_player_id"] == 1

    monkeypatch.setattr("src.services.roulette_service.RouletteGame.pull_trigger", lambda self, state, player_name: {"hit": False, "message": "tÄ±k", "player": player_name})
    result = await service.pull(chat_id=-100, user_id=1, action_id="pull-1")

    assert result["status"] == "playing"
    assert result["state"]["current_player_id"] == 2
    persisted = await database.get_game_state(-100, "roulette")
    assert persisted["current_player_id"] == 2


@pytest.mark.asyncio
async def test_roulette_rejects_wrong_turn_and_duplicate_actions():
    service = RouletteService(FakeDatabase())
    await service.create_lobby(chat_id=-100, owner_id=1, owner_name="Arel")
    await service.join(chat_id=-100, user_id=2, user_name="Deniz", action_id="join-2")
    await service.start(chat_id=-100, user_id=1, action_id="start-1")

    with pytest.raises(ValueError, match="not_turn"):
        await service.pull(chat_id=-100, user_id=2, action_id="wrong-turn")

    assert await service.join(chat_id=-100, user_id=2, user_name="Deniz", action_id="join-2") == {
        "status": "duplicate_action"
    }


@pytest.mark.asyncio
async def test_roulette_hit_finishes_round_and_cleans_state(monkeypatch):
    database = FakeDatabase()
    service = RouletteService(database)
    await service.create_lobby(chat_id=-100, owner_id=1, owner_name="Arel")
    await service.join(chat_id=-100, user_id=2, user_name="Deniz", action_id="join-2")
    await service.start(chat_id=-100, user_id=1, action_id="start-1")
    monkeypatch.setattr("src.services.roulette_service.RouletteGame.pull_trigger", lambda self, state, player_name: {"hit": True, "message": "bang", "player": player_name})

    result = await service.pull(chat_id=-100, user_id=1, action_id="pull-1")

    assert result["status"] == "finished"
    assert result["winner_id"] == 2
    assert await database.get_game_state(-100, "roulette") is None


@pytest.mark.asyncio
async def test_roulette_winner_reward_uses_stable_event_id(monkeypatch):
    class Ledger:
        async def record_score(self, **kwargs):
            self.score = kwargs

    class Economy:
        async def credit(self, *args, **kwargs):
            self.credit_args = (args, kwargs)

    database = FakeDatabase()
    leaderboard = Ledger()
    economy = Economy()
    service = RouletteService(database, leaderboard_service=leaderboard, economy_service=economy)
    await service.create_lobby(chat_id=-100, owner_id=1, owner_name="Arel")
    await service.join(chat_id=-100, user_id=2, user_name="Deniz", action_id="join-2")
    await service.start(chat_id=-100, user_id=1, action_id="start-1")
    monkeypatch.setattr("src.services.roulette_service.RouletteGame.pull_trigger", lambda self, state, player_name: {"hit": True, "message": "bang", "player": player_name})

    await service.pull(chat_id=-100, user_id=1, action_id="pull-1")

    assert leaderboard.score["event_id"].startswith("score:roulette:-100:")
    assert economy.credit_args[1]["event_id"].startswith("coin:roulette:-100:")
