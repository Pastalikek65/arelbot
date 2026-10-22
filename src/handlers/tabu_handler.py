"""Telegram adapter for the durable team Tabu game."""

from __future__ import annotations

from html import escape
import hashlib
import logging
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, Forbidden

from src.games import tabu
from src.storage.game_scope import forum_thread_id
from src.services.tabu_ui import callback_data, render_tabu, render_private_card, tabu_keyboard, tabu_error
from .handler_utils import is_admin

logger = logging.getLogger(__name__)


class TabuHandler:
    def __init__(self, service, *, sudo_manager=None, authorization=None):
        self.service, self.sudo_manager, self.authorization = service, sudo_manager, authorization

    async def _moderator(self, update, context):
        return await is_admin(update, context, self.sudo_manager, authorization=self.authorization)

    async def publish(self, bot, *, chat_id, thread_id=None, force=False):
        state = (await self.service.current(chat_id, thread_id) if force else
                 await self.service.claim_delivery(chat_id=chat_id, thread_id=thread_id, kind="panel"))
        if not state:
            return
        message_id = state.get("panel_message_id")
        kwargs = dict(chat_id=chat_id, text=render_tabu(state), parse_mode="HTML", reply_markup=tabu_keyboard(state))
        if message_id:
            try:
                await bot.edit_message_text(message_id=int(message_id), **kwargs)
            except BadRequest as exc:
                if "message is not modified" not in str(exc).lower():
                    if not any(reason in str(exc).lower() for reason in ("message to edit not found", "message can't be edited")):
                        raise
                    message_id = None
        if not message_id:
            sent = await bot.send_message(message_thread_id=thread_id, **kwargs)
            message_id = sent.message_id
        await self.service.acknowledge_panel(chat_id=chat_id, thread_id=thread_id, round_id=state["round_id"],
                                            expected_version=state["version"], message_id=message_id)

    async def deliver_card(self, bot, *, chat_id, thread_id=None):
        state = await self.service.claim_delivery(chat_id=chat_id, thread_id=thread_id, kind="card")
        if not state:
            return False
        card = tabu.private_card(state, user_id=state["narrator_id"], card_id=state["card_id"])
        buttons = InlineKeyboardMarkup([[InlineKeyboardButton("Süreyi başlat", callback_data=callback_data(state, "ack"))]])
        try:
            await bot.send_message(chat_id=int(state["narrator_id"]), text=render_private_card(card),
                                   parse_mode="HTML", reply_markup=buttons, protect_content=True)
        except Forbidden:
            self.service.metrics.inc("game_private_delivery_failure_total")
            return False
        await self.service.act(chat_id=chat_id, thread_id=thread_id, user_id=state["narrator_id"],
                               action_id=f"delivered:{state['card_id']}", action="delivered",
                               round_id=state["round_id"], card_id=state["card_id"])
        return True

    async def process(self, bot, row):
        chat_id, state = int(row["chat_id"]), row["state"]
        thread_id = state.get("thread_id")
        await self.service.advance(chat_id=chat_id, thread_id=thread_id, round_id=state["round_id"])
        try:
            await self.service.settle_scores(chat_id=chat_id, thread_id=thread_id)
        except Exception as exc:
            self.service.metrics.inc("game_score_retry_total")
            logger.warning("Tabu rewards deferred: %s", type(exc).__name__)
        await self.deliver_card(bot, chat_id=chat_id, thread_id=thread_id)
        await self.publish(bot, chat_id=chat_id, thread_id=thread_id)

    async def _flush(self, context, chat_id, thread_id):
        state = await self.service.current(chat_id, thread_id)
        if state:
            await self.process(context.bot, {"chat_id": chat_id, "state": state})

    async def command(self, update, context):
        chat, user, message = update.effective_chat, update.effective_user, update.effective_message
        if not message or not user or not chat:
            return
        if chat.type not in {"group", "supergroup"}:
            await message.reply_text("Tabu en az dört kişiyle bir grupta oynanır. Grupta /tabu yaz; takımını ve özel sohbet hazırlığını oyun panelinden seç.")
            return
        thread_id = forum_thread_id(message)
        args = list(getattr(context, "args", None) or [])
        action = args[0].lower() if args else "open"
        try:
            if action in {"open", "durum"}:
                if action == "durum":
                    state = await self.service.current(chat.id, thread_id)
                    if not state:
                        raise ValueError("tabu_not_found")
                else:
                    _, state = await self.service.open(chat_id=chat.id, thread_id=thread_id, user_id=user.id, user_name=user.first_name)
                if state.get("panel_message_id"):
                    await self.publish(context.bot, chat_id=chat.id, thread_id=thread_id, force=True)
                else:
                    sent = await message.reply_text(render_tabu(state), parse_mode="HTML", reply_markup=tabu_keyboard(state))
                    await self.service.acknowledge_panel(chat_id=chat.id, thread_id=thread_id, round_id=state["round_id"],
                                                        expected_version=state["version"], message_id=sent.message_id)
            else:
                state = await self.service.current(chat.id, thread_id)
                if not state:
                    raise ValueError("tabu_not_found")
                aliases = {"katil": "join", "takim": "team", "basla": "start", "pas": "pass", "ayril": "leave", "bitir": "stop", "tahmin": "text", "anlat": "text"}
                operation = aliases.get(action)
                if operation is None:
                    await message.reply_text("/tabu · /tabu katil kirmizi|mavi · /tabu basla · /tabu tahmin kelime · /tabu pas · /tabu ayril · /tabu bitir")
                    return
                team = {"kirmizi": "red", "kırmızı": "red", "mavi": "blue"}.get(args[1].lower()) if len(args) > 1 else None
                await self.service.act(chat_id=chat.id, thread_id=thread_id, user_id=user.id, user_name=user.first_name,
                                       action_id=f"cmd:{message.message_id}", action=operation, round_id=state["round_id"],
                                       card_id=state.get("card_id"), team=team, text=" ".join(args[1:]),
                                       moderator=await self._moderator(update, context) if operation == "stop" else False,
                                       submitted_at=message.date.timestamp() if getattr(message, "date", None) else None)
            await self._flush(context, chat.id, thread_id)
        except ValueError as exc:
            await message.reply_text(tabu_error(str(exc)))
        except Exception as exc:
            logger.warning("Tabu command deferred: %s", type(exc).__name__)
            await message.reply_text("Tabu işlemi tamamlanamadı. Biraz sonra /tabu durum ile oyunu kontrol et.")

    async def deep_link(self, update, context, payload):
        if update.effective_chat.type != "private":
            return False
        try:
            chat_id, topic, _ = await self.service.accept_invite(payload, user_id=update.effective_user.id)
            await update.effective_message.reply_text("Tabu için hazırsın. Gruba dönebilirsin; anlatıcı olduğunda kartın bu özel sohbete gönderilecek.")
            await self._flush(context, chat_id, topic)
        except ValueError as exc:
            await update.effective_message.reply_text(tabu_error(str(exc)))
        return True

    async def callback(self, update, context):
        query = update.callback_query
        try:
            parts = str(query.data).split(":")
            if len(parts) != 6 or parts[0] != "tb":
                raise ValueError("tabu_invalid_action")
            _, chat, topic, round_id, card_id, action = parts
            chat_id, thread_id = -int(chat, 36), int(topic, 36) or None
            private = update.effective_chat.type == "private"
            if private:
                if action not in {"ack", "pass"}:
                    raise ValueError("tabu_invalid_action")
            elif action == "ack":
                raise ValueError("tabu_invalid_action")
            elif (int(update.effective_chat.id) != chat_id
                  or forum_thread_id(query.message) != thread_id):
                raise ValueError("tabu_stale_round")
            state = await self.service.current(chat_id, thread_id)
            if not state or state["round_id"] != round_id:
                raise ValueError("tabu_stale_round")
            if action not in {"refresh", "replay"} and str(state.get("card_id") or "0") != card_id:
                raise ValueError("tabu_stale_card")
            user = update.effective_user
            if action == "replay":
                if state["phase"] not in {"finished", "cancelled"}:
                    raise ValueError("tabu_not_finished")
                await self.service.open(chat_id=chat_id, thread_id=thread_id,
                                        user_id=user.id, user_name=user.first_name)
            elif action == "ready":
                payload = await self.service.mint_invite(chat_id=chat_id, thread_id=thread_id, user_id=user.id,
                                                         round_id=round_id, request_id=f"cb:{query.id}")
                identity = await context.bot.get_me()
                await query.answer(url=f"https://t.me/{identity.username}?start={payload}")
                return
            elif action == "card":
                if int(state.get("narrator_id") or 0) != int(user.id):
                    raise ValueError("tabu_not_narrator")
                if state.get("delivered"):
                    raise ValueError("tabu_already_delivered")
                await self.deliver_card(context.bot, chat_id=chat_id, thread_id=thread_id)
            elif action == "refresh":
                try:
                    await query.edit_message_text(render_tabu(state), parse_mode="HTML", reply_markup=tabu_keyboard(state))
                except BadRequest as exc:
                    if "message is not modified" not in str(exc).lower():
                        raise
            else:
                team = action if action in {"red", "blue"} else None
                operation = ("team" if any(p["id"] == user.id for p in state["players"]) else "join") if team else action
                decision = action if action in {"uphold", "neutral"} else "uphold"
                if action in {"uphold", "neutral"}:
                    operation = "decide"
                if operation not in {"team", "join", "start", "pass", "stop", "leave", "ack", "appeal", "decide"}:
                    raise ValueError("tabu_invalid_action")
                input_floor = None
                if operation == "ack":
                    if int(state.get("narrator_id") or 0) != int(user.id):
                        raise ValueError("tabu_not_narrator")
                    leased = await self.service.claim_delivery(chat_id=chat_id, thread_id=thread_id, kind="start")
                    if leased is None:
                        raise ValueError("tabu_start_pending")
                    announced = await context.bot.send_message(chat_id=chat_id, message_thread_id=thread_id,
                        text="🎭 Anlatım başlıyor. Bu mesajdan sonraki takım tahminleri kabul edilir; güncel panelden süreyi takip et.")
                    input_floor = int(announced.message_id)
                await self.service.act(chat_id=chat_id, thread_id=thread_id, user_id=user.id, user_name=user.first_name,
                                       action_id=f"cb:{query.id}", action=operation, round_id=round_id,
                                       card_id=state.get("card_id"), team=team, decision=decision, input_floor=input_floor,
                                       moderator=await self._moderator(update, context) if operation in {"stop", "decide"} else False)
            await query.answer()
            await self._flush(context, chat_id, thread_id)
        except (ValueError, TypeError) as exc:
            await query.answer(tabu_error(str(exc)), show_alert=True)
        except Exception as exc:
            logger.warning("Tabu callback deferred: %s", type(exc).__name__)
            try:
                await query.answer("İşlem tamamlanamadı. Biraz sonra Yenile düğmesini kullan.", show_alert=True)
            except Exception:
                pass

    async def on_text(self, update, context, text):
        if update.effective_chat.type not in {"group", "supergroup"}:
            return False
        chat_id, user_id = update.effective_chat.id, update.effective_user.id
        thread_id = forum_thread_id(update.effective_message)
        state = await self.service.current(chat_id, thread_id)
        if not state or state["phase"] != "active":
            return False
        player = next((p for p in state["players"] if p["id"] == user_id and p.get("active", True)), None)
        if not player or player["team"] != state["active_team"]:
            return False
        message = update.effective_message
        edited = getattr(update, "edited_message", None) is message
        if edited:
            # A revised guess must never earn a later card. Only the current
            # narrator's clue can be rechecked for forbidden expressions.
            if user_id != state["narrator_id"] or not getattr(message, "edit_date", None):
                return True
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
            action_id = f"edit:{message.message_id}:{int(message.edit_date.timestamp())}:{digest}"
        else:
            action_id = f"msg:{message.message_id}"
        try:
            await self.service.act(chat_id=chat_id, thread_id=thread_id, user_id=user_id,
                                   action_id=action_id, action="text", text=text,
                                   round_id=state["round_id"], card_id=state["card_id"],
                                   submitted_at=message.date.timestamp() if getattr(message, "date", None) else None)
            await self._flush(context, chat_id, thread_id)
        except ValueError:
            await self._flush(context, chat_id, thread_id)
        return True
