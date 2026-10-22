"""Three distinct Turkish word-game rule contracts."""
import random

import pytest

from src.games import word_puzzles as games


def lobby(game_key, count=1):
    state = games.new_lobby(game_key=game_key, chat_id=-100, thread_id=7,
                            owner_id=1, owner_name="Bir", now=0)
    for user in range(2, count + 1):
        state = games.join(state, user_id=user, user_name=f"Oyuncu {user}", now=1)
    return state


def test_wordle_duplicate_letters_and_dictionary_gate():
    assert games.wordle_feedback("kalem", "elmek") == ("absent", "present", "present", "correct", "present")
    state = games.start(lobby("wordle_tr"), user_id=1, target="kalem", now=2)
    assert state["phase"] == "playing" and state["deadline"] is None
    assert "kalem" not in str(games.public_state(state))
    state["deadline"] = 902
    with pytest.raises(ValueError, match="word_puzzles_unknown_word"):
        games.submit_text(state, user_id=1, text="zzzzz", now=3)
    with pytest.raises(ValueError, match="word_puzzles_not_player"):
        games.submit_text(state, user_id=2, text="kalem", now=3)
    state = games.submit_text(state, user_id=1, text="KALEM", now=3)
    assert state["phase"] == "finished" and state["winner_ids"] == [1]
    assert state["score_pending"]


def test_antonym_vetted_pairs_aliases_and_first_correct_round():
    assert len(games.ANTONYM_CARDS) >= 60
    assert all(card["prompt"] and card["answers"] for card in games.ANTONYM_CARDS)
    state = games.start(lobby("karsi_kelime", 2), user_id=1,
                        card_order=list(range(5)), now=2)
    assert state["round_number"] == 1 and state["phase"] == "playing"
    assert state["target"] not in str(games.public_state(state))
    state["deadline"] = 92
    answer = state["answers"][0]
    state = games.submit_text(state, user_id=2, text=answer.upper(), now=3)
    assert state["phase"] == "reveal" and state["scores"]["2"] == 10
    assert state["deadline"] is None
    with pytest.raises(ValueError, match="word_puzzles_wrong_phase"):
        games.submit_text(state, user_id=1, text=answer, now=4)
    state["deadline"] = 9
    state = games.expire(state, now=9)
    assert state["round_number"] == 2 and state["phase"] == "playing"


def test_anagram_scramble_hint_cost_and_five_rounds():
    assert all(5 <= len(word) <= 9 for word in games.ANAGRAM_WORDS)
    state = games.start(lobby("anagram", 2), user_id=1, rng=random.Random(4),
                        word_order=list(range(5)), now=2)
    assert state["scramble"] != state["target"]
    assert state["scramble"] not in games.ANAGRAM_WORDS
    assert sorted(state["scramble"]) == sorted(state["target"])
    assert state["target"] not in str(games.public_state(state))
    state["deadline"] = 92
    state = games.hint(state, user_id=1, now=3)
    assert state["hint_count"] == 1 and state["round_value"] == 8
    state = games.submit_text(state, user_id=2, text=state["target"], now=4)
    assert state["scores"]["2"] == 8 and state["phase"] == "reveal"
    assert state["target"] in str(games.public_state(state))


def test_anagram_accepts_other_vetted_word_with_same_letters():
    assert "anahtar" in games.ANAGRAM_WORDS and "tarhana" in games.ANAGRAM_WORDS
    index = games.ANAGRAM_WORDS.index("anahtar")
    order = [index, *[n for n in range(5) if n != index][:4]]
    state = games.start(lobby("anagram"), user_id=1, word_order=order, now=2)
    state["deadline"] = 92
    state = games.submit_text(state, user_id=1, text="tarhana", now=3)
    assert state["phase"] == "reveal" and state["target"] == "tarhana"


def test_time_and_membership_boundaries():
    state = lobby("karsi_kelime", 30)
    with pytest.raises(ValueError, match="word_puzzles_full"):
        games.join(state, user_id=31, user_name="31", now=2)
    state = games.start(state, user_id=1, card_order=list(range(5)), now=3)
    with pytest.raises(ValueError, match="word_puzzles_waiting_panel"):
        games.submit_text(state, user_id=2, text="küçük", now=4)
    state["deadline"] = 93
    with pytest.raises(ValueError, match="word_puzzles_expired"):
        games.submit_text(state, user_id=2, text="küçük", now=93)
