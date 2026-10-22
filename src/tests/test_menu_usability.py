from datetime import datetime, timezone

from src.features.contracts import FeatureResult
from src.services.feature_presentation import format_feature_result
from src.services.menu_service import MenuService


def test_group_home_has_no_duplicate_destinations_or_private_plan_dead_end():
    text, markup = MenuService().main_menu(user_id=7, chat_type="supergroup")
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert len(callbacks) == len(set(callbacks))
    assert "menu_plan" not in callbacks
    assert "daily_brief:7" not in callbacks
    assert "notify_center:7" in callbacks
    assert "open_ayar_menu" in callbacks
    assert "grup" in text.casefold()


def test_private_home_keeps_personal_workflows_and_unique_actions():
    text, markup = MenuService().main_menu(user_id=7, chat_type="private")
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert len(callbacks) == len(set(callbacks))
    assert "menu_plan" in callbacks
    assert "daily_brief:7" in callbacks


def test_typed_result_does_not_discard_later_display_sections():
    result = FeatureResult(
        ok=True, data={}, display_blocks=("<b>Hava durumu</b>", "Bugün: 20 °C", "Yarın: yağışlı"),
        source="Hava kaynağı", fetched_at=datetime(2026, 10, 18, tzinfo=timezone.utc),
    )
    rendered = format_feature_result(result, "Veri alınamadı.")
    assert "Hava durumu" in rendered
    assert "Bugün: 20 °C" in rendered
    assert "Yarın: yağışlı" in rendered
    assert rendered.count("Kaynak:") == 1


def test_game_categories_keep_every_existing_game_reachable_with_a_short_home():
    menu = MenuService()
    _, home = menu.games_menu()
    home_buttons = [b for row in home.inline_keyboard for b in row]
    assert len(home_buttons) <= 11
    categories = [b.callback_data.split(":", 1)[1] for b in home_buttons if b.callback_data.startswith("menu_games:")]
    assert len(categories) == 5
    actions = set()
    for category in categories:
        _, markup = menu.games_menu(category=category)
        callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
        assert "menu_games" in callbacks
        assert len(callbacks) == len(set(callbacks))
        actions.update(callbacks)
    assert {"game_start_anlat", "game_hangman_difficulty", "game_word_categories", "game_start_quiz",
            "game_start_sise", "game_start_batak", "game_start_uno", "game_start_blackjack",
            "game_start_connect4", "game_start_tictactoe", "game_start_gomoku", "game_start_chess",
            "game_start_akinator", "game_start_guess_age", "game_start_chain", "game_start_tabu",
            "game_start_rusruleti", "game_start_duello"} <= actions
