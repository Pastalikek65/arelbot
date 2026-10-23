"""Telegram adapter for the five durable social arcade games."""
from __future__ import annotations

import logging

from telegram.error import BadRequest, TelegramError

from src.games import social_arcade as rules
from src.services import social_arcade_ui as ui
from src.storage.game_scope import forum_thread_id

from .handler_utils import is_admin

logger = logging.getLogger(__name__)
GAME_KEYS = rules.GAME_KEYS


class SocialArcadeHandler:
    GAME_KEYS = GAME_KEYS

    def __init__(self, service, sudo_manager=None, authorization=None):
        self.service = service
        self.sudo_manager = sudo_manager
        self.authorization = authorization

    async def _moderator(self, update, context):
        return await is_admin(update, context, self.sudo_manager,
                              authorization=self.authorization)

    @staticmethod
    async def _private_group_identity(bot, *, chat_id, thread_id, user_id, query_id, identity):
        """Reveal only after a harmless request proves ephemeral delivery."""
        common = {"chat_id": chat_id}
        if thread_id is not None:
            common["message_thread_id"] = thread_id

        async def send(text, *, callback_id=None):
            private = {"receiver_user_id": user_id}
            if callback_id:
                private["callback_query_id"] = callback_id
            return await bot.do_api_request("sendMessage", api_kwargs={
                **common, "text": text, "ephemeral_message_parameters": private})

        def verified(value):
            return (isinstance(value, dict) and value.get("message_id") == 0 and
                    (value.get("receiver_user") or {}).get("id") == user_id and
                    bool(value.get("ephemeral_message_id")))

        async def remove_public(value):
            if isinstance(value, dict) and value.get("message_id"):
                try:
                    await bot.delete_message(chat_id=chat_id,
                                             message_id=int(value["message_id"]))
                except TelegramError:
                    pass

        probe = None
        try:
            probe = await send("🔐 Kimim Ben kartı hazırlanıyor…", callback_id=query_id)
            if not verified(probe):
                await remove_public(probe)
                return False
            revealed = await send(f"🔐 Öznenin kimliği: {identity}")
            if not verified(revealed):
                await remove_public(revealed)
                return False
            return True
        except TelegramError:
            return False
        finally:
            if verified(probe):
                try:
                    await bot.do_api_request("deleteEphemeralMessage", api_kwargs={
                        "chat_id": chat_id, "receiver_user_id": user_id,
                        "ephemeral_message_id": probe["ephemeral_message_id"]})
                except TelegramError:
                    pass

    @staticmethod
    def accepts_text(state):
        return (state.get("game_key") == "ciz_bil" and state.get("phase") == "guess"
                and state.get("phase_ready") and bool(state.get("drawing_message_id")))

    async def deliver_drawing(self, bot, *, chat_id, thread_id=None):
        claim = await self.service.claim_delivery(game_key="ciz_bil", chat_id=chat_id,
            thread_id=thread_id, kind="drawing")
        if not claim:
            return
        sent = await bot.send_photo(chat_id=chat_id, photo=claim["drawing_file_id"],
            caption="✍️ Çizim hazır. Tahminleri grupta normal mesaj olarak yazın.",
            message_thread_id=thread_id)
        await self.service.acknowledge_drawing(game_key="ciz_bil", chat_id=chat_id,
            thread_id=thread_id, round_id=claim["round_id"],
            phase_seq=claim["phase_seq"], message_id=sent.message_id)

    async def publish(self, bot, *, game_key, chat_id, thread_id=None):
        state = await self.service.claim_delivery(game_key=game_key, chat_id=chat_id,
                                                  thread_id=thread_id, kind="panel")
        if not state:
            return False
        message_id = state.get("panel_message_id")
        old_id = message_id
        fresh_phase = (game_key == "ciz_bil" and bool(message_id) and
                       state.get("panel_phase_seq") != state["phase_seq"])
        if fresh_phase:
            message_id = None
        kwargs = {"chat_id": chat_id, "text": ui.group_text(state), "parse_mode": "HTML",
                  "reply_markup": ui.group_keyboard(state)}
        if message_id:
            try:
                await bot.edit_message_text(message_id=int(message_id), **kwargs)
            except BadRequest as exc:
                reason = str(exc).lower()
                if "message is not modified" in reason:
                    pass
                elif any(term in reason for term in ("message to edit not found", "message can't be edited")):
                    message_id = None
                else:
                    raise
        if not message_id:
            sent = await bot.send_message(message_thread_id=thread_id, **kwargs)
            message_id = sent.message_id
        await self.service.acknowledge_panel(game_key=game_key, chat_id=chat_id,
            thread_id=thread_id, round_id=state["round_id"],
            expected_version=state["version"], message_id=message_id)
        if fresh_phase and old_id != message_id:
            try:
                cleanup = getattr(bot, "edit_message_reply_markup", None)
                if callable(cleanup):
                    await cleanup(chat_id=chat_id, message_id=old_id, reply_markup=None)
            except TelegramError:
                pass
        return True

    async def deliver_private(self, bot, *, game_key, chat_id, thread_id=None):
        state = await self.service.current(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
        if not state or state["phase"] in rules.TERMINAL:
            return
        for uid in state.get("private_recipients", []):
            claimed = await self.service.claim_delivery(game_key=game_key, chat_id=chat_id,
                thread_id=thread_id, kind="private", user_id=uid)
            if not claimed:
                continue
            try:
                sent = await bot.send_message(chat_id=uid, text=ui.private_text(claimed, uid),
                    parse_mode="HTML", reply_markup=ui.private_keyboard(claimed, uid),
                    protect_content=True)
            except TelegramError:
                self.service.metrics.inc("social_arcade_private_delivery_failure_total")
                continue
            try:
                await self.service.acknowledge_private(game_key=game_key, chat_id=chat_id,
                    thread_id=thread_id, round_id=claimed["round_id"],
                    phase_seq=claimed["phase_seq"], user_id=uid, message_id=sent.message_id,
                    expected_version=claimed["version"])
            except ValueError:
                # The phase changed during delivery; the next pass refreshes the DM.
                continue

    async def process(self, bot, row):
        state = row.get("state", row)
        game_key, chat_id, thread_id = state["game_key"], int(state["chat_id"]), state.get("thread_id")
        await self.service.advance(game_key=game_key, chat_id=chat_id,
            thread_id=thread_id, round_id=state["round_id"])
        try:
            await self.service.settle_scores(game_key=game_key, chat_id=chat_id,
                                             thread_id=thread_id)
        except Exception as exc:  # noqa: BLE001 - transport boundary retains durable retries
            self.service.metrics.inc("social_arcade_score_retry_total")
            logger.warning("Social arcade rewards deferred: %s", type(exc).__name__)
        if game_key == "ciz_bil":
            await self.deliver_drawing(bot, chat_id=chat_id, thread_id=thread_id)
            fresh = await self.service.current(game_key=game_key, chat_id=chat_id,
                                               thread_id=thread_id)
            if fresh and fresh.get("drawing_pending"):
                return
        # A group-action phase needs one preparation receipt and one actionable receipt.
        for _ in range(3):
            await self.publish(bot, game_key=game_key, chat_id=chat_id, thread_id=thread_id)
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
            await message.reply_text("Bu oyun için önce bir grupta oyun salonunu aç.")
            return True
        thread_id = forum_thread_id(message)
        action = str((getattr(context, "args", None) or ["open"])[0]).casefold()
        try:
            if action in {"open", "aç", "ac", "durum"}:
                if action == "durum":
                    state = await self.service.current(game_key=game_key, chat_id=chat.id,
                                                       thread_id=thread_id)
                    if not state:
                        raise ValueError("sa_not_found")
                else:
                    _, state = await self.service.open(game_key=game_key, chat_id=chat.id,
                        thread_id=thread_id, user_id=user.id, user_name=user.first_name)
                await self._flush(context, game_key=game_key, chat_id=chat.id, thread_id=thread_id)
                return True
            state = await self.service.current(game_key=game_key, chat_id=chat.id,
                                               thread_id=thread_id)
            if not state:
                raise ValueError("sa_not_found")
            aliases = {"katıl": "join", "katil": "join", "başla": "start", "basla": "start",
                       "bitir": "stop", "iptal": "stop", "ayrıl": "leave", "ayril": "leave"}
            operation = aliases.get(action)
            if not operation:
                raise ValueError("sa_invalid_action")
            await self.service.act(game_key=game_key, chat_id=chat.id, thread_id=thread_id,
                user_id=user.id, user_name=user.first_name, action_id=f"cmd:{message.message_id}",
                action=operation, round_id=state["round_id"],
                moderator=await self._moderator(update, context) if operation == "stop" else False,
                submitted_at=message.date.timestamp() if getattr(message, "date", None) else None)
            await self._flush(context, game_key=game_key, chat_id=chat.id, thread_id=thread_id)
        except ValueError as exc:
            await message.reply_text(ui.error_message(str(exc)))
        return True

    async def deep_link(self, update, context, payload):
        if not str(payload).startswith("sa_"):
            return False
        if update.effective_chat.type != "private":
            return True
        try:
            key, chat_id, thread_id, _ = await self.service.accept_invite(
                payload, user_id=update.effective_user.id)
            await update.effective_message.reply_text("Özel sohbet hazır. Gruba dönebilirsin.")
            await self._flush(context, game_key=key, chat_id=chat_id, thread_id=thread_id)
        except ValueError as exc:
            await update.effective_message.reply_text(ui.error_message(str(exc)))
        return True

    async def callback(self, update, context):
        query = update.callback_query
        if not query or not str(query.data).startswith("sa:"):
            return False
        try:
            key, chat_id, thread_id, round_id, phase_seq, code = ui.parse_callback(query.data)
            state = await self.service.current(game_key=key, chat_id=chat_id,
                                               thread_id=thread_id)
            if not state or state["round_id"] != round_id or state["phase_seq"] != phase_seq:
                raise ValueError("sa_stale_round")
            private = update.effective_chat.type == "private"
            uid = update.effective_user.id
            if private:
                receipt = state.get("private_panels", {}).get(str(uid), {})
                if (receipt.get("phase_seq") != phase_seq or
                        int(receipt.get("message_id") or 0) != int(query.message.message_id)):
                    raise ValueError("sa_stale_private_panel")
                if uid not in state.get("private_recipients", []):
                    raise ValueError("sa_invalid_action")
            elif (update.effective_chat.id != chat_id or forum_thread_id(query.message) != thread_id or
                  int(query.message.message_id) != int(state.get("panel_message_id") or 0)):
                raise ValueError("sa_stale_panel")
            if code == "ready" and not private:
                if rules._person(state, uid)["ready"]:
                    await query.answer("Özel sohbetin zaten hazır.")
                    return True
                if key in {"kimim_ben", "ciz_bil"}:
                    # A successful DM proves that this user has already opened the bot.
                    # The link remains available when Telegram rejects the first DM.
                    try:
                        await context.bot.send_message(chat_id=uid,
                            text=("Çiz Bil özel sohbetin hazır. Sıran geldiğinde çizilecek kelime burada görünecek."
                                  if key == "ciz_bil" else
                                  "Kimim Ben özel sohbetin hazır. Oyun başlayınca kimlik kartın buraya gelecek."),
                            protect_content=True)
                    except TelegramError:
                        pass
                    else:
                        await self.service.act(game_key=key, chat_id=chat_id,
                            thread_id=thread_id, user_id=uid,
                            action_id=f"dm-ready:{query.id}", action="ready",
                            round_id=round_id, phase_seq=phase_seq)
                        await query.answer("Özel sohbet hazır.")
                        await self._flush(context, game_key=key, chat_id=chat_id,
                                          thread_id=thread_id)
                        return True
                payload = await self.service.mint_invite(game_key=key, chat_id=chat_id,
                    thread_id=thread_id, user_id=uid, round_id=round_id, request_id=query.id)
                identity = await context.bot.get_me()
                await query.answer(url=f"https://t.me/{identity.username}?start={payload}")
                return True
            if code == "card" and key == "kimim_ben" and not private:
                if state["phase"] not in {"question", "answer"}:
                    raise ValueError("sa_wrong_phase")
                subject = state["players"][state["subject_index"]]["id"]
                rules._person(state, uid)
                if uid == subject:
                    await query.answer("Kendi kimliğini göremezsin; soru sor veya tahmin et.",
                                       show_alert=True)
                    return True
                identity = state["identities"][str(subject)]
                delivered = await self._private_group_identity(context.bot,
                    chat_id=chat_id, thread_id=thread_id, user_id=uid,
                    query_id=query.id, identity=identity)
                if delivered:
                    await query.answer("Kimliğin yalnız sana gösterildi; özel sohbette de var.")
                else:
                    await query.answer(f"Öznenin kimliği: {identity}", show_alert=True)
                return True
            if code == "j" and not private:
                operation, value = "join", None
            elif code == "s" and not private:
                operation, value = "start", None
            elif code == "x" and not private:
                operation, value = "stop", None
            elif code == "leave" and not private:
                operation, value = "leave", None
            elif code.startswith("f") and not private:
                operation, value = "shot", int(code[1:], 36)
            elif code.startswith("a") and not private:
                operation, value = "answer", int(code[1:])
            elif code.startswith("w") and not private:
                operation, value = "wager", int(code[1:])
            elif code == "pass" and not private:
                operation, value = "pass", None
            elif code.startswith("l") and private:
                operation, value = "lie", int(code[1:])
            elif code.startswith("v") and private:
                operation, value = "vote", int(code[1:])
            elif code in {"yes", "no", "drink", "swap", "refuse"} and private:
                operation, value = code, None
            elif code.startswith("p") and private:
                operation, value = "poison", int(code[1:])
            elif code.startswith("c") and private:
                operation, value = "serve", int(code[1:])
            else:
                raise ValueError("sa_invalid_action")
            await self.service.act(game_key=key, chat_id=chat_id, thread_id=thread_id,
                user_id=uid, user_name=update.effective_user.first_name,
                action_id=f"cb:{query.id}", action=operation, value=value,
                round_id=round_id, phase_seq=phase_seq,
                private_message_id=query.message.message_id if private else None,
                moderator=await self._moderator(update, context) if operation == "stop" else False)
            await query.answer()
            await self._flush(context, game_key=key, chat_id=chat_id, thread_id=thread_id)
        except (ValueError, TypeError) as exc:
            await query.answer(ui.error_message(str(exc)), show_alert=True)
        except Exception as exc:  # noqa: BLE001 - transport boundary retains durable retries
            logger.warning("Social arcade callback deferred: %s", type(exc).__name__)
            try:
                await query.answer("İşlem tamamlanamadı. Güncel paneli kullan.", show_alert=True)
            except TelegramError:
                pass
        return True

    async def on_text(self, update, context, text):
        chat, user, message = update.effective_chat, update.effective_user, update.effective_message
        if not chat or chat.type not in {"group", "supergroup"} or not user or not message:
            return False
        topic = forum_thread_id(message)
        state = await self.service.current(game_key="ciz_bil", chat_id=chat.id,
                                           thread_id=topic)
        if not state or not self.accepts_text(state):
            return False
        if not any(p["id"] == user.id for p in state["players"]):
            return False
        if getattr(update, "edited_message", None) is message:
            return True
        try:
            await self.service.act(game_key="ciz_bil", chat_id=chat.id,
                thread_id=topic, user_id=user.id, action_id=f"guess:{message.message_id}",
                action="draw_guess", text=str(text), round_id=state["round_id"],
                phase_seq=state["phase_seq"],
                input_message_id=message.message_id,
                submitted_at=message.date.timestamp() if getattr(message, "date", None) else None)
        except ValueError:
            return True
        try:
            await self._flush(context, game_key="ciz_bil", chat_id=chat.id,
                              thread_id=topic)
        except Exception as exc:  # noqa: BLE001 - scored guess remains durable; worker retries panel
            logger.warning("Draw guess panel delivery deferred: %s", type(exc).__name__)
        return True

    async def on_private_photo(self, update, context, *, routes):
        chat, user, message = update.effective_chat, update.effective_user, update.effective_message
        if not chat or chat.type != "private" or not user or not message or not getattr(message, "photo", None):
            return False
        matches = []
        reply_id = getattr(getattr(message, "reply_to_message", None), "message_id", None)
        for route in routes:
            stored = str(route.get("game_type") or "")
            if stored != "ciz_bil" and not stored.startswith("ciz_bil_t"):
                continue
            topic = int(stored.removeprefix("ciz_bil_t")) if stored.startswith("ciz_bil_t") else None
            group_id = int(route["chat_id"])
            state = await self.service.current(game_key="ciz_bil", chat_id=group_id,
                                               thread_id=topic)
            if not state or state["phase"] != "draw" or state["players"][state["artist_index"]]["id"] != user.id:
                continue
            receipt = state.get("private_panels", {}).get(str(user.id), {})
            if receipt.get("phase_seq") != state["phase_seq"]:
                continue
            if reply_id is not None and int(reply_id) != int(receipt.get("message_id") or 0):
                continue
            matches.append((group_id, topic, state, receipt))
        if not matches:
            return False
        if len(matches) != 1:
            await message.reply_text("Çizimi ilgili güncel gizli kelime mesajına yanıt olarak gönder.")
            return True
        if (str(getattr(message, "caption", None) or "").strip() or
                getattr(message, "forward_origin", None) is not None or
                int(getattr(message.photo[-1], "file_size", 0) or 0) > 10_000_000):
            await message.reply_text("Çizimi açıklamasız, iletilmemiş ve 10 MB altında bir fotoğraf olarak gönder.")
            return True
        group_id, topic, state, receipt = matches[0]
        try:
            await self.service.act(game_key="ciz_bil", chat_id=group_id,
                thread_id=topic, user_id=user.id, action_id=f"drawing:{message.message_id}",
                action="drawing", value=message.photo[-1].file_id,
                round_id=state["round_id"], phase_seq=state["phase_seq"],
                private_message_id=receipt["message_id"], input_message_id=message.message_id,
                submitted_at=message.date.timestamp() if getattr(message, "date", None) else None)
            await self._flush(context, game_key="ciz_bil", chat_id=group_id, thread_id=topic)
        except ValueError as exc:
            await message.reply_text(ui.error_message(str(exc)))
        except Exception as exc:
            logger.warning("Draw delivery deferred: %s", type(exc).__name__)
            await message.reply_text("Çizimin kaydedildi. Gruba gönderim yeniden denenecek.")
        return True

    async def on_private_text(self, update, context, text, *, routes):
        chat, user, message = update.effective_chat, update.effective_user, update.effective_message
        if not chat or chat.type != "private" or not user or not message or not text:
            return False
        candidates = []
        for route in routes:
            stored = str(route.get("game_type", ""))
            key = next((key for key in GAME_KEYS if stored == key or stored.startswith(key + "_t")), None)
            if key is None:
                continue
            topic = int(stored[len(key)+2:]) if stored.startswith(key + "_t") else None
            group_id = int(route["chat_id"])
            state = await self.service.current(game_key=key, chat_id=group_id, thread_id=topic)
            if not state or state["phase"] in rules.TERMINAL:
                continue
            recipient = user.id in state.get("private_recipients", [])
            receipt = state.get("private_panels", {}).get(str(user.id), {})
            if not recipient or receipt.get("phase_seq") != state["phase_seq"]:
                continue
            if key == "iki_dogru_bir_yalan" and state["phase"] == "author":
                action, value = "claim", str(text)
            elif key == "kimim_ben" and state["phase"] == "question" and user.id == state["players"][state["subject_index"]]["id"]:
                raw = str(text).strip()
                action, value = ("question", raw[1:].strip().rstrip("?").strip() + "?") if raw.startswith("?") else \
                                ("guess", raw[1:].strip()) if raw.startswith("=") else (None, None)
            else:
                continue
            reply_id = getattr(getattr(message, "reply_to_message", None), "message_id", None)
            if reply_id is not None and int(reply_id) != int(receipt.get("message_id") or 0):
                continue
            candidates.append((key, group_id, topic, state, receipt, action, value, reply_id))
        if not candidates:
            return False
        replied = [candidate for candidate in candidates if candidate[-1] is not None]
        if len(replied or candidates) != 1:
            await message.reply_text("Lütfen ilgili oyunun son özel paneline yanıt ver.")
            return True
        key, group_id, topic, state, receipt, action, value, _ = (replied or candidates)[0]
        if getattr(update, "edited_message", None) is message:
            return True
        if action is None:
            await message.reply_text("Soru için ? ve kimlik tahmini için = ile başla.")
            return True
        try:
            await self.service.act(game_key=key, chat_id=group_id, thread_id=topic,
                user_id=user.id, action_id=f"dm:{message.message_id}", action=action,
                text=value, round_id=state["round_id"], phase_seq=state["phase_seq"],
                private_message_id=receipt["message_id"],
                input_message_id=message.message_id,
                submitted_at=message.date.timestamp() if getattr(message, "date", None) else None)
            await self._flush(context, game_key=key, chat_id=group_id, thread_id=topic)
        except ValueError as exc:
            await message.reply_text(ui.error_message(str(exc)))
        return True

    async def cancel_for(self, update, context):
        chat, user, message = update.effective_chat, update.effective_user, update.effective_message
        if not chat or chat.type not in {"group", "supergroup"} or not user or not message:
            return False
        topic = forum_thread_id(message)
        moderator = await self._moderator(update, context)
        cancelled = False
        for key in GAME_KEYS:
            state = await self.service.current(game_key=key, chat_id=chat.id, thread_id=topic)
            if not state or state["phase"] in rules.TERMINAL:
                continue
            if state["owner_id"] != user.id and not moderator:
                continue
            try:
                await self.service.act(game_key=key, chat_id=chat.id, thread_id=topic,
                    user_id=user.id, action_id=f"cancel:{message.message_id}:{key}",
                    action="stop", round_id=state["round_id"], moderator=moderator)
                cancelled = True
                await self._flush(context, game_key=key, chat_id=chat.id, thread_id=topic)
            except ValueError:
                continue
        return cancelled
