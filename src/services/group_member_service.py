"""Pure group member transition policy and safe Telegram HTML rendering."""

from __future__ import annotations

from html import escape
from typing import Any


class GroupMemberService:
    ACTIVE_STATUSES = {"member", "administrator", "creator", "restricted"}
    LEFT_STATUSES = {"left", "kicked"}

    @classmethod
    def render(
        cls,
        *,
        old_status: str,
        new_status: str,
        user_name: str,
        username: str | None,
        chat_title: str,
        settings: dict[str, Any] | None = None,
    ) -> str | None:
        settings = settings or {}
        old_status = str(old_status or "").casefold()
        new_status = str(new_status or "").casefold()
        safe_name = escape(str(user_name or "kullanıcı"))
        safe_chat = escape(str(chat_title or "grup"))
        mention = f"@{escape(username)}" if username else safe_name

        if old_status not in cls.ACTIVE_STATUSES and new_status in cls.ACTIVE_STATUSES:
            if not settings.get("welcome_enabled", False):
                return None
            template = settings.get(
                "welcome_text",
                "👋 <b>{name}</b>, <b>{chat}</b> grubuna hoş geldin! {mention}",
            )
            return cls._format(template, name=safe_name, chat=safe_chat, mention=mention)

        if old_status in cls.ACTIVE_STATUSES and new_status in cls.LEFT_STATUSES:
            if not settings.get("goodbye_enabled", False):
                return None
            template = settings.get(
                "goodbye_text",
                "👋 <b>{name}</b> gruptan ayrıldı. Güle güle!",
            )
            return cls._format(template, name=safe_name, chat=safe_chat, mention=mention)
        return None

    @staticmethod
    def _format(template: Any, **values: str) -> str:
        """Render only known placeholders and escape arbitrary template output."""
        template = str(template or "")[:1000]
        # Templates are admin-authored HTML.  Values are escaped before
        # interpolation; unknown placeholders remain literal for diagnostics.
        return template.replace("{name}", values["name"]).replace(
            "{chat}", values["chat"]
        ).replace("{mention}", values["mention"])
