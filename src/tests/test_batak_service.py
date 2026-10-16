from __future__ import annotations

import pytest

from src.services.batak_service import BatakService


class FakeDatabase:
    def __init__(self):
        self.states = {}

    async def get_game_state(self, chat_id, game_type):
        return self.states.get((int(chat_id), str(game_type)))

    async def save_game_state(self, chat_id, game_type, state):
        self.states[(int(chat_id), str(game_type))] = state

    async def delete_game_state(self, chat_id, game_type):
        self.states.pop((int(chat_id), str(game_type)), None)


class FakeLeaderboard:
    def __init__(self):
        self.rows = []

    async def record_score(self, **kwargs):
        self.rows.append(kwargs)
        return True


@pytest.mark.asyncio
async def test_batak_lobby_is_persistent_and_owner_starts_round():
    database = FakeDatabase()
    service = BatakService(database)
    await service.create_lobby(chat_id=-50, owner_id=1, owner_name="Kurucu")
    await service.join(chat_id=-50, user_id=2, user_name="Oyuncu", action_id="join-1")

    with pytest.raises(ValueError, match="not_owner"):
        await service.start(chat_id=-50, user_id=2, action_id="start-wrong")
    result = await service.start(chat_id=-50, user_id=1, action_id="start-1")

    assert result["state"]["phase"] == "playing"
    assert result["state"]["current_turn"] == 1
    assert len(result["state"]["hands"]["1"]) == 26


@pytest.mark.asyncio
async def test_batak_round_enforces_turns_follow_suit_and_settles_once():
    database = FakeDatabase()
    leaderboard = FakeLeaderboard()
    service = BatakService(database, leaderboard_service=leaderboard)
    await service.create_lobby(chat_id=-60, owner_id=1, owner_name="Bir")
    await service.join(chat_id=-60, user_id=2, user_name="Ä°ki", action_id="join-2")
    await service.start(chat_id=-60, user_id=1, action_id="start-2")

    with pytest.raises(ValueError, match="not_your_turn"):
        await service.play(chat_id=-60, user_id=2, card_index=0, action_id="wrong-turn")

    rounds = 0
    state = database.states[(-60, "batak")]
    while state["phase"] == "playing":
        state = database.states[(-60, "batak")]
        user_id = int(state["current_turn"])
        hand = state["hands"][str(user_id)]
        index = 0
        if state["trick"]:
            lead = state["trick"][0]["card"][1]
            same_suit = [i for i, card in enumerate(hand) if card[1] == lead]
            if same_suit:
                index = same_suit[0]
        await service.play(
            chat_id=-60,
            user_id=user_id,
            card_index=index,
            action_id=f"play-{rounds}",
        )
        rounds += 1
        assert rounds <= 52

    assert (-60, "batak") not in database.states
    assert len(leaderboard.rows) == 2


@pytest.mark.asyncio
async def test_batak_cancel_is_owner_scoped_and_removes_persistent_lobby():
    database = FakeDatabase()
    service = BatakService(database)
    await service.create_lobby(chat_id=-70, owner_id=1, owner_name="Kurucu")

    with pytest.raises(ValueError, match="not_owner"):
        await service.cancel(chat_id=-70, user_id=2)

    result = await service.cancel(chat_id=-70, user_id=1)

    assert result["status"] == "cancelled"
    assert (-70, "batak") not in database.states
