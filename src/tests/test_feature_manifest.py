from src.features.catalog import register_runtime_local_features
from src.features.registry import FeatureRegistry


def test_runtime_local_surfaces_have_one_typed_manifest_entry_each():
    registry = FeatureRegistry()
    register_runtime_local_features(registry)

    expected = {
        "notes",
        "tasks",
        "reminders",
        "notification_preferences",
        "price_alerts",
        "media",
        "group_moderation_settings",
        "group_personality",
        "personal_personality",
        "games_leaderboard",
        "connect4",
        "tictactoe",
            "gomoku",
        "notification_dedup",
        "group_targeted_replies",
        "guardian_moderation_enforcement",
    }
    assert {definition.key for definition in registry.definitions()} == expected
    command_map = registry.command_map()
    assert command_map["ayar"] == "group_moderation_settings"
    assert command_map["indir"] == "media"

    by_key = {definition.key: definition for definition in registry.definitions()}
    assert by_key["media"].async_mode is True
    assert by_key["media"].timeout_ms == 120000
    assert by_key["group_moderation_settings"].permission == "group_admin"
    assert by_key["games_leaderboard"].aliases == ("liderlik", "profil")
    assert by_key["personal_personality"].permission == "user_private"
    assert by_key["notification_dedup"].permission == "system"
    assert all(definition.audit_policy != "none" for definition in registry.definitions())


def test_group_personality_can_be_disabled_by_the_central_kill_switch():
    registry = FeatureRegistry(disabled_features={"group_personality"})
    register_runtime_local_features(registry)

    status = registry.feature_status("group_personality")
    assert status["enabled"] is False
    assert status["reason"] == "kill_switch"
    assert registry.feature_status("notes")["enabled"] is True
