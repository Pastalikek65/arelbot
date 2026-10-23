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
        "chess",
            "akinator",
        "guess_age",
        "uno",
        "kelime_zinciri",
        "tabu",
        "harf_maratonu",
        "sozcuk_avi",
        "yasak_harf",
        "ortak_hikaye",
        "bomba",
        "kor_siralama",
        "kronoloji",
        "dogru_yanlis",
        "dedektif",
        "amiral_batti",
        "iki_dogru_bir_yalan",
        "kimim_ben",
            "ciz_bil",
        "takim_bilgi_ligi",
        "zehirli_kadeh",
        "pisti", "papaz_kimde", "2048", "bes_ipucu", "emoji_bilmece",
        "wordle_tr", "karsi_kelime", "anagram", "hafiza_eslestirme",
        "mayin_tarlasi", "sudoku", "sayisal_tahmin", "hizli_buton",
            "sifre_tahmin", "fark_bulmaca",
        "casus",
        "vampir_koylu",
        "notification_dedup",
        "group_targeted_replies",
        "guardian_moderation_enforcement",
    }
    assert {definition.key for definition in registry.definitions()} == expected
    command_map = registry.command_map()
    assert command_map["ayar"] == "group_moderation_settings"
    assert command_map["indir"] == "media"
    assert command_map["casus"] == command_map["spy"] == "casus"
    assert command_map["vampir"] == command_map["vampirkoylu"] == "vampir_koylu"

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
