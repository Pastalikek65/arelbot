"""Independent full decision cycles for the four reasoning games."""

import itertools

from src.games import reasoning_arcade as game


def test_calendar_oracle_has_exactly_one_solution_for_all_five_rounds():
    for index in range(5):
        puzzle = game.calendar_puzzle(index)
        valid = [order for order in itertools.permutations(range(5))
                 if game.calendar_clues_hold(puzzle, order)]
        assert valid == [tuple(puzzle["solution"])]


def test_detective_oracle_has_exactly_one_solution_for_each_case():
    for index in range(3):
        case = game.detective_case(index)
        assignments = [(culprit, locations, objects)
                       for culprit in range(3)
                       for locations in itertools.permutations(range(3))
                       for objects in itertools.permutations(range(3))
                       if game.detective_assignment_clues_hold(
                           case, culprit, locations, objects)]
        assert assignments == [(case["culprit"],
                                tuple(case["place_by_person"]),
                                tuple(case["object_by_person"]))]
        partial = sum(game.detective_assignment_clues_hold(
            case, culprit, locations, objects, count=3)
            for culprit in range(3)
            for locations in itertools.permutations(range(3))
            for objects in itertools.permutations(range(3)))
        assert partial > 1  # Buying later clues has a real deduction benefit.
        valid = [(person, place, item)
                 for person, place, item in itertools.product(range(3), repeat=3)
                 if game.detective_clues_hold(case, person, place, item)]
        assert valid == [tuple(case["solution"])]


def test_true_false_explanations_match_algorithmic_answers():
    for index in range(10):
        claim = game.claim(index)
        assert isinstance(claim["answer"], bool)
        assert claim["answer"] == game.evaluate_claim(claim)
        assert claim["explanation"]


def test_seeded_matches_vary_while_every_clue_set_remains_uniquely_solvable():
    calendars, claims, cases = set(), set(), set()
    for seed in range(16):
        for index in range(5):
            puzzle = game.calendar_puzzle(index, seed=seed)
            valid = [order for order in itertools.permutations(range(5))
                     if game.calendar_clues_hold(puzzle, order)]
            assert valid == [tuple(puzzle["solution"])]
            calendars.add((tuple(puzzle["events"]), tuple(puzzle["solution"])))
        for index in range(10):
            proposition = game.claim(index, seed=seed)
            assert proposition["answer"] == game.evaluate_claim(proposition)
            claims.add(proposition["text"])
        for index in range(3):
            case = game.detective_case(index, seed=seed)
            valid = [(culprit, locations, objects)
                     for culprit in range(3)
                     for locations in itertools.permutations(range(3))
                     for objects in itertools.permutations(range(3))
                     if game.detective_assignment_clues_hold(case, culprit,
                                                              locations, objects)]
            assert valid == [(case["culprit"], tuple(case["place_by_person"]),
                              tuple(case["object_by_person"]))]
            cases.add(tuple(case["solution"]))
    assert len(calendars) > 40
    assert len(claims) > 50
    assert len(cases) > 3


def test_match_seed_survives_state_copy_but_is_not_public():
    one = game.new(game_key=game.CALENDAR, chat_id=-100, thread_id=None,
                   owner_id=1, owner_name="Bir", round_id="a", now=100, seed=123)
    two = game.new(game_key=game.CALENDAR, chat_id=-100, thread_id=None,
                   owner_id=1, owner_name="Bir", round_id="b", now=100, seed=456)
    one, two = (game.start(state, user_id=1, now=101) for state in (one, two))
    assert one["puzzle"] != two["puzzle"]
    assert game.calendar_puzzle(0, seed=123) == one["puzzle"]
    assert "puzzle_seed" not in game.public_state(one)


def test_rank_score_reverse_is_zero_and_correct_is_hundred():
    values = [11, 40, 23, 90, 7]
    assert game.rank_score(values, [2, 4, 3, 5, 1]) == 100
    assert game.rank_score(values, [4, 2, 3, 1, 5]) == 0
