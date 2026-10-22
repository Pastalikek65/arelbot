"""Telegram adapter for the persistent memory and mines grid games."""

from __future__ import annotations

import logging

from telegram.error import BadRequest

from src.games import grid_puzzles as grid
from src.services.grid_puzzles_ui import (CODE_ACTION, CODE_GAME, error_text,
                                          keyboard, render)
from src.storage.game_scope import forum_thread_id
from .handler_utils import is_admin

logger = logging.getLogger(__name__)
GAME_KEYS = grid.GAME_KEYS


class GridPuzzlesHandler:
    def __init__(self, service, sudo_manager=None, authorization=None):
        self.service = service
        self.sudo_manager = sudo_manager
        self.authorization = authorization

    async def _moderator(self, update, context):
        return await is_admin(update, context, self.sudo_manager, authorization=self.authorization)

    async def publish(self, bot, *, game_key, chat_id, thread_id=None, force=False):
        state = await self.service.claim_delivery(game_key=game_key,
            chat_id=chat_id, thread_id=thread_id, force=force)
        if not state:
            return
        message_id = state.get("panel_message_id")
        kwargs = dict(chat_id=chat_id, text=render(state), parse_mode="HTML",
                      reply_markup=keyboard(state))
        if message_id:
            try:
                await bot.edit_message_text(message_id=int(message_id), **kwargs)
            except BadRequest as exc:
                value = str(exc).lower()
                if "message is not modified" not in value:
                    if not any(part in value for part in ("message to edit not found", "message can't be edited")):
                        raise
                    message_id = None
        if not message_id:
            sent = await bot.send_message(message_thread_id=thread_id, **kwargs)
            message_id = sent.message_id
        await self.service.acknowledge_panel(game_key=game_key, chat_id=chat_id,
            thread_id=thread_id, round_id=state["round_id"],
            expected_version=state["version"], message_id=message_id)

    async def process(self, bot, row):
        chat_id, state = int(row["chat_id"]), row["state"]
        game_key, thread_id = state["game_key"], state.get("thread_id")
        try:
            await self.service.advance(game_key=game_key, chat_id=chat_id,
                                       thread_id=thread_id, round_id=state["round_id"])
        except ValueError as exc:
            if str(exc) == "grid_stale_round":
                return
            raise
        try:
            await self.service.settle_scores(game_key=game_key, chat_id=chat_id,
                                             thread_id=thread_id)
        except Exception as exc:
            self.service.metrics.inc("game_score_retry_total")
            logger.warning("Izgara oyunu puanı ertelendi: %s", type(exc).__name__)
        await self.publish(bot, game_key=game_key, chat_id=chat_id, thread_id=thread_id)

    async def _flush(self, context, game_key, chat_id, thread_id):
        state = await self.service.current(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
        if state:
            await self.process(context.bot, {"chat_id": chat_id, "state": state})

    async def command(self, update, context, game_key: str):
        if game_key not in GAME_KEYS:
            return
        chat, user, message = update.effective_chat, update.effective_user, update.effective_message
        if not chat or not user or not message:
            return
        if chat.type not in {"group", "supergroup"}:
            await message.reply_text("Bu oyunu bir grupta başlatabilirsin.")
            return
        thread_id = forum_thread_id(message)
        args = list(getattr(context, "args", None) or [])
        verb = args[0].lower() if args else "open"
        try:
            if verb in {"open", "durum"}:
                if verb == "durum":
                    state = await self.service.current(game_key=game_key, chat_id=chat.id,
                                                       thread_id=thread_id)
                    if not state:
                        raise ValueError("grid_not_found")
                else:
                    _, state = await self.service.open(game_key=game_key, chat_id=chat.id,
                        thread_id=thread_id, user_id=user.id, user_name=user.first_name)
                await self.publish(context.bot, game_key=game_key, chat_id=chat.id,
                                   thread_id=thread_id, force=bool(state.get("panel_message_id")))
            else:
                actions = {"katil": "join", "başla": "start", "basla": "start",
                           "bitir": "cancel"}
                action = actions.get(verb)
                if action is None or (game_key == grid.MINES_KEY and action != "cancel"):
                    await message.reply_text("Oyun panelindeki düğmeleri kullan. /hafiza veya /mayin durum ile paneli açabilirsin.")
                    return
                state = await self.service.current(game_key=game_key, chat_id=chat.id,
                                                   thread_id=thread_id)
                if not state:
                    raise ValueError("grid_not_found")
                await self.service.act(game_key=game_key, chat_id=chat.id,
                    thread_id=thread_id, user_id=user.id, user_name=user.first_name,
                    action_id=f"cmd:{message.message_id}", action=action,
                    round_id=state["round_id"], phase_seq=state["phase_seq"],
                    turn_seq=state["turn_seq"],
                    moderator=await self._moderator(update, context) if action == "cancel" else False)
            await self._flush(context, game_key, chat.id, thread_id)
        except ValueError as exc:
            await message.reply_text(error_text(str(exc)))
        except Exception as exc:
            logger.warning("Izgara oyunu komutu ertelendi: %s", type(exc).__name__)
            await message.reply_text("İşlem tamamlanamadı. Biraz sonra oyun durumunu yenile.")

    async def callback(self, update, context) -> bool:
        query = getattr(update, "callback_query", None)
        if not query or not str(query.data).startswith("gp:"):
            return False
        try:
            parts = str(query.data).split(":")
            if len(parts) != 9:
                raise ValueError("grid_invalid_action")
            _, game_code, chat_code, topic_code, round_id, phase_code, turn_code, action_code, cell_code = parts
            game_key, action = CODE_GAME[game_code], CODE_ACTION[action_code]
            chat_id = -int(chat_code, 36)
            thread_id = int(topic_code, 36) or None
            phase_seq, turn_seq = int(phase_code, 36), int(turn_code, 36)
            index = None if cell_code == "-" else int(cell_code, 36)
            if (update.effective_chat.type not in {"group", "supergroup"} or
                    update.effective_chat.id != chat_id or
                    forum_thread_id(query.message) != thread_id):
                raise ValueError("grid_stale_round")
            state = await self.service.current(game_key=game_key, chat_id=chat_id,
                                               thread_id=thread_id)
            if not state or state["round_id"] != round_id:
                raise ValueError("grid_stale_round")
            if (int(state.get("panel_message_id") or 0) != int(query.message.message_id)
                    or state["phase_seq"] != phase_seq or state["turn_seq"] != turn_seq):
                raise ValueError("grid_stale_phase")
            if action == "refresh":
                await self.publish(context.bot, game_key=game_key, chat_id=chat_id,
                                   thread_id=thread_id, force=True)
            else:
                if game_key == grid.MEMORY_KEY and action not in {"join", "start", "pick", "cancel"}:
                    raise ValueError("grid_invalid_action")
                if game_key == grid.MINES_KEY and action not in {"open", "flag", "mode", "cancel"}:
                    raise ValueError("grid_invalid_action")
                user = update.effective_user
                await self.service.act(game_key=game_key, chat_id=chat_id,
                    thread_id=thread_id, user_id=user.id, user_name=user.first_name,
                    action_id=f"cb:{query.id}", action=action,
                    round_id=round_id, phase_seq=phase_seq, turn_seq=turn_seq,
                    index=index, moderator=await self._moderator(update, context) if action == "cancel" else False)
            await query.answer()
            await self._flush(context, game_key, chat_id, thread_id)
        except (ValueError, KeyError, TypeError) as exc:
            await query.answer(error_text(str(exc)), show_alert=True)
        except Exception as exc:
            logger.warning("Izgara oyunu düğmesi ertelendi: %s", type(exc).__name__)
            try:
                await query.answer("İşlem tamamlanamadı. Paneli yenile.", show_alert=True)
            except Exception:
                pass
        return True

    async def on_text(self, update, context, text) -> bool:
        return False

    async def cancel_for(self, update, context) -> bool:
        chat, user, message = update.effective_chat, update.effective_user, update.effective_message
        if not chat or not user or not message or chat.type not in {"group", "supergroup"}:
            return False
        thread_id = forum_thread_id(message)
        cancelled = False
        moderator = await self._moderator(update, context)
        for game_key in GAME_KEYS:
            state = await self.service.current(game_key=game_key, chat_id=chat.id, thread_id=thread_id)
            if not state or state["phase"] in grid.TERMINAL:
                continue
            if user.id != state["owner_id"] and not moderator:
                continue
            await self.service.act(game_key=game_key, chat_id=chat.id, thread_id=thread_id,
                user_id=user.id, action_id=f"cancel:{message.message_id}:{game_key}",
                action="cancel", round_id=state["round_id"], phase_seq=state["phase_seq"],
                turn_seq=state["turn_seq"], moderator=moderator)
            await self._flush(context, game_key, chat.id, thread_id)
            cancelled = True
        return cancelled
