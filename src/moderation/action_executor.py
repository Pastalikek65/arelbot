"""Authorized, idempotent Telegram moderation action boundary."""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

try:
    from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TimedOut
except Exception:  # pragma: no cover - lightweight test environments
    BadRequest = Forbidden = NetworkError = RetryAfter = TimedOut = ()

from .action_ledger import ModerationActionLedger
from .contracts import ActionResult, ModerationAction


PermissionChecker = Callable[[ModerationAction], bool | Awaitable[bool]]
ActionHook = Callable[[ModerationAction], Any | Awaitable[Any]]


class TelegramModerationActionExecutor:
    """Execute one typed action after permission and idempotency checks.

    The Telegram bot client is injected so this boundary is straightforward to
    fake in tests.  Handlers should create an action and call ``execute``;
    they should never call Telegram moderation methods directly.
    """

    def __init__(
        self,
        bot: Any,
        *,
        ledger: ModerationActionLedger | None = None,
        permission_checker: PermissionChecker | None = None,
        warning_handler: ActionHook | None = None,
        log_handler: ActionHook | None = None,
    ) -> None:
        self.bot = bot
        self.ledger = ledger or ModerationActionLedger()
        self.permission_checker = permission_checker
        self.warning_handler = warning_handler
        self.log_handler = log_handler

    async def execute(self, action: ModerationAction) -> ActionResult:
        key = action.idempotency_key
        if not await self._allowed(action):
            return ActionResult("rejected", key, error_code="permission_denied")
        try:
            claimed = await self.ledger.claim(action)
        except Exception:
            return ActionResult("failed", key, error_code="moderation_ledger_unavailable")
        if not claimed:
            return ActionResult("skipped", key, error_code="duplicate_action")
        try:
            begun = await self.ledger.begin_send(key)
        except Exception:
            return ActionResult("failed", key, error_code="moderation_ledger_unavailable")
        if not begun:
            return ActionResult("skipped", key, error_code="action_claim_lost")
        try:
            sent = await self._dispatch(action)
            telegram_message_id = getattr(sent, "message_id", None)
            finalized = await self.ledger.mark_sent(key, telegram_message_id)
            if not finalized:
                try:
                    await self.ledger.mark_uncertain(key, "moderation_action_finalize_failed")
                except Exception:
                    pass
                return ActionResult("uncertain", key, error_code="moderation_action_finalize_failed")
            return ActionResult("sent", key, telegram_message_id=telegram_message_id)
        except Exception as exc:
            error_code, retryable = self._classify_transport_error(exc)
            # Deterministic Telegram rejections can safely be retried after
            # the operator fixes the target/permissions.  Network and timeout
            # failures remain uncertain because Telegram may have accepted the
            # mutation before the connection failed.
            try:
                if retryable:
                    await self.ledger.mark_uncertain(key, error_code)
                else:
                    await self.ledger.mark_failed(key, error_code)
            except Exception:
                pass
            return ActionResult(
                "uncertain" if retryable else "failed",
                key,
                error_code=error_code,
            )

    @staticmethod
    def _classify_transport_error(error: BaseException) -> tuple[str, bool]:
        """Map Telegram exceptions to stable, user-safe codes.

        The exception text is deliberately never returned.  The stable code
        lets the command surface explain the actual failure without exposing
        Telegram internals, credentials, or request payloads.
        The second value indicates whether the request may already have been
        accepted and therefore must remain idempotently uncertain.
        """

        description = str(getattr(error, "message", "") or error).casefold()
        if isinstance(error, Forbidden):
            return "telegram_forbidden", False
        if isinstance(error, BadRequest):
            if "not enough rights" in description or "administrator rights" in description:
                return "telegram_not_enough_rights", False
            if "administrator" in description and "user" in description:
                return "target_is_admin", False
            if "not found" in description or "participant" in description:
                return "target_not_member", False
            return "telegram_bad_request", False
        if isinstance(error, (RetryAfter, TimedOut, NetworkError)):
            return "telegram_action_uncertain", True
        return "telegram_action_uncertain", True

    async def _allowed(self, action: ModerationAction) -> bool:
        if action.actor_id <= 0:
            return False
        if self.permission_checker is None:
            # A destructive Telegram action must never become authorized just
            # because a caller forgot to inject the policy boundary.  Runtime
            # composition always supplies TelegramModerationAuthorization;
            # tests and custom integrations must opt into an explicit checker
            # as well.  Failing closed here prevents accidental privilege
            # bypass when this executor is reused outside the main wiring.
            return False
        try:
            result = self.permission_checker(action)
            if inspect.isawaitable(result):
                result = await result
            return bool(result)
        except Exception:
            return False

    async def _dispatch(self, action: ModerationAction) -> Any:
        name = action.action
        if name == "delete_message":
            return await self.bot.delete_message(
                chat_id=action.chat_id,
                message_id=action.target_message_id,
            )
        if name == "ban":
            return await self.bot.ban_chat_member(
                chat_id=action.chat_id,
                user_id=action.target_user_id,
                revoke_messages=True,
            )
        if name == "temp_ban":
            return await self.bot.ban_chat_member(
                chat_id=action.chat_id,
                user_id=action.target_user_id,
                until_date=datetime.now(timezone.utc) + timedelta(seconds=int(action.duration_seconds or 600)),
                revoke_messages=True,
            )
        if name == "unban":
            return await self.bot.unban_chat_member(
                chat_id=action.chat_id,
                user_id=action.target_user_id,
                only_if_banned=True,
            )
        if name == "kick":
            method = getattr(self.bot, "kick_chat_member", None)
            if callable(method):
                return await method(chat_id=action.chat_id, user_id=action.target_user_id)
            return await self.bot.ban_chat_member(
                chat_id=action.chat_id,
                user_id=action.target_user_id,
                revoke_messages=False,
            )
        if name in {"mute", "temp_mute", "captcha_quarantine"}:
            kwargs: dict[str, Any] = {
                "chat_id": action.chat_id,
                "user_id": action.target_user_id,
                "permissions": self._muted_permissions(),
            }
            if name == "temp_mute":
                kwargs["until_date"] = datetime.now(timezone.utc) + timedelta(seconds=int(action.duration_seconds or 60))
            return await self.bot.restrict_chat_member(**kwargs)
        if name == "unmute":
            return await self.bot.restrict_chat_member(
                chat_id=action.chat_id,
                user_id=action.target_user_id,
                permissions=self._unmuted_permissions(),
            )
        if name == "pin":
            return await self.bot.pin_chat_message(
                chat_id=action.chat_id,
                message_id=action.target_message_id,
                disable_notification=True,
            )
        if name == "unpin":
            return await self.bot.unpin_chat_message(
                chat_id=action.chat_id,
                message_id=action.target_message_id,
            )
        if name in {"promote", "demote"}:
            # Telegram uses the same method for both operations.  Guardian's
            # default promotion is deliberately least-privilege; demotion
            # clears every grant explicitly.
            promote = name == "promote"
            kwargs = {
                "chat_id": action.chat_id,
                "user_id": action.target_user_id,
                "can_manage_chat": False,
                "can_change_info": promote,
                "can_post_messages": promote,
                "can_edit_messages": promote,
                "can_delete_messages": promote,
                "can_invite_users": promote,
                "can_restrict_members": promote,
                "can_pin_messages": promote,
                "can_promote_members": False,
                "can_manage_video_chats": promote,
                "can_manage_topics": promote,
            }
            return await self.bot.promote_chat_member(**kwargs)
        if name == "warn":
            return await self._run_hook(self.warning_handler, action)
        if name == "log":
            return await self._run_hook(self.log_handler, action)
        raise ValueError("action_not_supported")

    @staticmethod
    def _muted_permissions() -> Any:
        try:
            from telegram import ChatPermissions

            return ChatPermissions(can_send_messages=False)
        except Exception:
            return {"can_send_messages": False}

    @staticmethod
    def _unmuted_permissions() -> Any:
        try:
            from telegram import ChatPermissions

            return ChatPermissions(
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
            )
        except Exception:
            return {"can_send_messages": True}

    @staticmethod
    async def _run_hook(hook: ActionHook | None, action: ModerationAction) -> Any:
        if hook is None:
            return None
        result = hook(action)
        if inspect.isawaitable(result):
            return await result
        return result


# Keep a transport-neutral name available to application wiring while the
# concrete class makes the Telegram boundary explicit in type checkers.
ModerationActionExecutor = TelegramModerationActionExecutor

__all__ = ["ModerationActionExecutor", "TelegramModerationActionExecutor"]
