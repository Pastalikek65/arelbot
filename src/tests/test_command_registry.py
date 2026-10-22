from __future__ import annotations

import pytest

from src.telegram.command_registry import (
    CommandDefinition,
    CommandRegistry,
    CommandScope,
    build_arel_command_registry,
)


def test_legacy_alias_resolves_to_canonical_but_is_hidden_from_menu():
    registry = CommandRegistry(
        (
            CommandDefinition(
                name="grupayari",
                description="Arel grup ayarları",
                handler="handle_ayar_command",
                scope=CommandScope.GROUP_ADMIN,
                aliases=("ayar", "ayarlar"),
            ),
        )
    )

    assert registry.resolve("/ayar").name == "grupayari"
    assert registry.resolve("ayarlar").is_legacy_alias is True
    assert [row[0] for row in registry.menu_rows(CommandScope.PUBLIC)] == []
    assert [row[0] for row in registry.menu_rows(CommandScope.GROUP_ADMIN)] == ["grupayari"]


def test_scope_visibility_includes_public_commands_for_group_admins():
    registry = CommandRegistry(
        (
            CommandDefinition("menu", "Menü", "handle_menu_command"),
            CommandDefinition("grupayari", "Grup ayarı", "handle_ayar_command", CommandScope.GROUP_ADMIN),
            CommandDefinition("arelmerkez", "Arel merkezi", "handle_admin_center_command", CommandScope.OWNER),
        )
    )

    assert [row[0] for row in registry.menu_rows(CommandScope.PUBLIC)] == ["menu"]
    assert [row[0] for row in registry.menu_rows(CommandScope.GROUP_ADMIN)] == ["menu", "grupayari"]
    assert [row[0] for row in registry.menu_rows(CommandScope.OWNER)] == ["menu", "grupayari", "arelmerkez"]


def test_registry_rejects_canonical_and_alias_collisions():
    with pytest.raises(ValueError, match="command_collision"):
        CommandRegistry(
            (
                CommandDefinition("bir", "Bir", "handler"),
                CommandDefinition("iki", "İki", "handler", aliases=("bir",)),
            )
        )


def test_arel_catalog_contains_moderation_canonical_names_and_hidden_guardian_aliases():
    registry = build_arel_command_registry()

    assert registry.resolve("/hizlikur").name == "hizlikur"
    assert registry.resolve("/kurulum").is_legacy_alias is True
    assert registry.resolve("/yasakla").name == "yasakla"
    assert registry.resolve("/ban").is_legacy_alias is True
    assert registry.resolve("/arelmerkez").scope is CommandScope.OWNER
    assert "hizlikur" in {row[0] for row in registry.menu_rows(CommandScope.GROUP_ADMIN)}
    assert "kurulum" not in {row[0] for row in registry.menu_rows(CommandScope.GROUP_ADMIN)}


def test_arel_catalog_display_text_is_valid_utf8_without_replacement_or_mojibake():
    registry = build_arel_command_registry()
    suspicious = ("\ufffd", "Ã", "Â", "Ä", "Å")

    for definition in registry.all_definitions():
        assert definition.description.strip()
        assert not any(fragment in definition.description for fragment in suspicious), definition


def test_guardian_command_vocabulary_is_callable_through_arels_registry():
    registry = build_arel_command_registry()
    for command in (
        "mute", "unmute", "ban", "unban", "kick", "tban", "tmute", "warn",
        "warns", "resetwarn", "del", "purge", "pin", "unpin", "report",
        "filter", "filters", "stop", "lock", "unlock", "locks", "setflood",
        "setfloodmode", "rules", "setrules", "welcome", "goodbye", "newfed",
        "joinfed", "leavefed", "fedstat", "fban", "funban",
    ):
        assert registry.resolve(f"/{command}") is not None, command
