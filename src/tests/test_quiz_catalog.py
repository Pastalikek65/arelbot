from src.games.quiz import QuizGame


def test_quiz_has_expanded_categories_and_turkish_aliases():
    game = QuizGame()
    assert {"muzik", "turkiye", "gundelik", "oyunlar"}.issubset(set(game.categories()))
    assert game.normalize_category("MÜZİK") == "muzik"
    assert game.normalize_category("oyun") == "oyunlar"


def test_quiz_question_is_four_choice_and_answer_is_present():
    game = QuizGame()
    question = game.get_question("Türkiye")
    assert question["category"] == "turkiye"
    assert len(question["options"]) == 4
    assert question["answer"] in question["options"]



def test_quiz_scope_is_seeded_and_does_not_repeat_until_category_cycle():
    first = QuizGame(seed=21)
    second = QuizGame(seed=21)

    a = [first.get_question("bilim", scope="chat:7")["question_id"] for _ in range(10)]
    b = [second.get_question("bilim", scope="chat:7")["question_id"] for _ in range(10)]

    assert a == b
    assert len(set(a)) == 10
    assert first.get_question("bilim", scope="chat:7")["cycle_reset"] is True


def test_quiz_difficulty_filter_keeps_category_and_returns_safe_projection():
    game = QuizGame(seed=9)
    question = game.get_question("bilim", scope="difficulty", difficulty="orta")

    assert question["category"] == "bilim"
    assert question["difficulty"] == "orta"
    assert len(question["options"]) == 4
    assert question["answer"] in question["options"]
    assert question["question_id"]
