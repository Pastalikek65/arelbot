from __future__ import annotations

import pytest

from src.services.batak_service import BatakService
from src.games.batak_round import BatakRound


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
    await service.join(chat_id=-60, user_id=2, user_name="İki", action_id="join-2")
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


@pytest.mark.asyncio
async def test_stale_card_button_is_rejected_without_mutating_a_new_turn():
    database = FakeDatabase()
    service = BatakService(database)
    await service.create_lobby(chat_id=-80, owner_id=1, owner_name="Bir")
    await service.join(chat_id=-80, user_id=2, user_name="İki", action_id="join")
    started = await service.start(chat_id=-80, user_id=1, action_id="start")
    old_token = BatakRound.turn_token(started["state"])
    moved = await service.play(chat_id=-80, user_id=1, card_index=0,
                               action_id="play-first", expected_turn_token=old_token)
    assert moved["state"]["current_turn"] == 2
    with pytest.raises(ValueError, match="stale_turn"):
        await service.play(chat_id=-80, user_id=2, card_index=0,
                           action_id="play-stale", expected_turn_token=old_token)
    assert (await service.snapshot(-80))["current_turn"] == 2
    assert len((await service.snapshot(-80))["trick"]) == 1
    fresh_token = BatakRound.turn_token(await service.snapshot(-80))
    hand = (await service.snapshot(-80))["hands"]["2"]
    lead = (await service.snapshot(-80))["trick"][0]["card"][1]
    legal_index = next((i for i, card in enumerate(hand) if card[1] == lead), 0)
    await service.play(chat_id=-80, user_id=2, card_index=legal_index,
                       action_id="play-stale", expected_turn_token=fresh_token)
    finished_trick = await service.snapshot(-80)
    assert finished_trick["completed_tricks"] == 1
    assert finished_trick["last_trick"]["winner_id"] in {1, 2}
    assert len(finished_trick["last_trick"]["cards"]) == 2


def test_batak_domain_render_keeps_hands_private():
    state = BatakRound.lobby(1, "Bir")
    BatakRound.join(state, 2, "İki")
    BatakRound.start(state)
    assert "Senin elin" not in BatakRound.render(state, 1)
    assert "1:" not in BatakRound.render(state, 1)


@pytest.mark.asyncio
async def test_old_lobby_buttons_cannot_join_or_start_a_new_lobby():
    service = BatakService(FakeDatabase())
    first = await service.create_lobby(chat_id=-90, owner_id=1, owner_name="Bir")
    old_id = first["state"]["lobby_id"]
    await service.join(chat_id=-90, user_id=2, user_name="İki", action_id="already-used",
                       expected_lobby_id=old_id)
    await service.cancel(chat_id=-90, user_id=1)
    second = await service.create_lobby(chat_id=-90, owner_id=1, owner_name="Bir")
    new_id = second["state"]["lobby_id"]
    assert old_id != new_id
    with pytest.raises(ValueError, match="stale_lobby"):
        await service.join(chat_id=-90, user_id=2, user_name="İki", action_id="old-join",
                           expected_lobby_id=old_id)
    with pytest.raises(ValueError, match="stale_lobby"):
        await service.join(chat_id=-90, user_id=2, user_name="İki", action_id="already-used",
                           expected_lobby_id=old_id)
    with pytest.raises(ValueError, match="stale_lobby"):
        await service.start(chat_id=-90, user_id=1, action_id="old-start",
                            expected_lobby_id=old_id)
    assert len((await service.snapshot(-90))["players"]) == 1
    await service.join(chat_id=-90, user_id=2, user_name="İki", action_id="old-join",
                       expected_lobby_id=new_id)
    await service.start(chat_id=-90, user_id=1, action_id="old-start",
                        expected_lobby_id=new_id)
    assert (await service.snapshot(-90))["phase"] == "playing"


@pytest.mark.asyncio
async def test_legacy_lobby_gets_persisted_button_identity_on_refresh():
    database = FakeDatabase()
    old = BatakRound.lobby(1, "Bir")
    old.pop("lobby_id")
    await database.save_game_state(-91, "batak", old)
    service = BatakService(database)
    refreshed = await service.snapshot(-91)
    assert len(refreshed["lobby_id"]) == 12
    assert (await service.snapshot(-91))["lobby_id"] == refreshed["lobby_id"]
    await service.join(chat_id=-91, user_id=2, user_name="İki", action_id="join",
                       expected_lobby_id=refreshed["lobby_id"])


@pytest.mark.asyncio
async def test_panel_binding_is_generation_scoped_and_old_panel_actions_are_rejected():
    service = BatakService(FakeDatabase())
    lobby = (await service.create_lobby(chat_id=-92, owner_id=1, owner_name="Bir"))["state"]
    bound = await service.bind_panel(chat_id=-92, message_id=101, expected_lobby_id=lobby["lobby_id"])
    assert bound["prompt_message_id"] == 101
    with pytest.raises(ValueError, match="stale_panel"):
        await service.join(chat_id=-92, user_id=2, user_name="İki", action_id="old-panel",
                           expected_lobby_id=lobby["lobby_id"], expected_panel_message_id=99)
    await service.join(chat_id=-92, user_id=2, user_name="İki", action_id="new-panel",
                       expected_lobby_id=lobby["lobby_id"], expected_panel_message_id=101)
    started = await service.start(chat_id=-92, user_id=1, action_id="start",
                                  expected_lobby_id=lobby["lobby_id"], expected_panel_message_id=101)
    game_id = started["state"]["game_id"]
    with pytest.raises(ValueError, match="stale_turn"):
        await service.bind_panel(chat_id=-92, message_id=102, expected_game_id="wrong")
    await service.defer_panel(chat_id=-92, expected_game_id=game_id)
    assert (await service.snapshot(-92))["panel_dirty"] is True
    rebound = await service.bind_panel(chat_id=-92, message_id=102, expected_game_id=game_id)
    assert rebound["prompt_message_id"] == 102 and rebound["panel_dirty"] is False
    with pytest.raises(ValueError, match="stale_panel"):
        await service.play(chat_id=-92, user_id=1, card_index=0, action_id="old-play",
                           expected_turn_token=BatakRound.turn_token(rebound),
                           expected_panel_message_id=101)
