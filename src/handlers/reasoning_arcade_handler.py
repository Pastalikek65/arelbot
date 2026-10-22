"""Telegram adapter for four durable, deterministic reasoning games."""

from __future__ import annotations

import logging

from telegram.error import BadRequest, TelegramError

from src.games import reasoning_arcade as rules
from src.services import reasoning_arcade_ui as ui
from src.storage.game_scope import forum_thread_id

from .handler_utils import is_admin

logger = logging.getLogger(__name__)
GAME_KEYS = rules.GAME_KEYS


class ReasoningArcadeHandler:
    GAME_KEYS = GAME_KEYS

    def __init__(self, service, sudo_manager=None, authorization=None):
        self.service = service
        self.sudo_manager = sudo_manager
        self.authorization = authorization

    async def _moderator(self, update, context):
        return await is_admin(update, context, self.sudo_manager,
                              authorization=self.authorization)

    @staticmethod
    def accepts_text(state) -> bool:
        return bool(state and state.get("game_key") == rules.DETECTIVE
                    and state.get("phase") == "playing")

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

    async def _flush(self, bot, *, game_key, chat_id, thread_id):
        await self.publish(bot, game_key=game_key, chat_id=chat_id, thread_id=thread_id)

    async def process(self, bot, row):
        state = row.get("state") or row
        game_key, chat_id = state["game_key"], int(row.get("chat_id", state["chat_id"]))
        topic = state.get("thread_id")
        try:
            await self.service.advance(game_key=game_key, chat_id=chat_id,
                                       thread_id=topic, round_id=state["round_id"])
        except ValueError:
            return
        try:
            await self.service.settle_scores(game_key=game_key, chat_id=chat_id,
                                             thread_id=topic)
        except Exception as exc:  # noqa: BLE001 - transport boundary retains durable retries
            logger.warning("Reasoning arcade rewards deferred: %s", type(exc).__name__)
        try:
            await self._flush(bot, game_key=game_key, chat_id=chat_id, thread_id=topic)
        except TelegramError as exc:
            logger.warning("Reasoning arcade panel deferred: %s", type(exc).__name__)

    async def command(self, update, context, game_key: str):
        if game_key not in GAME_KEYS:
            return
        chat, user, message = update.effective_chat, update.effective_user, update.effective_message
        if not chat or not user or not message:
            return
        if chat.type not in {"group", "supergroup"}:
            await message.reply_text("Bu oyunları gruptaki oyun salonundan aç.")
            return
        topic = forum_thread_id(message)
        args = list(getattr(context, "args", None) or [])
        operation = args[0].casefold() if args else "open"
        aliases = {"aç": "open", "ac": "open", "katıl": "join", "katil": "join",
                   "başla": "start", "basla": "start", "bitir": "stop",
                   "iptal": "stop", "durum": "status", "ayrıl": "leave",
                   "ayril": "leave", "çık": "leave", "cik": "leave"}
        operation = aliases.get(operation, operation)
        try:
            if operation == "open":
                await self.service.open(game_key=game_key, chat_id=chat.id,
                    thread_id=topic, user_id=user.id, user_name=user.first_name)
                await self._flush(context.bot, game_key=game_key,
                                  chat_id=chat.id, thread_id=topic)
                return
            state = await self.service.current(game_key=game_key, chat_id=chat.id,
                                               thread_id=topic)
            if not state:
                raise ValueError("ra_not_found")
            if operation == "status":
                await self.publish(context.bot, game_key=game_key,
                    chat_id=chat.id, thread_id=topic, force=True)
                return
            if operation not in {"join", "leave", "start", "stop"}:
                await message.reply_text("Komutlar: aç, katıl, ayrıl, başla, durum, bitir.")
                return
            await self.service.act(game_key=game_key, chat_id=chat.id, thread_id=topic,
                user_id=user.id, user_name=user.first_name,
                action_id=f"cmd:{game_key}:{message.message_id}", action=operation,
                round_id=state["round_id"], phase_seq=state["phase_seq"],
                moderator=await self._moderator(update, context) if operation == "stop" else False)
            await self._flush(context.bot, game_key=game_key,
                              chat_id=chat.id, thread_id=topic)
        except ValueError as exc:
            await message.reply_text(ui.error_message(str(exc)))
        except Exception as exc:  # noqa: BLE001 - transport boundary retains durable retries
            logger.warning("Reasoning arcade command deferred: %s", type(exc).__name__)
            await message.reply_text("İşlem tamamlanamadı. Güncel paneli tekrar aç.")

    async def callback(self, update, context) -> bool:
        query = getattr(update, "callback_query", None)
        if query is None or not str(getattr(query, "data", "")).startswith("ra:"):
            return False
        try:
            game, chat_id, topic, round_id, seq, code = ui.parse_callback(query.data)
            if (update.effective_chat.type not in {"group", "supergroup"}
                    or update.effective_chat.id != chat_id
                    or forum_thread_id(query.message) != topic):
                raise ValueError("ra_stale_round")
            state = await self.service.current(game_key=game, chat_id=chat_id,
                                               thread_id=topic)
            if (not state or state["round_id"] != round_id or state["phase_seq"] != seq
                    or int(state.get("panel_message_id") or 0) != int(query.message.message_id)):
                raise ValueError("ra_stale_panel")
            action = {"j": "join", "l": "leave", "s": "start", "x": "stop", "z": "clear",
                      "o": "confirm", "h": "hint"}.get(code)
            kwargs = {}
            if len(code) == 2 and code[0] == "r" and code[1] in "12345":
                action, kwargs["value"] = "rank", int(code[1])
            elif len(code) == 2 and code[0] == "c" and code[1] in "01234":
                action, kwargs["value"] = "choose", int(code[1])
            elif code in {"tk", "fk", "tr", "fr"}:
                action = "vote"
                kwargs.update(answer=code[0] == "t", risk=code[1] == "r")
            if action is None:
                raise ValueError("ra_invalid_action")
            user = update.effective_user
            _, updated = await self.service.act(
                game_key=game, chat_id=chat_id, thread_id=topic,
                user_id=user.id, user_name=user.first_name,
                action_id=f"cb:{query.id}", action=action,
                round_id=round_id, phase_seq=seq,
                moderator=await self._moderator(update, context) if action == "stop" else False,
                **kwargs)
            feedback = None
            if action in {"choose", "clear"}:
                order = updated.get("selections", {}).get(str(user.id), [])
                feedback = "Sıran: " + (" → ".join(str(index + 1) for index in order) or "boş")
            await query.answer(feedback)
            if updated.get("panel_dirty"):
                await self._flush(context.bot, game_key=game,
                                  chat_id=chat_id, thread_id=topic)
        except (ValueError, TypeError) as exc:
            await query.answer(ui.error_message(str(exc)), show_alert=True)
        except Exception as exc:  # noqa: BLE001 - transport boundary retains durable retries
            logger.warning("Reasoning arcade callback deferred: %s", type(exc).__name__)
            try:
                await query.answer("İşlem tamamlanamadı. Güncel paneli kullan.", show_alert=True)
            except TelegramError:
                pass
        return True

    async def on_text(self, update, context, text) -> bool:
        chat, user, message = update.effective_chat, update.effective_user, update.effective_message
        if (not chat or not user or not message or chat.type not in {"group", "supergroup"}
                or getattr(update, "edited_message", None) is not None):
            return False
        topic = forum_thread_id(message)
        state = await self.service.current(game_key=rules.DETECTIVE,
                                           chat_id=chat.id, thread_id=topic)
        if (not self.accepts_text(state) or state.get("deadline") is None
                or not any(p["id"] == user.id and p.get("active", True)
                           for p in state["players"])):
            return False
        if "/" not in str(text):
            return False
        reply = getattr(message, "reply_to_message", None)
        if reply and int(reply.message_id) != int(state.get("panel_message_id") or 0):
            return False
        try:
            await self.service.act(game_key=rules.DETECTIVE, chat_id=chat.id,
                thread_id=topic, user_id=user.id, action_id=f"msg:{message.message_id}",
                action="accuse", text=text, round_id=state["round_id"],
                phase_seq=state["phase_seq"], message_id=message.message_id,
                submitted_at=message.date.timestamp() if getattr(message, "date", None) else None)
            await self._flush(context.bot, game_key=rules.DETECTIVE,
                              chat_id=chat.id, thread_id=topic)
        except ValueError:
            return True
        except TelegramError as exc:
            logger.warning("Reasoning arcade panel deferred: %s", type(exc).__name__)
        return True

    async def cancel_for(self, update, context) -> bool:
        chat, user, message = update.effective_chat, update.effective_user, update.effective_message
        if not chat or not user or not message or chat.type not in {"group", "supergroup"}:
            return False
        topic = forum_thread_id(message)
        cancelled = False
        for game in GAME_KEYS:
            state = await self.service.current(game_key=game,
                                               chat_id=chat.id, thread_id=topic)
            if not state or state["phase"] in rules.TERMINAL:
                continue
            moderator = await self._moderator(update, context) if user.id != state["owner_id"] else False
            if user.id != state["owner_id"] and not moderator:
                continue
            await self.service.act(game_key=game, chat_id=chat.id, thread_id=topic,
                user_id=user.id, action_id=f"cancel:{game}:{message.message_id}",
                action="stop", round_id=state["round_id"], moderator=moderator)
            await self._flush(context.bot, game_key=game,
                              chat_id=chat.id, thread_id=topic)
            cancelled = True
        return cancelled
