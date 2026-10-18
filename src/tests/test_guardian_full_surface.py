from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.moderation.engine import ModerationEngine
from src.moderation.guardian_enforcement import evaluate_guardian_message
from src.moderation.guardian_surface import GuardianSurface


class Port:
    def __init__(self):
        self.settings = {}
        self.users = {7: {"first_name": "Arel"}}

    async def get_chat_settings(self, chat_id):
        return dict(self.settings.get(chat_id, {}))

    async def update_chat_setting_key(self, chat_id, key, value):
        self.settings.setdefault(chat_id, {})[key] = value

    async def get_user(self, user_id):
        return dict(self.users.get(user_id, {}))

    async def update_user(self, user_id, values):
        self.users.setdefault(user_id, {}).update(values)

    async def get_first_message(self, chat_id, user_id):
        return {"content": "ilk mesaj", "timestamp": datetime.now(timezone.utc)}

    async def get_member_message_log(self, chat_id, user_id, limit=20):
        return [{"content": "son mesaj", "timestamp": datetime.now(timezone.utc)}]

    async def get_inactive_users(self, chat_id, since, limit=50):
        return [{"_id": 42}]


@pytest.mark.asyncio
async def test_guardian_full_information_surface_is_deterministic():
    port = Port()
    surface = GuardianSurface(port)

    await surface.execute("fsub", chat_id=-100, user_id=7, args=["@arelkanal"], is_admin=True)
    assert port.settings[-100]["fsub_channel"] == "@arelkanal"
    result = await surface.execute("reload", chat_id=-100, user_id=7, is_admin=True)
    assert result and result.document["settings_count"] >= 1
    assert (await surface.execute("userinfo", chat_id=-100, user_id=7, args=["7"])).document["user_id"] == 7
    assert "ilk mesaj" in (await surface.execute("firstmsg", chat_id=-100, user_id=7, args=["7"], is_admin=True)).text
    assert "son mesaj" in (await surface.execute("memberlog", chat_id=-100, user_id=7, args=["7"], is_admin=True)).text
    assert "42" in (await surface.execute("zombies", chat_id=-100, user_id=7, args=["30"], is_admin=True)).text


@pytest.mark.asyncio
async def test_blocklist_mode_and_admin_action_are_domain_bound():
    port = Port()
    surface = GuardianSurface(port)
    result = await surface.execute("blocklistmode", chat_id=-100, user_id=7, args=["mute"], is_admin=True)
    assert result and port.settings[-100]["blocklist_mode"] == "mute"
    assert await surface.execute("adminmute", chat_id=-100, user_id=7, is_admin=True) is None


@pytest.mark.asyncio
async def test_guardian_extended_moderation_and_federation_surface_persists_state():
    port = Port()
    surface = GuardianSurface(port)
    result = await surface.execute("hayaletban", chat_id=-100, user_id=7, args=["88"], is_admin=True)
    assert result and 88 in port.settings[-100]["guardian_shadow_bans"]
    result = await surface.execute("reaksiyon", chat_id=-100, user_id=7, args=["ekle", "💣"], is_admin=True)
    assert result and port.settings[-100]["guardian_blocked_reactions"] == ["💣"]
    result = await surface.execute("packengelle", chat_id=-100, user_id=7, args=["spam-pack"], is_admin=True)
    assert result and "spam-pack" in port.settings[-100]["guardian_blocked_sticker_packs"]
    result = await surface.execute("fnote", chat_id=-100, user_id=7, args=["88", "dikkat", "gerekiyor"], is_admin=True)
    assert result and port.settings[-100]["guardian_federation"]["notes"]["88"]
    port.settings[-100]["guardian_federation"].update(id="arel-100", created_by=7)
    result = await surface.execute("setfedlog", chat_id=-100, user_id=7, args=["-100123"], is_admin=True)
    assert result and result.changed and port.settings[-100]["guardian_federation"]["log_channel"] == -100123
    result = await surface.execute("purge", chat_id=-100, user_id=7, target_message_id=55, is_admin=True)
    assert result and result.metadata["amount"] == 200 and result.metadata["end_message_id"] == 55


@pytest.mark.asyncio
async def test_fedbanlist_alias_returns_the_persisted_federation_ban_list():
    port = Port()
    port.settings[-100] = {"guardian_fed_bans": [42, 77]}
    result = await GuardianSurface(port).execute(
        "fedbanlist", chat_id=-100, user_id=7, args=[], is_group=True, is_admin=True
    )

    assert result is not None
    assert result.document == {"bans": [42, 77]}


@pytest.mark.asyncio
async def test_guardian_setup_and_alias_commands_are_real_domain_operations():
    port = Port()
    surface = GuardianSurface(port)
    result = await surface.execute("hizlikur", chat_id=-100, user_id=7, is_admin=True)
    assert result and result.changed
    assert port.settings[-100]["guardian_setup_complete"] is True
    assert port.settings[-100]["captcha_enabled"] is True
    result = await surface.execute("kayitkanali", chat_id=-100, user_id=7, args=["-100555"], is_admin=True)
    assert result and result.changed and port.settings[-100]["guardian_log_channel"] == -100555
    result = await surface.execute("notekle", chat_id=-100, user_id=7, args=["kurallar", "spam", "yok"], is_admin=True)
    assert result and port.settings[-100]["guardian_notes"]["kurallar"] == "spam yok"
    result = await surface.execute("ghelp", chat_id=-100, user_id=7, is_group=True)
    assert result and result.metadata["help"] is True and "/antiraid" in result.text


@pytest.mark.asyncio
async def test_guardian_presence_and_hashtag_note_state_are_repository_backed():
    port = Port()
    port.settings[-100] = {"guardian_notes": {"kurallar": "spam yok"}}
    port.users[7]["guardian_afk"] = {"reason": "kahve", "updated_at": datetime.now(timezone.utc)}
    surface = GuardianSurface(port)
    assert (await surface.get_note(-100, "#KURALLAR")) == "spam yok"
    assert (await surface.get_afk(7))["reason"] == "kahve"
    assert await surface.clear_afk(7)
    assert port.users[7]["guardian_afk"] is None


def test_guardian_shadow_and_reaction_policy_blocks_only_matching_members():
    decision = evaluate_guardian_message(
        attachment_type=None,
        settings={"guardian_shadow_bans": [88]},
        user={},
        user_id=88,
    )
    assert not decision.allowed and decision.action == "delete"
    decision = evaluate_guardian_message(
        attachment_type=None,
        settings={"guardian_blocked_reactions": ["💣"]},
        user={},
        user_id=89,
        text="bomba 💣",
    )
    assert not decision.allowed and decision.reason == "guardian_blocked_reaction"


def test_guardian_policy_covers_federation_and_free_roles():
    assert evaluate_guardian_message(
        attachment_type=None,
        settings={"guardian_fed_bans": [99]},
        user={},
        user_id=99,
    ).reason == "guardian_federation_ban"
    assert evaluate_guardian_message(
        attachment_type="photo",
        settings={"locked_types": ["photo"], "guardian_roles": {"99": "free"}},
        user={},
        user_id=99,
    ).allowed


def test_guardian_content_protection_settings_are_enforced():
    assert evaluate_guardian_message(
        attachment_type="photo", settings={"image_protection": True}, user={}, user_id=1
    ).reason == "guardian_image_protection"
    assert evaluate_guardian_message(
        attachment_type="sticker",
        sticker_set_name="spam_pack",
        settings={"guardian_blocked_sticker_packs": ["spam_pack"]},
        user={}, user_id=1,
    ).reason == "guardian_sticker_pack"
    assert evaluate_guardian_message(
        attachment_type=None, is_edited=True,
        settings={"edit_protection": True}, user={}, user_id=1,
    ).reason == "guardian_edit_protection"
    assert evaluate_guardian_message(
        attachment_type=None, settings={"fake_account_protection": True},
        user={"is_fake": True}, user_id=1,
    ).action == "ban"


def test_moderation_policy_activates_from_guardian_settings_without_extra_flag():
    engine = ModerationEngine(window_seconds=30)
    settings = {"flood_limit": 1}
    assert engine.evaluate(-100, 7, "one", settings).allowed
    assert engine.evaluate(-100, 7, "two", settings).reason == "flood"

