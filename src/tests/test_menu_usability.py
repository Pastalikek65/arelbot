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
