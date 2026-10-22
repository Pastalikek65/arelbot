"""Deterministic Tabu rules independent of Telegram and persistence."""

import pytest

from src.games import tabu
from src.games.tabu_cards import CARDS


def lobby():
    state = tabu.new_lobby(chat_id=-100, thread_id=7, owner_id=1, owner_name="A", round_id="match1", now=10)
    state = tabu.join(state, user_id=2, user_name="B", team="blue", now=10)
    state = tabu.join(state, user_id=3, user_name="C", team="red", now=10)
    state = tabu.join(state, user_id=4, user_name="D", team="blue", now=10)
    for uid in (1, 2, 3, 4):
        state = tabu.set_ready(state, user_id=uid, now=10)
    return state


def active():
    state = tabu.start(lobby(), user_id=1, now=10)
    card_id = state["card_id"]
    state = tabu.mark_card_delivered(state, user_id=1, card_id=card_id, now=11)
    state = tabu.ack_card(state, user_id=1, card_id=card_id, now=12)
    return state


def test_card_bank_has_150_original_turkish_cards():
    assert len(CARDS) >= 150
    assert len({card["target"] for card in CARDS}) == len(CARDS)
    assert all(len(card["forbidden"]) == 5 for card in CARDS)
    assert all(card["category"] and len(set(card["forbidden"])) == 5 for card in CARDS)


def test_ready_delivery_and_deadline_are_required():
    state = lobby()
    state = tabu.set_ready(state, user_id=4, ready=False)
    with pytest.raises(ValueError, match="not_ready"):
        tabu.start(state, user_id=1, now=10)
    state = tabu.start(tabu.set_ready(state, user_id=4), user_id=1, now=10)
    assert state["phase"] == "card_delivery"
    assert state["deadline"] is None
    with pytest.raises(ValueError, match="not_delivered"):
        tabu.ack_card(state, user_id=1, card_id=state["card_id"], now=11)
    state = tabu.mark_card_delivered(state, user_id=1, card_id=state["card_id"], now=11)
    state = tabu.ack_card(state, user_id=1, card_id=state["card_id"], now=12)
    assert state["deadline"] == 72
    assert tabu.public_state(state)["card_id"] == state["card_id"]
    assert "target" not in str(tabu.public_state(state))


def test_guess_appeal_and_single_score():
    state = active()
    card_id = state["card_id"]
    target = tabu.private_card(state, user_id=1, card_id=card_id)["target"]
    with pytest.raises(ValueError, match="not_guesser"):
        tabu.submit_guess(state, user_id=2, card_id=card_id, text=target, now=13)
    state = tabu.submit_guess(state, user_id=3, card_id=card_id, text=target, now=13)
    assert state["phase"] == "appeal" and state["team_scores"]["red"] == 0
    state = tabu.appeal(state, user_id=2, card_id=card_id, now=14)
    state = tabu.decide_appeal(state, user_id=99, card_id=card_id, decision="uphold", is_admin=True, now=15)
    state = tabu.advance(state, now=15)
    assert state["team_scores"]["red"] == 1
    with pytest.raises(ValueError, match="stale_card"):
        tabu.submit_guess(state, user_id=3, card_id=card_id, text=target, now=16)


def test_forbidden_inflection_and_spelling():
    state = active()
    card_id = state["card_id"]
    word = tabu.private_card(state, user_id=1, card_id=card_id)["forbidden"][0]
    state = tabu.submit_clue(state, user_id=1, card_id=card_id, text=f"{word}ler", now=13)
    assert state["pending_outcome"] == "forbidden"
    assert state["team_scores"]["red"] == 0


def test_timeout_and_restart_deadline():
    state = active()
    assert state["deadline"] == 72
    with pytest.raises(ValueError, match="expired"):
        tabu.submit_guess(state, user_id=3, card_id=state["card_id"], text="x", now=73)
    state = tabu.expire(state, now=73)
    assert state["pending_outcome"] == "timeout"
    assert state["appeal_until"] == 87
    assert tabu.advance(state, now=87)["phase"] == "card_delivery"


def test_leave_below_minimum_cancels():
    state = active()
    state = tabu.leave(state, user_id=3, now=13)
    assert state["phase"] == "cancelled"
    assert state["score_pending"] is False


def test_teams_card_quota_narrator_rotation_and_two_overtime_pairs():
    state = tabu.start(lobby(), user_id=1, now=10)
    assert state["quota"] == 4
    narrators = {"red": [], "blue": []}
    for index in range(12):
        team = state["active_team"]
        narrators[team].append(state["narrator_id"])
        card_id = state["card_id"]
        tick = 20 + index * 100
        state = tabu.mark_card_delivered(state, user_id=state["narrator_id"], card_id=card_id, now=tick)
        state = tabu.ack_card(state, user_id=state["narrator_id"], card_id=card_id, now=tick + 1)
        state = tabu.expire(state, now=tick + 61)
        state = tabu.advance(state, now=tick + 76)
        if index < 11:
            assert state["phase"] == "card_delivery"
    assert state["phase"] == "finished"
    assert state["winner_team"] is None
    assert state["overtime_pairs"] == 2
    assert state["cards_played"] == {"red": 6, "blue": 6}
    assert narrators["red"] == [1, 3, 1, 3, 1, 3]
    assert narrators["blue"] == [2, 4, 2, 4, 2, 4]


def test_pass_limit_and_neutral_judge_only():
    state = active()
    state = tabu.pass_card(state, user_id=1, card_id=state["card_id"], now=13)
    assert state["passes_left"]["red"] == 1
    with pytest.raises(ValueError, match="not_neutral_judge"):
        appealed = tabu.appeal(state, user_id=2, card_id=state["card_id"], now=14)
        tabu.decide_appeal(appealed, user_id=1, card_id=state["card_id"], decision="neutral", is_admin=True, now=15)
    appealed = tabu.appeal(state, user_id=2, card_id=state["card_id"], now=14)
    neutral = tabu.decide_appeal(appealed, user_id=99, card_id=state["card_id"], decision="neutral", is_admin=True, now=15)
    assert tabu.advance(neutral, now=15)["team_scores"]["red"] == 0
    with pytest.raises(ValueError, match="appeal_closed"):
        tabu.appeal(appealed, user_id=3, card_id=state["card_id"], now=16)


def test_turkish_letters_suffix_boundary_and_spelling_policy():
    assert tabu.expression_match("AĞAÇLAR", "ağaç") == "inflected"
    assert tabu.expression_match("a-ğ-a-ç", "ağaç") == "spelled"
    assert tabu.expression_match("İĞNE", "iğne") == "exact"
    assert tabu.expression_match("IŞIK", "ışık") == "exact"
    assert tabu.expression_match("ağaçlık", "ağaç") is None
    assert tabu.expression_match("ormanlık", "orman") is None
    assert tabu.expression_match("ağacı", "ağaç") == "inflected"
    assert tabu.expression_match("kitabı", "kitap") == "inflected"
    assert tabu.expression_match("ağacımız", "ağaç") == "inflected"
    assert tabu.expression_match("rengi", "renk") == "inflected"
    assert tabu.expression_match("ağaçgibi", "ağaç") == "inflected"
    assert tabu.expression_match("ağaçcık", "ağaç") is None


def test_target_alias_is_a_guess_and_is_forbidden_for_narrator():
    state = active()
    state["card"] = next(card for card in CARDS if card["target"] == "araba")
    alias = state["card"]["aliases"][0]
    clue = tabu.submit_clue(state, user_id=1, card_id=state["card_id"], text=alias, now=13)
    assert clue["pending_outcome"] == "forbidden"
    guess = tabu.submit_guess(state, user_id=3, card_id=state["card_id"], text=alias, now=13)
    assert guess["pending_outcome"] == "correct"


def test_appeal_group_view_cannot_reveal_clue_or_target():
    state = active()
    secret = state["card"]["target"]
    closed = tabu.submit_clue(state, user_id=1, card_id=state["card_id"],
                              text=f"{secret} özel ipucu", now=13)
    view = tabu.public_state(closed)
    assert view["pending_reason"] == "exact"
    assert "pending_evidence" not in view
    assert secret not in str(view)
    assert closed["pending_evidence"] is None


def test_owner_transfer_and_narrator_departure_single_version_step():
    state = lobby()
    state = tabu.join(state, user_id=5, user_name="E", team="red", now=10)
    state = tabu.set_ready(state, user_id=5, now=10)
    state = tabu.start(state, user_id=1, now=10)
    state = tabu.mark_card_delivered(state, user_id=1, card_id=state["card_id"], now=11)
    state = tabu.ack_card(state, user_id=1, card_id=state["card_id"], now=12)
    version = state["version"]
    state = tabu.leave(state, user_id=1, now=13)
    assert state["owner_id"] == 2
    assert state["version"] == version + 1
    assert state["phase"] == "appeal"
    assert state["pending_outcome"] == "neutral"
    replacement = tabu.advance(state, now=28)
    assert replacement["active_team"] == "red"
    assert replacement["cards_played"]["red"] == 0
    assert replacement["card_id"] != state["card_id"]


def test_lobby_owner_transfer_and_idle_expiry():
    state = lobby()
    state = tabu.leave(state, user_id=1, now=10)
    assert state["owner_id"] == 2
    with pytest.raises(ValueError, match="not_expired"):
        tabu.expire(state, now=1209)
    assert tabu.expire(state, now=1210)["phase"] == "cancelled"


def test_exact_deadlines_and_judge_timeout_keep_objective_result():
    state = active()
    card_id = state["card_id"]
    with pytest.raises(ValueError, match="expired"):
        tabu.submit_clue(state, user_id=1, card_id=card_id, text="ipucu", now=72)
    closed = tabu.expire(state, now=999)
    assert closed["appeal_until"] == 87  # restart cannot extend the clock
    with pytest.raises(ValueError, match="appeal_closed"):
        tabu.appeal(closed, user_id=2, card_id=card_id, now=87)
    with pytest.raises(ValueError, match="appeal_open"):
        tabu.advance(closed, now=86.999)
    appealed = tabu.appeal(closed, user_id=2, card_id=card_id, now=86)
    assert appealed["judge_deadline"] == 131
    with pytest.raises(ValueError, match="judge_closed"):
        tabu.decide_appeal(appealed, user_id=99, card_id=card_id,
                           decision="neutral", is_admin=True, now=131)
    with pytest.raises(ValueError, match="judge_open"):
        tabu.advance(appealed, now=130.999)
    next_card = tabu.advance(appealed, now=131)
    assert next_card["team_scores"]["red"] == 0
    assert next_card["card_id"] != card_id


def test_public_state_never_contains_secret_card_even_with_invite_hash():
    state = active()
    state["dm_invites"] = {"1": {"hash": "secret-token-hash"}}
    view = tabu.public_state(state)
    assert "card" not in view and "deck_seed" not in view and "dm_invites" not in view
    assert state["card"]["target"] not in str(view)


def test_duplicate_delivery_and_stale_card_do_not_renew_clock():
    state = active()
    deadline = state["deadline"]
    with pytest.raises(ValueError, match="not_delivery"):
        tabu.mark_card_delivered(state, user_id=1, card_id=state["card_id"], now=50)
    assert state["deadline"] == deadline
    with pytest.raises(ValueError, match="stale_card"):
        tabu.pass_card(state, user_id=1, card_id="wrong-card", now=13)


def test_secret_seed_builds_stable_nonrepeating_permutation():
    high_seed = (1 << 127) + 91
    first = tabu.new_lobby(chat_id=-100, thread_id=7, owner_id=1,
                           owner_name="A", round_id="a", deck_seed=high_seed, now=10)
    restarted = tabu.new_lobby(chat_id=-100, thread_id=7, owner_id=1,
                               owner_name="A", round_id="a", deck_seed=high_seed, now=10)
    changed = tabu.new_lobby(chat_id=-100, thread_id=7, owner_id=1,
                             owner_name="A", round_id="a", deck_seed=high_seed + 1, now=10)
    assert int(first["deck_seed"]) == high_seed
    assert first["deck_order"] == restarted["deck_order"]
    assert first["deck_order"] != changed["deck_order"]
    assert len(first["deck_order"]) == len(CARDS)
    assert len(set(first["deck_order"])) == len(CARDS)
    view = tabu.public_state(first)
    assert "deck_seed" not in view and "deck_order" not in view


def test_public_card_id_does_not_reveal_bank_card():
    state = active()
    bank_id = state["card"]["id"]
    public = tabu.public_state(state)
    assert bank_id not in public["card_id"]
    assert len(public["card_id"]) == 12
    assert all(char in "0123456789abcdef" for char in public["card_id"])
    assert bank_id not in str(public)


def test_card_delivery_timeout_cancels_without_reward_or_hidden_card():
    state = tabu.start(lobby(), user_id=1, now=10)
    with pytest.raises(ValueError, match="not_expired"):
        tabu.expire(state, now=129.999)
    cancelled = tabu.expire(state, now=130)
    assert cancelled["phase"] == "cancelled"
    assert cancelled["pending_reason"] == "card_ready_timeout"
    assert cancelled["score_pending"] is False
    assert cancelled["card"] is None
    assert tabu.public_state(cancelled)["pending_reason"] == "card_ready_timeout"


def test_first_delivery_extends_ready_window_once_only():
    state = tabu.start(lobby(), user_id=1, now=10)
    card_id = state["card_id"]
    state = tabu.mark_card_delivered(state, user_id=1, card_id=card_id, now=100)
    assert state["last_activity_at"] == 100
    with pytest.raises(ValueError, match="already_delivered"):
        tabu.mark_card_delivered(state, user_id=1, card_id=card_id, now=150)
    with pytest.raises(ValueError, match="not_expired"):
        tabu.expire(state, now=219.999)
    with pytest.raises(ValueError, match="card_ready_expired"):
        tabu.ack_card(state, user_id=1, card_id=card_id, now=220)
    assert tabu.expire(state, now=220)["phase"] == "cancelled"
    fresh = tabu.start(lobby(), user_id=1, now=10)
    with pytest.raises(ValueError, match="card_ready_expired"):
        tabu.mark_card_delivered(fresh, user_id=1, card_id=fresh["card_id"], now=130)
