"""Telegram adapters for Sudoku, number league and reflex."""
from __future__ import annotations

import logging

from telegram.error import BadRequest, TelegramError

from src.games import logic_puzzles as rules
from src.services import logic_puzzles_ui as ui
from src.storage.game_scope import forum_thread_id
from .handler_utils import is_admin

logger = logging.getLogger(__name__)
GAME_KEYS = rules.GAME_KEYS


class LogicPuzzlesHandler:
    GAME_KEYS = GAME_KEYS

    def __init__(self, service, sudo_manager=None, authorization=None):
        self.service = service
        self.sudo_manager = sudo_manager
        self.authorization = authorization

    async def _moderator(self, update, context):
        return await is_admin(update, context, self.sudo_manager,
                              authorization=self.authorization)

    async def publish(self, bot, *, game_key, chat_id, thread_id=None, force=False):
        state = await self.service.claim_panel(game_key=game_key, chat_id=chat_id,
                                               thread_id=thread_id, force=force)
        if not state:
            return
        message_id = state.get("panel_message_id")
        kwargs = {"chat_id": int(chat_id), "text": ui.render(state),
                  "parse_mode": "HTML", "reply_markup": ui.keyboard(state)}
        if message_id:
            try:
                await bot.edit_message_text(message_id=int(message_id), **kwargs)
            except BadRequest as exc:
                message = str(exc).lower()
                if "message is not modified" in message:
                    pass
                elif "message to edit not found" in message or "message can't be edited" in message:
                    message_id = None
                else:
                    raise
        if not message_id:
            sent = await bot.send_message(message_thread_id=thread_id, **kwargs)
            message_id = sent.message_id
        await self.service.acknowledge_panel(game_key=game_key, chat_id=chat_id,
                                             thread_id=thread_id, round_id=state["round_id"],
                                             expected_version=state["version"], message_id=message_id)

    async def process(self, bot, row):
        state = row["state"]
        game_key = state["game_key"]
        chat_id = int(row["chat_id"])
        thread_id = state.get("thread_id")
        try:
            await self.service.advance(game_key=game_key, chat_id=chat_id,
                                       thread_id=thread_id, round_id=state["round_id"],
                                       expected_phase=state["phase"],
                                       expected_deadline=state.get("deadline"),
                                       expected_round_no=state.get("round_no", 0),
                                       expected_version=state["version"])
        except ValueError:
            return
        try:
            await self.service.settle_scores(game_key=game_key, chat_id=chat_id,
                                             thread_id=thread_id)
        except Exception as exc:  # retryable idempotent ledger effect
            logger.warning("Logic puzzle rewards deferred: %s", type(exc).__name__)
        # Preparation and actionable panels must arrive in the same request;
        # otherwise players wait for the next five-second recovery tick.
        for _ in range(3):
            await self.publish(bot, game_key=game_key, chat_id=chat_id, thread_id=thread_id)
            current = await self.service.current(game_key=game_key, chat_id=chat_id,
                                                 thread_id=thread_id)
            if not current or not current.get("panel_dirty"):
                break

    async def _flush(self, context, *, game_key, chat_id, thread_id):
        state = await self.service.current(game_key=game_key, chat_id=chat_id,
                                           thread_id=thread_id)
        if state:
            await self.process(context.bot, {"chat_id": chat_id, "state": state})

    async def command(self, update, context, game_key: str):
        if game_key not in GAME_KEYS:
            return
        chat, user, message = update.effective_chat, update.effective_user, update.effective_message
        if not chat or not user or not message:
            return
        if chat.type not in {"group", "supergroup"}:
            await message.reply_text("Bu oyun için gruptaki oyun salonunu kullan.")
            return
        topic = forum_thread_id(message)
        args = list(getattr(context, "args", None) or [])
        command = args[0].lower() if args else "open"
        aliases = {"katıl": "join", "katil": "join", "başla": "start", "basla": "start",
                   "bitir": "stop", "durum": "status"}
        action = aliases.get(command, command)
        try:
            if action in {"open", "status", "4", "9"}:
                if action == "status":
                    state = await self.service.current(game_key=game_key, chat_id=chat.id,
                                                       thread_id=topic)
                    if not state:
                        raise ValueError("lp_not_found")
                    await self.publish(context.bot, game_key=game_key, chat_id=chat.id,
                                       thread_id=topic, force=True)
                else:
                    size = int(action) if action in {"4", "9"} else (
                        int(args[1]) if game_key == "sudoku" and len(args) > 1 and args[1] in {"4", "9"} else 4)
                    _, state = await self.service.open(game_key=game_key, chat_id=chat.id,
                        thread_id=topic, user_id=user.id, user_name=user.first_name, size=size)
                    await self.publish(context.bot, game_key=game_key, chat_id=chat.id,
                                       thread_id=topic, force=bool(state.get("panel_message_id")))
                return
            state = await self.service.current(game_key=game_key, chat_id=chat.id,
                                               thread_id=topic)
            if not state:
                raise ValueError("lp_not_found")
            if action not in {"join", "start", "stop"}:
                await message.reply_text("Komutlar: aç, katıl, başla, durum, bitir. Sudoku: 4 veya 9.")
                return
            await self.service.act(game_key=game_key, chat_id=chat.id, thread_id=topic,
                user_id=user.id, user_name=user.first_name,
                action_id=f"cmd:{message.message_id}", action=action,
                round_id=state["round_id"], round_no=state.get("round_no", 0),
                moderator=await self._moderator(update, context) if action == "stop" else False)
            await self._flush(context, game_key=game_key, chat_id=chat.id, thread_id=topic)
        except ValueError as exc:
            await message.reply_text(ui.error_message(str(exc)))
        except Exception as exc:
            logger.warning("Logic puzzle command deferred: %s", type(exc).__name__)
            await message.reply_text("Oyun işlemi tamamlanamadı. Güncel paneli tekrar aç.")

    async def callback(self, update, context) -> bool:
        query = getattr(update, "callback_query", None)
        if query is None or not str(getattr(query, "data", "")).startswith("lp:"):
            return False
        try:
            game, chat_id, topic, round_id, round_no, code = ui.parse_callback(query.data)
            if update.effective_chat.id != chat_id or forum_thread_id(query.message) != topic:
                raise ValueError("lp_stale_round")
            state = await self.service.current(game_key=game, chat_id=chat_id, thread_id=topic)
            if (not state or state["round_id"] != round_id or
                    state.get("round_no", 0) != round_no or
                    int(state.get("panel_message_id") or 0) != int(query.message.message_id)):
                raise ValueError("lp_stale_panel")
            if code == "a":
                await query.answer("Yeni oyun için oyun salonunu aç.", show_alert=True)
                return True
            action = {"j": "join", "s": "start", "u": "undo", "h": "hint",
                      "n": "next", "x": "stop", "e": "press",
                      "z": "code_clear", "t": "code_submit"}.get(code)
            kwargs = {}
            if code.startswith("c") and len(code) == 3:
                action = "select"
                kwargs.update(row=int(code[1], 36), col=int(code[2], 36))
            elif code.startswith("r") and len(code) == 2:
                action = "focus_row"
                kwargs["row"] = int(code[1], 36)
            elif code.startswith("b") and len(code) == 2:
                action = "focus_band"
                kwargs["row"] = int(code[1], 36)
            elif code.startswith("d") and len(code) == 4:
                action = "digit"
                kwargs["value"] = int(code[1], 36)
                kwargs["row"] = int(code[2], 36)
                kwargs["col"] = int(code[3], 36)
            elif code.startswith("p") and len(code) == 2:
                action = "press"
                index = int(code[1], 36)
                if not 0 <= index < len(rules.SYMBOLS):
                    raise ValueError("lp_invalid_action")
                kwargs["symbol"] = rules.SYMBOLS[index]
            elif code.startswith("k") and len(code) == 2:
                action = "code_digit"
                kwargs["value"] = code[1]
            elif code.startswith("f") and len(code) == 2:
                action = "spot_pick"
                kwargs["value"] = int(code[1], 36)
            if action is None:
                raise ValueError("lp_invalid_action")
            await self.service.act(game_key=game, chat_id=chat_id, thread_id=topic,
                user_id=update.effective_user.id, user_name=update.effective_user.first_name,
                action_id=f"cb:{query.id}", action=action, round_id=round_id,
                round_no=round_no,
                moderator=await self._moderator(update, context) if action == "stop" else False,
                **kwargs)
            await query.answer()
            await self._flush(context, game_key=game, chat_id=chat_id, thread_id=topic)
        except (ValueError, TypeError) as exc:
            await query.answer(ui.error_message(str(exc)), show_alert=True)
        except Exception as exc:
            logger.warning("Logic puzzle callback deferred: %s", type(exc).__name__)
            try:
                await query.answer("İşlem tamamlanamadı. Güncel paneli kullan.", show_alert=True)
            except TelegramError:
                pass
        return True

    async def on_text(self, update, context, text) -> bool:
        chat, user, message = update.effective_chat, update.effective_user, update.effective_message
        if not chat or not user or not message or chat.type not in {"group", "supergroup"}:
            return False
        content = str(text).strip()
        if not content.isascii() or not content.isdecimal() or len(content) > 4:
            return False
        topic = forum_thread_id(message)
        game = "sayisal_tahmin"
        state = await self.service.current(game_key=game, chat_id=chat.id, thread_id=topic)
        if not state or state["phase"] != "turn":
            return False
        if getattr(update, "edited_message", None) is message:
            return True
        try:
            await self.service.act(game_key=game, chat_id=chat.id, thread_id=topic,
                user_id=user.id, action_id=f"msg:{message.message_id}", action="guess",
                round_id=state["round_id"], round_no=state["round_no"], value=int(content),
                message_id=message.message_id,
                submitted_at=message.date.timestamp() if getattr(message, "date", None) else None)
            await self._flush(context, game_key=game, chat_id=chat.id, thread_id=topic)
        except ValueError:
            pass
        return True

    async def cancel_for(self, update, context) -> bool:
        chat, user, message = update.effective_chat, update.effective_user, update.effective_message
        if not chat or not user or not message:
            return False
        topic = forum_thread_id(message)
        cancelled = False
        for game in GAME_KEYS:
            state = await self.service.current(game_key=game, chat_id=chat.id, thread_id=topic)
            if not state or state["phase"] in rules.TERMINAL:
                continue
            if user.id != state["owner_id"] and not await self._moderator(update, context):
                continue
            await self.service.act(game_key=game, chat_id=chat.id, thread_id=topic,
                user_id=user.id, action_id=f"cancel:{message.message_id}:{game}",
                action="stop", round_id=state["round_id"],
                moderator=user.id != state["owner_id"])
            await self._flush(context, game_key=game, chat_id=chat.id, thread_id=topic)
            cancelled = True
        return cancelled
