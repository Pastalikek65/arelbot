"""Rule matrix for roles, protected targets, ballots, and stale phases."""

import pytest

from src.games import vampire
from src.services.vampire_ui import callback_data, group_keyboard, render_vampire


def active(count=7, roles=None):
    state = vampire.new_lobby(chat_id=-100, thread_id=7, owner_id=1,
                              owner_name="Bir", round_id="round", now=1)
    for uid in range(2, count + 1):
        state = vampire.join(state, user_id=uid, user_name=str(uid), now=2)
    for uid in range(1, count + 1):
        state = vampire.ready(state, user_id=uid, now=3)
    if roles is None:
        counts = vampire.role_counts(count)
        roles = [role for role, amount in counts.items() for _ in range(amount)]
    state = vampire.start(state, user_id=1, roles=roles, now=4)
    assert state["phase"] == "role_delivery"
    for uid in range(1, count + 1):
        state = vampire.role_ack(state, user_id=uid, now=5)
    assert state["phase"] == "night" and state["deadline"] is None
    return vampire.activate(state, now=10)


@pytest.mark.parametrize("count", range(7, 21))
def test_role_templates_and_secret_public_view(count):
    counts = vampire.role_counts(count)
    assert sum(counts.values()) == count
    assert counts["villager"] >= counts["vampire"]
    state = active(count)
    assert not any("role" in p for p in vampire.public_state(state)["players"])
    assert state["deadline"] == 100


def test_protection_and_kahin_result_and_night_timeout():
    state = active()
    # Seven players: vampire 1, seer 2, healer 3, villagers 4..7.
    state = vampire.night_action(state, user_id=3, target_id=4, now=11)
    state = vampire.night_action(state, user_id=1, target_id=4, now=12)
    state = vampire.night_action(state, user_id=2, target_id=1, now=13)
    assert vampire.seer_result(state, user_id=2) == {"target_id": 1, "is_vampire": True}
    state = vampire.advance(state, now=100)
    assert state["phase"] == "discussion" and state["last_deaths"] == []
    assert state["previous_protected"] == 4
    state = vampire.activate(state, now=101)
    state = vampire.advance(state, now=191)
    assert state["phase"] == "vote"
    state = vampire.activate(state, now=192)
    with pytest.raises(ValueError, match="stale_phase"):
        vampire.night_action(state, user_id=1, target_id=5, now=193)


def test_night_death_resets_inactivity_counter():
    state = active()
    state["empty_days"] = 2
    state = vampire.night_action(state, user_id=1, target_id=4, now=11)
    state = vampire.advance(state, now=100)
    assert state["phase"] == "discussion" and state["empty_days"] == 0


def test_two_vampires_need_strict_majority_and_dead_cannot_vote():
    state = active(10)
    vampires = [p["id"] for p in state["players"] if p["role"] == "vampire"]
    state = vampire.night_action(state, user_id=vampires[0], target_id=5, now=11)
    state = vampire.night_action(state, user_id=vampires[1], target_id=6, now=12)
    state = vampire.advance(state, now=100)
    assert state["last_deaths"] == []
    state = vampire.activate(state, now=101)
    state = vampire.advance(state, now=191)
    state = vampire.activate(state, now=192)
    for uid in range(1, 7):
        state = vampire.vote(state, user_id=uid, target_id=7, now=193)
    state = vampire.advance(state, now=252)
    assert 7 in state["last_deaths"]
    state = vampire.activate(state, now=253)
    with pytest.raises(ValueError, match="not_player"):
        vampire.night_action(state, user_id=7, target_id=8, now=254)


def test_tie_one_runoff_then_no_elimination_and_inactivity_cancel():
    state = active()
    state = vampire.advance(state, now=100)
    state = vampire.activate(state, now=101)
    state = vampire.advance(state, now=191)
    state = vampire.activate(state, now=192)
    for uid, target in {1: 4, 2: 4, 3: 5, 4: 5}.items():
        state = vampire.vote(state, user_id=uid, target_id=target, now=193)
    state = vampire.advance(state, now=252)
    assert state["phase"] == "runoff" and state["runoff_candidates"] == [4, 5]
    state = vampire.activate(state, now=253)
    for uid, target in {1: 4, 2: 4, 3: 5, 4: 5}.items():
        state = vampire.vote(state, user_id=uid, target_id=target, now=254)
    state = vampire.advance(state, now=283)
    assert state["phase"] == "night" and state["empty_days"] == 1
    for day in (2, 3):
        state = vampire.activate(state, now=day * 1000)
        state = vampire.advance(state, now=day * 1000 + 90)
        state = vampire.activate(state, now=day * 1000 + 91)
        state = vampire.advance(state, now=day * 1000 + 181)
        state = vampire.activate(state, now=day * 1000 + 182)
        state = vampire.advance(state, now=day * 1000 + 242)
    assert state["phase"] == "cancelled" and not state["score_pending"]


def test_departure_after_start_counts_as_death_but_role_delivery_cancels():
    state = active()
    state = vampire.leave(state, user_id=7, now=12)
    assert state["phase"] == "night" and not state["score_pending"]
    assert not next(p for p in state["players"] if p["id"] == 7)["active"]
    delivery = vampire.new_lobby(chat_id=-100, thread_id=None, owner_id=1,
                                 owner_name="Bir", round_id="r", now=1)
    for uid in range(2, 8):
        delivery = vampire.join(delivery, user_id=uid, user_name=str(uid), now=2)
    for uid in range(1, 8):
        delivery = vampire.ready(delivery, user_id=uid, now=3)
    delivery = vampire.start(delivery, user_id=1, now=4)
    delivery = vampire.leave(delivery, user_id=7, now=5)
    assert delivery["phase"] == "cancelled" and not delivery["score_pending"]


@pytest.mark.parametrize("count", range(7, 21))
def test_each_role_template_has_both_terminal_team_outcomes(count):
    original = active(count)
    vampires = [p for p in original["players"] if p["role"] == "vampire"]
    villagers = [p for p in original["players"] if p["role"] != "vampire"]
    for team, defeated in (("villagers", vampires), ("vampires", villagers)):
        state = vampire._copy(original)
        for person in defeated:
            next(p for p in state["players"] if p["id"] == person["id"])["alive"] = False
        assert vampire._winner(state) == team
        assert vampire._check_win(state) == team
        assert state["phase"] == "finished" and state["score_pending"]


def test_lobby_protection_settings_are_enforced():
    state = active()
    # Default permits self-healing but forbids the same target on consecutive nights.
    healer = next(p["id"] for p in state["players"] if p["role"] == "healer")
    state = vampire.night_action(state, user_id=healer, target_id=healer, now=11)
    state = vampire.advance(state, now=100)
    state = vampire.activate(state, now=101)
    state = vampire.advance(state, now=191)
    state = vampire.activate(state, now=192)
    state = vampire.advance(state, now=252)
    state = vampire.activate(state, now=253)
    with pytest.raises(ValueError, match="invalid_target"):
        vampire.night_action(state, user_id=healer, target_id=healer, now=254)
    lobby = vampire.new_lobby(chat_id=-100, thread_id=None, owner_id=1,
                              owner_name="Bir", round_id="r", now=1)
    lobby = vampire.configure(lobby, user_id=1, option="self_heal", enabled=False, now=2)
    assert lobby["settings"]["self_heal"] is False


def test_max_table_panel_callback_limits_and_no_active_secret_key():
    state = active(20)
    state["chat_id"] = -(2**63 - 1)
    state["thread_id"] = 2**63 - 1
    state["round_id"] = "f" * 12
    for person in state["players"]:
        person["name"] = "<&>" * 20
    panel = render_vampire(state)
    assert len(panel.encode("utf-8")) <= 4096
    for action in ("night", "vote", "refresh", "stop"):
        assert len(callback_data(state, action, 2**63 - 1).encode("utf-8")) <= 64
    visible = vampire.public_state(state)
    assert not any("role" in person for person in visible["players"])
    assert "actions" not in visible and "seer_findings" not in visible


def test_late_lobby_ack_and_leave_rejected_before_worker_tick():
    lobby = vampire.new_lobby(chat_id=-100, thread_id=None, owner_id=1,
                              owner_name="Bir", round_id="r", now=1)
    with pytest.raises(ValueError, match="stale_phase"):
        vampire.join(lobby, user_id=2, user_name="İki", now=1 + vampire.LOBBY_SECONDS)
    delivery = vampire._copy(lobby)
    delivery["phase"] = "role_delivery"
    delivery["last_activity_at"] = 1
    with pytest.raises(ValueError, match="stale_phase"):
        vampire.role_ack(delivery, user_id=1, now=1 + vampire.DELIVERY_SECONDS)
    state = active()
    with pytest.raises(ValueError, match="stale_phase"):
        vampire.leave(state, user_id=7, now=state["deadline"])


def test_terminal_panel_has_replay_and_game_lobby_actions():
    state = active()
    state = vampire.cancel(state, user_id=1)
    actions = [button.callback_data for row in group_keyboard(state).inline_keyboard
               for button in row]
    assert actions == ["game_start_vampire", "menu_games:social"]
