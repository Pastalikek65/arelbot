"""Telegram surface for the durable Casus game."""
from __future__ import annotations

import logging

from telegram.error import BadRequest, TelegramError

from src.games import spy
from src.services.spy_ui import (
    callback_data,
    guess_confirmation,
    guess_menu,
    render_role,
    render_spy,
    spy_error,
    spy_keyboard,
)
from src.storage.game_scope import forum_thread_id
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .handler_utils import is_admin

logger = logging.getLogger(__name__)


class SpyHandler:
    def __init__(self, service, sudo_manager=None, authorization=None):
        self.service, self.sudo_manager, self.authorization = service, sudo_manager, authorization

    async def _moderator(self, update, context):
        return await is_admin(update, context, self.sudo_manager, authorization=self.authorization)

    async def publish(self, bot, *, chat_id, thread_id=None, force=False):
        state = (await self.service.current(chat_id, thread_id) if force else
                 await self.service.claim_delivery(chat_id=chat_id, thread_id=thread_id, kind="panel"))
        if not state:
            return
        message_id = state.get("panel_message_id")
        kwargs = {"chat_id": chat_id, "text": render_spy(state), "parse_mode": "HTML",
                  "reply_markup": spy_keyboard(state)}
        if message_id:
            try:
                await bot.edit_message_text(message_id=int(message_id), **kwargs)
            except BadRequest as exc:
                if "message is not modified" in str(exc).lower():
                    pass
                elif any(part in str(exc).lower() for part in ("message to edit not found", "message can't be edited")):
                    message_id = None
                else:
                    raise
        if not message_id:
            sent = await bot.send_message(message_thread_id=thread_id, **kwargs)
            message_id = sent.message_id
        await self.service.acknowledge_panel(chat_id=chat_id, thread_id=thread_id,
                                             round_id=state["round_id"],
                                             expected_version=state["version"], message_id=message_id)

    async def deliver_roles(self, bot, *, chat_id, thread_id=None):
        state = await self.service.current(chat_id, thread_id)
        if not state or state["phase"] != "delivery":
            return
        for player in state["players"]:
            if not player["active"] or player["delivered"]:
                continue
            claimed = await self.service.claim_delivery(chat_id=chat_id, thread_id=thread_id,
                                                        kind="role", user_id=player["id"])
            if not claimed:
                continue
            role = spy.private_role(claimed, user_id=player["id"])
            buttons = [InlineKeyboardButton("Rolümü aldım", callback_data=callback_data(claimed, "ack"))]
            if role["spy"]:
                buttons.append(InlineKeyboardButton("Mekânı tahmin et", callback_data=callback_data(claimed, "g0")))
            keyboard = InlineKeyboardMarkup([buttons])
            try:
                sent = await bot.send_message(chat_id=player["id"],
                                       text=render_role(role, chat_id=chat_id, thread_id=thread_id),
                                       parse_mode="HTML", reply_markup=keyboard,
                                       protect_content=True)
            except TelegramError:
                self.service.metrics.inc("game_private_delivery_failure_total")
                continue
            await self.service.act(chat_id=chat_id, thread_id=thread_id,
                                   user_id=player["id"], action_id=f"delivered:{claimed['round_id']}:{player['id']}",
                                   action="delivered", round_id=claimed["round_id"],
                                   message_id=sent.message_id)

    async def process(self, bot, row):
        chat_id, state = int(row["chat_id"]), row["state"]
        thread_id = state.get("thread_id")
        await self.service.advance(chat_id=chat_id, thread_id=thread_id,
                                   round_id=state["round_id"])
        try:
            await self.service.settle_scores(chat_id=chat_id, thread_id=thread_id)
        except Exception as exc:  # noqa: BLE001 - retryable effect must not stop panel delivery
            self.service.metrics.inc("game_score_retry_total")
            logger.warning("Casus rewards deferred: %s", type(exc).__name__)
        await self.deliver_roles(bot, chat_id=chat_id, thread_id=thread_id)
        await self.publish(bot, chat_id=chat_id, thread_id=thread_id)

    async def _flush(self, context, chat_id, thread_id):
        state = await self.service.current(chat_id, thread_id)
        if state:
            await self.process(context.bot, {"chat_id": chat_id, "state": state})

    async def command(self, update, context):
        chat, user, message = update.effective_chat, update.effective_user, update.effective_message
        if not chat or not user or not message:
            return
        args = list(getattr(context, "args", None) or [])
        action = args[0].lower() if args else "open"
        if chat.type == "private":
            await message.reply_text("Casus oyunu için grupta /casus yaz. Casussan rol mesajındaki Mekânı tahmin et düğmesini kullan.")
            return
        if chat.type not in {"group", "supergroup"}:
            return
        thread_id = forum_thread_id(message)
        try:
            if action in {"open", "durum"}:
                if action == "durum":
                    state = await self.service.current(chat.id, thread_id)
                    if not state:
                        raise ValueError("spy_not_found")
                else:
                    _, state = await self.service.open(chat_id=chat.id, thread_id=thread_id,
                                                       user_id=user.id, user_name=user.first_name)
                if state.get("panel_message_id"):
                    await self.publish(context.bot, chat_id=chat.id, thread_id=thread_id, force=True)
                else:
                    sent = await message.reply_text(render_spy(state), parse_mode="HTML",
                                                    reply_markup=spy_keyboard(state))
                    await self.service.acknowledge_panel(chat_id=chat.id, thread_id=thread_id,
                        round_id=state["round_id"], expected_version=state["version"],
                        message_id=sent.message_id)
                return
            state = await self.service.current(chat.id, thread_id)
            if not state:
                raise ValueError("spy_not_found")
            aliases = {"katil": "join", "katıl": "join", "basla": "start", "başla": "start",
                       "ayril": "leave", "ayrıl": "leave", "bitir": "stop"}
            operation = aliases.get(action)
            if not operation:
                await message.reply_text("/casus · /casus katıl · /casus başla · /casus durum · /casus ayrıl · /casus bitir")
                return
            await self.service.act(chat_id=chat.id, thread_id=thread_id, user_id=user.id,
                                   user_name=user.first_name, action_id=f"cmd:{message.message_id}",
                                   action=operation, round_id=state["round_id"],
                                   moderator=await self._moderator(update, context) if operation == "stop" else False,
                                   submitted_at=message.date.timestamp() if getattr(message, "date", None) else None)
            await self._flush(context, chat.id, thread_id)
        except ValueError as exc:
            await message.reply_text(spy_error(str(exc)))
        except Exception as exc:  # noqa: BLE001 - Telegram gets a safe, generic failure
            logger.warning("Casus command deferred: %s", type(exc).__name__)
            await message.reply_text("Casus işlemi tamamlanamadı. Biraz sonra /casus durum ile kontrol et.")

    async def deep_link(self, update, context, payload):
        if update.effective_chat.type != "private" or not str(payload).startswith("sp_"):
            return False
        try:
            chat_id, topic, _ = await self.service.accept_invite(payload, user_id=update.effective_user.id)
            await update.effective_message.reply_text("Casus için özel sohbetin hazır. Gruba dönebilirsin.")
            await self._flush(context, chat_id, topic)
        except ValueError as exc:
            await update.effective_message.reply_text(spy_error(str(exc)))
        return True

    async def callback(self, update, context):
        query = update.callback_query
        try:
            parts = str(query.data).split(":", 5)
            if len(parts) != 6 or parts[0] != "sp":
                raise ValueError("spy_invalid_action")
            _, chat, topic, round_id, _version, action = parts
            chat_id, thread_id = -int(chat, 36), int(topic, 36) or None
            private = update.effective_chat.type == "private"
            if private:
                if action != "ack" and not action.startswith(("g", "s", "c")):
                    raise ValueError("spy_invalid_action")
            elif action == "ack" or update.effective_chat.id != chat_id or forum_thread_id(query.message) != thread_id:
                raise ValueError("spy_stale_round")
            state = await self.service.current(chat_id, thread_id)
            if not state or state["round_id"] != round_id:
                raise ValueError("spy_stale_round")
            if not private and int(query.message.message_id) != int(state.get("panel_message_id") or 0):
                raise ValueError("spy_stale_panel")
            user = update.effective_user
            if private:
                player = next((p for p in state["players"] if p["id"] == user.id and p["active"]), None)
                if not player or int(player.get("dm_message_id") or 0) != int(query.message.message_id):
                    raise ValueError("spy_stale_panel")
            if action == "ready":
                payload = await self.service.mint_invite(chat_id=chat_id, thread_id=thread_id,
                    user_id=user.id, round_id=round_id, request_id=query.id)
                bot = await context.bot.get_me()
                await query.answer(url=f"https://t.me/{bot.username}?start={payload}")
            elif action == "refresh":
                await query.answer()
                await self.publish(context.bot, chat_id=chat_id, thread_id=thread_id, force=True)
            elif private and action.startswith(("g", "s", "c")):
                if user.id != state["spy_id"] or state["phase"] not in {"question", "discussion", "vote"}:
                    raise ValueError("spy_guess_forbidden")
                if state.get("deadline") is None or state.get("guess_used"):
                    raise ValueError("spy_waiting_panel")
                if action.startswith("g"):
                    page = int(action[1:], 36)
                    await self.service.act(chat_id=chat_id, thread_id=thread_id,
                        user_id=user.id, action_id=f"cb:{query.id}", action="guess_menu",
                        round_id=round_id, message_id=query.message.message_id)
                    await context.bot.edit_message_text(chat_id=user.id, message_id=query.message.message_id,
                        text=f"🔐 <b>Mekânı seç</b> · Sayfa {page + 1}", parse_mode="HTML",
                        reply_markup=guess_menu(state, page))
                elif action.startswith("s"):
                    index = int(action[1:], 36)
                    if not 0 <= index < len(spy.LOCATIONS):
                        raise ValueError("spy_invalid_action")
                    await self.service.act(chat_id=chat_id, thread_id=thread_id,
                        user_id=user.id, action_id=f"cb:{query.id}", action="select_guess",
                        round_id=round_id, target_id=index, message_id=query.message.message_id)
                    text, keyboard = guess_confirmation(state, index)
                    await context.bot.edit_message_text(chat_id=user.id, message_id=query.message.message_id,
                        text=text, parse_mode="HTML", reply_markup=keyboard)
                else:
                    index = int(action[1:], 36)
                    if not 0 <= index < len(spy.LOCATIONS):
                        raise ValueError("spy_invalid_action")
                    await self.service.act(chat_id=chat_id, thread_id=thread_id, user_id=user.id,
                        action_id=f"cb:{query.id}", action="guess", round_id=round_id,
                        target_id=index, message_id=query.message.message_id,
                        text=spy.LOCATIONS[index][0])
                    await context.bot.edit_message_text(chat_id=user.id, message_id=query.message.message_id,
                        text="Tahminin işlendi. Sonuç grup panelinde.")
                    await self._flush(context, chat_id, thread_id)
                await query.answer()
            else:
                target = None
                if action.startswith(("t", "v")):
                    target_text, sequence_text = action[1:].split(".", 1)
                    target, sequence = int(target_text, 36), int(sequence_text, 36)
                    current_sequence = state["turn_index"] if action.startswith("t") else state["vote_round"]
                    if sequence != current_sequence:
                        raise ValueError("spy_stale_panel")
                operation = "target" if action.startswith("t") else "vote" if action.startswith("v") else \
                            "confirm" if action == "ack" else action
                if operation not in {"join", "start", "leave", "stop", "confirm", "target", "vote"}:
                    raise ValueError("spy_invalid_action")
                await self.service.act(chat_id=chat_id, thread_id=thread_id,
                    user_id=user.id, user_name=user.first_name, action_id=f"cb:{query.id}",
                    action=operation, round_id=round_id, target_id=target,
                    moderator=await self._moderator(update, context) if operation == "stop" else False)
                await query.answer()
                await self._flush(context, chat_id, thread_id)
        except (ValueError, TypeError) as exc:
            await query.answer(spy_error(str(exc)), show_alert=True)
        except Exception as exc:  # noqa: BLE001 - callback must always be answered
            logger.warning("Casus callback deferred: %s", type(exc).__name__)
            try:
                await query.answer("İşlem tamamlanamadı. Güncel paneli kullan.", show_alert=True)
            except TelegramError:
                pass

    async def on_text(self, update, context, text):
        chat, user, message = update.effective_chat, update.effective_user, update.effective_message
        if not chat or not user or not message or chat.type not in {"group", "supergroup"}:
            return False
        thread_id = forum_thread_id(message)
        state = await self.service.current(chat.id, thread_id)
        if not state or state["phase"] != "question":
            return False
        asker = state["question_order"][state["turn_index"]]
        expected = state["target_id"] if state["question_pending"] else asker
        if user.id != expected or state["target_id"] is None:
            return False
        if getattr(update, "edited_message", None) is message:
            return True
        try:
            await self.service.act(chat_id=chat.id, thread_id=thread_id, user_id=user.id,
                action_id=f"msg:{message.message_id}", action="text", text=text,
                round_id=state["round_id"], message_id=message.message_id,
                submitted_at=message.date.timestamp() if getattr(message, "date", None) else None)
            await self._flush(context, chat.id, thread_id)
        except ValueError:
            await self._flush(context, chat.id, thread_id)
        return True
