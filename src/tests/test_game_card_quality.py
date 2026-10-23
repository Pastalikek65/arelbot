"""Content invariants for the original Turkish card banks."""

from collections import Counter

from src.games import clue_arcade, word_arcade
from src.games.tabu_cards import TABU_CARDS
from src.games.turkish_words import canonical_word


def test_tabu_cards_have_unique_answers_and_playable_forbidden_lists():
    assert len(TABU_CARDS) >= 200
    answers = [canonical_word(card["target"]) for card in TABU_CARDS]
    assert len(set(answers)) == len(answers)
    assert len({card["id"] for card in TABU_CARDS}) == len(TABU_CARDS)
    for card in TABU_CARDS:
        forbidden = [canonical_word(word) for word in card["forbidden"]]
        assert card["category"] and len(forbidden) == len(set(forbidden)) == 5
        assert all(word and word != canonical_word(card["target"]) for word in forbidden)


def test_five_clues_are_distinct_and_do_not_print_the_answer():
    assert len(clue_arcade.CLUES) >= 60
    assert len(clue_arcade.CATEGORIES) == len(clue_arcade.CLUES)
    answers = [clue_arcade.normalize(row[0]) for row in clue_arcade.CLUES]
    assert len(set(answers)) == len(answers)
    for index, row in enumerate(clue_arcade.CLUES):
        card = clue_arcade.card(clue_arcade.BES_IPUCU, index)
        clues = [clue_arcade.normalize(clue) for clue in row[1:]]
        assert card["category"] and len(clues) == len(set(clues)) == 5
        assert all(clue for clue in clues)
        assert all(answers[index] not in clue for clue in clues)


def test_emoji_answers_and_accepted_aliases_do_not_overlap_between_cards():
    assert len(clue_arcade.EMOJI) >= 85
    aliases = []
    for index, row in enumerate(clue_arcade.EMOJI):
        card = clue_arcade.card(clue_arcade.EMOJI_BILMECE, index)
        assert row[0] and card["category"]
        assert clue_arcade.normalize(row[1]) in card["aliases"]
        assert len(card["word_lengths"]) == len(clue_arcade.normalize(row[1]).split())
        aliases.extend(card["aliases"])
    assert all(count == 1 for count in Counter(aliases).values())


def test_forbidden_letter_cards_are_unique_and_letter_occurs_in_answer():
    assert len(word_arcade.FORBIDDEN_CARDS) >= 44
    answers = [canonical_word(answer) for answer, _ in word_arcade.FORBIDDEN_CARDS]
    assert len(set(answers)) == len(answers)
    for answer, letter in word_arcade.FORBIDDEN_CARDS:
        assert len(letter) == 1 and letter in canonical_word(answer)
        assert answer in word_arcade.ORIGINAL_WORDS


def test_clue_decks_cover_every_card_before_repeating_at_rollover():
    for game_key, pool in ((clue_arcade.BES_IPUCU, len(clue_arcade.CLUES)),
                           (clue_arcade.EMOJI_BILMECE, len(clue_arcade.EMOJI))):
        state = clue_arcade.new(game_key=game_key, chat_id=1, thread_id=None,
                                owner_id=1, owner_name="Oyuncu", round_id="cards",
                                now=0, prior={"deck": list(range(pool)),
                                              "deck_cursor": pool - 1})
        state = clue_arcade.start(state, user_id=1, now=1)
        previous = state["card"]["id"]
        assert previous == pool - 1
        clue_arcade._next_round(state, 2)
        assert state["card"]["id"] != previous
        assert len(state["deck"]) == len(set(state["deck"])) == pool
