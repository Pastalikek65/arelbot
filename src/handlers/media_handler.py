"""
Media Handler - FotoÄŸraf, Ses, DokÃ¼man iÅŸleyicileri

Orchestrator'dan ayrÄ±lmÄ±ÅŸ medya mantÄ±ÄŸÄ±.
"""
import logging
from datetime import datetime, timezone
from pathlib import Path
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from src.media.cookie_vault import CookieVaultError
from src.security.log_redaction import summarize_exception
from src.storage.state_scope import get_scoped_state, pop_scoped_state, set_scoped_state

logger = logging.getLogger(__name__)


class MediaHandler:
    def __init__(self, chat_engine, sudo_manager, tool_manager, vision_service=None, stt_service=None, tts_service=None, media_service=None, sticker_render_service=None, document_service=None, attachment_service=None):
        self.chat_engine = chat_engine
        self.sudo_manager = sudo_manager
        self.tool_manager = tool_manager
        self.vision_service = vision_service
        self.stt_service = stt_service
        self.tts_service = tts_service
        self.media_service = media_service
        self.document_service = document_service
        self.sticker_render_service = sticker_render_service
        self.attachment_service = attachment_service
        self.user_states = {}

    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.message.photo:
            return

        # A photo is passive input by default.  It enters the attachment
        # pipeline only after an explicit command, mention, or reply to Arel;
        # this prevents private-chat photos from unexpectedly starting vision
        # or sticker work.
        intent_state = get_scoped_state(
            self.user_states,
            user_id=update.effective_user.id,
            chat_id=update.effective_chat.id,
        ) or {}
        if not await self._has_explicit_photo_intent(update, context) and intent_state.get("state") != "awaiting_intent_photo":
            return

        if not await self._should_respond(update, context):
            return

        if not self.attachment_service:
            await update.message.reply_text("GÃ¶rsel iÅŸleme servisi bu ortamda etkin deÄŸil.")
            return
        photo = update.message.photo[-1]
        if intent_state.get("state") == "awaiting_intent_photo":
            status = await update.message.reply_text("Ä°ÅŸlemi kuyruÄŸa alÄ±yorum; gÃ¶rseli kendi kendine yorumlamaya kalkmÄ±yorum.")
            state = {
                "source_file_id": photo.file_id,
                "source_key": getattr(photo, "file_unique_id", None) or photo.file_id,
                "filename": "image.jpg",
                "mime_type": "image/jpeg",
                "file_size": getattr(photo, "file_size", None),
                "caption": update.message.caption or "",
                "reply_to_message_id": update.message.message_id,
                "user_name": update.effective_user.full_name,
                "username": update.effective_user.username,
            }
            try:
                job_id, duplicate = await self.enqueue_photo_action(
                    chat_id=update.effective_chat.id,
                    user_id=update.effective_user.id,
                    status_message_id=status.message_id,
                    state=state,
                    action=str(intent_state.get("action") or "vision"),
                )
                await status.edit_text(
                    f"â³ {'Bu gÃ¶rsel iÃ§in aktif iÅŸ zaten var' if duplicate else 'GÃ¶rsel iÅŸlemi kuyruÄŸa alÄ±ndÄ±'}: <code>{job_id}</code>",
                    parse_mode="HTML",
                )
            except Exception as exc:
                logger.warning("Explicit photo intent enqueue failed: %s", summarize_exception(exc, label="photo_intent_enqueue"))
                await status.edit_text("GÃ¶rsel iÅŸlemi baÅŸlatÄ±lamadÄ±; biraz sonra tekrar dene.")
            pop_scoped_state(self.user_states, user_id=update.effective_user.id, chat_id=update.effective_chat.id)
            return
        status = await update.message.reply_text(
            "ğŸ–¼ï¸ <b>GÃ¶rseli nasÄ±l iÅŸleyelim?</b>\n\n"
            "Arel kendi kafasÄ±na gÃ¶re sticker yapmaya baÅŸlamÄ±yor; seÃ§imi sen yapÄ±yorsun.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("ğŸ” GÃ¶rseli analiz et", callback_data=f"photo_action:{update.effective_user.id}:vision"),
                 InlineKeyboardButton("ğŸ”¤ Metni oku", callback_data=f"photo_action:{update.effective_user.id}:ocr")],
                [InlineKeyboardButton("âœ¨ Sticker yap", callback_data=f"photo_action:{update.effective_user.id}:sticker"),
                 InlineKeyboardButton("Ä°ptal", callback_data=f"photo_action:{update.effective_user.id}:cancel")],
            ]),
            parse_mode="HTML",
        )
        set_scoped_state(
            self.user_states,
            user_id=update.effective_user.id,
            chat_id=update.effective_chat.id,
            value={
                "state": "awaiting_photo_action",
                "source_file_id": photo.file_id,
                "source_key": getattr(photo, "file_unique_id", None) or photo.file_id,
                "filename": "image.jpg",
                "mime_type": "image/jpeg",
                "file_size": getattr(photo, "file_size", None),
                "caption": update.message.caption or "",
                "reply_to_message_id": update.message.message_id,
                "user_name": update.effective_user.full_name,
                "username": update.effective_user.username,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "status_message_id": status.message_id,
            },
        )

    async def _has_explicit_photo_intent(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        message = update.effective_message or update.message
        if not message:
            return False
        caption = (getattr(message, "caption", None) or "").strip().casefold()
        if caption:
            first_token = caption.split(maxsplit=1)[0].split("@", 1)[0]
            if first_token in {"/sticker", "/incele", "/gorsel", "/gÃ¶rsel", "/ocr", "/analiz"}:
                return True
            if "arel" in caption:
                return True
            bot_info = getattr(context, "bot_info", None)
            username = getattr(bot_info, "username", None)
            if username and f"@{username.casefold()}" in caption:
                return True
        reply = getattr(message, "reply_to_message", None)
        bot_info = getattr(context, "bot_info", None)
        if reply and bot_info and getattr(getattr(reply, "from_user", None), "id", None) == getattr(bot_info, "id", None):
            return True
        return False

    async def enqueue_photo_action(self, *, chat_id: int, user_id: int, status_message_id: int, state: dict, action: str):
        """Queue an explicit photo action after the user chooses it inline."""
        if not self.attachment_service:
            raise RuntimeError("ATTACHMENT_QUEUE_UNAVAILABLE")
        action = str(action).casefold()
        if action not in {"vision", "ocr", "sticker"}:
            raise ValueError("PHOTO_ACTION_INVALID")
        job_type = "sticker" if action == "sticker" else "vision"
        caption = str(state.get("caption") or "")
        if action == "ocr":
            caption = (
                "Bu gÃ¶rseldeki yazÄ±larÄ± oku. YalnÄ±zca gÃ¶rselde gerÃ§ekten gÃ¶rdÃ¼ÄŸÃ¼n metni yaz; "
                "okunamayan bÃ¶lÃ¼mleri [belirsiz] olarak belirt."
            )
        return await self.attachment_service.submit(
            job_type=job_type,
            chat_id=chat_id,
            user_id=user_id,
            status_message_id=status_message_id,
            source_file_id=state["source_file_id"],
            source_key=state["source_key"],
            filename=state.get("filename") or "image.jpg",
            mime_type=state.get("mime_type") or "image/jpeg",
            file_size=state.get("file_size"),
            caption=caption,
            reply_to_message_id=state.get("reply_to_message_id"),
            user_name=state.get("user_name"),
            username=state.get("username"),
            sticker_kind="static" if action == "sticker" else None,
        )

    async def handle_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.message.document:
            return

        if not await self._should_respond(update, context):
            return

        doc = update.message.document
        if not doc.file_name:
            return

        file_name = doc.file_name.lower()

        # Administrator cookie upload flow. The document is consumed once and
        # never copied to a public or predictable path.
        user_state = get_scoped_state(
            self.user_states,
            user_id=update.effective_user.id,
            chat_id=update.effective_chat.id,
        ) or {}
        if user_state.get("state") == "awaiting_media_cookie" and self.media_service:
            platform = user_state.get("platform")
            try:
                await self.media_service.save_cookie_file(platform, doc.file_id, update.effective_user.id)
                pop_scoped_state(
                    self.user_states,
                    user_id=update.effective_user.id,
                    chat_id=update.effective_chat.id,
                )
                await update.message.reply_text(f"Cookie encrypted vaulta kaydedildi: {platform}.")
            except CookieVaultError:
                await update.message.reply_text("Cookie kaydedilemedi; dosya biÃ§imini ve yÃ¶netici yetkisini kontrol et.")
            except Exception as exc:
                logger.error("Media cookie upload failed: %s", summarize_exception(exc, label="media_cookie_upload"))
                await update.message.reply_text("Cookie dosyasÄ± iÅŸlenemedi.")
            return

        # Animated GIFs are passive media.  Sending one must not silently
        # invoke document/RAG processing and produce a worker error in chat.
        # Users can still explicitly turn it into a sticker by replying with
        # /sticker, which is handled by the command route.
        if file_name.endswith(".gif") or str(getattr(doc, "mime_type", "")).casefold() == "image/gif":
            return

        # Telegram uses ``document`` for every arbitrary file type, including
        # videos, archives and executables.  Those are not RAG inputs.  Keep
        # them passive instead of creating a status message that immediately
        # turns into a user-facing validation error.  Supported documents
        # (PDF, TXT and DOCX) still follow the durable ingestion path below.
        allowed_extensions = {".pdf", ".txt", ".docx"}
        if Path(file_name).suffix.casefold() not in allowed_extensions:
            logger.debug("Ignoring unsupported passive Telegram document")
            return

        if not self.document_service:
            await update.message.reply_text("Belge kuyruÄŸu bu ortamda etkin deÄŸil (DOCUMENT_QUEUE_UNAVAILABLE).")
            return

        # Only metadata validation and a durable enqueue happen in the update
        # handler. Telegram download and RAG parsing run in a worker.
        status_msg = await update.message.reply_text("ğŸ“¥ Belge kuyruÄŸa alÄ±nÄ±yorâ€¦")
        try:
            job_id, duplicate = await self.document_service.submit(
                chat_id=update.effective_chat.id,
                user_id=update.effective_user.id,
                status_message_id=status_msg.message_id,
                source_file_id=doc.file_id,
                source_key=getattr(doc, "file_unique_id", None) or doc.file_id,
                filename=doc.file_name,
                mime_type=getattr(doc, "mime_type", None),
                file_size=getattr(doc, "file_size", None),
            )
            if duplicate:
                await status_msg.edit_text(
                    f"â³ Bu belge iÃ§in aktif bir iÅŸ zaten var.\nÄ°ÅŸ: <code>{job_id}</code>",
                    parse_mode="HTML",
                )
            else:
                await status_msg.edit_text(
                    f"â³ Belge kuyruÄŸa alÄ±ndÄ±.\nÄ°ÅŸ: <code>{job_id}</code>",
                    parse_mode="HTML",
                )
        except Exception as exc:
            logger.warning("Document enqueue failed: %s", summarize_exception(exc, label="document_enqueue"))
            await status_msg.edit_text("âŒ Belge kuyruÄŸa alÄ±namadÄ±; dosya sÄ±nÄ±rlarÄ±nÄ± kontrol edip tekrar dene.", parse_mode="HTML")

    async def handle_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.message.voice:
            return

        if not await self._should_respond(update, context):
            return

        user_id = update.effective_user.id
        chat_id = update.effective_chat.id

        if not self.attachment_service:
            await update.message.reply_text("Ses iÅŸleme servisi bu ortamda etkin deÄŸil.")
            return
        status = await update.message.reply_text("ğŸ§ Ses kuyruÄŸa alÄ±nÄ±yorâ€¦")
        voice = update.message.voice
        try:
            job_id, duplicate = await self.attachment_service.submit(
                job_type="voice", chat_id=update.effective_chat.id, user_id=update.effective_user.id,
                status_message_id=status.message_id, source_file_id=voice.file_id,
                source_key=getattr(voice, "file_unique_id", None) or voice.file_id,
                filename="voice.ogg", mime_type="audio/ogg", file_size=getattr(voice, "file_size", None),
                reply_to_message_id=update.message.message_id, user_name=update.effective_user.full_name,
                username=update.effective_user.username,
            )
            await status.edit_text((f"â³ Bu ses iÃ§in aktif iÅŸ zaten var.\nÄ°ÅŸ: <code>{job_id}</code>" if duplicate else f"â³ Ses kuyruÄŸa alÄ±ndÄ±.\nÄ°ÅŸ: <code>{job_id}</code>"), parse_mode="HTML")
        except Exception as exc:
            logger.warning("Voice enqueue failed: %s", summarize_exception(exc, label="voice_enqueue"))
            await status.edit_text("âŒ Ses kuyruÄŸa alÄ±namadÄ±; biraz sonra tekrar dene.")

    async def handle_sticker_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = update.message.reply_to_message
        if not msg or not (msg.photo or msg.document or msg.video or msg.animation or msg.video_note):
            await update.message.reply_text(
                "<b>ğŸ–¼ï¸/ğŸ“¹ LÃ¼tfen bir resim veya kÄ±sa videoya yanÄ±t vererek /sticker yazÄ±n.</b>",
                parse_mode='HTML'
            )
            return

        if not self.attachment_service:
            await update.message.reply_text("Sticker iÅŸleme servisi bu ortamda etkin deÄŸil.")
            return
        status_msg = await update.message.reply_text("<b>âœ¨ Sticker kuyruÄŸa alÄ±nÄ±yorâ€¦</b>", parse_mode="HTML")
        try:
            source = msg.photo[-1] if msg.photo else (msg.document or msg.video or msg.animation or msg.video_note)
            is_video = bool(msg.video or msg.animation or msg.video_note)
            job_id, duplicate = await self.attachment_service.submit(
                job_type="sticker", chat_id=update.effective_chat.id, user_id=update.effective_user.id,
                status_message_id=status_msg.message_id, source_file_id=source.file_id,
                source_key=getattr(source, "file_unique_id", None) or source.file_id,
                filename=getattr(source, "file_name", None) or ("video.mp4" if is_video else "image.jpg"),
                mime_type=getattr(source, "mime_type", None), file_size=getattr(source, "file_size", None),
                reply_to_message_id=msg.message_id, user_name=update.effective_user.full_name,
                username=update.effective_user.username, sticker_kind="video" if is_video else "static",
            )
            await status_msg.edit_text((f"â³ Bu medya iÃ§in aktif iÅŸ zaten var.\nÄ°ÅŸ: <code>{job_id}</code>" if duplicate else f"â³ Sticker kuyruÄŸa alÄ±ndÄ±.\nÄ°ÅŸ: <code>{job_id}</code>"), parse_mode="HTML")
        except Exception as exc:
            logger.warning("Sticker enqueue failed: %s", summarize_exception(exc, label="sticker_enqueue"))
            await status_msg.edit_text("âŒ Sticker kuyruÄŸa alÄ±namadÄ±; biraz sonra tekrar dene.")

    async def _should_respond(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        if not update.effective_chat:
            return False

        chat_id = update.effective_chat.id
        user_id = update.effective_user.id

        if self.sudo_manager.is_blacklisted(user_id, chat_id):
            logger.warning("Prevented interaction from blacklisted actor")
            return False

        if update.effective_chat.type == "private":
            return True

        message = update.effective_message
        if not message:
            return False

        if not context.bot_info:
            context.bot_info = await context.bot.get_me()

        bot_username = context.bot_info.username
        text = (message.text or message.caption or "").lower()
        triggers = ["arel", f"@{bot_username}".lower()]

        if any(trigger in text for trigger in triggers):
            return True

        if message.reply_to_message and message.reply_to_message.from_user.id == context.bot_info.id:
            if message.reply_to_message.video or message.reply_to_message.animation:
                return any(trigger in text for trigger in triggers)
            return True

        return False

