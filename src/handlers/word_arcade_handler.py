"""Telegram transport for the five deterministic Word Arcade games."""
from __future__ import annotations

import hashlib
import logging

from telegram.error import BadRequest, TelegramError

from src.games import word_arcade as rules
from src.services import word_arcade_ui as ui
from src.storage.game_scope import forum_thread_id

from .handler_utils import is_admin

logger = logging.getLogger(__name__)
GAME_KEYS = rules.GAME_KEYS


class WordArcadeHandler:
    GAME_KEYS = GAME_KEYS

    def __init__(self, service, sudo_manager=None, authorization=None):
        self.service, self.sudo_manager, self.authorization = service, sudo_manager, authorization

    @staticmethod
    def accepts_text(state):
        return (state.get("game_key"), state.get("phase")) in {
            ("sozcuk_avi", "hunt"), ("yasak_harf", "clue"),
            ("ortak_hikaye", "story_turn"), ("bomba", "bomb_turn")}

    async def _moderator(self, update, context):
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

    async def deliver_private(self, bot, *, chat_id, thread_id=None):
        state = await self.service.claim_private(game_key="yasak_harf", chat_id=chat_id,
                                                 thread_id=thread_id)
        if not state:
            return False
        try:
            sent = await bot.send_message(chat_id=state["narrator_id"],
                text=ui.private_role(state), parse_mode="HTML",
                reply_markup=ui.private_keyboard(state), protect_content=True)
        except TelegramError as exc:
            self.service.metrics.inc("word_arcade_private_delivery_failure_total")
            logger.warning("Word Arcade private delivery deferred: %s", type(exc).__name__)
            return False
        await self.service.acknowledge_private(game_key="yasak_harf", chat_id=chat_id,
            thread_id=thread_id, round_id=state["round_id"],
            expected_version=state["version"], message_id=sent.message_id)
        return True

    async def process(self, bot, row):
        state = row.get("state") or row
        game_key, chat_id = state["game_key"], int(row.get("chat_id", state["chat_id"]))
        topic = state.get("thread_id")
        await self.service.advance(game_key=game_key, chat_id=chat_id,
                                   thread_id=topic, round_id=state["round_id"])
        try:
            await self.service.settle_scores(game_key=game_key, chat_id=chat_id,
                                             thread_id=topic)
        except Exception as exc:  # noqa: BLE001 - durable pending effect retries
            self.service.metrics.inc("word_arcade_score_retry_total")
            logger.warning("Word Arcade rewards deferred: %s", type(exc).__name__)
        if game_key == "yasak_harf":
            await self.deliver_private(bot, chat_id=chat_id, thread_id=topic)
        try:
            await self.publish(bot, game_key=game_key, chat_id=chat_id, thread_id=topic)
        except TelegramError as exc:
            self.service.metrics.inc("word_arcade_panel_delivery_failure_total")
            logger.warning("Word Arcade panel deferred: %s", type(exc).__name__)

    async def _flush(self, bot, *, game_key, chat_id, thread_id):
        state = await self.service.current(game_key=game_key, chat_id=chat_id,
                                           thread_id=thread_id)
        if state:
            await self.process(bot, {"chat_id": chat_id, "state": state})

    async def command(self, update, context, game_key: str):
        if game_key not in GAME_KEYS:
            return
        chat, user, message = update.effective_chat, update.effective_user, update.effective_message
        if not chat or not user or not message:
            return
        if chat.type not in {"group", "supergroup"}:
            await message.reply_text("Bu oyunu bir grupta açabilirsin.")
            return
        topic = forum_thread_id(message)
        args = list(getattr(context, "args", None) or [])
        command = args[0].casefold() if args else "open"
        aliases = {"katıl": "join", "katil": "join", "başla": "start", "basla": "start",
                   "ayrıl": "leave", "ayril": "leave", "bitir": "stop",
                   "iptal": "stop", "durum": "refresh"}
        try:
            if command in {"open", "aç", "ac"}:
                _, state = await self.service.open(game_key=game_key, chat_id=chat.id,
                    thread_id=topic, user_id=user.id, user_name=user.first_name)
                await self.publish(context.bot, game_key=game_key, chat_id=chat.id,
                                   thread_id=topic, force=bool(state.get("panel_message_id")))
                return
            state = await self.service.current(game_key=game_key, chat_id=chat.id,
                                               thread_id=topic)
            if not state:
                raise ValueError("wa_not_found")
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
            logger.warning("Word Arcade command deferred: %s", type(exc).__name__)
            await message.reply_text("İşlem tamamlanamadı. Biraz sonra durum komutunu kullan.")

    async def callback(self, update, context) -> bool:
        query = getattr(update, "callback_query", None)
        if not query or not str(getattr(query, "data", "")).startswith("wa:"):
            return False
        try:
            game, chat_id, topic, round_id, seq, action = ui.parse_callback(query.data)
            state = await self.service.current(game_key=game, chat_id=chat_id, thread_id=topic)
            if not state or state["round_id"] != round_id or state["phase_seq"] != seq:
                raise ValueError("wa_stale_round")
            private = update.effective_chat.type == "private"
            if private:
                if (action != "ack" or game != "yasak_harf" or
                        update.effective_user.id != state.get("narrator_id") or
                        int(query.message.message_id) != int(state.get("private_message_id") or 0)):
                    raise ValueError("wa_stale_private")
                await self.service.act(game_key=game, chat_id=chat_id, thread_id=topic,
                    user_id=update.effective_user.id, action_id=f"cb:{query.id}",
                    action="secret_ack", round_id=round_id, phase_seq=seq,
                    message_id=query.message.message_id)
                await query.answer()
                await self._flush(context.bot, game_key=game, chat_id=chat_id, thread_id=topic)
                return True
            if (update.effective_chat.type not in {"group", "supergroup"} or
                    update.effective_chat.id != chat_id or forum_thread_id(query.message) != topic or
                    int(query.message.message_id) != int(state.get("panel_message_id") or 0)):
                raise ValueError("wa_stale_panel")
            if action == "dm":
                token = await self.service.mint_invite(game_key=game, chat_id=chat_id,
                    thread_id=topic, user_id=update.effective_user.id, round_id=round_id)
                me = await context.bot.get_me()
                await query.answer(url=f"https://t.me/{me.username}?start={token}")
                return True
            if action == "refresh":
                await query.answer()
                await self.publish(context.bot, game_key=game, chat_id=chat_id,
                                   thread_id=topic, force=True)
                return True
            if action not in {"join", "start", "leave", "stop"}:
                raise ValueError("wa_invalid_action")
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
            logger.warning("Word Arcade callback deferred: %s", type(exc).__name__)
            try:
                await query.answer("İşlem tamamlanamadı. Güncel paneli kullan.", show_alert=True)
            except TelegramError:
                pass
        return True

    async def deep_link(self, update, context, payload):
        if update.effective_chat.type != "private" or not str(payload).startswith("wa_"):
            return False
        message, user = update.effective_message, update.effective_user
        try:
            chat_id, topic, game = await self.service.accept_invite(payload, user_id=user.id)
            state = await self.service.current(game_key=game, chat_id=chat_id, thread_id=topic)
            if game == "harf_maratonu":
                sent = await message.reply_text(
                    "🔐 Harf Maratonu kapalı yanıtın: Kategorideki sözcüğü bu mesaja yanıt olarak yaz. "
                    "Yanıtın tur bitene kadar gruba gösterilmez.", protect_content=True)
            else:
                sent = await message.reply_text("Gizli kartın hazırlanıyor. Bu özel sohbeti açık tut.",
                                                protect_content=True)
            await self.service.register_private(game_key=game, chat_id=chat_id,
                thread_id=topic, user_id=user.id, round_id=state["round_id"],
                message_id=sent.message_id)
            if game == "yasak_harf":
                await self.deliver_private(context.bot, chat_id=chat_id, thread_id=topic)
            await self.publish(context.bot, game_key=game, chat_id=chat_id, thread_id=topic)
        except ValueError as exc:
            await message.reply_text(ui.error_message(str(exc)))
        except TelegramError as exc:
            logger.warning("Word Arcade private entry deferred: %s", type(exc).__name__)
            await message.reply_text("Özel sohbet işlemi tamamlanamadı. Gruptaki güncel düğmeyi tekrar kullan.")
        return True

    async def on_private_text(self, update, context, text, *, routes: list[dict]) -> bool:
        if update.effective_chat.type != "private" or getattr(update, "edited_message", None):
            return False
        user, message = update.effective_user, update.effective_message
        reply_id = int(getattr(getattr(message, "reply_to_message", None), "message_id", 0) or 0)
        if not reply_id:
            return False
        matches = []
        for route in routes:
            raw = str(route.get("game_type", ""))
            stem, marker, suffix = raw.rpartition("_t")
            game = stem if marker and suffix.isdecimal() else raw
            topic = int(suffix) if marker and suffix.isdecimal() else None
            if game != "harf_maratonu":
                continue
            chat_id = int(route["chat_id"])
            state = await self.service.current(game_key=game, chat_id=chat_id, thread_id=topic)
            if (state and state["phase"] == "collect" and
                    any(p["id"] == user.id and p["active"] for p in state["players"]) and
                    int(state.get("dm_messages", {}).get(str(user.id)) or 0) == reply_id):
                matches.append(state)
        if len(matches) != 1:
            return False
        state = matches[0]
        try:
            await self.service.act(game_key="harf_maratonu", chat_id=state["chat_id"],
                thread_id=state["thread_id"], user_id=user.id,
                action_id=f"dm:{user.id}:{message.message_id}", action="text",
                round_id=state["round_id"], phase_seq=state["phase_seq"],
                text=text, private=True, message_id=message.message_id,
                reply_to_message_id=reply_id,
                submitted_at=message.date.timestamp() if getattr(message, "date", None) else None)
            await self.publish(context.bot, game_key="harf_maratonu",
                chat_id=state["chat_id"], thread_id=state["thread_id"])
        except ValueError as exc:
            await message.reply_text(ui.error_message(str(exc)))
        return True

    async def on_text(self, update, context, text) -> bool:
        chat, user, message = update.effective_chat, update.effective_user, update.effective_message
        if not chat or not user or not message or chat.type not in {"group", "supergroup"}:
            return False
        topic = forum_thread_id(message)
        reply = getattr(message, "reply_to_message", None)
        candidates = []
        for game in GAME_KEYS:
            state = await self.service.current(game_key=game, chat_id=chat.id, thread_id=topic)
            if not state or not self.accepts_text(state):
                continue
            if reply and int(reply.message_id) != int(state.get("panel_message_id") or 0):
                continue
            if any(p["id"] == user.id and p["active"] for p in state["players"]):
                candidates.append(state)
        if len(candidates) != 1:
            return False
        if getattr(update, "edited_message", None) is message:
            if candidates[0]["game_key"] == "yasak_harf" and candidates[0].get("narrator_id") == user.id:
                return await self.on_clue_text(update, context, text)
            return True
        state = candidates[0]
        try:
            status, _ = await self.service.act(game_key=state["game_key"],
                chat_id=chat.id, thread_id=topic, user_id=user.id,
                action_id=f"msg:{chat.id}:{message.message_id}", action="text",
                round_id=state["round_id"], phase_seq=state["phase_seq"],
                text=text, message_id=message.message_id,
                submitted_at=message.date.timestamp() if getattr(message, "date", None) else None)
            if status == "applied":
                await self.publish(context.bot, game_key=state["game_key"],
                                   chat_id=chat.id, thread_id=topic)
        except ValueError:
            return True
        except TelegramError as exc:
            self.service.metrics.inc("word_arcade_panel_delivery_failure_total")
            logger.warning("Word Arcade panel deferred: %s", type(exc).__name__)
        return True

    async def on_clue_text(self, update, context, text) -> bool:
        """Route narrator captions and edits before media or other game consumers."""
        chat, user, message = update.effective_chat, update.effective_user, update.effective_message
        if not chat or not user or not message or chat.type not in {"group", "supergroup"}:
            return False
        topic = forum_thread_id(message)
        state = await self.service.current(game_key="yasak_harf", chat_id=chat.id,
                                           thread_id=topic)
        if (not state or state["phase"] != "clue" or
                state.get("narrator_id") != user.id or
                not any(p["id"] == user.id and p["active"] for p in state["players"])):
            return False
        reply = getattr(message, "reply_to_message", None)
        if reply and int(reply.message_id) != int(state.get("panel_message_id") or 0):
            return False
        edited = getattr(update, "edited_message", None) is message
        if edited and not getattr(message, "edit_date", None):
            return True
        if edited:
            digest = hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:12]
            action_id = f"edit:{message.message_id}:{int(message.edit_date.timestamp())}:{digest}"
            operation = "edit_clue"
        else:
            action_id = f"msg:{chat.id}:{message.message_id}"
            operation = "text"
        try:
            status, _ = await self.service.act(game_key="yasak_harf", chat_id=chat.id,
                thread_id=topic, user_id=user.id, action_id=action_id,
                action=operation, round_id=state["round_id"],
                phase_seq=state["phase_seq"], text=text,
                message_id=message.message_id,
                submitted_at=message.date.timestamp() if getattr(message, "date", None) else None)
            if status == "applied":
                await self.publish(context.bot, game_key="yasak_harf",
                                   chat_id=chat.id, thread_id=topic)
        except ValueError:
            return True
        except TelegramError as exc:
            self.service.metrics.inc("word_arcade_panel_delivery_failure_total")
            logger.warning("Word Arcade clue panel deferred: %s", type(exc).__name__)
        return True

    async def cancel_for(self, update, context) -> bool:
        chat, user, message = update.effective_chat, update.effective_user, update.effective_message
        if not chat or not user or not message or chat.type not in {"group", "supergroup"}:
            return False
        topic, cancelled = forum_thread_id(message), False
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
