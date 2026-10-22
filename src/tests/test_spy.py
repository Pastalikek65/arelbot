"""Rules and leak checks for Casus."""
import random

import pytest

from src.games import spy
from src.services.spy_ui import render_spy


def ready_match(count=4):
    state = spy.new_lobby(chat_id=-100, thread_id=7, owner_id=1, owner_name="Bir", now=0)
    for user in range(2, count + 1):
        state = spy.join(state, user_id=user, user_name=f"Oyuncu {user}", now=1)
    state = spy.start(state, user_id=1, spy_id=1, location_index=0, first_index=0, now=2)
    for user in range(1, count + 1):
        state = spy.delivered(state, user_id=user, now=3)
        state = spy.confirm(state, user_id=user, now=4)
    state["deadline"] = 4 + spy.TURN_SECONDS
    return state


def to_vote(state):
    for index, asker in enumerate(state["question_order"]):
        target = state["question_order"][(index + 1) % len(state["question_order"])]
        state = spy.question_target(state, user_id=asker, target_id=target, now=5 + index)
        state = spy.group_text(state, user_id=asker, text="Bugün yoğun mu?", now=6 + index)
        state = spy.group_text(state, user_id=target, text="Biraz yoğun.", now=7 + index)
    assert state["phase"] == "discussion"
    state["deadline"] = 20 + spy.DISCUSSION_SECONDS
    state = spy.expire(state, now=state["deadline"])
    state["deadline"] = 120 + spy.VOTE_SECONDS
    return state


def test_content_and_private_role_safety():
    assert len(spy.LOCATIONS) >= 30
    assert len({place for place, _ in spy.LOCATIONS}) == len(spy.LOCATIONS)
    assert set(spy.LOCATION_ROLES) == {place for place, _ in spy.LOCATIONS}
    assert all(len(roles) >= 4 and len(set(roles)) == len(roles)
               for roles in spy.LOCATION_ROLES.values())
    state = spy.new_lobby(chat_id=-100, thread_id=7, owner_id=1, owner_name="Bir", now=0)
    for user in (2, 3, 4):
        state = spy.join(state, user_id=user, user_name=str(user), now=1)
    state = spy.start(state, user_id=1, spy_id=1, location_index=0, first_index=0, now=2)
    assert spy.private_role(state, user_id=1) == {"spy": True, "location": None, "role": None}
    assert spy.private_role(state, user_id=2)["location"] == "İlçe kütüphanesi"
    assert "İlçe kütüphanesi" not in render_spy(state)
    assert "spy_id" not in spy.public_state(state)
    assert "location" not in spy.public_state(state)
    with pytest.raises(ValueError, match="spy_not_delivered"):
        spy.confirm(state, user_id=2, now=3)


def test_each_location_assigns_distinct_private_roles_to_innocents():
    for location_index, (place, primary_role) in enumerate(spy.LOCATIONS):
        state = spy.new_lobby(chat_id=-100, thread_id=None,
                              owner_id=1, owner_name="Bir", now=0)
        for user in (2, 3, 4):
            state = spy.join(state, user_id=user, user_name=str(user), now=1)
        state = spy.start(state, user_id=1, spy_id=1, location_index=location_index,
                          first_index=0, rng=random.Random(location_index), now=2)
        assert spy.LOCATION_ROLES[place][0] == primary_role
        assert "1" not in state["assigned_roles"]
        assert len(set(state["assigned_roles"].values())) == 3
        assert {spy.private_role(state, user_id=user)["role"] for user in (2, 3, 4)} <= \
               set(spy.LOCATION_ROLES[place])
        assert spy.private_role(state, user_id=1) == {"spy": True, "location": None, "role": None}
        assert "assigned_roles" not in spy.public_state(state)
        assert place not in render_spy(state)


def test_large_table_role_distribution_balanced_and_seed_fixture_repeatable():
    lobby = spy.new_lobby(chat_id=-100, thread_id=None,
                          owner_id=1, owner_name="Bir", now=0)
    for user in range(2, 17):
        lobby = spy.join(lobby, user_id=user, user_name=str(user), now=1)
    left = spy.start(lobby, user_id=1, spy_id=1, location_index=0,
                     first_index=0, rng=random.Random(41), now=2)
    right = spy.start(lobby, user_id=1, spy_id=1, location_index=0,
                      first_index=0, rng=random.Random(41), now=2)
    assert left["assigned_roles"] == right["assigned_roles"]
    assert "1" not in left["assigned_roles"]
    counts = [list(left["assigned_roles"].values()).count(role)
              for role in spy.LOCATION_ROLES["İlçe kütüphanesi"]]
    assert max(counts) - min(counts) <= 1


def test_player_bounds_and_all_ack_required():
    state = spy.new_lobby(chat_id=-100, thread_id=None, owner_id=1, owner_name="Bir", now=0)
    with pytest.raises(ValueError, match="spy_player_count"):
        spy.start(state, user_id=1, now=1)
    for user in range(2, 17):
        state = spy.join(state, user_id=user, user_name=str(user), now=1)
    with pytest.raises(ValueError, match="spy_full"):
        spy.join(state, user_id=17, user_name="17", now=1)
    state = spy.start(state, user_id=1, spy_id=16, location_index=35, first_index=15, now=2)
    for user in range(1, 17):
        state = spy.delivered(state, user_id=user, now=3)
        if user != 16:
            state = spy.confirm(state, user_id=user, now=4)
    assert state["phase"] == "delivery"
    state = spy.confirm(state, user_id=16, now=4)
    assert state["phase"] == "question"
    assert state["question_order"][0] == 16


def test_question_answer_sequence_and_timeout():
    state = ready_match()
    with pytest.raises(ValueError, match="spy_invalid_target"):
        spy.question_target(state, user_id=1, target_id=1, now=5)
    state = spy.question_target(state, user_id=1, target_id=2, now=5)
    with pytest.raises(ValueError, match="spy_not_turn"):
        spy.group_text(state, user_id=2, text="Erken yanıt", now=6)
    state = spy.group_text(state, user_id=1, text="Nasıl?", now=6)
    with pytest.raises(ValueError, match="spy_not_turn"):
        spy.group_text(state, user_id=1, text="Tekrar", now=7)
    state = spy.group_text(state, user_id=2, text="İyi.", now=7)
    assert state["turn_index"] == 1
    with pytest.raises(ValueError, match="spy_expired"):
        spy.group_text(state, user_id=2, text="Geç kaldım", now=state["deadline"])
    state = spy.expire(state, now=state["deadline"])
    assert state["phase"] == "discussion"
    state["deadline"] = 100 + spy.DISCUSSION_SECONDS
    state = spy.expire(state, now=state["deadline"])
    assert state["phase"] == "vote"


def test_majority_tie_revote_and_repeat_tie_spy_wins():
    state = to_vote(ready_match())
    for voter, target in ((1, 2), (2, 1), (3, 1), (4, 2)):
        state = spy.vote(state, user_id=voter, target_id=target, now=10)
    with pytest.raises(ValueError, match="spy_already_voted"):
        spy.vote(state, user_id=1, target_id=3, now=10)
    state = spy.close_vote(state, now=11)
    assert state["vote_round"] == 2 and set(state["candidates"]) == {1, 2}
    state["deadline"] = 11 + spy.REVOTE_SECONDS
    for voter, target in ((1, 2), (2, 1), (3, 1), (4, 2)):
        state = spy.vote(state, user_id=voter, target_id=target, now=12)
    state = spy.close_vote(state, now=13)
    assert state["winner"] == "spy" and state["reason"] == "vote_tie"


def test_majority_spy_found_and_innocent_accused():
    for target, winner in ((1, "group"), (2, "spy")):
        state = to_vote(ready_match())
        for voter in (1, 2, 3, 4):
            if voter != target:
                state = spy.vote(state, user_id=voter, target_id=target, now=10)
        state = spy.close_vote(state, now=state["deadline"])
        assert state["winner"] == winner


def test_private_one_guess_and_leaving():
    state = ready_match()
    with pytest.raises(ValueError, match="spy_guess_forbidden"):
        spy.guess(state, user_id=2, location="İlçe kütüphanesi", now=5)
    assert spy.guess(state, user_id=1, location="İlçe kütüphanesi", now=5)["winner"] == "spy"
    assert spy.guess(state, user_id=1, location="Başka", now=5)["winner"] == "group"
    assert spy.leave(state, user_id=1, now=5)["winner"] == "group"
    assert spy.leave(state, user_id=2, now=5)["phase"] == "cancelled"
    with pytest.raises(ValueError, match="spy_not_owner"):
        spy.cancel(state, user_id=2, now=5)
    assert spy.cancel(state, user_id=2, moderator=True, now=5)["phase"] == "cancelled"


def test_delivery_timeout_and_public_result():
    state = spy.new_lobby(chat_id=-100, thread_id=None, owner_id=1, owner_name="Bir", now=0)
    assert spy.expire(state, now=spy.LOBBY_SECONDS)["phase"] == "cancelled"
    state = ready_match()
    result = spy.guess(state, user_id=1, location="Yanlış", now=5)
    view = spy.public_state(result)
    assert view["location"] == "İlçe kütüphanesi"
    assert view["spy_id"] == 1
