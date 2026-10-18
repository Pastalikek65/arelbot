from __future__ import annotations

import pytest

from src.moderation.guardian_surface import GuardianSurface
from src.telegram.command_registry import CommandScope, build_arel_command_registry


class Port:
    def __init__(self):
        self.settings = {}
        self.users = {}

    async def get_chat_settings(self, chat_id):
        return dict(self.settings.get(chat_id, {}))

    async def update_chat_setting_key(self, chat_id, key, value):
        self.settings.setdefault(chat_id, {})[key] = value

    async def get_user(self, user_id):
        return dict(self.users.get(user_id, {}))

    async def update_user(self, user_id, data):
        self.users.setdefault(user_id, {}).update(data)


@pytest.mark.asyncio
async def test_guardian_surface_persists_group_toggle_and_text():
    port = Port()
    surface = GuardianSurface(port)

    result = await surface.execute("/antilink", chat_id=-100, user_id=7, args=["on"], is_admin=True)
    assert result and result.changed is True
    assert port.settings[-100]["link_filter"] is True

    result = await surface.execute("setwelcome", chat_id=-100, user_id=7, args=["selam", "{name}"], is_admin=True)
    assert result and port.settings[-100]["welcome_text"] == "selam {name}"


@pytest.mark.asyncio
async def test_guardian_surface_aliases_notes_and_exports_are_bounded():
    port = Port()
    surface = GuardianSurface(port)
    await surface.execute("save", chat_id=-100, user_id=7, args=["duyuru", "hello"], is_admin=True)
    result = await surface.execute("get", chat_id=-100, user_id=7, args=["duyuru"], is_admin=False)
    assert result and result.text == "hello"
    export = await surface.execute("export", chat_id=-100, user_id=7, args=[], is_admin=True)
    assert export and export.document and export.document["chat_id"] == -100


@pytest.mark.asyncio
async def test_guardian_surface_applies_group_aliases_disables_and_note_authorization():
    port = Port()
    surface = GuardianSurface(port)
    await surface.execute("alias", chat_id=-100, user_id=7, args=["kurallar", "rules"], is_admin=True)
    assert await surface.resolve_command(-100, "kurallar") == "rules"
    await surface.execute("disable", chat_id=-100, user_id=7, args=["rules"], is_admin=True)
    assert await surface.is_command_disabled(-100, "rules") is True
    denied = await surface.execute("save", chat_id=-100, user_id=9, args=["x", "y"], is_admin=False)
    assert denied and denied.changed is False
    assert "yöneticileri" in denied.text


@pytest.mark.asyncio
async def test_guardian_surface_can_remove_shadow_ban_and_set_group_language():
    port = Port()
    surface = GuardianSurface(port)
    await surface.execute("hayaletban", chat_id=-100, user_id=7, args=["42"], is_admin=True)
    removed = await surface.execute("unsban", chat_id=-100, user_id=7, args=["42"], is_admin=True)
    assert removed and removed.changed is True
    language = await surface.execute("language", chat_id=-100, user_id=7, args=["tr"], is_admin=True)
    assert language and language.changed
    assert port.settings[-100]["guardian_language"] == "tr"


def test_registry_contains_full_guardian_command_surface_without_duplicate_names():
    registry = build_arel_command_registry()
    for name in ("antilink", "captcha", "gban", "recurring", "alias", "userinfo", "nightmode", "export"):
        assert registry.resolve(name) is not None
    assert len(registry.names(include_aliases=True)) == len(set(registry.names(include_aliases=True)))
    assert registry.resolve("antilink").scope is CommandScope.GROUP_ADMIN
