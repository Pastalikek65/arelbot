import time

from src.games.engine import (
    DIFFICULTIES,
    calculate_hangman_score,
    calculate_word_guess_score,
    calculate_word_game_score,
    normalize_difficulty,
    parse_word_game_request,
)
from src.games.word_guess import WordGuessGame
from src.tools.games_tool import GamesTool


def test_difficulty_aliases_are_turkish_aware_and_bounded():
    assert normalize_difficulty("kolay").key == "easy"
    assert normalize_difficulty("normal").key == "normal"
    assert normalize_difficulty("zor").key == "hard"
    assert normalize_difficulty("uzman").key == "expert"
    assert normalize_difficulty("unknown").key == "normal"
    assert set(DIFFICULTIES) == {"easy", "normal", "hard", "expert"}


def test_word_game_request_separates_category_and_difficulty():
    category, difficulty = parse_word_game_request(["ÅŸehirler", "zor"])

    assert category == "ÅŸehirler"
    assert difficulty.key == "hard"


def test_score_increases_for_longer_faster_and_harder_rounds():
    easy = calculate_word_game_score(
        word="kelime", elapsed_seconds=40, difficulty="kolay", combo=1
    )
    expert = calculate_word_game_score(
        word="kelime", elapsed_seconds=40, difficulty="uzman", combo=3
    )
    assert expert > easy

    fast = calculate_word_guess_score(
        elapsed_seconds=10, difficulty="zor", attempts=1
    )
    slow = calculate_word_guess_score(
        elapsed_seconds=170, difficulty="zor", attempts=4
    )
    assert fast > slow


def test_score_is_safe_for_invalid_timings_and_never_exceeds_telegram_range():
    score = calculate_word_game_score(
        word="a", elapsed_seconds=-10, difficulty="uzman", combo=999
    )
    assert 1 <= score <= 100


def test_word_guess_picks_words_matching_the_requested_difficulty():
    game = WordGuessGame()
    state = game.start(99, difficulty="uzman")["game_state"]

    assert state["difficulty"] == "expert"
    assert state["time_limit"] == 150
    assert len(state["word"]) >= 5


import pytest


@pytest.mark.asyncio
async def test_word_game_persists_difficulty_and_target_bounds():
    result = await GamesTool().execute(
        game_type="word_game", category="bilim", difficulty="zor"
    )

    assert result["difficulty"] == "hard"
    assert result["time_limit"] == 180
    assert 8 <= result["target_count"] <= min(12, result["dictionary_size"])


@pytest.mark.asyncio
async def test_hangman_persists_difficulty_and_lives():
    result = await GamesTool().execute(game_type="hangman", difficulty="uzman")

    assert result["difficulty"] == "expert"
    assert result["lives"] == result["max_lives"] == 4
    assert result["time_limit"] == 150
    assert 5 <= len(result["word"]) <= 20


def test_hangman_score_rewards_speed_and_remaining_lives():
    careful = calculate_hangman_score(
        elapsed_seconds=20,
        difficulty="uzman",
        lives_remaining=4,
        max_lives=4,
        wrong_guesses=0,
    )
    battered = calculate_hangman_score(
        elapsed_seconds=140,
        difficulty="uzman",
        lives_remaining=1,
        max_lives=4,
        wrong_guesses=3,
    )

    assert careful > battered
    assert 1 <= careful <= 100

