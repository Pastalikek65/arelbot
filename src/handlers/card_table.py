"""Telegram transport for Pişti and Papaz Kimde."""
from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, TelegramError

from src.games import card_table as rules
from src.ui import card_table_ui as ui
from src.storage.game_scope import forum_thread_id

from .handler_utils import is_admin

logger = logging.getLogger(__name__)
GAME_KEYS = rules.GAME_KEYS


class CardTableHandler:
    GAME_KEYS = GAME_KEYS

    def __init__(self, service, sudo_manager=None, authorization=None, inline_service=None):
        self.service = service
        self.sudo_manager = sudo_manager
        self.authorization = authorization
        self.inline_service = inline_service

    async def _moderator(self, update, context):
        return await is_admin(update, context, self.sudo_manager,
                              authorization=self.authorization)

    @staticmethod
    def accepts_text(state):
        return False

    async def on_text(self, update, context, text):
        return False

    async def publish(self, bot, *, game_key, chat_id, thread_id=None):
        claimed = await self.service.claim_delivery(game_key=game_key, chat_id=chat_id,
            thread_id=thread_id, kind="panel")
        if not claimed:
            return False
        message_id = claimed.get("panel_message_id")
        kwargs = {"chat_id": int(chat_id), "text": ui.group_text(claimed),
                  "parse_mode": "HTML", "reply_markup": ui.group_keyboard(claimed)}
        if message_id:
            try:
                await bot.edit_message_text(message_id=int(message_id), **kwargs)
            except BadRequest as exc:
                reason = str(exc).lower()
                if "message is not modified" in reason:
                    pass
                elif any(part in reason for part in ("message to edit not found", "message can't be edited")):
                    message_id = None
                else:
                    raise
        if not message_id:
            sent = await bot.send_message(message_thread_id=thread_id, **kwargs)
            message_id = sent.message_id
        await self.service.acknowledge_panel(game_key=game_key, chat_id=chat_id,
            thread_id=thread_id, round_id=claimed["round_id"],
            expected_version=claimed["version"], message_id=message_id)
        return True

    async def deliver_private(self, bot, *, game_key, chat_id, thread_id=None):
        state = await self.service.current(game_key=game_key, chat_id=chat_id,
                                           thread_id=thread_id)
        if not state or state["phase"] != "playing":
            return
        for uid in state["private_recipients"]:
            claimed = await self.service.claim_delivery(game_key=game_key, chat_id=chat_id,
                thread_id=thread_id, kind="private", user_id=uid)
            if not claimed:
                continue
            message_id = claimed.get("private_panels", {}).get(str(uid), {}).get("message_id")
            keyboard = ui.private_keyboard(claimed, uid)
            if keyboard is not None and self.inline_service is not None:
                gallery = InlineKeyboardButton(
                    "🃏 Kart galerisi",
                    switch_inline_query_current_chat=self.inline_service.create_query(claimed),
                )
                keyboard = InlineKeyboardMarkup([*keyboard.inline_keyboard, [gallery]])
            kwargs = {"chat_id": uid, "text": ui.private_text(claimed, uid),
                      "parse_mode": "HTML", "reply_markup": keyboard}
            try:
                if message_id:
                    try:
                        await bot.edit_message_text(message_id=int(message_id), **kwargs)
                    except BadRequest as exc:
                        reason = str(exc).lower()
                        if "message is not modified" in reason:
                            pass
                        elif any(part in reason for part in ("message to edit not found", "message can't be edited")):
                            message_id = None
                        else:
                            raise
                if not message_id:
                    sent = await bot.send_message(protect_content=True, **kwargs)
                    message_id = sent.message_id
            except TelegramError:
                self.service.metrics.inc("card_table_private_delivery_failure_total")
                continue
            await self.service.acknowledge_private(game_key=game_key, chat_id=chat_id,
                thread_id=thread_id, round_id=claimed["round_id"],
                phase_seq=claimed["phase_seq"], user_id=uid,
                message_id=message_id, expected_version=claimed["version"])

    async def process(self, bot, row):
        state = row.get("state", row)
        game_key, chat_id, thread_id = state["game_key"], int(state["chat_id"]), state.get("thread_id")
        await self.service.advance(game_key=game_key, chat_id=chat_id,
            thread_id=thread_id, round_id=state["round_id"])
        try:
            await self.service.settle_scores(game_key=game_key, chat_id=chat_id,
                                             thread_id=thread_id)
        except Exception as exc:
            self.service.metrics.inc("card_table_score_retry_total")
            logger.warning("Card table rewards deferred: %s", type(exc).__name__)
        for _ in range(3):
            try:
                await self.publish(bot, game_key=game_key, chat_id=chat_id, thread_id=thread_id)
            except TelegramError as exc:
                self.service.metrics.inc("card_table_panel_delivery_failure_total")
                logger.warning("Card table panel deferred: %s", type(exc).__name__)
            await self.deliver_private(bot, game_key=game_key, chat_id=chat_id,
                                       thread_id=thread_id)

    async def _flush(self, context, *, game_key, chat_id, thread_id=None):
        state = await self.service.current(game_key=game_key, chat_id=chat_id,
                                           thread_id=thread_id)
        if state:
            await self.process(context.bot, {"state": state})

    async def command(self, update, context, game_key: str):
        if game_key not in GAME_KEYS:
            return False
        chat, user, message = update.effective_chat, update.effective_user, update.effective_message
        if not chat or not user or not message:
            return True
        if chat.type not in {"group", "supergroup"}:
            await message.reply_text("Bu kart oyununu bir grupta aç.")
            return True
        topic = forum_thread_id(message)
        operation = str((getattr(context, "args", None) or ["open"])[0]).casefold()
        aliases = {"katıl": "join", "katil": "join", "başla": "start", "basla": "start",
                   "ayrıl": "leave", "ayril": "leave", "bitir": "stop", "iptal": "stop"}
        try:
            if operation in {"open", "aç", "ac", "durum"}:
                if operation == "durum":
                    state = await self.service.current(game_key=game_key, chat_id=chat.id,
                        thread_id=topic)
                    if not state:
                        raise ValueError("ct_not_found")
                else:
                    _, state = await self.service.open(game_key=game_key, chat_id=chat.id,
                        thread_id=topic, user_id=user.id, user_name=user.first_name)
                await self._flush(context, game_key=game_key, chat_id=chat.id, thread_id=topic)
                return True
            action = aliases.get(operation)
            if action is None:
                raise ValueError("ct_invalid_action")
            state = await self.service.current(game_key=game_key, chat_id=chat.id,
                                               thread_id=topic)
            if not state:
                raise ValueError("ct_not_found")
            await self.service.act(game_key=game_key, chat_id=chat.id, thread_id=topic,
                user_id=user.id, user_name=user.first_name, action_id=f"cmd:{message.message_id}",
                action=action, round_id=state["round_id"], phase_seq=state["phase_seq"],
                moderator=await self._moderator(update, context) if action == "stop" else False,
                submitted_at=message.date.timestamp() if getattr(message, "date", None) else None)
            await self._flush(context, game_key=game_key, chat_id=chat.id, thread_id=topic)
        except ValueError as exc:
            await message.reply_text(ui.error_message(str(exc)))
        return True

    async def deep_link(self, update, context, payload):
        if not str(payload).startswith("ct_"):
            return False
        if update.effective_chat.type != "private":
            return True
        try:
            game_key, chat_id, topic, _ = await self.service.accept_invite(
                payload, user_id=update.effective_user.id)
            await update.effective_message.reply_text("Özel sohbet hazır; gruba dönüp oyunu başlatabilirsiniz.")
            await self._flush(context, game_key=game_key, chat_id=chat_id, thread_id=topic)
        except ValueError as exc:
            await update.effective_message.reply_text(ui.error_message(str(exc)))
        return True

    async def callback(self, update, context):
        query = getattr(update, "callback_query", None)
        if not query or not str(query.data).startswith("ct:"):
            return False
        try:
            game_key, chat_id, topic, round_id, sequence, code = ui.parse_callback(query.data)
            state = await self.service.current(game_key=game_key, chat_id=chat_id,
                                               thread_id=topic)
            if not state or state["round_id"] != round_id or state["phase_seq"] != sequence:
                raise ValueError("ct_stale_phase")
            private = update.effective_chat.type == "private"
            uid = update.effective_user.id
            if private:
                receipt = state.get("private_panels", {}).get(str(uid), {})
                if (receipt.get("phase_seq") != sequence or
                        int(receipt.get("message_id") or 0) != int(query.message.message_id)):
                    raise ValueError("ct_stale_hand")
                if uid not in state.get("private_recipients", []):
                    raise ValueError("ct_stale_hand")
            elif (update.effective_chat.type not in {"group", "supergroup"} or
                  update.effective_chat.id != chat_id or forum_thread_id(query.message) != topic or
                  int(query.message.message_id) != int(state.get("panel_message_id") or 0)):
                raise ValueError("ct_stale_panel")
            if code == "ready" and not private:
                payload = await self.service.mint_invite(game_key=game_key, chat_id=chat_id,
                    thread_id=topic, user_id=uid, round_id=round_id)
                me = await context.bot.get_me()
                await query.answer(url=f"https://t.me/{me.username}?start={payload}")
                return True
            if private:
                if game_key == "pisti" and code.startswith("p") and code[1:].isdigit():
                    action, value = "play", int(code[1:])
                elif game_key == "papaz_kimde" and code == "draw":
                    action, value = "draw", None
                else:
                    raise ValueError("ct_invalid_action")
            elif code in {"join", "start", "leave", "stop"}:
                action, value = code, None
            else:
                raise ValueError("ct_invalid_action")
            await self.service.act(game_key=game_key, chat_id=chat_id, thread_id=topic,
                user_id=uid, user_name=update.effective_user.first_name,
                action_id=f"cb:{query.id}", action=action, value=value,
                round_id=round_id, phase_seq=sequence,
                private_message_id=query.message.message_id if private else None,
                moderator=await self._moderator(update, context) if action == "stop" else False)
            await query.answer()
            await self._flush(context, game_key=game_key, chat_id=chat_id, thread_id=topic)
        except (ValueError, TypeError) as exc:
            await query.answer(ui.error_message(str(exc)), show_alert=True)
        except Exception as exc:
            logger.warning("Card table callback deferred: %s", type(exc).__name__)
            try:
                await query.answer("İşlem tamamlanamadı. Güncel paneli kullan.", show_alert=True)
            except TelegramError:
                pass
        return True

    async def cancel_for(self, update, context):
        chat, user, message = update.effective_chat, update.effective_user, update.effective_message
        if not chat or chat.type not in {"group", "supergroup"} or not user or not message:
            return False
        topic, cancelled = forum_thread_id(message), False
        moderator = await self._moderator(update, context)
        for game_key in GAME_KEYS:
            state = await self.service.current(game_key=game_key, chat_id=chat.id,
                                               thread_id=topic)
            if not state or state["phase"] in rules.TERMINAL:
                continue
            if state["owner_id"] != user.id and not moderator:
                continue
            try:
                await self.service.act(game_key=game_key, chat_id=chat.id,
                    thread_id=topic, user_id=user.id,
                    action_id=f"cancel:{game_key}:{message.message_id}",
                    action="stop", round_id=state["round_id"], moderator=moderator)
                cancelled = True
                await self._flush(context, game_key=game_key, chat_id=chat.id, thread_id=topic)
            except ValueError:
                continue
        return cancelled
