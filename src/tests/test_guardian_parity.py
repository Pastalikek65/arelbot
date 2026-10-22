from __future__ import annotations

import pytest

from src.moderation.guardian_surface import GuardianSurface


class Port:
    def __init__(self):
        self.settings: dict[int, dict] = {}
        self.users = {}

    async def update_user(self, user_id, data):
        self.users.setdefault(user_id, {}).update(data)

    async def get_chat_settings(self, chat_id):
        return dict(self.settings.get(chat_id, {}))

    async def update_chat_setting_key(self, chat_id, key, value):
        self.settings.setdefault(chat_id, {})[key] = value


@pytest.mark.asyncio
async def test_staff_without_target_routes_to_staff_transport_action():
    surface = GuardianSurface(Port())

    result = await surface.execute(
        "staff",
        chat_id=-100,
        user_id=7,
        args=[],
        is_group=True,
        is_admin=True,
    )

    assert result is not None
    assert result.metadata["transport_action"] == "staff_list"


@pytest.mark.asyncio
async def test_group_alias_is_visible_to_runtime_catch_all_dispatcher():
    port = Port()
    surface = GuardianSurface(port)
    await surface.execute("alias", chat_id=-100, user_id=7, args=["kurallar", "rules"], is_admin=True)

    assert await surface.has_custom_alias(-100, "kurallar") is True
    assert await surface.has_custom_alias(-100, "bilinmeyen") is False


def test_federation_ban_list_alias_is_part_of_the_guardian_surface():
    assert "fedbanlist" in GuardianSurface.COMMANDS


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command,args",
    [
        ("banlimit", ["5"]),
        ("promotelimit", ["5"]),
        ("grupinfolimit", ["5"]),
        ("privaterules", ["on"]),
        ("language", ["tr"]),
        ("connect", ["-100123"]),
        ("disconnect", []),
        ("logchannel", ["-100123"]),
        ("setappeallog", ["-100123"]),
        ("setfedlog", ["-100123"]),
        ("cleanservice", ["on"]),
        ("cleanwelcome", ["on"]),
        ("welcomemute", ["on"]),
    ],
)
async def test_guardian_controls_accept_authorized_configuration(command, args):
    port = Port()
    port.settings[-100] = {"guardian_federation": {"id": "arel-100", "created_by": 7}}
    surface = GuardianSurface(port)

    result = await surface.execute(
        command,
        chat_id=-100,
        user_id=7,
        args=args,
        is_group=True,
        is_admin=True,
    )

    assert result is not None
    assert result.changed is True
    assert result.metadata.get("error_code") != "guardian_feature_unavailable"

