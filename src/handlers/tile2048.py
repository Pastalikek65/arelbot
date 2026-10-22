"""Telegram adapter for the persistent solo 2048 match."""

from __future__ import annotations

import logging

from telegram.error import BadRequest, TelegramError

from src.games import tile2048 as rules
from src.storage.game_scope import forum_thread_id
from src.ui import tile2048_ui as ui

from .handler_utils import is_admin

logger = logging.getLogger(__name__)
GAME_KEYS = rules.GAME_KEYS


class Tile2048Handler:
    GAME_KEYS = GAME_KEYS

    def __init__(self, service, sudo_manager=None, authorization=None):
        self.service = service
        self.sudo_manager = sudo_manager
        self.authorization = authorization

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
        chat_id = int(row.get("chat_id", state["chat_id"]))
        topic = state.get("thread_id")
        try:
            await self.service.advance(game_key=rules.GAME_KEY, chat_id=chat_id,
                                       thread_id=topic, round_id=state["round_id"])
        except ValueError:
            return
        try:
            await self.service.settle_scores(game_key=rules.GAME_KEY,
                                             chat_id=chat_id, thread_id=topic)
        except Exception as exc:  # noqa: BLE001 - persistent score_pending retries
            logger.warning("2048 reward deferred: %s", type(exc).__name__)
        try:
            await self.publish(bot, game_key=rules.GAME_KEY,
                               chat_id=chat_id, thread_id=topic)
        except TelegramError as exc:
            logger.warning("2048 panel deferred: %s", type(exc).__name__)

    async def _flush(self, bot, *, chat_id, thread_id):
        state = await self.service.current(game_key=rules.GAME_KEY,
                                           chat_id=chat_id, thread_id=thread_id)
        if state:
            await self.process(bot, {"chat_id": chat_id, "state": state})

    async def command(self, update, context, game_key: str):
        if game_key != rules.GAME_KEY:
            return
        chat, user, message = update.effective_chat, update.effective_user, update.effective_message
        if not chat or not user or not message:
            return
        topic = forum_thread_id(message)
        args = list(getattr(context, "args", None) or [])
        operation = args[0].casefold() if args else "open"
        operation = {"aç": "open", "ac": "open", "durum": "status",
                     "bitir": "stop", "iptal": "stop"}.get(operation, operation)
        try:
            if operation == "open":
                await self.service.open(game_key=game_key, chat_id=chat.id,
                    thread_id=topic, user_id=user.id, user_name=user.first_name)
                await self._flush(context.bot, chat_id=chat.id, thread_id=topic)
                return
            state = await self.service.current(game_key=game_key, chat_id=chat.id,
                                               thread_id=topic)
            if not state:
                raise ValueError("t2_not_found")
            if operation == "status":
                await self.publish(context.bot, game_key=game_key,
                    chat_id=chat.id, thread_id=topic, force=True)
                return
            if operation != "stop":
                await message.reply_text("Komutlar: /2048, /2048 durum, /2048 bitir.")
                return
            await self.service.act(game_key=game_key, chat_id=chat.id,
                thread_id=topic, user_id=user.id, action_id=f"cmd:{message.message_id}",
                action="stop", round_id=state["round_id"], phase_seq=state["phase_seq"],
                moderator=await self._moderator(update, context))
            await self._flush(context.bot, chat_id=chat.id, thread_id=topic)
        except ValueError as exc:
            await message.reply_text(ui.error_message(str(exc)))
        except Exception as exc:  # noqa: BLE001 - Telegram boundary stays generic
            logger.warning("2048 command deferred: %s", type(exc).__name__)
            await message.reply_text("İşlem tamamlanamadı. Güncel paneli tekrar aç.")

    async def callback(self, update, context) -> bool:
        query = getattr(update, "callback_query", None)
        if query is None or not str(getattr(query, "data", "")).startswith("t2:"):
            return False
        try:
            chat_id, topic, round_id, seq, code = ui.parse_callback(query.data)
            if (update.effective_chat.id != chat_id
                    or forum_thread_id(query.message) != topic):
                raise ValueError("t2_stale_panel")
            state = await self.service.current(game_key=rules.GAME_KEY,
                                               chat_id=chat_id, thread_id=topic)
            if (not state or state["round_id"] != round_id or
                    state["phase_seq"] != seq or
                    int(state.get("panel_message_id") or 0) != int(query.message.message_id)):
                raise ValueError("t2_stale_panel")
            directions = {"u": "up", "d": "down", "l": "left", "r": "right"}
            action = "move" if code in directions else "cashout" if code == "c" else "stop"
            await self.service.act(game_key=rules.GAME_KEY, chat_id=chat_id,
                thread_id=topic, user_id=update.effective_user.id,
                action_id=f"cb:{query.id}", action=action,
                round_id=round_id, phase_seq=seq,
                direction=directions.get(code),
                moderator=await self._moderator(update, context) if action == "stop" else False)
            await query.answer()
            try:
                await self._flush(context.bot, chat_id=chat_id, thread_id=topic)
            except TelegramError as exc:
                logger.warning("2048 panel deferred: %s", type(exc).__name__)
        except (ValueError, TypeError) as exc:
            await query.answer(ui.error_message(str(exc)), show_alert=True)
        except Exception as exc:  # noqa: BLE001 - Telegram boundary stays generic
            logger.warning("2048 callback deferred: %s", type(exc).__name__)
            try:
                await query.answer("İşlem tamamlanamadı. Güncel paneli kullan.", show_alert=True)
            except TelegramError:
                pass
        return True

    async def on_text(self, update, context, text) -> bool:
        return False

    async def cancel_for(self, update, context) -> bool:
        chat, user, message = update.effective_chat, update.effective_user, update.effective_message
        if not chat or not user or not message:
            return False
        topic = forum_thread_id(message)
        state = await self.service.current(game_key=rules.GAME_KEY,
                                           chat_id=chat.id, thread_id=topic)
        if not state or state["phase"] in rules.TERMINAL:
            return False
        moderator = await self._moderator(update, context) if user.id != state["owner_id"] else False
        if user.id != state["owner_id"] and not moderator:
            return False
        await self.service.act(game_key=rules.GAME_KEY, chat_id=chat.id,
            thread_id=topic, user_id=user.id,
            action_id=f"cancel:{message.message_id}:2048", action="stop",
            round_id=state["round_id"], phase_seq=state["phase_seq"], moderator=moderator)
        await self._flush(context.bot, chat_id=chat.id, thread_id=topic)
        return True
