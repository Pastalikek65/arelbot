"""Telegram transport operations for the embedded Guardian policy.

The command/domain modules only persist intent.  This service is the narrow
place where Telegram membership and permission calls are made, which keeps
handlers from accumulating a second moderation implementation.
"""

from __future__ import annotations

from html import escape
import re
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .guardian_enforcement import validate_captcha_challenge
from src.security.output_sanitizer import sanitize_html


class GuardianTelegramService:
    """Apply Guardian's Telegram-only gates through an injected bot client."""

    _USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")
    _ACTIVE_MEMBER_STATUSES = frozenset({"member", "administrator", "creator", "owner", "restricted"})

    async def route_private_command(self, *, bot: Any, data_port: Any, surface: Any,
                                     command: str, user_id: int, args: list, message: Any) -> bool:
        """Allow only configuration/read commands through a revalidated connection."""
        from .authorization import TelegramModerationAuthorization
        allowed = {"rules", "setrules", "privaterules", "language", "cleanservice", "cleanwelcome",
                   "welcomemute", "welcome", "goodbye", "setwelcome", "setgoodbye",
                   "banlimit", "promotelimit", "grupinfolimit", "antilink", "captcha"}
        if command == "disconnect":
            await data_port.update_user(user_id, {"guardian_connected_chat": None})
            await message.reply_text("Özel sohbet bağlantısı kaldırıldı.")
            return True
        row = await data_port.get_user(user_id) or {}
        target = row.get("guardian_connected_chat")
        if command == "connect":
            if not args or not re.fullmatch(r"-\d+", str(args[0])):
                await message.reply_text("Kullanım: /connect negatif_grup_id")
                return True
            target = int(args[0])
        elif not target or command not in allowed:
            return False
        if not isinstance(target, int) or target >= 0 or not await TelegramModerationAuthorization(bot).is_admin(target, user_id):
            await message.reply_text("Bağlı grubun güncel yöneticisi olmalısın; grup ayarı değiştirilmedi.")
            return True
        if command != "connect" and await surface.is_command_disabled(target, command):
            await message.reply_text("Bu komut bağlı grupta kapalı; ayar değiştirilmedi.")
            return True
        if command == "rules":
            await self.deliver_rules(bot=bot, message=message, chat_id=user_id, user_id=user_id,
                                     settings=await surface.effective_settings(target))
            return True
        result = await surface.execute(command, chat_id=target, user_id=user_id,
                                       args=[] if command == "connect" else args, is_group=True, is_admin=True)
        if result is not None:
            if result.changed:
                audit = getattr(data_port, "add_audit_log", None)
                if callable(audit):
                    await audit(user_id, f"guardian_setting:{command}", chat_id=target)
                await self.emit_event(bot=bot, chat_id=target, actor_id=user_id, action=command,
                                      data_port=data_port)
            await message.reply_text(sanitize_html(result.text), parse_mode="HTML")
        return True

    async def cleanup_service_message(self, *, message: Any, settings: dict) -> bool:
        if not settings.get("clean_service_enabled") or message is None:
            return False
        fields = ("new_chat_members", "left_chat_member", "new_chat_title", "new_chat_photo",
                  "delete_chat_photo", "group_chat_created", "supergroup_chat_created",
                  "channel_chat_created", "migrate_to_chat_id", "migrate_from_chat_id", "pinned_message")
        if not any(getattr(message, name, None) for name in fields):
            return False
        try:
            await message.delete()
            return True
        except Exception:
            return False

    async def deliver_welcome(self, *, bot: Any, data_port: Any, chat_id: int,
                              user_id: int, text: str | None, settings: dict, joined: bool = True) -> None:
        """Apply bounded join mute and replace the preceding welcome when enabled."""
        if joined and settings.get("welcome_mute_enabled"):
            from telegram import ChatPermissions
            from datetime import datetime, timedelta, timezone
            member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            if str(getattr(member, "status", "")) not in {"administrator", "creator", "owner"}:
                await bot.restrict_chat_member(chat_id=chat_id, user_id=user_id,
                    permissions=ChatPermissions(can_send_messages=False),
                    until_date=datetime.now(timezone.utc) + timedelta(seconds=300))
        if not text:
            return
        if joined and settings.get("clean_welcome_enabled") and settings.get("guardian_last_welcome_id"):
            try:
                await bot.delete_message(chat_id=chat_id, message_id=int(settings["guardian_last_welcome_id"]))
            except Exception:
                pass  # Old greetings can already have been removed by admins.
        sent = await bot.send_message(chat_id=chat_id, text=sanitize_html(text), parse_mode="HTML")
        if joined and settings.get("clean_welcome_enabled"):
            await data_port.update_chat_setting_key(chat_id, "guardian_last_welcome_id", int(sent.message_id))

    async def deliver_rules(self, *, bot: Any, message: Any, chat_id: int, user_id: int, settings: dict) -> bool:
        text = str(settings.get("guardian_rules") or settings.get("rules") or "Bu grubun henüz yazılı bir kuralı yok.")[:4096]
        if settings.get("private_rules") and chat_id < 0:
            try:
                await bot.send_message(chat_id=user_id, text=text)
                await message.reply_text("Kurallar özel mesajına gönderildi.")
                return True
            except Exception:
                await message.reply_text("Kuralları almak için önce Arel'in özel sohbetinde /start yaz, ardından /rules komutunu tekrar kullan.")
                return False
        await message.reply_text(text)
        return True

    async def emit_event(self, *, bot: Any, chat_id: int, actor_id: int, action: str,
                         settings: dict | None = None, data_port: Any = None,
                         target_user_id: int | None = None, appeal: bool = False) -> None:
        """Emit identifiers and action names only; never copy member message content."""
        if settings is None:
            from .guardian_surface import GuardianSurface
            try:
                settings = await GuardianSurface(data_port).effective_settings(chat_id)
            except Exception:
                return
        key = "guardian_appeal_log_channel" if appeal else "guardian_log_channel"
        channels = {settings.get(key)}
        if not appeal and action in {"fban", "funban", "fedapprove", "fedunapprove", "setfedlog", "setappeallog"}:
            channels.add(settings.get("guardian_federation_log_channel"))
        text = (f"Guardian · {escape(str(action)[:48])}\nGrup: {int(chat_id)}\n"
                f"İşlemi yapan: {int(actor_id)}" + (f"\nHedef: {int(target_user_id)}" if target_user_id else ""))
        for channel in channels:
            if not isinstance(channel, int) or channel >= 0:
                continue
            try:
                await bot.send_message(chat_id=channel, text=text, parse_mode="HTML")
            except Exception:
                # Audit delivery cannot undo an already completed moderation action.
                pass

    async def resolve_member_username(
        self,
        *,
        bot: Any,
        chat_id: int,
        username: str,
        data_port: Any,
    ) -> int | None:
        """Resolve a seen Telegram username to a member id safely.

        Telegram's Bot API does not provide a username-to-id lookup for an
        arbitrary group member.  We therefore use only the username index
        populated from messages Arel has observed, then verify the resulting
        id with ``get_chat_member`` in the current chat before returning it.
        The method fails closed when the user has not been observed or is no
        longer a member; callers can still ask for a reply to the target
        message, which is always unambiguous.
        """
        normalized = str(username or "").strip().lstrip("@").strip()
        if not self._USERNAME_RE.fullmatch(normalized):
            return None
        finder = getattr(data_port, "find_user_by_username", None)
        if not callable(finder) or bot is None or not hasattr(bot, "get_chat_member"):
            return None
        try:
            row = await finder(normalized)
        except Exception:
            return None
        if not isinstance(row, dict):
            return None
        raw_id = row.get("user_id", row.get("_id"))
        try:
            user_id = int(raw_id)
        except (TypeError, ValueError):
            return None
        if user_id <= 0:
            return None
        try:
            member = await bot.get_chat_member(chat_id=int(chat_id), user_id=user_id)
        except Exception:
            return None
        status = str(getattr(member, "status", "") or "").casefold()
        if status not in self._ACTIVE_MEMBER_STATUSES:
            return None
        if status == "restricted" and getattr(member, "is_member", True) is False:
            return None
        return user_id

    async def enforce_fsub(
        self,
        *,
        bot: Any,
        chat_id: int,
        user_id: int,
        message: Any,
        settings: dict[str, Any] | None,
        is_admin: bool,
    ) -> bool:
        """Return ``True`` when the message was consumed by forced-sub.

        A lookup failure is fail-closed for the member gate (the user is
        asked to join and the message is removed), while admins are exempt.
        No channel membership data is persisted.
        """
        channel = str((settings or {}).get("fsub_channel") or "").strip()
        if not channel or is_admin or not bot or message is None:
            return False
        allowed = False
        try:
            member = await bot.get_chat_member(chat_id=channel, user_id=int(user_id))
            allowed = str(getattr(member, "status", "")).casefold() not in {"left", "kicked", "banned"}
        except Exception:
            allowed = False
        if allowed:
            return False
        target = channel.lstrip("@")
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("📢 kanala katıl", url=f"https://t.me/{target}")]])
        try:
            await message.reply_text(
                "burada yazmak için önce zorunlu kanala katılman gerekiyor",
                reply_markup=markup,
            )
        except Exception:
            pass
        try:
            await message.delete()
        except Exception:
            pass
        return True

    async def enforce_pending_captcha(
        self, *, bot: Any, chat_id: int, user_id: int, message: Any,
        settings: dict[str, Any] | None, is_admin: bool, data_port: Any | None = None,
    ) -> bool:
        """Suppress messages from a member until the join challenge passes.

        Button, text and math challenges share the same persisted pending map.
        A valid answer is consumed atomically through the injected data port;
        media messages never bypass the quarantine.
        """
        if is_admin or not isinstance(settings, dict) or not settings.get("captcha_enabled"):
            return False
        pending = settings.get("guardian_pending_captcha") or {}
        key = str(int(user_id))
        challenge = pending.get(key)
        if challenge is None:
            return False
        challenge = challenge if isinstance(challenge, dict) else {}
        timeout_seconds = max(30, min(3600, int(settings.get("captcha_timeout_seconds", 300) or 300)))
        challenge_type = str(challenge.get("type") or settings.get("captcha_type") or "button").casefold()
        supplied = str(getattr(message, "text", "") or "").strip()
        validation = validate_captcha_challenge(
            challenge,
            chat_id=int(chat_id),
            user_id=int(user_id),
            timeout_seconds=timeout_seconds,
            supplied_answer=(supplied if challenge_type in {"math", "text"} else None),
            require_created_at=bool(challenge.get("created_at")),
        )
        if validation.reason == "expired":
            pending.pop(key, None)
            if data_port is not None and hasattr(data_port, "update_chat_setting_key"):
                try:
                    await data_port.update_chat_setting_key(chat_id, "guardian_pending_captcha", pending)
                except Exception:
                    pass
            return True
        if validation.valid and challenge_type in {"math", "text"} and supplied:
            pending.pop(key, None)
            if data_port is not None and hasattr(data_port, "update_chat_setting_key"):
                try:
                    await data_port.update_chat_setting_key(chat_id, "guardian_pending_captcha", pending)
                except Exception:
                    return True
            await self.clear_captcha_quarantine(bot=bot, chat_id=int(chat_id), user_id=int(user_id))
            challenge_message_id = challenge.get("message_id")
            if bot and challenge_message_id:
                try:
                    await bot.delete_message(chat_id=int(chat_id), message_id=int(challenge_message_id))
                except Exception:
                    pass
            try:
                await message.reply_text("✅ doğrulama tamamlandı, artık yazabilirsin")
            except Exception:
                pass
            return True
        try:
            await message.delete()
        except Exception:
            pass
        return True

    async def clear_captcha_quarantine(self, *, bot: Any, chat_id: int, user_id: int) -> bool:
        """Restore member send permissions after a successful challenge."""

        if not bot or not hasattr(bot, "restrict_chat_member"):
            return False
        try:
            from telegram import ChatPermissions

            await bot.restrict_chat_member(
                chat_id=int(chat_id),
                user_id=int(user_id),
                permissions=ChatPermissions(
                    can_send_messages=True,
                    can_send_audios=True,
                    can_send_documents=True,
                    can_send_photos=True,
                    can_send_videos=True,
                    can_send_video_notes=True,
                    can_send_voice_notes=True,
                    can_send_polls=True,
                    can_add_web_page_previews=True,
                    can_invite_users=True,
                    can_manage_topics=True,
                ),
            )
            return True
        except Exception:
            return False

    async def set_night_permissions(self, *, bot: Any, chat_id: int, locked: bool) -> bool:
        """Lock or unlock the default member permissions for night mode."""
        if not bot:
            return False
        try:
            from telegram import ChatPermissions

            permissions = ChatPermissions(
                can_send_messages=not locked,
                can_send_audios=not locked,
                can_send_documents=not locked,
                can_send_photos=not locked,
                can_send_videos=not locked,
                can_send_video_notes=not locked,
                can_send_voice_notes=not locked,
                can_send_polls=not locked,
                can_add_web_page_previews=not locked,
                can_invite_users=not locked,
            )
            await bot.set_chat_permissions(chat_id=int(chat_id), permissions=permissions)
            return True
        except Exception:
            return False

    async def apply_admin_permissions(self, *, bot: Any, chat_id: int, user_id: int, profile: str) -> bool:
        """Apply a bounded Guardian administrator profile through Bot API."""
        if not bot or not hasattr(bot, "promote_chat_member"):
            return False
        profile = str(profile or "none").casefold()
        if profile == "moderator":
            values = {"can_manage_chat": False, "can_change_info": False, "can_post_messages": False,
                      "can_edit_messages": False, "can_delete_messages": True, "can_invite_users": True,
                      "can_restrict_members": True, "can_pin_messages": True, "can_promote_members": False,
                      "can_manage_video_chats": True, "can_manage_topics": True}
        elif profile == "full":
            values = {"can_manage_chat": False, "can_change_info": True, "can_post_messages": True,
                      "can_edit_messages": True, "can_delete_messages": True, "can_invite_users": True,
                      "can_restrict_members": True, "can_pin_messages": True, "can_promote_members": False,
                      "can_manage_video_chats": True, "can_manage_topics": True}
        elif profile == "remove_restrict":
            try:
                member = await bot.get_chat_member(chat_id=int(chat_id), user_id=int(user_id))
                values = {name: bool(getattr(member, name, False)) for name in (
                    "can_manage_chat", "can_change_info", "can_post_messages", "can_edit_messages",
                    "can_delete_messages", "can_invite_users", "can_restrict_members", "can_pin_messages",
                    "can_promote_members", "can_manage_video_chats", "can_manage_topics")}
                values["can_restrict_members"] = False
            except Exception:
                return False
        else:
            values = {name: False for name in (
                "can_manage_chat", "can_change_info", "can_post_messages", "can_edit_messages",
                "can_delete_messages", "can_invite_users", "can_restrict_members", "can_pin_messages",
                "can_promote_members", "can_manage_video_chats", "can_manage_topics")}
        try:
            await bot.promote_chat_member(chat_id=int(chat_id), user_id=int(user_id), **values)
            return True
        except Exception:
            return False

    async def list_administrators(self, *, bot: Any, chat_id: int) -> list[dict[str, Any]]:
        """Return a bounded, presentation-safe administrator list."""
        if not bot or not hasattr(bot, "get_chat_administrators"):
            return []
        try:
            members = await bot.get_chat_administrators(chat_id=int(chat_id))
        except Exception:
            return []
        rows: list[dict[str, Any]] = []
        for member in list(members or [])[:100]:
            user = getattr(member, "user", None)
            if user is None:
                continue
            rows.append(
                {
                    "user_id": int(getattr(user, "id", 0) or 0),
                    "name": str(getattr(user, "full_name", None) or getattr(user, "first_name", "kullanıcı"))[:96],
                    "username": str(getattr(user, "username", "") or "")[:64],
                    "status": str(getattr(member, "status", "administrator"))[:32],
                }
            )
        return rows

    async def unpin_all(self, *, bot: Any, chat_id: int) -> bool:
        if not bot or not hasattr(bot, "unpin_all_chat_messages"):
            return False
        try:
            await bot.unpin_all_chat_messages(chat_id=int(chat_id))
            return True
        except Exception:
            return False

    async def purge_range(self, *, bot: Any, chat_id: int, end_message_id: int, amount: int) -> int:
        """Best-effort bounded purge for contiguous Telegram message ids."""
        if not bot or not hasattr(bot, "delete_message"):
            return 0
        deleted = 0
        for message_id in range(int(end_message_id), max(0, int(end_message_id) - min(int(amount), 200)), -1):
            try:
                await bot.delete_message(chat_id=int(chat_id), message_id=message_id)
                deleted += 1
            except Exception:
                continue
        return deleted


__all__ = ["GuardianTelegramService"]
