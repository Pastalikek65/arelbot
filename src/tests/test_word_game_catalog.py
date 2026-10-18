import pytest

from src.tools.games_tool import GamesTool, WORD_GAME_CATEGORIES
from src.services.menu_service import MenuService
from src.games.vocabulary import (
    WORD_GAME_VOCABULARY,
    all_game_words,
    can_form_word,
    normalize_game_word,
    words_for_rack,
)
from src.games.word_guess import WordGuessGame


@pytest.mark.asyncio
async def test_word_game_supports_curated_categories_and_rack_metadata():
    tool = GamesTool()
    result = await tool.execute(game_type="word_game", category="bilim")
    assert result["category"] == "bilim"
    assert len(result["letters"]) == 8
    assert result["target_count"] >= 7
    assert result["category_hint"]
    assert set(result["available_categories"]) == set(WORD_GAME_CATEGORIES)


@pytest.mark.asyncio
async def test_word_game_unknown_category_falls_back_to_a_supported_category():
    tool = GamesTool()
    result = await tool.execute(game_type="word_game", category="olmayan")
    assert result["category"] in WORD_GAME_CATEGORIES


@pytest.mark.asyncio
async def test_quiz_tool_renders_the_question_without_exposing_the_answer():
    tool = GamesTool()
    result = await tool.execute(game_type="quiz", category="bilim")

    assert "Oyun servisi" not in result
    assert "SORU" in result
    assert "Cevap:" not in result


def test_games_menu_opens_word_game_category_picker():
    _text, markup = MenuService().games_menu()
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]

    assert "game_word_categories" in callbacks


def test_word_game_category_picker_has_all_curated_categories():
    _text, markup = MenuService().word_game_categories_menu()
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]

    assert {item.split(":", 1)[1] for item in callbacks if item.startswith("game_word_category:")} == set(WORD_GAME_CATEGORIES)


def test_word_game_difficulty_picker_exposes_all_profiles():
    _text, markup = MenuService().word_game_difficulty_menu()
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]

    assert {item.split(":", 1)[1] for item in callbacks if item.startswith("game_word_difficulty:")} == {
        "easy", "normal", "hard", "expert"
    }


def test_hangman_difficulty_picker_exposes_all_profiles():
    _text, markup = MenuService().hangman_difficulty_menu()
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]

    assert {item.split(":", 1)[1] for item in callbacks if item.startswith("game_hangman_difficulty:")} == {
        "easy", "normal", "hard", "expert"
    }


@pytest.mark.asyncio
async def test_word_game_accepts_turkish_character_free_category_alias():
    tool = GamesTool()
    result = await tool.execute(game_type="word_game", category="sehirler")

    assert result["category"] == "şehirler"


def test_word_vocabulary_is_large_deduplicated_and_turkish_aware():
    words = all_game_words()

    assert len(words) >= 300
    assert len(words) == len({normalize_game_word(word) for word in words})
    assert all(len(normalize_game_word(word)) >= 3 for word in words)
    assert all(len(items) >= 20 for items in WORD_GAME_VOCABULARY.values())
    assert normalize_game_word("ÇIĞLIK") == "ciglik"


def test_word_bank_can_validate_words_against_a_letter_rack():
    assert can_form_word("İSTANBUL", "İSTANBUL")
    assert can_form_word("bal", "İSTANBUL")
    assert not can_form_word("kalem", "İSTANBUL")


def test_every_category_has_at_least_seven_dictionary_answers():
    for category, (rack, _hint) in WORD_GAME_CATEGORIES.items():
        answers = words_for_rack(category, rack)
        assert len(answers) >= 7, category


def test_anlatmaca_uses_the_expanded_shared_vocabulary():
    game = WordGuessGame()

    assert len(game.words) >= 600


def test_anlatmaca_filters_requested_category_and_has_a_complete_hint_chain():
    game = WordGuessGame()

    result = game.start(42, category="bilim")
    state = result["game_state"]

    assert state["category"] == "bilim"
    assert len(state["hints"]) >= 6
    assert all(str(hint).strip() for hint in state["hints"])
    assert state["word"].casefold() not in state["description"].casefold()


def test_anlatmaca_accepts_turkish_category_aliases():
    game = WordGuessGame()

    state = game.start(42, category="doğa")["game_state"]

    assert state["category"] == "doğa"


@pytest.mark.asyncio
async def test_word_game_exposes_a_real_dictionary_pool():
    tool = GamesTool()
    result = await tool.execute(game_type="word_game", category="teknoloji")

    assert result["dictionary_size"] >= 7
    assert len(result["valid_words"]) >= 7
    assert result["target_count"] <= len(result["valid_words"])
