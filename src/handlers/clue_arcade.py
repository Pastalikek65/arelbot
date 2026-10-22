"""Telegram transport for the five-clue and emoji riddle games."""
from __future__ import annotations

import logging

from telegram.error import BadRequest, TelegramError

from src.games import clue_arcade as rules
from src.ui import clue_arcade_ui as ui
from src.storage.game_scope import forum_thread_id

from .handler_utils import is_admin

logger = logging.getLogger(__name__)
GAME_KEYS = rules.GAME_KEYS


class ClueArcadeHandler:
    GAME_KEYS = GAME_KEYS

    def __init__(self, service, sudo_manager=None, authorization=None):
        self.service, self.sudo_manager, self.authorization = service, sudo_manager, authorization

    @staticmethod
    def accepts_text(state):
        return state.get("game_key") in GAME_KEYS and state.get("phase") == "playing"

    async def _moderator(self, update, context):
        if update.effective_chat.type == "private":
            return False
        return await is_admin(update, context, self.sudo_manager,
                              authorization=self.authorization)

    async def publish(self, bot, *, game_key, chat_id, thread_id=None, force=False):
        state = await self.service.claim_panel(game_key=game_key, chat_id=chat_id,
                                               thread_id=thread_id, force=force)
        if not state:
            return False
        message_id = state.get("panel_message_id")
        payload = {"chat_id": int(chat_id), "text": ui.render(state),
                   "parse_mode": "HTML", "reply_markup": ui.keyboard(state)}
        if message_id:
            try:
                await bot.edit_message_text(message_id=int(message_id), **payload)
            except BadRequest as exc:
                detail = str(exc).lower()
                if "message is not modified" in detail:
                    pass
                elif "message to edit not found" in detail or "message can't be edited" in detail:
                    message_id = None
                else:
                    raise
        if not message_id:
            sent = await bot.send_message(message_thread_id=thread_id, **payload)
            message_id = sent.message_id
        await self.service.acknowledge_panel(game_key=game_key, chat_id=chat_id,
            thread_id=thread_id, round_id=state["round_id"],
            expected_version=state["version"], message_id=message_id)
        return True

    async def process(self, bot, row):
        state = row.get("state") or row
        game_key = state["game_key"]
        chat_id = int(row.get("chat_id", state["chat_id"]))
        topic = state.get("thread_id")
        await self.service.advance(game_key=game_key, chat_id=chat_id,
                                   thread_id=topic, round_id=state["round_id"])
        try:
            await self.service.settle_scores(game_key=game_key, chat_id=chat_id,
                                             thread_id=topic)
        except Exception as exc:  # durable idempotent score retry
            self.service.metrics.inc("clue_arcade_score_retry_total")
            logger.warning("Clue arcade score deferred: %s", type(exc).__name__)
        try:
            await self.publish(bot, game_key=game_key, chat_id=chat_id, thread_id=topic)
        except TelegramError as exc:
            self.service.metrics.inc("clue_arcade_panel_failure_total")
            logger.warning("Clue arcade panel deferred: %s", type(exc).__name__)

    async def _flush(self, bot, *, game_key, chat_id, thread_id):
        state = await self.service.current(game_key=game_key, chat_id=chat_id,
                                           thread_id=thread_id)
        if state:
            await self.process(bot, {"chat_id": chat_id, "state": state})

    async def command(self, update, context, game_key):
        if game_key not in GAME_KEYS:
            return
        chat, user, message = update.effective_chat, update.effective_user, update.effective_message
        if not chat or not user or not message:
            return
        topic = forum_thread_id(message) if chat.type in {"group", "supergroup"} else None
        args = list(getattr(context, "args", None) or [])
        command = args[0].casefold() if args else "open"
        aliases = {"katıl": "join", "katil": "join", "başla": "start", "basla": "start",
                   "ayrıl": "leave", "ayril": "leave", "bitir": "stop",
                   "iptal": "stop", "durum": "refresh"}
        try:
            if command in {"open", "aç", "ac"}:
                _, state = await self.service.open(game_key=game_key, chat_id=chat.id,
                    thread_id=topic, user_id=user.id, user_name=user.first_name,
                    solo=chat.type == "private")
                await self.publish(context.bot, game_key=game_key, chat_id=chat.id,
                                   thread_id=topic, force=bool(state.get("panel_message_id")))
                return
            state = await self.service.current(game_key=game_key, chat_id=chat.id,
                                               thread_id=topic)
            if not state:
                raise ValueError("ca_not_found")
            action = aliases.get(command)
            if action == "refresh":
                await self.publish(context.bot, game_key=game_key, chat_id=chat.id,
                                   thread_id=topic, force=True)
                return
            if action not in {"join", "start", "leave", "stop"}:
                await message.reply_text("Komutlar: katıl · başla · durum · ayrıl · bitir")
                return
            await self.service.act(game_key=game_key, chat_id=chat.id, thread_id=topic,
                user_id=user.id, user_name=user.first_name,
                action_id=f"cmd:{game_key}:{message.message_id}", action=action,
                round_id=state["round_id"], phase_seq=state["phase_seq"],
                moderator=await self._moderator(update, context) if action == "stop" else False,
                submitted_at=message.date.timestamp() if getattr(message, "date", None) else None)
            await self._flush(context.bot, game_key=game_key, chat_id=chat.id, thread_id=topic)
        except ValueError as exc:
            await message.reply_text(ui.error_message(str(exc)))
        except Exception as exc:  # noqa: BLE001 - safe user-facing failure
            logger.warning("Clue arcade command deferred: %s", type(exc).__name__)
            await message.reply_text("İşlem tamamlanamadı. Biraz sonra durum komutunu kullan.")

    async def callback(self, update, context) -> bool:
        query = getattr(update, "callback_query", None)
        if not query or not str(getattr(query, "data", "")).startswith("ca:"):
            return False
        try:
            game, chat_id, topic, round_id, seq, token = ui.parse_callback(query.data)
            state = await self.service.current(game_key=game, chat_id=chat_id, thread_id=topic)
            if not state or state["round_id"] != round_id or state["phase_seq"] != seq:
                raise ValueError("ca_stale_round")
            chat = update.effective_chat
            if (chat.id != chat_id or
                    (chat.type in {"group", "supergroup"} and forum_thread_id(query.message) != topic) or
                    int(query.message.message_id) != int(state.get("panel_message_id") or 0)):
                raise ValueError("ca_stale_panel")
            if token == "r":
                await query.answer()
                await self.publish(context.bot, game_key=game, chat_id=chat_id,
                                   thread_id=topic, force=True)
                return True
            action = {"j": "join", "s": "start", "l": "leave",
                      "x": "stop", "p": "pass"}.get(token)
            if not action:
                raise ValueError("ca_invalid_action")
            user = update.effective_user
            await self.service.act(game_key=game, chat_id=chat_id, thread_id=topic,
                user_id=user.id, user_name=user.first_name,
                action_id=f"cb:{query.id}", action=action,
                round_id=round_id, phase_seq=seq,
                moderator=await self._moderator(update, context) if action == "stop" else False)
            await query.answer()
            await self._flush(context.bot, game_key=game, chat_id=chat_id, thread_id=topic)
        except (ValueError, TypeError) as exc:
            await query.answer(ui.error_message(str(exc)), show_alert=True)
        except Exception as exc:  # noqa: BLE001 - callback must be answered
            logger.warning("Clue arcade callback deferred: %s", type(exc).__name__)
            try:
                await query.answer("İşlem tamamlanamadı. Güncel paneli kullan.", show_alert=True)
            except TelegramError:
                pass
        return True

    async def on_text(self, update, context, text) -> bool:
        chat, user, message = update.effective_chat, update.effective_user, update.effective_message
        if not chat or not user or not message or chat.type not in {"private", "group", "supergroup"}:
            return False
        topic = forum_thread_id(message) if chat.type in {"group", "supergroup"} else None
        states = []
        for game in GAME_KEYS:
            state = await self.service.current(game_key=game, chat_id=chat.id, thread_id=topic)
            if not state or not self.accepts_text(state) or state.get("deadline") is None:
                continue
            reply = getattr(message, "reply_to_message", None)
            if reply and int(reply.message_id) != int(state.get("panel_message_id") or 0):
                continue
            if state["solo"] and user.id != state["owner_id"]:
                continue
            states.append(state)
        if len(states) != 1:
            return False
        if getattr(update, "edited_message", None) is message:
            return True
        state = states[0]
        try:
            status, result = await self.service.act(game_key=state["game_key"],
                chat_id=chat.id, thread_id=topic, user_id=user.id,
                user_name=user.first_name, action_id=f"msg:{chat.id}:{message.message_id}",
                action="text", round_id=state["round_id"], phase_seq=state["phase_seq"],
                text=text, message_id=message.message_id,
                submitted_at=message.date.timestamp() if getattr(message, "date", None) else None)
            if status == "applied" and result.get("panel_dirty"):
                await self.publish(context.bot, game_key=state["game_key"],
                                   chat_id=chat.id, thread_id=topic)
        except ValueError:
            return True
        except TelegramError as exc:
            self.service.metrics.inc("clue_arcade_panel_failure_total")
            logger.warning("Clue arcade panel deferred: %s", type(exc).__name__)
        return True

    async def cancel_for(self, update, context) -> bool:
        chat, user, message = update.effective_chat, update.effective_user, update.effective_message
        if not chat or not user or not message or chat.type not in {"private", "group", "supergroup"}:
            return False
        topic = forum_thread_id(message) if chat.type in {"group", "supergroup"} else None
        cancelled = False
        for game in GAME_KEYS:
            state = await self.service.current(game_key=game, chat_id=chat.id, thread_id=topic)
            if not state or state["phase"] in rules.TERMINAL:
                continue
            moderator = await self._moderator(update, context) if user.id != state["owner_id"] else False
            if user.id != state["owner_id"] and not moderator:
                continue
            await self.service.act(game_key=game, chat_id=chat.id, thread_id=topic,
                user_id=user.id, action_id=f"cancel:{game}:{message.message_id}",
                action="stop", round_id=state["round_id"], moderator=moderator)
            await self.publish(context.bot, game_key=game, chat_id=chat.id, thread_id=topic)
            cancelled = True
        return cancelled
