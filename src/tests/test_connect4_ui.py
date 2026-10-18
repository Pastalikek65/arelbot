from src.services.word_game_ui import (
    connect4_drop,
    connect4_join,
    connect4_keyboard,
    connect4_start,
    new_connect4_state,
    render_connect4_text,
)


def test_connect4_lobby_is_round_scoped_and_two_player():
    state = new_connect4_state(owner_id=1, owner_name="A", chat_id=-100)
    callbacks = [button.callback_data for row in connect4_keyboard(state).inline_keyboard for button in row]
    assert state["round_id"] in callbacks[0]
    assert "connect4:" in callbacks[0]
    assert connect4_join(state, user_id=2, user_name="B")["changed"] is True
    assert connect4_join(state, user_id=3, user_name="C")["status"] == "full"
    assert connect4_start(state)["status"] == "started"


def test_connect4_turns_and_win_are_deterministic():
    state = new_connect4_state(owner_id=1, owner_name="A", chat_id=-100)
    connect4_join(state, user_id=2, user_name="B")
    connect4_start(state)
    # A/B alternate in column pairs; A gets the bottom row line.
    for column in (0, 0, 1, 1, 2, 2):
        actor = state["current_player_id"]
        assert connect4_drop(state, user_id=actor, column=column)["changed"]
    actor = state["current_player_id"]
    result = connect4_drop(state, user_id=actor, column=3)
    assert result["status"] == "won"
    assert state["winner_id"] == actor
    assert "KAZANAN" in render_connect4_text(state).upper()


def test_connect4_rejects_stale_turn_and_invalid_board_without_mutating():
    state = new_connect4_state(owner_id=1, owner_name="A", chat_id=-100)
    connect4_join(state, user_id=2, user_name="B")
    connect4_start(state)
    before = [row[:] for row in state["board"]]
    assert connect4_drop(state, user_id=2, column=0)["status"] == "not_your_turn"
    assert connect4_drop(state, user_id=1, column=99)["status"] == "invalid_column"
    assert state["board"] == before


def test_connect4_rejects_malformed_players_and_cells_before_mutation():
    state = new_connect4_state(owner_id=1, owner_name="A", chat_id=-100)
    connect4_join(state, user_id=2, user_name="B")
    connect4_start(state)
    state["board"][-1][0] = 999
    before = [row[:] for row in state["board"]]
    result = connect4_drop(state, user_id=1, column=0)
    assert result["status"] == "invalid_state"
    assert state["board"] == before


def test_connect4_turn_timeout_closes_the_round_without_a_move():
    import time

    state = new_connect4_state(owner_id=1, owner_name="A", chat_id=-100)
    connect4_join(state, user_id=2, user_name="B")
    connect4_start(state)
    state["turn_expires_at"] = time.time() - 1
    result = connect4_drop(state, user_id=1, column=0)
    assert result["status"] == "expired"
    assert state["status"] == "expired"
    assert all(cell == 0 for row in state["board"] for cell in row)
