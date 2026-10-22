"""Abandoned roulette turns close and deliver their terminal panel once."""

from copy import deepcopy

import pytest

from src.services.roulette_service import RouletteService


class Repository:
    available = True

    def __init__(self):
        self.states = {}

    async def get_game_state(self, chat_id, game_type):
        return deepcopy(self.states.get((chat_id, game_type)))

    async def save_game_state(self, chat_id, game_type, state):
        self.states[(chat_id, game_type)] = deepcopy(state)

    async def delete_game_state(self, chat_id, game_type):
        self.states.pop((chat_id, game_type), None)


class Coins:
    def __init__(self):
        self.events = []

    async def credit(self, *args, **kwargs):
        self.events.append((args, kwargs))
        return True


async def playing_table(repository, coins):
    service = RouletteService(repository, economy_service=coins)
    lobby = await service.create_lobby(chat_id=-100, owner_id=1, owner_name="Bir")
    assert await service.acknowledge_panel(-100, lobby["state"]["game_id"], 71)
    await service.join(chat_id=-100, user_id=2, user_name="İki", action_id="join-2",
                       game_id=lobby["state"]["game_id"], panel_message_id=71)
    started = await service.start(chat_id=-100, user_id=1, action_id="start-1",
                                  game_id=lobby["state"]["game_id"], panel_message_id=71)
    return service, started["state"]


@pytest.mark.asyncio
async def test_timeout_closes_abandoned_turn_and_delivers_canonical_result_after_restart():
    repository, coins = Repository(), Coins()
    service, state = await playing_table(repository, coins)
    deadline = float(state["turn_expires_at"])
    assert await service.expire(chat_id=-100, now=deadline - 0.01) is None
    assert (await service._load(-100))["phase"] == "playing"

    restarted = RouletteService(repository, economy_service=coins)
    result = await restarted.expire(chat_id=-100, now=deadline)
    assert result["status"] == "expired"
    assert result["state"]["panel_message_id"] == 71
    assert result["state"]["turn_expires_at"] is None
    assert result["state"]["panel_dirty"]
    assert "Sıra süresi doldu" in result["text"]
    assert await restarted.expire(chat_id=-100, now=deadline + 10) is None
    assert coins.events == []

    with pytest.raises(ValueError, match="result_pending"):
        await restarted.create_lobby(chat_id=-100, owner_id=3, owner_name="Üç")
    panel = await restarted.claim_result_panel(chat_id=-100, now=deadline + 11)
    assert panel["game_id"] == state["game_id"]
    assert panel["panel_message_id"] == 71
    assert await restarted.claim_result_panel(chat_id=-100, now=deadline + 12) is None
    assert not await restarted.acknowledge_result_panel(chat_id=-100, game_id="old", message_id=71)
    assert await restarted.acknowledge_result_panel(
        chat_id=-100, game_id=state["game_id"], message_id=71
    )
    assert not (await restarted._load(-100))["panel_dirty"]
    fresh = await restarted.create_lobby(chat_id=-100, owner_id=3, owner_name="Üç")
    assert fresh["state"]["game_id"] != state["game_id"]


@pytest.mark.asyncio
async def test_late_callback_uses_same_terminal_outbox_and_cannot_award_winner(monkeypatch):
    repository, coins = Repository(), Coins()
    service, state = await playing_table(repository, coins)
    monkeypatch.setattr("src.services.roulette_service.time.time",
                        lambda: float(state["turn_expires_at"]) + 1)
    result = await service.pull(chat_id=-100, user_id=1, action_id="late-pull",
                                game_id=state["game_id"], panel_message_id=71)
    assert result["status"] == "expired" and result["terminal"]
    assert result["state"]["panel_dirty"]
    assert result["state"].get("winner_id") is None
    assert coins.events == []
    assert await service.claim_result_panel(chat_id=-100, now=float(state["turn_expires_at"]) + 2)
    assert await service.acknowledge_result_panel(
        chat_id=-100, game_id=state["game_id"], message_id=71
    )
    with pytest.raises(ValueError, match="game_not_playing"):
        await service.pull(chat_id=-100, user_id=1, action_id="another",
                           game_id=state["game_id"], panel_message_id=71)
