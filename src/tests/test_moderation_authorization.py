from types import SimpleNamespace

import pytest

from src.moderation.authorization import TelegramModerationAuthorization
from src.moderation.contracts import ModerationAction


class MemberBot:
    id = 900

    def __init__(self, statuses):
        self.statuses = statuses

    async def get_chat_member(self, *, chat_id, user_id):
        return SimpleNamespace(status=self.statuses.get(user_id, "member"))


@pytest.mark.asyncio
async def test_automatic_actions_cannot_target_group_admins():
    checker = TelegramModerationAuthorization(MemberBot({42: "administrator"}), bot_id=900)
    action = ModerationAction(action="ban", chat_id=-100, actor_id=900, target_user_id=42)
    assert await checker(action) is False


@pytest.mark.asyncio
async def test_manual_action_requires_admin_actor_and_protects_admin_target():
    checker = TelegramModerationAuthorization(MemberBot({7: "administrator", 42: "member"}))
    action = ModerationAction(action="mute", chat_id=-100, actor_id=7, target_user_id=42)
    assert await checker(action) is True

    ordinary_actor = ModerationAction(action="mute", chat_id=-100, actor_id=8, target_user_id=42)
    assert await checker(ordinary_actor) is False


@pytest.mark.asyncio
async def test_lookup_failure_denies_user_action_closed():
    class BrokenBot(MemberBot):
        async def get_chat_member(self, *, chat_id, user_id):
            raise RuntimeError("lookup failed")

    checker = TelegramModerationAuthorization(BrokenBot({}), bot_id=900)
    action = ModerationAction(action="ban", chat_id=-100, actor_id=900, target_user_id=42)
    assert await checker(action) is False


@pytest.mark.asyncio
async def test_group_access_diagnostic_distinguishes_missing_bot_admin_role():
    checker = TelegramModerationAuthorization(
        MemberBot({7: "administrator", 900: "member"}),
        bot_id=900,
    )

    state = await checker.diagnose_group_access(-100, 7)

    assert state == {
        "actor_status": "administrator",
        "bot_status": "member",
        "actor_is_admin": True,
        "bot_is_admin": False,
    }
