"""Telegram adapter for the durable Turkish word-puzzle games."""
from __future__ import annotations

import logging

from telegram.error import BadRequest, TelegramError

from src.games import word_puzzles as games
from src.services.word_puzzles_ui import KEYS, error, keyboard, render_game
from src.storage.game_scope import forum_thread_id

from .handler_utils import is_admin

logger = logging.getLogger(__name__)
GAME_KEYS = games.GAME_KEYS


class WordPuzzlesHandler:
    GAME_KEYS = GAME_KEYS

    def __init__(self, service, sudo_manager=None, authorization=None):
        self.service = service
        self.sudo_manager = sudo_manager
        self.authorization = authorization

    async def publish(self, bot, *, game_key, chat_id, thread_id=None, force=False):
        state = await self.service.claim_panel(game_key=game_key, chat_id=chat_id,
                                               thread_id=thread_id, force=force)
        if not state:
            return False
        message_id = state.get("panel_message_id")
        payload = {"chat_id": int(chat_id), "text": render_game(state), "parse_mode": "HTML",
                   "reply_markup": keyboard(state)}
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

    async def _flush(self, bot, *, game_key, chat_id, thread_id):
        await self.publish(bot, game_key=game_key, chat_id=chat_id, thread_id=thread_id)

    async def process(self, bot, row):
        state = row.get("state") or row
        game_key = state["game_key"]
        chat_id = int(row.get("chat_id", state["chat_id"]))
        thread_id = state.get("thread_id")
        await self.service.advance(game_key=game_key, chat_id=chat_id,
                                   thread_id=thread_id, round_id=state["round_id"])
        try:
            await self.service.settle_scores(game_key=game_key, chat_id=chat_id,
                                             thread_id=thread_id)
        except Exception as exc:  # noqa: BLE001 - durable score_pending retries
            self.service.metrics.inc("game_score_retry_total")
            logger.warning("Word puzzle rewards deferred: %s", type(exc).__name__)
        try:
            await self._flush(bot, game_key=game_key, chat_id=chat_id, thread_id=thread_id)
        except TelegramError as exc:
            self.service.metrics.inc("game_panel_delivery_failure_total")
            logger.warning("Word puzzle panel deferred: %s", type(exc).__name__)

    async def command(self, update, context, game_key: str):
        if game_key not in GAME_KEYS:
            return
        chat, user, message = update.effective_chat, update.effective_user, update.effective_message
        if not chat or not user or not message:
            return
        if chat.type not in {"group", "supergroup"}:
            await message.reply_text("Bu oyunları bir grupta açabilirsin.")
            return
        thread_id = forum_thread_id(message)
        args = list(getattr(context, "args", None) or [])
        command = args[0].casefold() if args else "open"
        aliases = {"katıl": "join", "katil": "join", "başla": "start", "basla": "start",
                   "ayrıl": "leave", "ayril": "leave", "bitir": "stop",
                   "iptal": "stop", "ipucu": "hint", "durum": "refresh"}
        try:
            if command in {"open", "aç", "ac"}:
                await self.service.open(game_key=game_key, chat_id=chat.id, thread_id=thread_id,
                                        user_id=user.id, user_name=user.first_name)
                await self._flush(context.bot, game_key=game_key, chat_id=chat.id,
                                  thread_id=thread_id)
                return
            state = await self.service.current(game_key=game_key, chat_id=chat.id,
                                               thread_id=thread_id)
            if not state:
                raise ValueError("word_puzzles_not_found")
            operation = aliases.get(command)
            if not operation:
                await message.reply_text("Komutlar: katıl · başla · ipucu · durum · ayrıl · bitir")
                return
            if operation == "refresh":
                await self.publish(context.bot, game_key=game_key, chat_id=chat.id,
                                   thread_id=thread_id, force=True)
                return
            await self.service.act(game_key=game_key, chat_id=chat.id, thread_id=thread_id,
                user_id=user.id, user_name=user.first_name,
                action_id=f"cmd:{game_key}:{chat.id}:{message.message_id}",
                action=operation, round_id=state["round_id"], phase_seq=state["phase_seq"],
                moderator=await is_admin(update, context, self.sudo_manager,
                    authorization=self.authorization) if operation == "stop" else False,
                submitted_at=message.date.timestamp() if getattr(message, "date", None) else None)
            await self._flush(context.bot, game_key=game_key, chat_id=chat.id,
                              thread_id=thread_id)
        except ValueError as exc:
            await message.reply_text(error(str(exc)))
        except Exception as exc:  # noqa: BLE001 - safe user-facing failure
            logger.warning("Word puzzle command deferred: %s", type(exc).__name__)
            await message.reply_text("İşlem tamamlanamadı. Biraz sonra durum komutunu kullan.")

    async def callback(self, update, context) -> bool:
        query = getattr(update, "callback_query", None)
        if not query or not str(getattr(query, "data", "")).startswith("wp:"):
            return False
        try:
            parts = str(query.data).split(":")
            if len(parts) != 7 or parts[1] not in KEYS:
                raise ValueError("word_puzzles_invalid_action")
            _, code, chat_code, topic_code, round_id, seq_code, action = parts
            game_key = KEYS[code]
            chat_id = -int(chat_code, 36)
            thread_id = int(topic_code, 36) or None
            phase_seq = int(seq_code, 36)
            if (update.effective_chat.type not in {"group", "supergroup"} or
                    update.effective_chat.id != chat_id or
                    forum_thread_id(query.message) != thread_id):
                raise ValueError("word_puzzles_stale_round")
            state = await self.service.current(game_key=game_key, chat_id=chat_id,
                                               thread_id=thread_id)
            if (not state or state["round_id"] != round_id or
                    state["phase_seq"] != phase_seq or
                    int(state.get("panel_message_id") or 0) != int(query.message.message_id)):
                raise ValueError("word_puzzles_stale_panel")
            if action == "refresh":
                await query.answer()
                await self.publish(context.bot, game_key=game_key, chat_id=chat_id,
                                   thread_id=thread_id, force=True)
                return True
            if action not in {"join", "start", "leave", "stop", "hint"}:
                raise ValueError("word_puzzles_invalid_action")
            user = update.effective_user
            await self.service.act(game_key=game_key, chat_id=chat_id, thread_id=thread_id,
                user_id=user.id, user_name=user.first_name,
                action_id=f"cb:{game_key}:{query.id}", action=action,
                round_id=round_id, phase_seq=phase_seq,
                moderator=await is_admin(update, context, self.sudo_manager,
                    authorization=self.authorization) if action == "stop" else False)
            await query.answer()
            await self._flush(context.bot, game_key=game_key, chat_id=chat_id,
                              thread_id=thread_id)
        except (ValueError, TypeError) as exc:
            await query.answer(error(str(exc)), show_alert=True)
        except Exception as exc:  # noqa: BLE001 - callback must be answered
            logger.warning("Word puzzle callback deferred: %s", type(exc).__name__)
            try:
                await query.answer("İşlem tamamlanamadı. Güncel paneli kullan.", show_alert=True)
            except TelegramError:
                pass
        return True

    async def on_text(self, update, context, text) -> bool:
        chat, user, message = update.effective_chat, update.effective_user, update.effective_message
        if (not chat or not user or not message or chat.type not in {"group", "supergroup"} or
                getattr(update, "edited_message", None) is not None):
            return False
        thread_id = forum_thread_id(message)
        reply = getattr(message, "reply_to_message", None)
        candidates = []
        for game_key in GAME_KEYS:
            state = await self.service.current(game_key=game_key, chat_id=chat.id,
                                               thread_id=thread_id)
            if not state or state["phase"] != "playing":
                continue
            if reply and int(reply.message_id) != int(state.get("panel_message_id") or 0):
                continue
            if any(p["id"] == user.id and p["active"] for p in state["players"]):
                candidates.append(state)
        if len(candidates) != 1:
            return False
        state = candidates[0]
        try:
            status, _ = await self.service.act(game_key=state["game_key"], chat_id=chat.id,
                thread_id=thread_id, user_id=user.id,
                action_id=f"msg:{chat.id}:{message.message_id}", action="text",
                round_id=state["round_id"], phase_seq=state["phase_seq"], text=text,
                message_id=message.message_id,
                submitted_at=message.date.timestamp() if getattr(message, "date", None) else None)
            if status == "applied":
                await self._flush(context.bot, game_key=state["game_key"], chat_id=chat.id,
                                  thread_id=thread_id)
            return True
        except ValueError:
            # A participant's answer belongs to this active game even when it
            # is invalid or stale. Do not let it fall through to AI routing.
            return True
        except TelegramError as exc:
            self.service.metrics.inc("game_panel_delivery_failure_total")
            logger.warning("Word puzzle panel deferred: %s", type(exc).__name__)
            return True

    async def cancel_for(self, update, context) -> bool:
        chat, user, message = update.effective_chat, update.effective_user, update.effective_message
        if not chat or not user or not message or chat.type not in {"group", "supergroup"}:
            return False
        topic = forum_thread_id(message)
        cancelled = False
        for game_key in GAME_KEYS:
            state = await self.service.current(game_key=game_key, chat_id=chat.id, thread_id=topic)
            if not state or state["phase"] in {"finished", "cancelled"}:
                continue
            moderator = (await is_admin(update, context, self.sudo_manager,
                authorization=self.authorization)) if user.id != state["owner_id"] else False
            if not moderator and user.id != state["owner_id"]:
                continue
            await self.service.act(game_key=game_key, chat_id=chat.id, thread_id=topic,
                user_id=user.id, action_id=f"cancel:{game_key}:{message.message_id}",
                action="stop", round_id=state["round_id"], moderator=moderator)
            await self._flush(context.bot, game_key=game_key, chat_id=chat.id, thread_id=topic)
            cancelled = True
        return cancelled
