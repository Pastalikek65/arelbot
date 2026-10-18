"""Telegram-side authorization for typed moderation actions.

The handler layer supplies the Telegram transport to this policy object; the
handler itself never decides whether a target may be moderated.  Automatic
actions are attributed to the bot and may never target a group administrator
or creator.  Manual actions require an administrator/creator actor and also
protect administrators from peer moderation.
"""

from __future__ import annotations

from typing import Any

from .contracts import ModerationAction


_ADMIN_STATUSES = frozenset({"administrator", "creator", "owner"})


class TelegramModerationAuthorization:
    """Async permission checker suitable for ``TelegramModerationActionExecutor``.

    ``get_chat_member`` is intentionally called here, behind the injected
    transport boundary.  Any lookup failure denies the action; a moderation
    failure must never become an authorization bypass.
    """

    def __init__(self, bot: Any, *, bot_id: int | None = None) -> None:
        self.bot = bot
        self.bot_id = int(bot_id or getattr(bot, "id", 0) or 0)

    async def __call__(self, action: ModerationAction) -> bool:
        if action.actor_id <= 0 or action.chat_id >= 0:
            return False
        if action.target_user_id is not None and action.target_user_id == action.actor_id:
            return action.action in {"unban", "unmute"}

        # A provider/automatic decision is represented by the bot actor.  It
        # may enforce policy on ordinary users, but never on admins.
        automatic = self.bot_id > 0 and action.actor_id == self.bot_id
        if automatic:
            target_status = await self._target_status(action)
            return target_status is not None and target_status not in _ADMIN_STATUSES

        actor_status = await self._member_status(action.actor_id, action.chat_id)
        if actor_status not in _ADMIN_STATUSES:
            return False
        if action.action in {"unban", "unmute"}:
            return True
        target_status = await self._target_status(action)
        # ``adminmute``/``adminunmute`` are intentionally explicit Guardian
        # commands.  The command surface gates them to owner/sudo actors;
        # this flag only prevents the ordinary peer-admin guard from undoing
        # that deliberate escalation.
        if bool(getattr(action, "metadata", {}).get("guardian_admin_override")):
            return target_status is not None
        if action.action == "demote":
            return target_status in _ADMIN_STATUSES
        return target_status is not None and target_status not in _ADMIN_STATUSES

    async def is_admin(self, chat_id: int, user_id: int) -> bool:
        """Resolve a member's privileged status for the domain service."""
        if int(chat_id) >= 0 or int(user_id) <= 0:
            return False
        return (await self._member_status(int(user_id), int(chat_id))) in _ADMIN_STATUSES

    async def diagnose_group_access(self, chat_id: int, user_id: int) -> dict[str, Any]:
        """Return safe, transport-level reasons for a failed admin check.

        Telegram only guarantees reliable member lookups to an administrator
        bot in many group configurations.  Exposing the two statuses lets the
        handler tell an actual group admin that Arel itself lacks the role,
        instead of incorrectly blaming the user.
        """
        actor_status = await self._member_status(int(user_id), int(chat_id))
        bot_status = await self._member_status(int(self.bot_id), int(chat_id)) if self.bot_id > 0 else None
        return {
            "actor_status": actor_status,
            "bot_status": bot_status,
            "actor_is_admin": actor_status in _ADMIN_STATUSES,
            "bot_is_admin": bot_status in _ADMIN_STATUSES,
        }

    async def _member_status(self, user_id: int, chat_id: int) -> str | None:
        try:
            member = await self.bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        except Exception:
            return None
        status = str(getattr(member, "status", "") or "").casefold()
        return status or None

    async def _target_status(self, action: ModerationAction) -> str | None:
        target = action.target_user_id
        if target is None:
            return "member"
        return await self._member_status(target, action.chat_id)


__all__ = ["TelegramModerationAuthorization"]
