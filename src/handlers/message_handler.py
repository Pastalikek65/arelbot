"""
Message Handler - Ana mesaj işleme mantığı

Grup ve DM mesajlarını işler, AI yanıtlarını üretir.
"""
import logging
import asyncio
import time
import traceback
from html import escape
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputSticker
from telegram.constants import StickerFormat
from telegram.ext import ContextTypes

from config import Config
from src.utils import tr_upper
from .handler_utils import remove_job_by_name, log_event, log_error_event, should_respond, is_group_admin
from src.media.url_normalizer import extract_media_url
from src.security.output_sanitizer import (
    render_model_html,
    render_model_plain_text,
    sanitize_html,
    strip_internal_attribution,
)
from src.security.error_codes import public_error_message
from src.security.log_redaction import summarize_exception, summarize_private_attachment, summarize_private_text
from src.stickers.validator import StickerValidationError, make_pack_name, validate_short_name, validate_emoji
from src.games.state_store import hydrate_games, persist_game, delete_game
from src.games.vocabulary import can_form_word, normalize_game_word
from src.games.engine import calculate_hangman_score, calculate_word_game_score, calculate_word_guess_score
from src.games.hangman import apply_guess
from src.services.word_game_ui import word_game_keyboard, render_word_game_text
from src.services.word_guess_ui import word_guess_keyboard
from src.services.group_member_service import GroupMemberService
from src.storage.handler_data_port import HandlerDataPort
from src.storage.state_scope import get_scoped_state, pop_scoped_state
from src.features.chat_feature_policy import is_chat_feature_enabled
from src.ai.personality import PersonalityPolicy
from src.ai.conversation_style import NORMAL_MODE, is_normal_mode
from src.group_replies import GroupMessageRef, GroupReplyPlan, GroupReplyRouter, GroupTurnContext, ReplySegment
from src.storage.group_reply_store import RecentMessageIndex, ReplyDeliveryLedger
from src.services.group_reply_delivery import GroupReplyDeliveryService
from src.services.guess_age_service import GuessAgeServiceError
from src.moderation.contracts import ModerationAction
from src.moderation.action_executor import TelegramModerationActionExecutor
from src.moderation.action_ledger import ModerationActionLedger
from src.moderation.authorization import TelegramModerationAuthorization
from src.moderation.event_normalizer import build_moderation_event
from src.moderation.guardian_enforcement import (
    build_guardian_generation_id,
    evaluate_guardian_message,
    new_guardian_challenge_nonce,
)
from src.moderation.telegram_guardian_service import GuardianTelegramService
from src.moderation.guardian_surface import GuardianSurface

logger = logging.getLogger(__name__)


class MessageHandler:
    def __init__(
        self,
        chat_engine,
        sudo_manager,
        image_router,
        tool_manager,
        leaderboard_service=None,
        economy_service=None,
        sticker_tag_service=None,
        image_generation_service=None,
        data_port=None,
        recent_message_index=None,
        reply_delivery_ledger=None,
        group_reply_router=None,
        group_reply_delivery=None,
        group_reply_metrics=None,
        authorization=None,
        guess_age_service=None,
    ):
        self.chat_engine = chat_engine
        self.data_port = data_port or HandlerDataPort.from_engine(chat_engine)
        self.sudo_manager = sudo_manager
        self.image_router = image_router
        self.image_generation_service = image_generation_service
        self.tool_manager = tool_manager
        self.leaderboard_service = leaderboard_service
        self.economy_service = economy_service
        self.sticker_tag_service = sticker_tag_service
        self.recent_message_index = recent_message_index or RecentMessageIndex()
        self.reply_delivery_ledger = reply_delivery_ledger or ReplyDeliveryLedger()
        self.group_reply_router = group_reply_router or GroupReplyRouter()
        self.group_reply_delivery = group_reply_delivery or GroupReplyDeliveryService(
            ledger=self.reply_delivery_ledger,
        )
        self.group_reply_metrics = group_reply_metrics
        self.authorization = authorization
        self.guess_age_service = guess_age_service
        self.guardian_telegram_service = GuardianTelegramService()
        self.guardian_surface = GuardianSurface(self.data_port)
        # The executor is created lazily with the current Telegram bot.  The
        # ledger itself is injected by the orchestrator and survives retries.
        self.moderation_action_ledger = None
        # A group/topic lock serializes candidate snapshot, model planning and
        # delivery. Incoming messages can still be recorded concurrently, but
        # two triggers cannot plan against a half-updated pending window.
        self._group_reply_locks: dict[tuple[int, int | None], asyncio.Lock] = {}
        self.bot_info = None

    def _group_reply_lock(self, chat_id: int, thread_id: int | None) -> asyncio.Lock:
        key = (int(chat_id), int(thread_id) if thread_id is not None else None)
        lock = self._group_reply_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._group_reply_locks[key] = lock
        return lock

    def _observe_group_reply(self, name: str, seconds: float) -> None:
        observe = getattr(self.group_reply_metrics, "observe", None)
        if observe is not None:
            observe(name, seconds)

    async def _enforce_moderation_decision(self, update: Update, context: ContextTypes.DEFAULT_TYPE, decision, event) -> bool:
        """Apply one decision through the idempotent action boundary.

        ``True`` means the update is consumed and must not continue into AI,
        media or game routing.  Transport errors stay internal and never
        become a group-facing error message.
        """
        if getattr(decision, "allowed", True):
            return False
        action_name = {"delete": "delete_message", "mute": "mute", "ban": "ban"}.get(
            str(getattr(decision, "action", "") or "").casefold()
        )
        if not action_name:
            return True
        message_id = int(getattr(event, "message_id", 0) or 0)
        actor_id = int(getattr(getattr(context, "bot", None), "id", 0) or 1)
        action = ModerationAction(
            action=action_name,
            chat_id=int(event.chat_id),
            actor_id=actor_id,
            target_user_id=int(event.user_id),
            target_message_id=message_id,
            thread_id=event.thread_id,
            reason=str(getattr(decision, "reason", None) or "moderation")[:128],
            metadata={
                "source": "local_moderation",
                "policy_version": int(getattr(event, "settings_version", 1)),
                "generation_id": f"telegram:{event.chat_id}:{event.message_id}",
            },
        )
        ledger = getattr(self, "moderation_action_ledger", None)
        if ledger is None:
            ledger = ModerationActionLedger()
        bot = getattr(context, "bot", None)
        executor = TelegramModerationActionExecutor(
            bot,
            ledger=ledger,
            permission_checker=TelegramModerationAuthorization(
                bot,
                bot_id=getattr(bot, "id", None),
            ),
        )
        result = await executor.execute(action)
        if getattr(result, "ok", False):
            await self.guardian_telegram_service.emit_event(
                bot=bot, chat_id=int(event.chat_id), actor_id=actor_id, action=action_name,
                target_user_id=int(event.user_id), data_port=self.data_port,
            )
        if getattr(result, "status", None) == "rejected" and getattr(result, "error_code", None) == "permission_denied":
            # A stale or unavailable membership lookup must never consume a
            # privileged member's message as if moderation succeeded.
            return False
        return True

    @staticmethod
    def _current_message_reply_kwargs(update: Update) -> dict:
        """Attach group output to the one message that triggered it."""

        chat = getattr(update, "effective_chat", None)
        message = getattr(update, "effective_message", None)
        if getattr(chat, "type", None) not in {"group", "supergroup"}:
            return {}
        message_id = getattr(message, "message_id", None)
        return {"reply_to_message_id": message_id} if message_id is not None else {}

    @classmethod
    def _game_reply_kwargs(cls, update: Update, state: dict) -> dict:
        """Bind a game response to its panel instead of the latest chat line."""
        chat = getattr(update, "effective_chat", None)
        prompt_id = state.get("prompt_message_id") if isinstance(state, dict) else None
        if getattr(chat, "type", None) in {"group", "supergroup"} and prompt_id:
            return {"reply_to_message_id": int(prompt_id)}
        return cls._current_message_reply_kwargs(update)

    async def _record_game_result(self, update: Update, game_type: str, score: int, result_key: str) -> None:
        """Record a completed text-game round through the idempotent ledger."""
        if not self.leaderboard_service or not update.effective_user or not update.effective_chat:
            return
        message = update.effective_message
        message_id = getattr(message, "message_id", 0) or 0
        user = update.effective_user
        event_id = f"{game_type}:{update.effective_chat.id}:{message_id}:{user.id}:{result_key}"
        try:
            accepted = await self.leaderboard_service.record_score(
                user_id=int(user.id),
                chat_id=int(update.effective_chat.id),
                game_type=game_type,
                score=int(score),
                user_name=getattr(user, "full_name", None) or getattr(user, "first_name", None),
                event_id=event_id,
            )
            if accepted and self.economy_service:
                await self.economy_service.credit(
                    int(user.id),
                    int(update.effective_chat.id),
                    amount=max(1, int(score) // 2),
                    reason=f"{game_type}_reward",
                    event_id=f"coin:{event_id}",
                )
        except Exception:
            # The result remains visible if the optional reward ledger is down;
            # idempotent event ids make a later retry safe.
            logger.warning("Could not record %s game result", game_type, exc_info=True)

    async def handle_service_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Consume only real Telegram service messages when cleanup is enabled."""
        chat = getattr(update, "effective_chat", None)
        if getattr(chat, "type", None) not in {"group", "supergroup"}:
            return
        settings = await self.data_port.get_chat_settings(int(chat.id)) or {}
        await self.guardian_telegram_service.cleanup_service_message(
            message=getattr(update, "effective_message", None), settings=settings,
        )

    async def handle_chat_member_update(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Send configured welcome/goodbye messages for real group transitions."""
        event = getattr(update, "chat_member", None)
        if event is None or not event.chat or event.chat.type not in {"group", "supergroup"}:
            return
        member = event.new_chat_member
        old_member = event.old_chat_member
        user = member.user
        if getattr(user, "is_bot", False):
            return
        try:
            settings = await self.data_port.get_chat_settings(event.chat.id)
        except Exception:
            logger.warning("Could not load group member settings", exc_info=True)
            return
        joined = getattr(old_member, "status", "") in {"left", "kicked"} and getattr(member, "status", "") in {"member", "restricted"}
        if joined and bool(settings.get("antiraid_enabled")):
            mode = str(settings.get("antiraid_mode") or "quarantine").casefold()
            action_name = "ban" if mode in {"ban", "kick"} else "captcha_quarantine"
            bot = getattr(context, "bot", None)
            try:
                action = ModerationAction(
                    action=action_name,
                    chat_id=int(event.chat.id),
                    actor_id=int(getattr(bot, "id", 0) or 1),
                    target_user_id=int(user.id),
                    reason="guardian_antiraid",
                    metadata={"source": "guardian_antiraid", "generation_id": f"antiraid:{event.chat.id}:{user.id}"},
                )
                await TelegramModerationActionExecutor(
                    bot,
                    ledger=self.moderation_action_ledger or ModerationActionLedger(),
                    permission_checker=TelegramModerationAuthorization(bot, bot_id=getattr(bot, "id", None)),
                ).execute(action)
            except Exception:
                logger.warning("Guardian anti-raid action failed", exc_info=True)
        # Guardian's captcha is a scoped join challenge.  The pending map is
        # bounded and persisted in the current chat document so a worker
        # restart cannot turn every new member into an untracked challenge.
        if (
            getattr(old_member, "status", "") in {"left", "kicked"}
            and bool(settings.get("captcha_enabled"))
            and not getattr(user, "is_bot", False)
        ):
            captcha_type = str(settings.get("captcha_type") or "button").casefold()
            challenge = {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "message_id": None,
                "chat_id": int(event.chat.id),
                "user_id": int(user.id),
                "nonce": new_guardian_challenge_nonce(),
                "type": captcha_type if captcha_type in {"button", "math", "text"} else "button",
            }
            challenge["generation_id"] = build_guardian_generation_id(
                "captcha", chat_id=int(event.chat.id), user_id=int(user.id), nonce=challenge["nonce"]
            )
            if challenge["type"] == "math":
                left = secrets.randbelow(8) + 2
                right = secrets.randbelow(8) + 2
                challenge["answer"] = str(left + right)
                challenge["prompt"] = f"{left} + {right} = ?"
            elif challenge["type"] == "text":
                token = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(5))
                challenge["answer"] = token
                challenge["prompt"] = token
            pending = dict(settings.get("guardian_pending_captcha") or {})
            pending[str(user.id)] = challenge
            pending = dict(list(pending.items())[-200:])
            await self.data_port.update_chat_setting_key(event.chat.id, "guardian_pending_captcha", pending)
            try:
                prompt = "gruba yazmadan önce doğrulama düğmesine bas"
                if challenge["type"] == "math":
                    prompt = f"gruba yazmadan önce işlemi çöz: <code>{escape(challenge['prompt'])}</code>"
                elif challenge["type"] == "text":
                    prompt = f"gruba yazmadan önce şu kodu gönder: <code>{escape(challenge['prompt'])}</code>"
                markup = None
                if challenge["type"] == "button":
                    markup = InlineKeyboardMarkup([[
                        InlineKeyboardButton(
                            "✅ Doğrula",
                            callback_data=f"guardian_captcha:{event.chat.id}:{user.id}:{challenge['nonce']}",
                        )
                    ]])
                challenge = await context.bot.send_message(
                    chat_id=event.chat.id,
                    text=f"🛡️ <b>{escape(getattr(user, 'full_name', None) or getattr(user, 'first_name', 'yeni üye'))}</b>, {prompt}.",
                    parse_mode="HTML",
                    reply_markup=markup,
                )
                pending[str(user.id)]["message_id"] = getattr(challenge, "message_id", None)
                await self.data_port.update_chat_setting_key(event.chat.id, "guardian_pending_captcha", pending)
                job_queue = getattr(context, "job_queue", None)
                if job_queue is not None:
                    job_queue.run_once(
                        self._captcha_timeout_callback,
                        when=max(30, min(3600, int(settings.get("captcha_timeout_seconds", 300) or 300))),
                        data={"chat_id": int(event.chat.id), "user_id": int(user.id)},
                        name=f"guardian_captcha:{event.chat.id}:{user.id}",
                    )
            except Exception:
                logger.warning("Guardian captcha challenge delivery failed", exc_info=True)
        text = GroupMemberService.render(
            old_status=getattr(old_member, "status", ""),
            new_status=getattr(member, "status", ""),
            user_name=getattr(user, "full_name", None) or getattr(user, "first_name", "kullanıcı"),
            username=getattr(user, "username", None),
            chat_title=getattr(event.chat, "title", None) or "grup",
            settings=settings,
        )
        await self.guardian_telegram_service.deliver_welcome(
            bot=context.bot, data_port=self.data_port, chat_id=int(event.chat.id), user_id=int(getattr(user, "id", 0) or 0),
            text=text, settings=settings, joined=joined,
        )

    async def _captcha_timeout_callback(self, context: ContextTypes.DEFAULT_TYPE):
        """Remove an unanswered join challenge without group-facing errors."""
        data = getattr(getattr(context, "job", None), "data", None) or {}
        try:
            chat_id, user_id = int(data["chat_id"]), int(data["user_id"])
        except (KeyError, TypeError, ValueError):
            return
        settings = await self.data_port.get_chat_settings(chat_id) or {}
        pending = dict(settings.get("guardian_pending_captcha") or {})
        challenge = pending.get(str(user_id))
        if not challenge:
            return
        bot = getattr(context, "bot", None)
        try:
            nonce = str(challenge.get("nonce") or "") if isinstance(challenge, dict) else ""
            generation_id = (
                build_guardian_generation_id("captcha-timeout", chat_id=chat_id, user_id=user_id, nonce=nonce)
                if nonce
                else f"captcha-timeout:{chat_id}:{user_id}"
            )
            action = ModerationAction(
                action="ban",
                chat_id=chat_id,
                actor_id=int(getattr(bot, "id", 0) or 1),
                target_user_id=user_id,
                reason="guardian_captcha_timeout",
                metadata={"source": "guardian_captcha", "generation_id": generation_id},
            )
            await TelegramModerationActionExecutor(
                bot,
                ledger=self.moderation_action_ledger or ModerationActionLedger(),
                permission_checker=TelegramModerationAuthorization(bot, bot_id=getattr(bot, "id", None)),
            ).execute(action)
            message_id = challenge.get("message_id") if isinstance(challenge, dict) else None
            if message_id:
                try:
                    await bot.delete_message(chat_id=chat_id, message_id=int(message_id))
                except Exception:
                    pass
        finally:
            pending.pop(str(user_id), None)
            await self.data_port.update_chat_setting_key(chat_id, "guardian_pending_captcha", pending)

    async def _handle_guardian_presence(self, update: Update) -> bool:
        """Run Guardian's AFK and ``#note`` listeners for group text messages.

        The upstream Guardian exposes these as message listeners rather than
        slash commands.  Keep them scoped to groups and the injected Arel
        repository so they never enter the AI path or read another chat's
        state.  ``True`` means a note was rendered and normal routing should
        stop; AFK notices alone do not suppress an explicit Arel mention.
        """
        message = getattr(update, "effective_message", None)
        chat = getattr(update, "effective_chat", None)
        user = getattr(update, "effective_user", None)
        if not message or not chat or chat.type not in {"group", "supergroup"} or not user:
            return False

        now = datetime.now(timezone.utc)
        own_afk = await self.guardian_surface.get_afk(int(user.id))
        if own_afk:
            raw_created = own_afk.get("updated_at") or own_afk.get("created_at")
            created_at = None
            if isinstance(raw_created, datetime):
                created_at = raw_created if raw_created.tzinfo else raw_created.replace(tzinfo=timezone.utc)
            elif raw_created:
                try:
                    created_at = datetime.fromisoformat(str(raw_created).replace("Z", "+00:00"))
                    if created_at.tzinfo is None:
                        created_at = created_at.replace(tzinfo=timezone.utc)
                except (TypeError, ValueError):
                    created_at = None
            if created_at is None or (now - created_at).total_seconds() > 5:
                if await self.guardian_surface.clear_afk(int(user.id)):
                    await message.reply_text("hoş geldin, AFK kaydını kapattım")

        notified: set[int] = set()

        async def notify_afk(target_user: object) -> None:
            target_id = int(getattr(target_user, "id", 0) or 0)
            if target_id <= 0 or target_id in notified or target_id == int(user.id):
                return
            afk = await self.guardian_surface.get_afk(target_id)
            if not afk:
                return
            notified.add(target_id)
            name = escape(str(getattr(target_user, "full_name", None) or getattr(target_user, "first_name", "kullanıcı"))[:96])
            reason = escape(str(afk.get("reason") or "bir süre uzakta")[:256])
            await message.reply_text(f"{name} şu an AFK\nsebep: {reason}")

        replied = getattr(message, "reply_to_message", None)
        replied_user = getattr(replied, "from_user", None)
        if replied_user is not None:
            await notify_afk(replied_user)
        for entity in list(getattr(message, "entities", None) or ()):
            if str(getattr(entity, "type", "")) == "text_mention":
                mentioned = getattr(entity, "user", None)
                if mentioned is not None:
                    await notify_afk(mentioned)

        raw_text = str(getattr(message, "text", "") or "").strip()
        match = re.match(r"^#([\w-]{1,64})", raw_text, flags=re.UNICODE)
        if match:
            note = await self.guardian_surface.get_note(int(chat.id), match.group(1))
            if note is not None:
                await message.reply_text(sanitize_html(note), parse_mode="HTML")
                return True
        return False

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.message.text:
            return

        raw_message_text = update.message.text.strip()
        text = tr_upper(raw_message_text)
        media_url = extract_media_url(update.message.text)

        moderation = getattr(self, "moderation", None)
        if moderation and update.effective_chat.type in {"group", "supergroup"}:
            settings_failed = False
            try:
                settings = await GuardianSurface(self.data_port).effective_settings(update.effective_chat.id)
            except Exception as exc:
                logger.warning("Could not load group moderation settings: type=%s", type(exc).__name__[:64])
                settings_failed = True
                settings = None
            if isinstance(settings, dict):
                settings = dict(settings)
                settings["_sender_is_bot"] = bool(getattr(update.effective_user, "is_bot", False))
            is_admin = self.sudo_manager.is_sudo(update.effective_user.id) or await is_group_admin(
                update, context, authorization=self.authorization
            )
            event = build_moderation_event(update, is_admin=is_admin)
            moderation_service = getattr(self, "moderation_service", None)
            if settings_failed:
                # Do not execute destructive policy with unknown settings.
                # Other independent flows (especially media downloads) are
                # still allowed to proceed without a group-facing error.
                logger.warning("moderation_degraded settings_unavailable chat=%s", update.effective_chat.id)
            else:
                # Forced-sub is a Telegram membership gate and must run
                # before any AI/media/game routing.  It is scoped to this
                # group and leaves privileged members untouched.
                if await self.guardian_telegram_service.enforce_pending_captcha(
                    bot=getattr(context, "bot", None),
                    chat_id=int(update.effective_chat.id),
                    user_id=int(update.effective_user.id),
                    message=update.effective_message,
                    settings=settings or {},
                    is_admin=is_admin,
                    data_port=self.data_port,
                ):
                    return
                if await self.guardian_telegram_service.enforce_fsub(
                    bot=getattr(context, "bot", None),
                    chat_id=int(update.effective_chat.id),
                    user_id=int(update.effective_user.id),
                    message=update.effective_message,
                    settings=settings or {},
                    is_admin=is_admin,
                ):
                    return
                guardian_user = {}
                if event is not None:
                    try:
                        guardian_user = await self.data_port.get_user(int(event.user_id)) or {}
                    except Exception:
                        guardian_user = {}
                guardian_decision = evaluate_guardian_message(
                    attachment_type=getattr(event, "attachment_type", None),
                    settings=settings,
                    user=guardian_user,
                    user_id=getattr(event, "user_id", None),
                    text="\n".join(part for part in (event.text, event.caption or "") if part),
                    sticker_set_name=getattr(event, "sticker_set_name", None),
                    attachment_id=getattr(event, "attachment_id", None),
                    is_edited=bool(getattr(event, "is_edited", False)),
                    is_admin=is_admin,
                ) if event is not None else None
                if event is not None and guardian_decision is not None and not guardian_decision.allowed:
                    decision = guardian_decision
                elif event is not None and moderation_service is not None:
                    decision = await moderation_service.evaluate_event(event, settings)
                elif event is not None:
                    decision = moderation.evaluate(
                        event.chat_id,
                        event.user_id,
                        "\n".join(part for part in (event.text, event.caption or "") if part),
                        settings,
                        event.is_admin,
                    )
                else:
                    decision = type("Decision", (), {"allowed": True, "reason": None})()
                if media_url and decision.reason == "link_filter":
                    decision = type(decision)(True)
                if event is not None and await self._enforce_moderation_decision(update, context, decision, event):
                    return

        # Social links are handled by the bounded media queue before AI chat.
        # This also works in groups where the bot is not explicitly mentioned.
        if media_url and getattr(self, "media_service", None):
            if self.sudo_manager.is_blacklisted(update.effective_user.id, update.effective_chat.id):
                return
            await self.orchestrator_media_link(update, context, media_url)
            return

        if await self._check_game_inputs(update, context, text):
            return

        message = update.effective_message
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        full_name = update.effective_user.full_name
        username = update.effective_user.username
        chat_type = update.effective_chat.type

        # Telegram retries can deliver the same update again. Use the stable
        # chat/message identity as the generation seed so the delivery ledger
        # can suppress a duplicate reply; update_id is only an extra hint.
        update_id = getattr(update, "update_id", None)
        message_id = getattr(message, "message_id", None)
        turn_id = (
            f"telegram:{chat_id}:{message_id}:{update_id}"
            if message_id is not None
            else str(uuid.uuid4())
        )
        record_kwargs = {
            "telegram_message_id": getattr(message, "message_id", None),
            "reply_to_message_id": getattr(getattr(message, "reply_to_message", None), "message_id", None),
            "thread_id": getattr(message, "message_thread_id", None),
            "author_user_id": user_id,
            "trigger_kind": "observed",
            "chat_type": chat_type,
            "turn_id": turn_id,
        }
        try:
            await self.chat_engine.record_message(
                chat_id,
                user_id,
                full_name,
                message.text,
                username=username,
                **record_kwargs,
            )
        except TypeError as exc:
            if "unexpected keyword" not in str(exc).casefold():
                raise
            await self.chat_engine.record_message(chat_id, user_id, full_name, message.text)
        if chat_type in {"group", "supergroup"} and getattr(message, "message_id", None):
            await self.recent_message_index.add_message(
                {
                    "schema_version": 1,
                    "turn_id": turn_id,
                    "chat_id": chat_id,
                    "thread_id": getattr(message, "message_thread_id", None),
                    "telegram_message_id": int(message.message_id),
                    "author_user_id": user_id,
                    "username": username,
                    "display_name": full_name,
                    "text": message.text,
                    "created_at": datetime.now(timezone.utc),
                    "chat_type": chat_type,
                    "trigger_kind": "observed",
                    "role": "user",
                }
            )

        if await self._handle_guardian_presence(update):
            return

        if not await self._should_respond(update, context):
            return

        asyncio.create_task(self._log_event(context, message=message.text, user_info=f"{full_name}", chat_type=chat_type))

        user_id_str = update.effective_user.id
        state_payload = get_scoped_state(
            getattr(self, "user_states", None),
            user_id=user_id_str,
            chat_id=chat_id,
        )
        if state_payload is not None:
            state = state_payload.get('state')
            if state == 'awaiting_pack_title':
                await self._handle_pack_title_input(update, context)
                return
            elif state == 'awaiting_pack_emoji':
                await self._handle_pack_emoji_input(update, context)
                return
            elif state == 'awaiting_pack_name':
                await self._handle_pack_name_input(update, context)
                return

        command_text = raw_message_text.casefold()
        if command_text.startswith('/ara ') or command_text.startswith('/search '):
            query = raw_message_text.split(None, 1)[1] if len(raw_message_text.split()) > 1 else None
            if query:
                try:
                    search_result = await self.chat_engine.search_web(query)
                    if not search_result.ok:
                        return
                    payload = search_result.data if isinstance(search_result.data, dict) else {}
                    search_text = str(payload.get("content") or "")
                    if not search_text:
                        return
                    persona_mode = await self._resolve_persona_mode(update, chat_id)
                    response = await self.chat_engine.get_response(
                        chat_id,
                        query,
                        user_display_name=full_name,
                        user_id=user_id,
                        username=username,
                        persona_mode=persona_mode,
                        context=search_text,
                        is_group_admin=await is_group_admin(update, context, authorization=getattr(self, "authorization", None)),
                    )
                    await update.effective_message.reply_text(
                        render_model_html(response),
                        parse_mode='HTML',
                        **self._current_message_reply_kwargs(update),
                    )
                    return
                except Exception as e:
                    logger.error("Manual search failed: %s", summarize_exception(e, label="manual_search"))
                    return

        await self.process_chat_request(update, chat_id, message.text, context, turn_id=turn_id)

    async def orchestrator_media_link(self, update, context, url):
        if not await is_chat_feature_enabled(
            self.data_port,
            update.effective_chat.id,
            "media_enabled",
            True,
        ):
            return
        status = await update.effective_message.reply_text("🔎 Bağlantı analiz ediliyor…")
        try:
            job_id, duplicate = await self.media_service.submit(
                url,
                update.effective_chat.id,
                update.effective_user.id,
                status.message_id,
                chat_type=getattr(update.effective_chat, "type", "private"),
            )
            if duplicate:
                await status.edit_text(f"⏳ Bu bağlantı için aktif bir iş var.\nİş: <code>{job_id}</code>", parse_mode="HTML")
            else:
                await status.edit_text(f"⏳ İndirme kuyruğa alındı.\nİş: <code>{job_id}</code>", parse_mode="HTML")
        except Exception as exc:
            logger.error("Could not queue media URL: %s", summarize_exception(exc, label="media_enqueue"))
            if getattr(update.effective_chat, "type", None) in {"group", "supergroup"}:
                # Group link failures stay in the worker/log path; do not
                # broadcast rate limits or provider details to everyone.
                try:
                    await status.delete()
                except Exception:
                    pass
                return
            if str(exc) == "rate_limited":
                await status.edit_text("⚠️ Çok sık medya isteği gönderdin. Lütfen bir dakika bekle.")
            else:
                await status.edit_text("⚠️ Medya işi başlatılamadı. Lütfen daha sonra tekrar deneyin.")

    async def _check_game_inputs(self, update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
        await hydrate_games(self.data_port, update.effective_chat.id, context.chat_data)
        age_state = context.chat_data.get("guess_age")
        if (
            getattr(self, "guess_age_service", None) is not None
            and isinstance(age_state, dict)
            and age_state.get("status") == "playing"
            and str(text or "").strip().isdecimal()
            and getattr(update.effective_chat, "type", None) in {"group", "supergroup"}
        ):
            try:
                _, outcome = await self.guess_age_service.submit(
                    chat_id=int(update.effective_chat.id),
                    user_id=int(update.effective_user.id),
                    display_name=str(update.effective_user.first_name or "oyuncu"),
                    guess=str(text).strip(),
                )
                await update.effective_message.reply_text(
                    f"tahmin kaydedildi · +{int(outcome.score)} puan",
                    reply_to_message_id=getattr(update.effective_message, "message_id", None),
                )
            except GuessAgeServiceError as exc:
                messages = {
                    "GUESS_AGE_ROUND_EXPIRED": "bu turun süresi doldu; sonuç panelini yenile",
                    "guess_age_already_answered": "bu tur için tahminini zaten bıraktın",
                }
                notice = messages.get(exc.code)
                if notice:
                    await update.effective_message.reply_text(notice)
            return True
        word_guess = context.chat_data.get("word_guess")
        job_queue = getattr(context, "job_queue", None)
        if word_guess and job_queue is not None:
            # APScheduler jobs are process-local.  Re-arm the four-minute
            # timeout when a persisted word game is first hydrated after a
            # restart, while avoiding duplicate timers on later messages.
            job_name = f"word_guess_timeout_{update.effective_chat.id}"
            if not job_queue.get_jobs_by_name(job_name):
                started_at = float(word_guess.get("start_time", time.time()) or time.time())
                time_limit = int(word_guess.get("time_limit", 240) or 240)
                remaining = max(1, int(time_limit - (time.time() - started_at)))
                job_queue.run_once(
                    self._word_guess_timeout_callback,
                    when=remaining,
                    chat_id=update.effective_chat.id,
                    name=job_name,
                )
        if 'hangman' in context.chat_data and len(text) == 1 and text.isalpha():
            game = context.chat_data['hangman']
            result = apply_guess(game, text)
            if result['status'] == 'invalid':
                if result.get('reason') == 'round_finished':
                    context.chat_data.pop('hangman', None)
                    await delete_game(self.data_port, update.effective_chat.id, "hangman")
                    await update.message.reply_text("Bu adam asmaca turu sona ermiş.")
                else:
                    await update.message.reply_text("Geçerli bir harf yazmalısın.")
                return True
            if result['status'] == 'duplicate':
                letter = escape(str(result.get('letter', text)).upper())
                await update.message.reply_text(f"⚠️ <b>{letter}</b> harfini zaten söylemiştin!", parse_mode='HTML')
                return True
            if result['status'] == 'won':
                elapsed = max(0.0, time.time() - float(game.get("start_time", time.time())))
                score = calculate_hangman_score(
                    elapsed_seconds=elapsed,
                    difficulty=game.get("difficulty", "normal"),
                    lives_remaining=game.get("lives", 0),
                    max_lives=game.get("max_lives", 6),
                    wrong_guesses=game.get("wrong_guesses", 0),
                    hints_used=game.get("hints_used", 0),
                )
                await self._record_game_result(update, "hangman", score, "win")
                await update.message.reply_text(f"🥳 <b>TEBRİKLER!</b> Kelimeyi buldun: <b>{game['word']}</b> 🏆", parse_mode='HTML')
                del context.chat_data['hangman']
                await delete_game(self.data_port, update.effective_chat.id, "hangman")
                return True
            if result['status'] == 'lost':
                await update.message.reply_text(f"💀 <b>MAALESEF!</b> Canın bitti. Kelime: <b>{game['word']}</b> idi. 🪦", parse_mode='HTML')
                del context.chat_data['hangman']
                await delete_game(self.data_port, update.effective_chat.id, "hangman")
                return True

            await persist_game(self.data_port, update.effective_chat.id, "hangman", game)
            display = " ".join(game['hidden'])
            await update.message.reply_text(
                f"📝 <b>Adam Asmaca</b>\n\n"
                f"Kelime: <code>{display}</code>\n"
                f"Kalan Can: <b>{game['lives']}</b> ❤️\n"
                f"Tahmin Edilenler: {', '.join(game['guessed_letters'])}",
                parse_mode='HTML'
            )
            return True

        if 'word_game' in context.chat_data:
            game = context.chat_data['word_game']
            letters = game['letters']
            guess = normalize_game_word(text)
            valid_words = {
                normalize_game_word(word)
                for word in game.get('valid_words', [])
            }
            is_valid = can_form_word(guess, letters)
            if valid_words:
                is_valid = is_valid and guess in valid_words

            if len(guess) < 3 or not is_valid:
                await update.message.reply_text(
                    "🧩 Bu kelime sözlükte yok ya da raftaki harflerle kurulamıyor. "
                    "Harfleri yeniden karıştırıp başka bir kelime dene.",
                    reply_markup=word_game_keyboard(game),
                    parse_mode='HTML',
                )
                return True

            # Normalize states created by older releases before checking for
            # duplicates, so a restart cannot make `KEDI` and `kedi` count as
            # two different answers.
            normalized_found = []
            seen_found = set()
            for found in game.get('found_words', []):
                normalized_found_word = normalize_game_word(found)
                if normalized_found_word and normalized_found_word not in seen_found:
                    seen_found.add(normalized_found_word)
                    normalized_found.append(normalized_found_word)
            game['found_words'] = normalized_found

            if guess in normalized_found:
                await update.message.reply_text(
                    f"⚠️ <b>{escape(text.strip())}</b> zaten bulundu!",
                    reply_markup=word_game_keyboard(game),
                    parse_mode='HTML',
                )
            else:
                game['found_words'].append(guess)
                count = len(game['found_words'])
                if count >= game['target_count']:
                    elapsed = max(0.0, time.time() - float(game.get("start_time", time.time())))
                    score = calculate_word_game_score(
                        guess,
                        elapsed_seconds=elapsed,
                        difficulty=game.get("difficulty", "normal"),
                        combo=count,
                        hints_used=game.get("hints_used", 0),
                    )
                    await self._record_game_result(update, "word_game", score, "win")
                    await update.message.reply_text(f"🏆 <b>TEBRİKLER!</b> {game['target_count']} kelimeyi buldunuz ve oyunu kazandınız! 🔥", parse_mode='HTML')
                    del context.chat_data['word_game']
                    await delete_game(self.data_port, update.effective_chat.id, "word_game")
                else:
                    await persist_game(self.data_port, update.effective_chat.id, "word_game", game)
                    await update.message.reply_text(
                        render_word_game_text(
                            game,
                            note=f"✅ <b>{escape(text.strip())}</b> kabul edildi.",
                        ),
                        reply_markup=word_game_keyboard(game),
                        parse_mode='HTML',
                    )
            return True

        if 'word_guess' in context.chat_data:
            game = context.chat_data['word_guess']
            guess = normalize_game_word(text)
            game['attempts'] = int(game.get('attempts', 0) or 0) + 1
            target_word = game['word']
            target_normalized = normalize_game_word(target_word)

            match = False
            if guess == target_normalized:
                match = True
            elif "(" in target_word:
                main_part = target_word.split("(")[0].strip()
                if guess == normalize_game_word(main_part):
                    match = True

            if match:
                await update.message.reply_text(
                    f"🥳 <b>TEBRİKLER!</b>\n\n"
                    f"Kelimeyi doğru bildiniz: <b>{escape(str(game['word']))}</b> 🏆\n\n"
                    f"✍️ Bildiren: {update.effective_user.mention_html()}\n"
                    f"🧠 <i>{escape(str(game['word']))} hakkında açıklama yukarıdaydı.</i>\n"
                    f"⏳ <b>3 saniye sonra yeni kelime geliyor...</b>",
                    parse_mode='HTML',
                    **self._game_reply_kwargs(update, game),
                )

                job_name = f"word_guess_timeout_{update.effective_chat.id}"
                self._remove_job_by_name(context, job_name)
                job_name_hint = f"word_guess_hint_{update.effective_chat.id}"
                self._remove_job_by_name(context, job_name_hint)
                job_name_skip = f"word_guess_skip_{update.effective_chat.id}"
                self._remove_job_by_name(context, job_name_skip)

                del context.chat_data['word_guess']
                await delete_game(self.data_port, update.effective_chat.id, "word_guess")
                elapsed = max(0.0, time.time() - float(game.get("start_time", time.time())))
                score = calculate_word_guess_score(
                    elapsed_seconds=elapsed,
                    difficulty=game.get("difficulty", "normal"),
                    attempts=game.get("attempts", 1),
                    hints_used=game.get("hint_index", 0),
                )
                await self._record_game_result(update, "word_guess", score, "win")

                context.job_queue.run_once(
                    self._auto_next_anlat_callback,
                    when=3,
                    chat_id=update.effective_chat.id,
                    name=f"auto_next_anlat_{update.effective_chat.id}"
                )
                return True

            # A wrong guess belongs to the game too. Consuming it here keeps
            # the normal dispatcher from sending it to AI, which used to
            # cause avoidable provider timeouts and "AI unavailable" replies.
            await persist_game(self.data_port, update.effective_chat.id, "word_guess", game)
            reply_message = getattr(update, "message", None) or getattr(update, "effective_message", None)
            if not callable(getattr(reply_message, "reply_text", None)):
                return True
            await reply_message.reply_text(
                "❌ Bu kelime olmadı. İpucu aç veya başka bir tahmin dene.",
                reply_markup=word_guess_keyboard(game),
                parse_mode="HTML",
                **self._game_reply_kwargs(update, game),
            )
            return True

        return False

    async def _word_guess_timeout_callback(self, context: ContextTypes.DEFAULT_TYPE):
        """Expire a persisted word-guess session after its four-minute TTL."""

        job = getattr(context, "job", None)
        chat_id = getattr(job, "chat_id", None)
        application = getattr(context, "application", None)
        chat_data = getattr(application, "chat_data", {}).get(chat_id) if application else None
        game = chat_data.get("word_guess") if chat_data else None
        if not game:
            return
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "⏰ <b>Süre doldu!</b>\n\n"
                f"Doğru cevap: <b>{escape(str(game.get('word', 'bilinmiyor')))}</b>"
            ),
            parse_mode="HTML",
        )
        chat_data.pop("word_guess", None)
        await delete_game(self.data_port, chat_id, "word_guess")

    def _remove_job_by_name(self, context, name):
        remove_job_by_name(context, name)

    async def _auto_next_anlat_callback(self, context: ContextTypes.DEFAULT_TYPE):
        job = context.job
        chat_id = job.chat_id
        chat_data = context.application.chat_data.get(chat_id)
        if chat_data and 'word_guess' in chat_data:
            return
        await context.bot.send_message(chat_id, "🔄 <b>Yeni kelime geliyor...</b>", parse_mode='HTML')
        from src.handlers.command_handler import CommandHandler
        cmd_handler = CommandHandler(self.chat_engine, self.sudo_manager, self.image_router, self.tool_manager)
        await cmd_handler._start_word_guess_game(chat_id, context)

    async def _should_respond(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        if not update.effective_chat:
            return False

        chat_id = update.effective_chat.id
        user_id = update.effective_user.id

        if self.sudo_manager.is_blacklisted(user_id, chat_id):
            logger.warning("Prevented interaction from blacklisted user/chat")
            return False

        if update.effective_chat.type == "private":
            return True

        message = update.effective_message
        if not message:
            return False

        if not self.bot_info:
            self.bot_info = await context.bot.get_me()

        bot_username = self.bot_info.username

        text = (message.text or message.caption or "").lower()
        triggers = ["arel", f"@{bot_username}".lower()]

        if any(trigger in text for trigger in triggers):
            return True

        if message.reply_to_message and message.reply_to_message.from_user.id == self.bot_info.id:
            if message.reply_to_message.video or message.reply_to_message.animation:
                return any(trigger in text for trigger in triggers)
            return True

        return False

    async def _log_event(self, context: ContextTypes.DEFAULT_TYPE, message: str = None, photo_path: str = None, user_info: str = "", chat_type: str = "private"):
        try:
            if getattr(Config, "LOG_CHANNEL_ERRORS_ONLY", False):
                return
            is_group = chat_type in ["group", "supergroup"]
            target_chat_id = Config.LOG_CHANNEL_ID
            target_thread_id = Config.GROUP_LOG_THREAD_ID if is_group else Config.DM_LOG_THREAD_ID

            if target_thread_id == 1:
                target_thread_id = None

            actor_label = "assistant_output" if user_info == "Sistem Yanıtı" else "user_input"
            actor = "assistant" if actor_label == "assistant_output" else "user"
            log_text = f"🚨 <b>SİSTEM KAYDI</b>\n👤 <b>Actor:</b> {actor}\n"
            log_text += f"🏠 <b>Tip:</b> {chat_type.upper()}\n\n"
            log_text += f"📝 <b>Payload:</b> {summarize_private_text(message, label=actor_label)}"
            if photo_path:
                log_text += f"\n📎 {summarize_private_attachment(photo_path)}"

            # Operational channels must never become a copy of user uploads.
            # Keep the safe summary in a text event and discard the attachment
            # at this boundary; the worker pipeline owns user media delivery.
            await context.bot.send_message(
                chat_id=target_chat_id,
                text=log_text,
                parse_mode='HTML',
                message_thread_id=target_thread_id
            )
        except Exception as e:
            logger.error("Failed to log to channel: %s", summarize_exception(e, label="log_channel"))

    async def _resolve_persona_mode(self, update: Update, chat_id: int) -> str | None:
        """Resolve the persistent tone scope without mixing group and user settings."""
        chat_type = getattr(getattr(update, "effective_chat", None), "type", "")
        user_id = int(getattr(getattr(update, "effective_user", None), "id", 0) or 0)
        if chat_type in {"group", "supergroup"}:
            try:
                settings = await self.data_port.get_chat_settings(chat_id)
                stored_mode = (settings or {}).get("persona_mode") if isinstance(settings, dict) else None
                if is_normal_mode(stored_mode):
                    return NORMAL_MODE
                return PersonalityPolicy.normalize_mode(stored_mode) if stored_mode else None
            except Exception:
                logger.warning("Could not load group persona setting", exc_info=True)
                return None
        if chat_type == "private":
            try:
                user_settings = await self.data_port.get_user(user_id)
                stored_mode = (user_settings or {}).get("persona_mode") if isinstance(user_settings, dict) else None
                if is_normal_mode(stored_mode):
                    return NORMAL_MODE
                return PersonalityPolicy.normalize_mode(stored_mode) if stored_mode else None
            except Exception:
                logger.warning("Could not load private persona setting", exc_info=True)
                return None
        return None

    @staticmethod
    def _group_message_ref(row: dict) -> GroupMessageRef | None:
        try:
            created_at = row.get("created_at") or row.get("timestamp")
            if not isinstance(created_at, datetime):
                created_at = datetime.fromtimestamp(float(created_at), tz=timezone.utc)
            elif created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            return GroupMessageRef(
                chat_id=int(row["chat_id"]),
                thread_id=(int(row["thread_id"]) if row.get("thread_id") is not None else None),
                message_id=int(row.get("telegram_message_id", row.get("message_id"))),
                user_id=int(row.get("author_user_id", row.get("user_id"))),
                display_name=str(row.get("display_name") or "Kullanıcı")[:80],
                text=str(row.get("text", row.get("content", "")))[:4096],
                created_at=created_at,
                message_kind=str(row.get("message_kind") or "text"),
                already_replied=bool(row.get("replied_at")),
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            return None

    async def _process_group_targeted_replies(
        self,
        update: Update,
        chat_id: int,
        text: str,
        context: ContextTypes.DEFAULT_TYPE,
        *,
        turn_id: str | None = None,
    ) -> bool:
        """Serialize one group/topic targeted-reply turn."""

        tool_intent = getattr(self.chat_engine, "_should_attach_tools", None)
        if callable(tool_intent) and tool_intent(text) is True:
            # The reply planner only writes conversational text. Feature
            # requests must continue through get_response's actual tool path.
            return False

        message = update.effective_message
        thread_id = getattr(message, "message_thread_id", None)
        async with self._group_reply_lock(chat_id, thread_id):
            return await self._process_group_targeted_replies_unlocked(
                update,
                chat_id,
                text,
                context,
                turn_id=turn_id,
            )

    async def _process_group_targeted_replies_unlocked(
        self,
        update: Update,
        chat_id: int,
        text: str,
        context: ContextTypes.DEFAULT_TYPE,
        *,
        turn_id: str | None = None,
    ) -> bool:
        """Build and deliver separate replies for the bounded pending window."""

        if not callable(getattr(self.chat_engine, "get_group_reply_plan", None)):
            return False
        if not getattr(Config, "GROUP_TARGETED_REPLIES_ENABLED", True):
            return False
        message = update.effective_message
        current_id = getattr(message, "message_id", None)
        if not current_id or getattr(update.effective_chat, "type", None) not in {"group", "supergroup"}:
            return False
        thread_id = getattr(message, "message_thread_id", None)
        generation_hint = turn_id
        if generation_hint:
            existing_delivery = await self.reply_delivery_ledger.get(
                self.reply_delivery_ledger.make_key(
                    chat_id,
                    thread_id,
                    int(current_id),
                    feature=GroupReplyDeliveryService.FEATURE,
                    generation_id=generation_hint,
                )
            )
            if existing_delivery and existing_delivery.get("status") in {"sent", "uncertain", "skipped"}:
                return True
        now = datetime.now(timezone.utc)
        rows = await self.recent_message_index.list_candidates(
            chat_id,
            thread_id=thread_id,
            since=now - timedelta(seconds=getattr(Config, "GROUP_REPLY_WINDOW_SECONDS", 300)),
            limit=getattr(Config, "GROUP_REPLY_MAX_CANDIDATES", 8),
            latest_per_user=False,
            include_replied=True,
        )
        refs = tuple(ref for row in rows if (ref := self._group_message_ref(row)) is not None)
        current_ref = next((ref for ref in refs if ref.message_id == int(current_id)), None)
        if current_ref is not None and current_ref.already_replied:
            # The persistent attribution is authoritative for Telegram retry
            # delivery.  Reopening it would send a second reply when two
            # concurrent updates share the same pending window.
            return True
        refs = tuple(ref for ref in refs if not ref.already_replied)
        if current_ref is None:
            current_ref = GroupMessageRef(
                chat_id=int(chat_id),
                thread_id=(int(thread_id) if thread_id is not None else None),
                message_id=int(current_id),
                user_id=int(update.effective_user.id),
                display_name=str(update.effective_user.full_name or "Kullanıcı")[:80],
                text=str(text or "")[:4096],
                created_at=now,
            )
            refs = (*refs, current_ref)
        turn_context = GroupTurnContext(
            current_message=current_ref,
            candidates=refs,
            trigger_kind="mention_or_reply",
            max_segments=getattr(Config, "GROUP_REPLY_MAX_SEGMENTS", 3),
            window_seconds=getattr(Config, "GROUP_REPLY_WINDOW_SECONDS", 300),
        )
        planning_started = time.perf_counter()
        try:
            raw_plan = await self.chat_engine.get_group_reply_plan(turn_context)
        except Exception as exc:
            logger.warning("Group reply plan failed: %s", summarize_exception(exc, label="group_reply_plan"))
            raw_plan = None
        safe_fallback = None
        if raw_plan is None:
            try:
                safe_fallback = await self.chat_engine.get_response(
                    chat_id,
                    text,
                    user_display_name=update.effective_user.full_name,
                    user_id=update.effective_user.id,
                    username=update.effective_user.username,
                    persist_conversation=False,
                    purpose="group_targeted_reply_fallback",
                    is_group_admin=await is_group_admin(update, context, authorization=getattr(self, "authorization", None)),
                )
            except TypeError as exc:
                if "unexpected keyword" not in str(exc).casefold():
                    logger.warning("Group reply fallback failed: %s", summarize_exception(exc, label="group_reply_fallback"))
                else:
                    try:
                        safe_fallback = await self.chat_engine.get_response(chat_id, text)
                    except Exception as fallback_exc:
                        logger.warning(
                            "Group reply fallback failed: %s",
                            summarize_exception(fallback_exc, label="group_reply_fallback"),
                        )
            except Exception as exc:
                logger.warning("Group reply fallback failed: %s", summarize_exception(exc, label="group_reply_fallback"))
            if not safe_fallback:
                safe_fallback = "mesajını gördüm, ama şu an cevap üretirken takıldım."
            raw_plan = "{}"
        plan = self.group_reply_router.build_plan(
            turn_context,
            raw_plan,
            safe_fallback_text=safe_fallback,
        )
        self._observe_group_reply("group_reply_plan_seconds", time.perf_counter() - planning_started)
        if getattr(Config, "GROUP_TARGETED_REPLIES_SHADOW", False):
            return True
        if not plan.segments:
            # An empty JSON plan is valid for a passive group turn, but this
            # method is called only after an explicit mention/reply trigger.
            # Never leave that trigger unanswered just because the fast route
            # returned an empty list or a provider response was malformed.
            if current_ref.message_id == int(current_id):
                if not safe_fallback:
                    try:
                        safe_fallback = await self.chat_engine.get_response(
                            chat_id,
                            text,
                            user_display_name=update.effective_user.full_name,
                            user_id=update.effective_user.id,
                            username=update.effective_user.username,
                            persist_conversation=False,
                            purpose="group_targeted_reply_empty_plan_fallback",
                            is_group_admin=await is_group_admin(update, context, authorization=getattr(self, "authorization", None)),
                        )
                    except Exception as exc:
                        logger.warning(
                            "Group empty-plan fallback failed: %s",
                            summarize_exception(exc, label="group_reply_empty_fallback"),
                        )
                if not safe_fallback:
                    safe_fallback = "buradayım ya, bağlantı bi an tökezledi 😅"
                plan = GroupReplyPlan(
                    (ReplySegment(current_ref, str(safe_fallback).strip()[:4096]),),
                    plan.rejected_reason or "empty_plan_fallback",
                    plan.correlation_id,
                )
            if not plan.segments:
                return True
        generation_id = turn_id or plan.correlation_id or str(uuid.uuid4())
        delivery = await self.group_reply_delivery.deliver(
            bot=context.bot,
            plan=plan,
            generation_id=generation_id,
        )
        deliveries = [delivery]
        # If Telegram reports that a non-current target was deleted, preserve
        # the current trigger's answer as one controlled fallback.  The
        # delivery ledger still owns deduplication and exact reply binding.
        missing_targets = set(getattr(delivery, "missing_target_message_ids", ()))
        current_target_id = current_ref.message_id
        current_already_sent = current_target_id in set(delivery.sent_target_message_ids)
        current_was_deleted = current_target_id in missing_targets
        fallback_segment = None
        if missing_targets and not current_already_sent and not current_was_deleted:
            fallback_text = safe_fallback
            if not fallback_text:
                fallback_text = next(
                    (segment.text for segment in plan.segments if segment.target_message.message_id == current_target_id),
                    None,
                )
            if fallback_text:
                fallback_segment = ReplySegment(current_ref, fallback_text)
                increment = getattr(self.group_reply_metrics, "inc", None)
                if increment is not None:
                    increment("group_reply_reply_fallback_total")
                fallback_plan = GroupReplyPlan(
                    (fallback_segment,),
                    "target_message_missing",
                    f"{generation_id}:fallback",
                )
                fallback_delivery = await self.group_reply_delivery.deliver(
                    bot=context.bot,
                    plan=fallback_plan,
                    generation_id=f"{generation_id}:fallback",
                )
                deliveries.append(fallback_delivery)
        sent_target_ids = {
            target_id
            for item in deliveries
            for target_id in item.sent_target_message_ids
        }
        sent_targets = tuple(
            segment for segment in plan.segments
            if segment.target_message.message_id in sent_target_ids
        )
        sent_id_by_target = {
            target_id: sent_id
            for item in deliveries
            for target_id, sent_id in zip(item.sent_target_message_ids, item.sent_message_ids)
        }
        if len(deliveries) > 1:
            sent_targets = tuple((*sent_targets, *(
                segment for segment in ((fallback_segment,) if fallback_segment is not None else ())
                if segment.target_message.message_id in sent_target_ids
            )))
        for segment in sent_targets:
            target = segment.target_message
            try:
                await self.recent_message_index.mark_replied(
                    target.chat_id,
                    target.thread_id,
                    target.message_id,
                    turn_id=generation_id,
                )
                sent_message_id = sent_id_by_target.get(target.message_id)
                await self.chat_engine.record_message(
                    chat_id,
                    None,
                    "Arel",
                    segment.text,
                    role="assistant",
                    telegram_message_id=sent_message_id,
                    reply_to_message_id=target.message_id,
                    target_user_id=target.user_id,
                    thread_id=target.thread_id,
                    trigger_kind="group_targeted_reply",
                    chat_type=getattr(update.effective_chat, "type", "supergroup"),
                    turn_id=generation_id,
                )
            except TypeError as exc:
                if "unexpected keyword" not in str(exc).casefold():
                    raise
                await self.chat_engine.record_message(chat_id, None, "Arel", segment.text, role="assistant")
        return True

    async def process_chat_request(
        self,
        update: Update,
        chat_id: int,
        text: str,
        context: ContextTypes.DEFAULT_TYPE,
        image_path: str = None,
        turn_id: str | None = None,
    ):
        attempt = 0
        max_retries = 10

        while True:
            attempt += 1
            try:
                user_info = f"{update.effective_user.full_name} (@{update.effective_user.username}, {update.effective_user.id})"
                chat_type = update.effective_chat.type

                if attempt == 1:
                    await self._log_event(context, message=text, user_info=user_info, chat_type=chat_type)

                if chat_type in {"group", "supergroup"}:
                    targeted = await self._process_group_targeted_replies(
                        update,
                        chat_id,
                        text,
                        context,
                        turn_id=turn_id,
                    )
                    if targeted:
                        return

                full_name = update.effective_user.full_name
                username = update.effective_user.username
                user_id = update.effective_user.id
                persona_mode = await self._resolve_persona_mode(update, chat_id)

                response = await self.chat_engine.get_response(
                    chat_id, text, image_path=image_path, user_display_name=full_name,
                    user_id=user_id, username=username, persona_mode=persona_mode,
                    is_group_admin=await is_group_admin(update, context, authorization=getattr(self, "authorization", None)),
                )

                if not response:
                    logger.info("Arel chose to stay silent")
                    return

                # Reply attribution is persistence/delivery metadata. Strip it
                # before logging or rendering so Telegram IDs cannot leak into
                # a group conversation when a model echoes its context.
                response = strip_internal_attribution(response).strip()
                if not response:
                    logger.info("Arel response contained only internal metadata")
                    return

                await self._log_event(context, message=f"🤖 <b>Bot Yanıtı:</b>\n{response}", user_info="Sistem Yanıtı", chat_type=chat_type)

                if re.search(r'\[?<?NO_REPLY>?\]?', response, re.I):
                    logger.info("Bot chose silence")
                    return

                match = re.search(r'<GENERATE_IMAGE>(.*?)(?:</GENERATE_IMAGE>|$)', response, re.DOTALL | re.IGNORECASE)

                if not match and "<GENERATE_IMAGE>" in response.upper():
                    parts = response.split("<GENERATE_IMAGE>")
                    if len(parts) > 1:
                        image_prompt = parts[1].split("</GENERATE_IMAGE>")[0].strip()
                        match = True

                if match:
                    image_prompt = match.group(1).strip() if hasattr(match, 'group') else image_prompt
                    image_prompt = image_prompt.replace('`', '').strip()
                    logger.info("Silent handover detected: %s", summarize_private_text(image_prompt, label="image_prompt"))
                    await self.process_image_request(update, image_prompt, context)
                    return

                async def send_safely(text: str):
                    reply_kwargs = self._current_message_reply_kwargs(update)
                    try:
                        await update.effective_message.reply_text(
                            render_model_html(text),
                            parse_mode='HTML',
                            **reply_kwargs,
                        )
                    except Exception as html_e:
                        if "Can't parse entities" in str(html_e) or "Bad Request" in str(html_e):
                            logger.warning(f"HTML Parsing failed. Falling back to plain text.")
                            await update.effective_message.reply_text(
                                render_model_plain_text(text),
                                parse_mode=None,
                                **reply_kwargs,
                            )
                        elif "Message to be replied not found" in str(html_e):
                            logger.warning("Reply target missing, falling back to direct message.")
                            await context.bot.send_message(chat_id=chat_id, text=render_model_html(text), parse_mode='HTML')
                        else:
                            raise html_e

                await send_safely(response)
                return

            except Exception as e:
                error_str = str(e).lower()
                is_quota = any(x in error_str for x in ["quota", "429", "rate limit", "limit aşıldı"])

                if is_quota:
                    if attempt < max_retries:
                        logger.warning(f"Key exhausted (Attempt {attempt}/{max_retries}), rotating...")
                        await asyncio.sleep(1)
                        continue
                    else:
                        msg = "⚠️ <b>Zihin Kapasitesi Dolu</b>\n\nŞu an tüm düşünce kanallarım limitlerine ulaştı. Lütfen birkaç dakika sonra tekrar dene."
                        await update.effective_message.reply_text(
                            msg,
                            parse_mode='HTML',
                            **self._current_message_reply_kwargs(update),
                        )
                        return
                else:
                    # The global redaction filter intentionally removes full
                    # tracebacks. Keep a safe source location so a production
                    # AttributeError can be diagnosed without logging user
                    # text, provider payloads, URLs or secrets.
                    trace = traceback.extract_tb(e.__traceback__) if e.__traceback__ else ()
                    location = "unknown"
                    if trace:
                        frame = trace[-1]
                        location = f"{os.path.basename(frame.filename)}:{frame.lineno}:{frame.name}"
                    logger.error(
                        "Critical execution error (Attempt %s): %s location=%s",
                        attempt,
                        summarize_exception(e, label="chat_execution"),
                        location,
                    )
                    await log_error_event(
                        context,
                        f"{summarize_exception(e, label='chat_execution')} location={location}",
                        chat_type=getattr(update.effective_chat, "type", "unknown"),
                    )

                    if "Not enough rights" in error_str:
                        return

                    readable_error = (
                        "❌ <b>Bir Sorun Oluştu</b>\n\n"
                        + escape(public_error_message(e, "İşlem tamamlanamadı; biraz sonra tekrar dene."))
                    )
                    try:
                        await update.effective_message.reply_text(
                            readable_error,
                            parse_mode='HTML',
                            **self._current_message_reply_kwargs(update),
                        )
                    except:
                        await update.effective_message.reply_text(
                            "❌ Sistemde teknik bir aksaklık oluştu.",
                            parse_mode=None,
                            **self._current_message_reply_kwargs(update),
                        )
                    return

            finally:
                if image_path and os.path.exists(image_path):
                    try:
                        os.remove(image_path)
                        logger.info("Cleaned up temporary vision image")
                    except Exception as clean_e:
                        logger.error("Failed to cleanup temporary image: %s", summarize_exception(clean_e, label="image_cleanup"))

    async def process_image_request(self, update: Update, prompt: str, context: ContextTypes.DEFAULT_TYPE):
        """Enqueue AI image generation; the worker owns all provider I/O."""
        if self.image_generation_service is None:
            await update.effective_message.reply_text("⚠️ Görsel servisi şu an hazır değil.")
            return
        status = await update.effective_message.reply_text("🖼️ Görsel isteği kuyruğa alınıyor…")
        try:
            job_id, duplicate = await self.image_generation_service.submit(
                chat_id=update.effective_chat.id,
                user_id=update.effective_user.id,
                status_message_id=status.message_id,
                prompt=prompt,
                reply_to_message_id=getattr(update.effective_message, "message_id", None),
                message_thread_id=getattr(update.effective_message, "message_thread_id", None),
            )
            label = "Bu açıklama için aktif bir iş var" if duplicate else "Görsel isteği kuyruğa alındı"
            await status.edit_text(f"✅ {label}.\n🆔: <code>{job_id}</code>", parse_mode="HTML")
        except Exception as exc:
            code = getattr(exc, "code", "image_generation_error")
            message = "Görsel açıklaması geçersiz." if code == "image_prompt_invalid" else "Görsel isteği başlatılamadı."
            await status.edit_text(f"❌ {message}", parse_mode="HTML")

    async def _handle_pack_title_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if not hasattr(self, 'user_states'):
            return
        state = get_scoped_state(
            self.user_states,
            user_id=user_id,
            chat_id=update.effective_chat.id,
        )
        if state is None:
            return
        title = update.message.text
        if len(title) > 64:
            await update.message.reply_text("⚠️ Başlık çok uzun (max 64 karakter).")
            return

        title = " ".join(title.split())
        if not title or any(ord(char) < 32 for char in title):
            await update.message.reply_text("⚠️ Başlık boş olamaz.")
            return
        state['data']['title'] = title
        state['state'] = 'awaiting_pack_emoji'
        await update.message.reply_text(
            "✅ Başlık kaydedildi. Şimdi paket sticker'ı için 1-3 emoji gönderin "
            "(örnek: 😀 😂).",
            parse_mode='HTML'
        )

    async def _handle_pack_emoji_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if not hasattr(self, 'user_states'):
            return
        state = get_scoped_state(
            self.user_states,
            user_id=user_id,
            chat_id=update.effective_chat.id,
        )
        if state is None:
            return
        values = [value for value in (update.message.text or '').split() if value]
        if not 1 <= len(values) <= 3:
            await update.message.reply_text("⚠️ 1-3 emoji yazmalısın. Örn: 😀 😂")
            return
        try:
            normalized = [validate_emoji(value) for value in values]
        except StickerValidationError:
            await update.message.reply_text("⚠️ Lütfen boşluksuz, geçerli emoji değerleri kullan.")
            return
        state['data']['emojis'] = normalized
        state['state'] = 'awaiting_pack_name'
        await update.message.reply_text(
            "✅ Emoji kaydedildi. Şimdi paket için kısa bir <b>bağlantı ismi</b> belirle.\n"
            "<i>(Sadece İngilizce küçük harf, sayı ve alt çizgi; örn: arel_komik)</i>",
            parse_mode='HTML'
        )

    async def _handle_pack_name_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if not hasattr(self, 'user_states'):
            return
        state = get_scoped_state(
            self.user_states,
            user_id=user_id,
            chat_id=update.effective_chat.id,
        )
        if state is None:
            return
        short_name = update.message.text.strip().lower()

        try:
            validate_short_name(short_name)
        except StickerValidationError:
            await update.message.reply_text(
                "⚠️ <b>Geçersiz isim!</b>\n\n"
                "Paket adı yalnızca <b>küçük harf, sayı ve alt çizgi (_)</b> içerebilir.\n\n"
                "Örn: <code>arel_komik</code> veya <code>benim_paketim1</code>",
                parse_mode='HTML'
            )
            return

        state_data = state['data']
        title = state_data.get('title')
        if not title:
            await update.message.reply_text("⚠️ Paket başlığı bulunamadı; işlemi yeniden başlat.")
            return
        bot_info = await context.bot.get_me()
        try:
            full_pack_name = make_pack_name(short_name, bot_info.username)
        except StickerValidationError as exc:
            await update.message.reply_text("⚠️ Paket kısa adı geçersiz; yalnızca küçük harf, sayı ve alt çizgi kullan.")
            return

        state_data['short_name'] = short_name
        state_data['full_pack_name'] = full_pack_name
        state['state'] = 'awaiting_pack_confirmation'
        keyboard = [[
            InlineKeyboardButton("✅ Paketi oluştur", callback_data=f"stk_confirm_pack:{user_id}"),
            InlineKeyboardButton("İptal", callback_data=f"stk_cancel_pack:{user_id}"),
        ]]
        await update.message.reply_text(
            "<b>Sticker paketi hazır</b>\n\n"
            f"Başlık: <b>{title}</b>\n"
            f"Emojiler: {' '.join(state_data.get('emojis') or ['✨'])}\n"
            f"Kısa ad: <code>{full_pack_name}</code>\n\n"
            "Oluşturmayı onaylıyor musun?",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='HTML',
        )

    async def confirm_pack_creation(self, user_id: int, chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> dict:
        """Create the pack only after the explicit callback confirmation."""
        state = get_scoped_state(
            self.user_states,
            user_id=user_id,
            chat_id=chat_id,
        )
        if not state or state.get('state') != 'awaiting_pack_confirmation':
            raise ValueError('sticker_creation_expired')
        from datetime import datetime, timezone
        data = state.get('data') or {}
        file_id = data.get('file_id')
        full_pack_name = data.get('full_pack_name')
        title = data.get('title')
        emojis = data.get('emojis') or ['✨']
        if not file_id or not full_pack_name or not title:
            raise ValueError('sticker_creation_invalid')
        sticker_format = StickerFormat.VIDEO if data.get('is_video') else StickerFormat.STATIC
        remote_created = await context.bot.create_new_sticker_set(
            user_id=int(user_id),
            name=full_pack_name,
            title=title,
            stickers=[InputSticker(sticker=file_id, emoji_list=emojis, format=sticker_format)],
        )
        if remote_created is False:
            raise RuntimeError("STICKER_REMOTE_CREATE_FAILED")
        pack_id = str(uuid.uuid4())
        pack_record = {
            '_id': pack_id,
            'user_id': int(user_id),
            'chat_id': int(chat_id),
            'pack_name': full_pack_name,
            'pack_title': title,
            'pack_type': 'video' if data.get('is_video') else 'static',
            'is_public': True,
            'created_at': datetime.now(timezone.utc),
        }
        try:
            persisted_id = await self.data_port.create_sticker_pack(pack_record)
            if persisted_id is None or persisted_id is False:
                raise RuntimeError("STICKER_PACK_PERSISTENCE_UNAVAILABLE")
        except Exception as exc:
            logger.error(
                "Sticker pack persistence failed after Telegram creation: %s",
                summarize_exception(exc, label="sticker_pack_persist"),
            )
            # Do not leave an inaccessible remote set behind when persistence
            # failed.  If Telegram cannot remove it, keep the original error
            # and let the operator reconcile the explicitly logged pack id.
            delete_remote = getattr(context.bot, "delete_sticker_set", None)
            if delete_remote is not None:
                try:
                    await delete_remote(full_pack_name)
                except Exception as cleanup_exc:
                    logger.error(
                        "Orphan sticker pack cleanup failed: %s",
                        summarize_exception(cleanup_exc, label="sticker_pack_cleanup"),
                    )
            raise RuntimeError("STICKER_PACK_PERSISTENCE_UNAVAILABLE") from exc
        try:
            await self._register_sticker(pack_id, file_id, ' '.join(emojis), context, chat_id=chat_id, user_id=user_id)
        except Exception as exc:
            # The Telegram set and owner record are already durable.  Catalog
            # tagging is secondary and must not make a usable pack look failed.
            logger.warning(
                "Sticker catalog registration failed after pack creation: %s",
                summarize_exception(exc, label="sticker_catalog"),
            )
        pop_scoped_state(self.user_states, user_id=user_id, chat_id=chat_id)
        return {'title': title, 'link': f'https://t.me/addstickers/{full_pack_name}'}

    async def _register_sticker(self, pack_id: str, file_id: str, emojis: str, context: ContextTypes.DEFAULT_TYPE, *, chat_id: int = 0, user_id: int = 0):
        await self.data_port.register_sticker(pack_id, file_id, emojis)
        if self.sticker_tag_service:
            try:
                await self.sticker_tag_service.submit(
                    chat_id=chat_id,
                    user_id=user_id,
                    sticker_file_id=file_id,
                    source_key=file_id,
                )
            except Exception:
                logger.warning("Sticker tag job could not be queued", exc_info=True)
