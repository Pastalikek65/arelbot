from __future__ import annotations

import asyncio

import pytest

from src.games.uno import (
    COLORS,
    UnoError,
    build_deck,
    can_play,
    choose_color,
    challenge_uno,
    draw_card,
    join_player,
    new_uno_state,
    play_card,
    score_card,
    skip_after_draw,
    start_round,
    validate_state,
)
from src.services.uno_service import UnoService
from src.services.uno_ui import uno_keyboard, render_uno_hand_alert
from src.handlers.callback_handler import CallbackHandler


class MemoryGameDb:
    def __init__(self):
        self.states = {}

    async def get_game_state(self, chat_id, game_type):
        value = self.states.get((int(chat_id), game_type))
        return None if value is None else {**value}

    async def save_game_state(self, chat_id, game_type, state):
        self.states[(int(chat_id), game_type)] = state

    async def delete_game_state(self, chat_id, game_type):
        self.states.pop((int(chat_id), game_type), None)


def _playing():
    state = new_uno_state(chat_id=-100, owner_id=1, owner_name="bir")
    state = join_player(state, user_id=2, name="iki")
    return start_round(state)


def test_classic_deck_and_round_are_deterministic_shape():
    deck = build_deck()
    assert len(deck) == 108
    assert {card["color"] for card in deck} == {*COLORS, "black"}
    state = _playing()
    assert validate_state(state)
    assert state["status"] == "playing"
    assert all(len(player["hand"]) == 7 for player in state["players"])
    assert state["discard"][-1]["color"] in COLORS


def test_join_is_bounded_and_duplicate_safe():
    state = new_uno_state(chat_id=-100, owner_id=1, owner_name="bir")
    state = join_player(state, user_id=2, name="iki")
    assert len(join_player(state, user_id=2, name="iki")["players"]) == 2
    for user_id in range(3, 9):
        state = join_player(state, user_id=user_id, name=str(user_id))
    with pytest.raises(UnoError, match="UNO_LOBBY_FULL"):
        join_player(state, user_id=9, name="dokuz")


def test_turn_and_card_validation_and_idempotency():
    state = _playing()
    player = state["players"][0]
    playable = {"id": "forced", "color": state["discard"][-1]["color"], "kind": "0"}
    player["hand"][0] = playable
    moved = play_card(state, user_id=1, card_id=playable["id"], action_id="a1")
    with pytest.raises(UnoError, match="UNO_DUPLICATE_ACTION"):
        play_card(moved, user_id=1, card_id=playable["id"], action_id="a1")
    with pytest.raises(UnoError, match="UNO_NOT_YOUR_TURN"):
        draw_card(moved, user_id=1, action_id="wrong-turn")


def test_wild_card_requires_color_and_draw_can_be_skipped():
    state = _playing()
    player = state["players"][0]
    wild = {"id": "wild-test", "color": "black", "kind": "wild"}
    player["hand"] = [wild, {"id": "spare", "color": "red", "kind": "1"}]
    state["discard"] = [{"id": "top", "color": "red", "kind": "4"}]
    moved = play_card(state, user_id=1, card_id="wild-test", action_id="wild-1")
    assert moved["awaiting_color"] is True
    moved = choose_color(moved, user_id=1, color="blue", action_id="color-1")
    assert moved["current_color"] == "blue"
    moved["deck"] = [{"id": "draw-blue", "color": "blue", "kind": "5"}]
    drawn = draw_card(moved, user_id=2, action_id="draw-1")
    assert drawn["players"][1]["drawn_card_id"]
    skipped = skip_after_draw(drawn, user_id=2, action_id="skip-1")
    assert skipped["current_player_id"] == 1


def test_missed_uno_challenge_penalizes_target():
    state = _playing()
    state["players"][1]["hand"] = [{"id": "one", "color": "red", "kind": "1"}]
    state["uno_vulnerable_id"] = 2
    challenged = challenge_uno(state, user_id=1, target_id=2, action_id="challenge-1")
    assert len(challenged["players"][1]["hand"]) == 3
    with pytest.raises(UnoError, match="UNO_CHALLENGE_NOT_ALLOWED"):
        challenge_uno(challenged, user_id=1, target_id=2, action_id="challenge-2")


def test_score_and_service_persistence():
    assert score_card({"kind": "draw4", "color": "black"}) == 50
    db = MemoryGameDb()
    service = UnoService(db)

    async def scenario():
        state = await service.start(chat_id=-100, owner_id=1, owner_name="bir")
        state = await service.join(chat_id=-100, user_id=2, name="iki")
        state = await service.begin(chat_id=-100, user_id=1, action_id="begin")
        return await service.view(-100, 1)

    state = asyncio.run(scenario())
    assert state and state["status"] == "playing"
    # Another user's hand is hidden in a view; the Mini App will receive the
    # same contract through its authenticated owner-scoped session.
    assert all(card["kind"] == "hidden" for card in state["players"][1]["hand"])


def test_ui_has_lobby_and_miniapp_extension_point():
    state = new_uno_state(chat_id=-100, owner_id=1, owner_name="bir")
    markup = uno_keyboard(state, user_id=1, miniapp_url="https://example.invalid/uno")
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row if button.callback_data]
    assert f"uno:{state['round_id']}:{state.get('revision', 0)}:join" in callbacks
    assert any(getattr(button, "url", None) for row in markup.inline_keyboard for button in row)


def test_group_keyboard_hides_cards_and_stays_usable_after_turn_changes():
    state = _playing()
    state["players"][0]["hand"] = [{"id": "secret-red", "color": "red", "kind": "7"}]
    state["players"][1]["hand"] = [{"id": "other-blue", "color": "blue", "kind": "8"}]
    state["discard"] = [{"id": "top", "color": "red", "kind": "5"}]
    state["current_color"] = "red"
    markup = uno_keyboard(state, user_id=2)
    labels = [button.text for row in markup.inline_keyboard for button in row]
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row if button.callback_data]
    assert "Kırmızı 7" not in labels
    assert "🃏 #1" in labels
    assert not any("secret-red" in callback for callback in callbacks)
    assert f"uno:{state['round_id']}:{state.get('revision', 0)}:playpos:1" in callbacks
    assert "Kırmızı 7" in render_uno_hand_alert(state, user_id=1)
    assert "Kırmızı 7" not in render_uno_hand_alert(state, user_id=2)


def test_draw_penalties_apply_immediately_and_skip_the_target():
    state = _playing()
    state["current_color"] = "red"
    state["discard"] = [{"id": "top", "color": "red", "kind": "5"}]
    state["players"][0]["hand"] = [
        {"id": "red-draw", "color": "red", "kind": "draw2"},
        {"id": "spare", "color": "blue", "kind": "1"},
    ]
    before = len(state["players"][1]["hand"])
    after = play_card(state, user_id=1, card_id="red-draw", action_id="draw-two")
    assert len(after["players"][1]["hand"]) == before + 2
    assert after["current_player_id"] == 1
    assert after["players"][1]["pending_draw"] == 0

    state["players"][0]["hand"][0] = {"id": "wild-draw", "color": "black", "kind": "draw4"}
    after = play_card(state, user_id=1, card_id="wild-draw", action_id="draw-four")
    after = choose_color(after, user_id=1, color="blue", action_id="choose-blue")
    assert len(after["players"][1]["hand"]) == before + 4
    assert after["current_player_id"] == 1


@pytest.mark.asyncio
async def test_expired_turn_draws_and_advances_instead_of_ending_match():
    db = MemoryGameDb()
    service = UnoService(db)
    state = await service.start(chat_id=-100, owner_id=1, owner_name="bir")
    await service.join(chat_id=-100, user_id=2, name="iki")
    state = await service.begin(chat_id=-100, user_id=1, action_id="begin")
    state["turn_expires_at"] = 0
    db.states[(-100, "uno")] = state
    before = len(state["players"][0]["hand"])
    advanced = await service.current(-100)
    assert advanced["status"] == "playing"
    assert advanced["current_player_id"] == 2
    assert len(advanced["players"][0]["hand"]) == before + 1


@pytest.mark.asyncio
async def test_numbered_card_button_plays_only_current_players_card():
    db = MemoryGameDb()
    service = UnoService(db)
    await service.start(chat_id=-100, owner_id=1, owner_name="bir")
    await service.join(chat_id=-100, user_id=2, name="iki")
    state = await service.begin(chat_id=-100, user_id=1, action_id="begin")
    state["current_color"] = "red"
    state["discard"] = [{"id": "top", "color": "red", "kind": "5"}]
    state["players"][0]["hand"] = [{"id": "secret", "color": "red", "kind": "7"},
                                    {"id": "spare", "color": "blue", "kind": "1"}]
    db.states[(-100, "uno")] = state
    with pytest.raises(UnoError, match="UNO_NOT_YOUR_TURN"):
        await service.action(chat_id=-100, user_id=2, action="playpos", card_position=1, action_id="wrong")
    moved = await service.action(chat_id=-100, user_id=1, action="playpos", card_position=1, action_id="right")
    assert moved["discard"][-1]["id"] == "secret"


@pytest.mark.asyncio
async def test_uno_callback_rejects_cross_topic_forum_click():
    db = MemoryGameDb()
    service = UnoService(db)
    state = await service.start(chat_id=-100, owner_id=1, owner_name="bir", thread_id=42)

    class Query:
        def __init__(self):
            self.message = type("Message", (), {"message_id": 700, "message_thread_id": 99})()
            self.answers = []

        async def answer(self, *args, **kwargs):
            self.answers.append((args, kwargs))

        async def edit_message_text(self, *args, **kwargs):
            raise AssertionError("cross-topic UNO callback must not edit the panel")

    update = type("Update", (), {
        "effective_chat": type("Chat", (), {"id": -100, "type": "supergroup"})(),
        "effective_user": type("User", (), {"id": 2, "first_name": "iki"})(),
    })()
    query = Query()
    handler = CallbackHandler(type("Engine", (), {})(), type("Sudo", (), {})(), None, data_port=db, uno_service=service)
    await handler._handle_uno_callback(update, query, type("Context", (), {})(), f"uno:{state['round_id']}:join")
    assert query.answers
    assert len((await service.current(-100))["players"]) == 1


@pytest.mark.asyncio
async def test_old_card_position_cannot_mutate_a_new_hand_revision():
    db = MemoryGameDb()
    service = UnoService(db)
    await service.start(chat_id=-100, owner_id=1, owner_name="bir")
    await service.join(chat_id=-100, user_id=2, name="iki")
    state = await service.begin(chat_id=-100, user_id=1, action_id="begin")
    state["deck"] = [{"id": "playable", "color": state["current_color"], "kind": "5"}]
    db.states[(-100, "uno")] = state
    old_revision = int(state.get("revision", 0))
    await service.action(chat_id=-100, user_id=1, action="draw", action_id="draw", expected_revision=old_revision)
    with pytest.raises(UnoError, match="UNO_STALE_ACTION"):
        await service.action(chat_id=-100, user_id=1, action="playpos", card_position=1,
                             action_id="old-click", expected_revision=old_revision)


def test_last_draw_two_penalty_counts_in_winner_points():
    state = _playing()
    state["current_color"] = "red"
    state["discard"] = [{"id": "top", "color": "red", "kind": "5"}]
    state["players"][0]["hand"] = [{"id": "last", "color": "red", "kind": "draw2"}]
    state["players"][1]["hand"] = [{"id": "other", "color": "blue", "kind": "1"}]
    state["deck"] = [{"id": "two", "color": "blue", "kind": "2"}, {"id": "three", "color": "blue", "kind": "3"}]
    result = play_card(state, user_id=1, card_id="last", action_id="finish")
    assert len(result["players"][1]["hand"]) == 3
    assert result["players"][0]["score"] == 6


def test_spectator_cannot_penalize_a_player():
    state = _playing()
    state["players"][1]["hand"] = [{"id": "one", "color": "red", "kind": "1"}]
    with pytest.raises(UnoError, match="UNO_PLAYER_NOT_FOUND"):
        challenge_uno(state, user_id=999, target_id=2, action_id="spectator")


@pytest.mark.asyncio
async def test_next_round_preserves_match_score_and_rotates_dealer():
    db = MemoryGameDb()
    service = UnoService(db)
    await service.start(chat_id=-100, owner_id=1, owner_name="bir")
    await service.join(chat_id=-100, user_id=2, name="iki")
    state = await service.begin(chat_id=-100, user_id=1, action_id="begin")
    state.update(status="finished", winner_id=1, current_player_id=None, turn_expires_at=None)
    state["players"][0]["score"] = 80
    old_round = state["round_id"]
    db.states[(-100, "uno")] = state
    result = await service.next_round(chat_id=-100, user_id=1, action_id="next", expected_revision=state.get("revision", 0))
    assert result["round_id"] != old_round and result["status"] == "playing"
    assert result["players"][0]["score"] == 80
    assert result["round_number"] == 2 and result["current_player_id"] == 2


@pytest.mark.asyncio
async def test_large_round_points_are_normalized_without_blocking_next_round():
    class Leaderboard:
        async def record_score(self, **kwargs):
            assert 0 <= kwargs["score"] <= 100
    db = MemoryGameDb()
    service = UnoService(db, leaderboard_service=Leaderboard())
    state = _playing()
    state.update(status="finished", winner_id=1, round_points=240, score_pending=True)
    state["players"][0]["score"] = 240
    db.states[(-100, "uno")] = state
    state = await service.current(-100)
    assert state["score_pending"] is False
    assert (await service.next_round(chat_id=-100, user_id=1, action_id="next"))["players"][0]["score"] == 240


@pytest.mark.asyncio
async def test_opening_uno_in_another_forum_topic_does_not_replace_its_panel():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from src.handlers.command_handler import CommandHandler
    db = MemoryGameDb()
    service = UnoService(db)
    await service.start(chat_id=-100, owner_id=1, owner_name="Bir", thread_id=42)
    await service.set_prompt_message(chat_id=-100, message_id=700)
    message = SimpleNamespace(message_thread_id=99, reply_text=AsyncMock(return_value=SimpleNamespace(message_id=701)))
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=-100, type="supergroup"),
                             effective_user=SimpleNamespace(id=1, first_name="Bir"), effective_message=message, message=message)
    handler = CommandHandler(SimpleNamespace(), SimpleNamespace(), None, None, data_port=db, uno_service=service)
    await handler.handle_uno_command(update, SimpleNamespace())
    assert (await service.current(-100))["prompt_message_id"] == 700
    assert not message.reply_text.call_args.kwargs.get("reply_markup")
