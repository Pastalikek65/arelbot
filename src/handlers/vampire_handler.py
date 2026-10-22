"""Telegram adapter for the persistent Vampir Köylü game."""

from __future__ import annotations

from html import escape
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, Forbidden

from src.games import vampire
from src.services.vampire_ui import (ACTION_CODES, callback_data, error_text, group_keyboard,
                                     private_keyboard, private_text, render_vampire)
from src.storage.game_scope import forum_thread_id
from .handler_utils import is_admin

logger = logging.getLogger(__name__)


class VampireHandler:
    def __init__(self, service, sudo_manager=None, authorization=None):
        self.service = service
        self.sudo_manager = sudo_manager
        self.authorization = authorization

    async def _moderator(self, update, context):
        return await is_admin(update, context, self.sudo_manager, authorization=self.authorization)

    async def publish(self, bot, *, chat_id, thread_id=None, force=False):
        state = (await self.service.current(chat_id, thread_id) if force else
                 await self.service.claim_delivery(chat_id=chat_id, thread_id=thread_id, kind="panel"))
        if not state:
            return
        message_id = state.get("panel_message_id")
        kwargs = dict(chat_id=chat_id, text=render_vampire(state), parse_mode="HTML",
                      reply_markup=group_keyboard(state))
        if message_id:
            try:
                await bot.edit_message_text(message_id=int(message_id), **kwargs)
            except BadRequest as exc:
                message = str(exc).lower()
                if "message is not modified" not in message:
                    if not any(term in message for term in ("message to edit not found", "message can't be edited")):
                        raise
                    message_id = None
        if not message_id:
            sent = await bot.send_message(message_thread_id=thread_id, **kwargs)
            message_id = sent.message_id
        await self.service.acknowledge_panel(chat_id=chat_id, thread_id=thread_id,
            round_id=state["round_id"], expected_version=state["version"], message_id=message_id)

    async def deliver_private(self, bot, *, chat_id, thread_id=None):
        state = await self.service.current(chat_id, thread_id)
        if not state or state["phase"] not in {"role_delivery", "night", "vote", "runoff"}:
            return
        if state["phase"] != "role_delivery" and not state.get("panel_ready"):
            return
        if state["phase"] == "role_delivery":
            recipients = [p for p in state["players"] if p["active"]]
        elif state["phase"] == "night":
            recipients = [p for p in vampire.living(state) if p["role"] != "villager"]
        else:
            recipients = vampire.living(state)
        for person in recipients:
            user_id = person["id"]
            claimed = await self.service.claim_delivery(chat_id=chat_id, thread_id=thread_id,
                                                        kind="private", user_id=user_id)
            if not claimed:
                continue
            # A prior send may have advanced the phase. The claim's generation binds the message.
            try:
                sent = await bot.send_message(chat_id=user_id, text=private_text(claimed, user_id),
                    parse_mode="HTML", reply_markup=private_keyboard(claimed, user_id), protect_content=True)
            except Forbidden:
                self.service.metrics.inc("game_private_delivery_failure_total")
                continue
            await self.service.acknowledge_private(chat_id=chat_id, thread_id=thread_id,
                round_id=claimed["round_id"], phase_seq=claimed["phase_seq"],
                user_id=user_id, message_id=sent.message_id)

    async def deliver_findings(self, bot, *, chat_id, thread_id=None):
        state = await self.service.current(chat_id, thread_id)
        if not state:
            return
        for finding_id, item in state.get("seer_findings", {}).items():
            if item["delivered"]:
                continue
            claimed = await self.service.claim_delivery(chat_id=chat_id, thread_id=thread_id,
                kind="finding", user_id=finding_id)
            if not claimed:
                continue
            finding = claimed["seer_findings"][finding_id]
            target = next(p for p in claimed["players"] if p["id"] == finding["target_id"])
            try:
                await bot.send_message(chat_id=finding["user_id"], protect_content=True,
                    text=f"🔐 Kâhin sonucu: {escape(target['name'])} " +
                         ("vampir." if finding["is_vampire"] else "vampir değil."))
            except Forbidden:
                self.service.metrics.inc("game_private_delivery_failure_total")
                continue
            await self.service.acknowledge_finding(chat_id=chat_id, thread_id=thread_id,
                round_id=claimed["round_id"], finding_id=finding_id, user_id=finding["user_id"])

    async def process(self, bot, row):
        chat_id, state = int(row["chat_id"]), row["state"]
        thread_id = state.get("thread_id")
        await self.service.advance(chat_id=chat_id, thread_id=thread_id, round_id=state["round_id"])
        try:
            await self.service.settle_scores(chat_id=chat_id, thread_id=thread_id)
        except Exception as exc:
            self.service.metrics.inc("game_score_retry_total")
            logger.warning("Vampir Köylü puan kaydı ertelendi: %s", type(exc).__name__)
        await self.publish(bot, chat_id=chat_id, thread_id=thread_id)
        await self.deliver_private(bot, chat_id=chat_id, thread_id=thread_id)
        await self.deliver_findings(bot, chat_id=chat_id, thread_id=thread_id)

    async def _flush(self, context, chat_id, thread_id):
        state = await self.service.current(chat_id, thread_id)
        if state:
            await self.process(context.bot, {"chat_id": chat_id, "state": state})

    async def command(self, update, context):
        chat, user, message = update.effective_chat, update.effective_user, update.effective_message
        if not chat or not user or not message:
            return
        if chat.type not in {"group", "supergroup"}:
            await message.reply_text("Vampir Köylü 7–20 kişiyle bir grupta oynanır. Grupta /vampir yaz.")
            return
        thread_id = forum_thread_id(message)
        args = list(getattr(context, "args", None) or [])
        operation = args[0].lower() if args else "open"
        try:
            if operation in {"open", "durum"}:
                if operation == "durum":
                    state = await self.service.current(chat.id, thread_id)
                    if not state:
                        raise ValueError("vampire_not_found")
                else:
                    _, state = await self.service.open(chat_id=chat.id, thread_id=thread_id,
                                                      user_id=user.id, user_name=user.first_name)
                if state.get("panel_message_id"):
                    await self.publish(context.bot, chat_id=chat.id, thread_id=thread_id, force=True)
                else:
                    sent = await message.reply_text(render_vampire(state), parse_mode="HTML",
                                                    reply_markup=group_keyboard(state))
                    await self.service.acknowledge_panel(chat_id=chat.id, thread_id=thread_id,
                        round_id=state["round_id"], expected_version=state["version"], message_id=sent.message_id)
            else:
                actions = {"katil": "join", "başla": "start", "basla": "start", "ayar": "configure",
                           "ayril": "leave", "ayrıl": "leave", "bitir": "stop"}
                action = actions.get(operation)
                if action is None:
                    await message.reply_text("/vampir · /vampir katil · /vampir basla · /vampir durum · /vampir ayar tekrar_koruma|kendini_iyilestirme|olumde_rol acik|kapali · /vampir ayril · /vampir bitir")
                    return
                option = None
                enabled = None
                if action == "configure":
                    option = {"tekrar_koruma": "repeat_protection", "kendini_iyilestirme": "self_heal",
                              "olumde_rol": "reveal_on_death"}.get(args[1].lower()) if len(args) > 1 else None
                    enabled = {"acik": True, "açık": True, "kapali": False, "kapalı": False}.get(args[2].lower()) if len(args) > 2 else None
                    if option is None or enabled is None:
                        raise ValueError("vampire_invalid_setting")
                state = await self.service.current(chat.id, thread_id)
                if not state:
                    raise ValueError("vampire_not_found")
                await self.service.act(chat_id=chat.id, thread_id=thread_id, user_id=user.id,
                    user_name=user.first_name, action_id=f"cmd:{message.message_id}", action=action,
                    round_id=state["round_id"], phase_seq=state["phase_seq"],
                    option=option, enabled=enabled,
                    moderator=await self._moderator(update, context) if action == "stop" else False,
                    submitted_at=message.date.timestamp() if getattr(message, "date", None) else None)
            await self._flush(context, chat.id, thread_id)
        except ValueError as exc:
            await message.reply_text(error_text(str(exc)))
        except Exception as exc:
            logger.warning("Vampir Köylü komutu ertelendi: %s", type(exc).__name__)
            await message.reply_text("İşlem tamamlanamadı. Biraz sonra /vampir durum yaz.")

    async def deep_link(self, update, context, payload):
        if update.effective_chat.type != "private":
            return False
        try:
            chat_id, thread_id, _ = await self.service.accept_invite(payload, user_id=update.effective_user.id)
            await update.effective_message.reply_text("Özel sohbetin hazır. Gruba dönebilirsin; rolün burada bildirilecek.")
            await self._flush(context, chat_id, thread_id)
        except ValueError as exc:
            await update.effective_message.reply_text(error_text(str(exc)))
        return True

    async def callback(self, update, context):
        query = update.callback_query
        answered = False
        try:
            parts = str(query.data).split(":")
            if len(parts) != 7 or parts[0] != "vp":
                raise ValueError("vampire_invalid_action")
            _, encoded_chat, encoded_topic, round_id, encoded_seq, action, encoded_target = parts
            action = next((name for name, code in ACTION_CODES.items() if code == action), None)
            if action is None:
                raise ValueError("vampire_invalid_action")
            chat_id, thread_id = -int(encoded_chat, 36), int(encoded_topic, 36) or None
            phase_seq = int(encoded_seq, 36)
            target_id = int(encoded_target, 36) or None
            private = update.effective_chat.type == "private"
            state = await self.service.current(chat_id, thread_id)
            if not state or state["round_id"] != round_id:
                raise ValueError("vampire_stale_round")
            if int(state["phase_seq"]) != phase_seq:
                raise ValueError("vampire_stale_phase")
            user = update.effective_user
            if private:
                if update.effective_chat.id != user.id or action not in {"ack", "night", "vote"}:
                    raise ValueError("vampire_invalid_action")
                delivered = state.get("private_delivery", {}).get(str(user.id))
                if not delivered or int(delivered) != int(query.message.message_id):
                    raise ValueError("vampire_stale_phase")
                operation = action
                if operation == "night" and state["phase"] != "night":
                    raise ValueError("vampire_stale_phase")
                if operation == "vote" and state["phase"] not in {"vote", "runoff"}:
                    raise ValueError("vampire_stale_phase")
            else:
                if (update.effective_chat.id != chat_id or
                    forum_thread_id(query.message) != thread_id or
                    int(state.get("panel_message_id") or 0) != int(query.message.message_id) or
                    action not in {"join", "ready", "start", "refresh", "leave", "stop",
                                   "repeat_protection", "self_heal", "reveal_on_death"}):
                    raise ValueError("vampire_stale_phase")
                operation = "configure" if action in {"repeat_protection", "self_heal", "reveal_on_death"} else action
            if action == "ready":
                payload = await self.service.mint_invite(chat_id=chat_id, thread_id=thread_id,
                    user_id=user.id, round_id=round_id, request_id=f"cb:{query.id}")
                bot = await context.bot.get_me()
                await query.answer(url=f"https://t.me/{bot.username}?start={payload}")
                answered = True
            elif action == "refresh":
                await self.publish(context.bot, chat_id=chat_id, thread_id=thread_id, force=True)
            else:
                option = action if operation == "configure" else None
                enabled = target_id == 1 if operation == "configure" else None
                if operation == "configure" and (target_id not in {1, 2} or state["settings"][option] == enabled):
                    raise ValueError("vampire_stale_phase")
                await self.service.act(chat_id=chat_id, thread_id=thread_id,
                    user_id=user.id, user_name=user.first_name, action_id=f"cb:{query.id}",
                    action=operation, round_id=round_id, phase_seq=phase_seq,
                    target_id=target_id, option=option, enabled=enabled,
                    moderator=await self._moderator(update, context) if operation == "stop" else False)
            if not answered:
                await query.answer()
            await self._flush(context, chat_id, thread_id)
        except (ValueError, TypeError, StopIteration) as exc:
            await query.answer(error_text(str(exc)), show_alert=True)
        except Exception as exc:
            logger.warning("Vampir Köylü düğmesi ertelendi: %s", type(exc).__name__)
            try:
                await query.answer("İşlem tamamlanamadı. Güncel paneli kontrol et.", show_alert=True)
            except Exception:
                pass
