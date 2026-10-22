"""Rules that distinguish five deterministic Turkish word games."""
import random

import pytest

from src.games import word_arcade as arcade


def lobby(key, players):
    state = arcade.new_lobby(game_key=key, chat_id=-100, thread_id=7,
                             owner_id=1, owner_name="Bir", now=0)
    for user_id in range(2, players + 1):
        state = arcade.join(state, user_id=user_id, user_name=str(user_id), now=1)
    return state


def test_marathon_seals_answers_then_scores_only_unique_valid_words():
    state = arcade.start(lobby("harf_maratonu", 3), user_id=1, now=2,
                         card_order=[0, 1, 2, 3, 4])
    assert state["phase"] == "collect" and "submissions" not in arcade.public_state(state)
    state["deadline"] = 32
    category, letter = state["category"], state["letter"]
    first, second = arcade.category_words(category, letter)[:2]
    state = arcade.submit(state, user_id=1, text=first, now=3)
    state = arcade.submit(state, user_id=2, text=first, now=4)
    state = arcade.submit(state, user_id=3, text=second, now=5)
    state = arcade.expire(state, now=32)
    assert state["phase"] == "reveal" and state["scores"]["3"] > 0
    assert state["scores"]["1"] == state["scores"]["2"] == 0


def test_word_hunt_validates_eight_neighbors_without_cell_reuse():
    state = arcade.start(lobby("sozcuk_avi", 1), user_id=1, now=2)
    assert state["phase"] == "hunt" and len(state["board"]) == 4
    assert len(arcade.board_words(state["board"])) >= 4
    assert arcade.find_path(state["board"], "kedi") is not None
    assert arcade.find_path(state["board"], "zzzz") is None
    state["deadline"] = 122
    state = arcade.submit(state, user_id=1, text="kedi", now=3)
    assert state["scores"]["1"] > 0
    with pytest.raises(ValueError, match="already_claimed"):
        arcade.submit(state, user_id=1, text="kedi", now=4)


def test_word_hunt_trie_agrees_with_independent_path_check():
    board = arcade.HUNT_BOARD
    expected = {word for word in arcade.HUNT_WORDS if arcade.find_path(board, word)}
    assert set(arcade.board_words(board)) == expected
    assert arcade.board_words(("aaaa", "aaaa", "aaaa", "aaaa")) == ()


def test_word_hunt_100_seeds_produce_many_solvable_boards():
    boards = [tuple(arcade.generate_hunt_board(random.Random(seed)))
              for seed in range(100)]
    assert len(set(boards)) >= 90
    assert all(len(arcade.board_words(board)) >= 6 for board in boards)
    assert all(arcade.find_path(board, "kedi") for board in boards)


def test_forbidden_letter_secret_ack_and_group_clue_restrictions():
    state = arcade.start(lobby("yasak_harf", 2), user_id=1, now=2,
                         card_order=[0, 1, 2, 3])
    assert state["phase"] == "secret_delivery"
    assert state["target"] not in str(arcade.public_state(state))
    state["private_message_id"] = 100
    state = arcade.secret_ack(state, user_id=state["narrator_id"], now=3)
    assert state["phase"] == "clue" and state["deadline"] is None
    state["deadline"] = 63
    with pytest.raises(ValueError, match="forbidden_letter"):
        arcade.submit(state, user_id=state["narrator_id"], text=state["forbidden"] + " ipucu", now=4)
    with pytest.raises(ValueError, match="target_leak"):
        arcade.submit(state, user_id=state["narrator_id"], text=state["target"], now=4)


def test_story_needs_two_sentences_required_word_and_continuity():
    state = arcade.start(lobby("ortak_hikaye", 3), user_id=1, now=2)
    assert state["phase"] == "story_turn"
    state["deadline"] = 32
    required = state["required_word"]
    with pytest.raises(ValueError, match="two_sentences"):
        arcade.submit(state, user_id=1, text=f"{required} uzun bir yolculuğa çıktı.", now=3)
    state = arcade.submit(state, user_id=1,
                          text=f"{required} yola çıktı. Bahçede bir kapı gördü.", now=4)
    assert state["turn_index"] == 1 and state["scores"]["1"] == 10
    assert state["continuity_word"] == "gördü"


def test_bomb_fuse_survives_valid_pass_and_explosion_costs_life():
    state = arcade.start(lobby("bomba", 2), user_id=1, now=2,
                         bigram="ka", fuse_seconds=90)
    assert state["phase"] == "bomb_turn" and state["deadline"] is None
    state["deadline"] = 12
    state["fuse_deadline"] = 92
    state = arcade.submit(state, user_id=1, text="kalem", now=3)
    assert state["current_id"] == 2 and state["fuse_remaining"] == 89
    assert state["deadline"] is None
    state["deadline"] = 13
    state["fuse_deadline"] = 92
    state = arcade.expire(state, now=13, rng=random.Random(3))
    assert state["lives"]["2"] == 1


def test_authored_banks_have_real_choices_and_local_words():
    from src.games.turkish_words import ORIGINAL_WORDS
    assert all(words <= ORIGINAL_WORDS for words in arcade.CATEGORY_LEXICON.values())
    assert all(words <= ORIGINAL_WORDS for words in arcade.BIGRAM_WORDS.values())
    assert all(len(arcade.category_words(category, letter)) >= 20
               for letter, category in arcade.MARATHON_CARDS)
    assert len(arcade.BIGRAM_WORDS) >= 10
    assert all(len(words) >= 30 for words in arcade.BIGRAM_WORDS.values())
    exhausted = arcade.BIGRAM_WORDS["ka"]
    assert arcade._next_bigram(exhausted, random.Random(1)) != "ka"
    assert len(arcade.FORBIDDEN_CARDS) >= 24
    assert all(target in ORIGINAL_WORDS and forbidden in target
               for target, forbidden in arcade.FORBIDDEN_CARDS)


def test_marathon_full_five_rounds_do_not_reveal_before_reveal():
    state = arcade.start(lobby("harf_maratonu", 2), user_id=1, now=2,
                         card_order=list(range(5)))
    for round_no in range(1, 6):
        assert state["phase"] == "collect" and state["round_no"] == round_no
        assert "submissions" not in arcade.public_state(state)
        word = arcade.category_words(state["category"], state["letter"])[0]
        state["deadline"] = 30 * round_no
        state = arcade.submit(state, user_id=1, text=word, now=30 * round_no - 1)
        state = arcade.expire(state, now=30 * round_no)
        assert state["phase"] == "reveal"
        state["deadline"] = 30 * round_no + 5
        state = arcade.expire(state, now=30 * round_no + 5)
    assert state["phase"] == "finished" and state["scores"]["1"] == 50


def test_forbidden_full_rotation_two_turns_each():
    state = arcade.start(lobby("yasak_harf", 2), user_id=1, now=2,
                         card_order=list(range(4)))
    narrators = []
    for round_no in range(1, 5):
        narrators.append(state["narrator_id"])
        state["private_message_id"] = 100 + round_no
        state = arcade.secret_ack(state, user_id=state["narrator_id"], now=round_no * 100)
        state["deadline"] = round_no * 100 + 60
        state = arcade.expire(state, now=round_no * 100 + 60)
        assert state["phase"] == "reveal"
        state["deadline"] = round_no * 100 + 65
        state = arcade.expire(state, now=round_no * 100 + 65)
    assert narrators == [1, 2, 1, 2] and state["phase"] == "finished"


def test_story_full_two_turns_each_with_visible_bounded_output():
    state = arcade.start(lobby("ortak_hikaye", 3), user_id=1, now=2)
    for turn in range(6):
        state["deadline"] = 100 + turn
        required = state["required_word"]
        continuity = f" {state['continuity_word']}" if state["continuity_word"] else ""
        text = f"{required}{continuity} birlikte yürüdü. Sonra sakin sakin baktı."
        state = arcade.submit(state, user_id=state["players"][turn % 3]["id"],
                              text=text, now=50 + turn)
    assert state["phase"] == "finished" and len(state["story"]) == 6
    assert all(points == 20 for points in state["scores"].values())
    assert arcade._utf16("\n".join(state["story"])) <= 3000


def test_bomb_last_survivor_after_two_lives_each():
    state = arcade.start(lobby("bomba", 2), user_id=1, now=2,
                         bigram="ka", fuse_seconds=60)
    for turn in range(3):
        state["deadline"] = 20 + turn
        state["fuse_deadline"] = 80 + turn
        state = arcade.expire(state, now=20 + turn, rng=random.Random(turn))
    assert state["phase"] == "finished" and state["lives"]["1"] == 0
    assert state["winner_ids"] == [2] and state["scores"]["2"] >= 20


def test_bomb_current_player_leave_passes_to_live_player_without_resetting_fuse():
    state = arcade.start(lobby("bomba", 3), user_id=1, now=2,
                         bigram="ka", fuse_seconds=90)
    state["deadline"], state["fuse_deadline"] = 12, 92
    state = arcade.submit(state, user_id=1, text="kalem", now=3)
    state["deadline"], state["fuse_deadline"] = 13, 92
    state = arcade.leave(state, user_id=2, now=8)
    assert state["phase"] == "bomb_turn" and state["current_id"] == 3
    assert state["fuse_remaining"] == 84 and state["fuse_deadline"] == 92
    assert state["deadline"] is None and state["phase_seq"] == 3
    state["deadline"], state["fuse_deadline"] = 18, 92
    state = arcade.submit(state, user_id=3, text="kale", now=9)
    assert state["current_id"] == 1


def test_story_active_author_leave_skips_to_next_member():
    state = arcade.start(lobby("ortak_hikaye", 5), user_id=1, now=2)
    state["deadline"] = 30
    state = arcade.leave(state, user_id=2, now=5)
    assert state["deadline"] == 30
    state = arcade.submit(state, user_id=1,
        text="kapı yavaşça açıldı. İçeride sakin bir kuş vardı.", now=6)
    assert state["turn_index"] == 2
    state["deadline"] = 45
    state = arcade.leave(state, user_id=3, now=7)
    assert state["turn_index"] == 3 and state["deadline"] is None
    state["deadline"] = 52
    state = arcade.submit(state, user_id=4,
        text=f"{state['required_word']} vardı kuş oradaydı. Herkes onu dikkatle izledi.", now=8)
    assert state["scores"]["4"] == 10 and len(state["story"]) == 2
