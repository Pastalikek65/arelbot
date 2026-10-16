"""Telegram moderation transport.

The command layer decides *who* may moderate and resolves the target.  This
small adapter owns the Telegram API calls so handlers do not contain a remote
transport implementation and the action is easy to fake in tests.
"""

from __future__ import annotations

from typing import Any


class TelegramModerationService:
    """Execute member moderation actions through an injected bot client."""

    async def ban_member(self, bot: Any, chat_id: int, user_id: int) -> None:
        if bot is None or not hasattr(bot, "ban_chat_member"):
            raise RuntimeError("telegram_moderation_unavailable")
        await bot.ban_chat_member(chat_id=int(chat_id), user_id=int(user_id))

    async def unban_member(self, bot: Any, chat_id: int, user_id: int) -> None:
        if bot is None or not hasattr(bot, "unban_chat_member"):
            raise RuntimeError("telegram_moderation_unavailable")
        await bot.unban_chat_member(chat_id=int(chat_id), user_id=int(user_id))

