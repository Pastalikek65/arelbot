"""
Quote Handler - Alıntı sticker sistemi handler'ı.

Orchestrator'dan ayrıştırılmış alıntı oluşturma ve yönetme mantığı.
"""
import logging
import html
from typing import TYPE_CHECKING

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from src.quotes.render_service import QuoteRenderService
from src.quotes.options import QuoteOptionsError, normalize_quote_options, normalize_template_name
from src.security.log_redaction import summarize_exception
from src.storage.handler_data_port import HandlerDataPort
from src.features.chat_feature_policy import is_chat_feature_enabled

if TYPE_CHECKING:
    from src.chat_engine import ChatEngine
    from src.sudo_manager import SudoManager

logger = logging.getLogger(__name__)


class QuoteHandler:
    """Alıntı sticker sistemi için handler."""

    def __init__(self, chat_engine: 'ChatEngine', sudo_manager: 'SudoManager', render_service: QuoteRenderService | None = None, quote_service=None, data_port=None):
        self.chat_engine = chat_engine
        self.data_port = data_port or HandlerDataPort.from_engine(chat_engine)
        self.sudo_manager = sudo_manager
        self.render_service = render_service or QuoteRenderService()
        self.quote_service = quote_service

    async def _quote_enabled(self, chat_id: int) -> bool:
        return await is_chat_feature_enabled(self.data_port, chat_id, "q_enabled", True)

    async def handle_q_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Ana alıntı oluşturma komutu (/q)."""
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id

        if not await self._quote_enabled(chat_id):
            return
        try:
            defaults = await self.data_port.get_chat_settings(chat_id)
        except Exception:
            defaults = {}

        options = {
            'count': 1,
            'is_png': bool(defaults.get('q_is_png', False)),
            'show_reply': bool(defaults.get('q_show_reply', False)),
            'scale': defaults.get('q_scale', 1.0),
            'name_color': defaults.get('q_name_color'),
            'text_color': defaults.get('q_text_color'),
            'bg_color': defaults.get('q_bg_color'),
            'theme': defaults.get('q_theme', 'dark'),
            'ai_query': None,
            'as_file': bool(defaults.get('q_as_file', False)),
            'crop': bool(defaults.get('q_crop', False)),
            'rate_enabled': defaults.get('q_rate_enabled', True),
            'emoji': defaults.get('q_emoji', "✨")
        }

        args = [a.lower() for a in context.args]

        if args and args[0] == "*":
            options['ai_query'] = " ".join(context.args[1:])
        else:
            for arg in args:
                if arg.isdigit():
                    options['count'] = min(int(arg), 10)
                elif arg == 'p':
                    options['is_png'] = True
                elif arg == 'i':
                    options['as_file'] = True
                elif arg == 'r':
                    options['show_reply'] = True
                elif arg == 'rate':
                    options['rate_enabled'] = True
                elif arg == 'c':
                    options['crop'] = True
                elif arg.startswith('s'):
                    try:
                        options['scale'] = float(arg[1:])
                    except Exception:
                        pass
                elif arg.startswith('#') or (len(arg) == 6 and all(c in '0123456789abcdef' for c in arg)):
                    options['bg_color'] = arg if arg.startswith('#') else f"#{arg}"
                elif arg.startswith('theme='):
                    options['theme'] = arg.split('=', 1)[1]

        messages_to_quote = []

        if options['ai_query']:
            messages_to_quote = await self._get_ai_selected_messages(chat_id, options)
        elif update.message.reply_to_message:
            messages_to_quote = await self._get_reply_messages(update, options, chat_id)

        if not messages_to_quote:
            await update.message.reply_text("⚠️ Alıntılanacak mesaj bulunamadı.")
            return

        await self._generate_and_send_quote(update, context, messages_to_quote, options, chat_id)

    async def handle_qtop_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """En iyi alıntıları gösterme komutu (/qtop)."""
        if not await self._quote_enabled(update.effective_chat.id):
            return
        chat_id = update.effective_chat.id
        keyboard = [
            [InlineKeyboardButton("🏆 En İyi Alıntıları Göster", switch_inline_query_current_chat=f"top:{chat_id}")]
        ]
        await update.message.reply_text(
            "✨ <b>Grubun en iyi alıntılarını keşfet!</b>\n\n"
            "Aşağıdaki butona basarak en çok beğenilen ve alıntılanan mesajları inline modda inceleyebilirsin.",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    async def handle_qrand_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Rastgele alıntı gösterme komutu (/qrand)."""
        if not await self._quote_enabled(update.effective_chat.id):
            return
        chat_id = update.effective_chat.id
        try:
            quote = await self.data_port.get_random_quote(chat_id)
            if quote:
                await update.message.reply_sticker(sticker=quote['sticker_file_id'])
            else:
                await update.message.reply_text(
                    "🕵️‍♂️ Bu grupta henüz hiç alıntı sticker'ı oluşturulmamış. `/q` ile ilkini sen yap!"
                )
        except Exception as e:
            logger.error("Quote random error: %s", summarize_exception(e, label="quote_random"))
            await update.message.reply_text("⚠️ Rastgele alıntı seçilirken bir sorun oluştu.")

    async def handle_qd_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Alıntı silme komutu (/qd)."""
        if not await self._quote_enabled(update.effective_chat.id):
            return
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        args = context.args

        try:
            if args and args[0].lower() == 'all':
                if not self.sudo_manager.is_sudo(user_id):
                    await update.message.reply_text("⛔ Tüm alıntıları silmek için sudo yetkisi gerekli.")
                    return
                count = await self.data_port.delete_quotes(chat_id)
                await update.message.reply_text(f"🗑️ Bu gruptaki **{count}** alıntı başarıyla silindi.", parse_mode='Markdown')
            elif args and args[0].isdigit():
                n = int(args[0])
                count = await self.data_port.delete_quotes(chat_id, limit=n)
                await update.message.reply_text(f"🗑️ Son **{count}** alıntı silindi.", parse_mode='Markdown')
            else:
                count = await self.data_port.delete_quotes(chat_id, limit=1)
                if count > 0:
                    await update.message.reply_text("🗑️ Son alıntı silindi.")
                else:
                    await update.message.reply_text("📭 Bu grupta silinecek alıntı bulunamadı.")

            try:
                await update.message.delete()
            except Exception:
                pass

        except Exception as e:
            logger.error("Quote delete error: %s", summarize_exception(e, label="quote_delete"))
            await update.message.reply_text("⚠️ Alıntı silinirken bir hata oluştu.")

    async def handle_qtemplate_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Save, apply and remove named quote style templates."""
        if not await self._quote_enabled(update.effective_chat.id):
            return
        user_id = update.effective_user.id
        args = [arg.strip().lower() for arg in (context.args or [])]
        if not args:
            try:
                templates = await self.data_port.get_quote_templates(user_id)
            except Exception:
                templates = []
            names = ", ".join(html.escape(str(item.get("name", ""))) for item in templates) or "yok"
            await update.effective_message.reply_text(
                f"Şablonlar: <code>{names}</code>\nKullanım: /qtemplate save|use|delete isim", parse_mode="HTML"
            )
            return
        action, name = args[0], args[1] if len(args) > 1 else ""
        try:
            if name:
                name = normalize_template_name(name)
        except QuoteOptionsError:
            await update.effective_message.reply_text(
                "Geçersiz şablon adı. 1-32 karakter, harf/rakam/alt çizgi veya tire kullan."
            )
            return
        if action == "save" and name:
            try:
                settings = await self.data_port.get_chat_settings(update.effective_chat.id)
            except Exception:
                settings = {}
            try:
                template_options = normalize_quote_options({
                    "theme": settings.get("q_theme", "dark"),
                    "bg_color": settings.get("q_bg_color"),
                    "name_color": settings.get("q_name_color"),
                    "text_color": settings.get("q_text_color"),
                    "scale": settings.get("q_scale", 1.0),
                    "is_png": settings.get("q_is_png", False),
                    "as_file": settings.get("q_as_file", False),
                    "show_reply": settings.get("q_show_reply", False),
                    "crop": settings.get("q_crop", False),
                    "emoji": settings.get("q_emoji", "✨"),
                    "rate_enabled": settings.get("q_rate_enabled", True),
                })
                await self.data_port.save_quote_template(user_id, name, template_options)
            except (QuoteOptionsError, ValueError):
                await update.effective_message.reply_text(
                    "Mevcut quote ayarları geçerli bir şablona dönüştürülemedi."
                )
                return
            await update.effective_message.reply_text(f"✅ Quote şablonu kaydedildi: <b>{name}</b>", parse_mode="HTML")
            return
        template = await self.data_port.get_quote_template(user_id, name)
        if action == "use" and template:
            try:
                template_options = normalize_quote_options(template.get("options", {}))
            except QuoteOptionsError:
                await update.effective_message.reply_text(
                    "Bu şablon eski veya geçersiz ayarlar içeriyor; yeniden kaydetmelisin."
                )
                return
            for key, value in template_options.items():
                await self.data_port.update_chat_setting_key(update.effective_chat.id, f"q_{key}", value)
            await update.effective_message.reply_text(f"✅ Quote şablonu uygulandı: <b>{name}</b>", parse_mode="HTML")
            return
        if action == "delete" and name:
            deleted = await self.data_port.delete_quote_template(user_id, name)
            await update.effective_message.reply_text("✅ Şablon silindi." if deleted else "Şablon bulunamadı.")
            return
        await update.effective_message.reply_text("Kullanım: /qtemplate save|use|delete isim")

    async def _get_ai_selected_messages(self, chat_id: int, options: dict) -> list:
        """AI ile mesaj seçimi yapar."""
        messages_to_quote = []
        recent = await self.data_port.get_recent_messages_with_users(chat_id, limit=150)

        context_list = []
        for msg_doc in reversed(recent):
            user_info = msg_doc.get("user_info", {})
            name = user_info.get("first_name", f"ID:{msg_doc.get('user_id')}")
            context_list.append(f"ID:{msg_doc['_id']} | {name}: {msg_doc['content']}")
        context_str = "\n".join(context_list)

        ai_prompt = (
            f"Sorgu: '{options['ai_query']}'\n\n"
            f"Aşağıdaki mesajlardan sorguya en uygun 1-5 tanesini seç. "
            f"Sadece seçtiğin ID'leri virgülle ayırarak gönder."
        )

        ai_res = await self.chat_engine.get_response(
            chat_id,
            ai_prompt,
            context=context_str,
            persist_conversation=False,
            purpose="quote_selection",
        )
        if not ai_res:
            return messages_to_quote

        target_ids = [id.strip() for id in ai_res.split(',') if id.strip()]

        if target_ids:
            results = await self.data_port.get_messages_by_ids(target_ids)
            for m in results:
                user_info = m.get("user_info", {})
                name = user_info.get("first_name", f"ID:{m.get('user_id')}")
                messages_to_quote.append({
                    'text': m['content'],
                    'name': name,
                    'user_id': m.get('user_id'),
                    'message_id': str(m['_id'])
                })

        return messages_to_quote

    async def _get_reply_messages(self, update: Update, options: dict, chat_id: int) -> list:
        """Yanıtlanan mesajı ve istenen ardışık mesajları quote girdilerine dönüştürür."""
        messages_to_quote = []
        replied = update.message.reply_to_message
        if replied is None:
            return messages_to_quote

        # /q 5 five source messages means. The optional r flag adds context.
        try:
            requested = max(1, min(int(options.get("count", 1)), 10))
        except (TypeError, ValueError):
            requested = 1
        parent = getattr(replied, "reply_to_message", None) if options.get("show_reply") else None
        # QuoteRenderService has the same ten-message safety ceiling.
        target_size = min(10, requested + (1 if parent is not None else 0))
        seen = set()

        def append_telegram(message) -> None:
            name, user_id = self._get_user_info(message)
            text = message.text or message.caption or "[Medya]"
            message_id = getattr(message, "message_id", None)
            key = ("telegram", message_id, user_id, text)
            if key in seen:
                return
            seen.add(key)
            messages_to_quote.append({
                "text": text,
                "name": name,
                "user_id": user_id,
                "message_id": message_id,
                "entities": self._extract_entities(message),
            })

        def append_document(document) -> None:
            user_info = document.get("user_info") or {}
            user_id = document.get("user_id", 0)
            name = user_info.get("first_name") or user_info.get("title") or f"ID:{user_id}"
            text = document.get("content") or document.get("text") or "[Medya]"
            document_id = document.get("_id")
            timestamp = document.get("timestamp")
            key = ("db", str(document_id)) if document_id is not None else ("db", user_id, text, str(timestamp))
            if key in seen:
                return
            seen.add(key)
            messages_to_quote.append({
                "text": text,
                "name": name,
                "user_id": user_id,
                "message_id": str(document_id) if document_id is not None else None,
            })

        if parent is not None:
            append_telegram(parent)
        append_telegram(replied)

        replied_text = replied.text or replied.caption or ""
        replied_user_id = replied.from_user.id if replied.from_user else 0
        replied_db = None
        database = self.data_port
        if database is not None and getattr(database, "available", False) and replied_text:
            try:
                replied_db = await database.find_latest_message_for_quote(
                    chat_id, replied_user_id, replied_text
                )
            except Exception as exc:
                logger.warning("Quote source lookup failed: %s", type(exc).__name__)

        if replied_db and len(messages_to_quote) < target_size:
            try:
                results = await database.get_sequential_messages(
                    chat_id, replied_db["timestamp"], requested
                )
            except Exception as exc:
                logger.warning("Quote sequence lookup failed: %s", type(exc).__name__)
                results = []

            source_skipped = False
            for document in results:
                # The Telegram reply is already present; skip its persisted counterpart.
                if replied_db.get("_id") is not None and document.get("_id") == replied_db.get("_id"):
                    continue
                document_user_id = document.get("user_id")
                document_text = document.get("content") or document.get("text") or "[Medya]"
                if (
                    not source_skipped
                    and document_user_id == replied_user_id
                    and document_text == replied_text
                ):
                    source_skipped = True
                    continue
                append_document(document)
                if len(messages_to_quote) >= target_size:
                    break

        return messages_to_quote[:target_size]

    def _get_user_info(self, msg) -> tuple:
        """Mesajdaki kullanıcı bilgisini döndürür."""
        if msg.from_user:
            return msg.from_user.full_name, msg.from_user.id
        elif msg.sender_chat:
            return msg.sender_chat.title, msg.sender_chat.id
        return "Anonim", 0

    def _extract_entities(self, msg) -> list:
        """Mesajdaki entity'leri çıkarır."""
        entities = []
        raw_ents = msg.entities or msg.caption_entities or []
        for ent in raw_ents:
            entities.append({
                "type": ent.type,
                "offset": ent.offset,
                "length": ent.length
            })
        return entities

    async def _generate_and_send_quote(self, update: Update, context: ContextTypes.DEFAULT_TYPE,
                                        messages_to_quote: list, options: dict, chat_id: int):
        """Validate and enqueue quote rendering; workers perform Telegram I/O."""
        if self.quote_service is None:
            await update.message.reply_text("Quote kuyruğu bu ortamda etkin değil.")
            return
        status = await update.message.reply_text("🖼️ Alıntı kuyruğa alınıyor…")
        try:
            job_id, duplicate = await self.quote_service.submit(
                chat_id=chat_id,
                user_id=update.effective_user.id,
                status_message_id=status.message_id,
                messages=messages_to_quote,
                options=options,
                reply_to_message_id=update.message.message_id,
            )
            text = (f"⏳ Bu alıntı için aktif iş zaten var.\nİş: <code>{job_id}</code>" if duplicate
                    else f"⏳ Alıntı kuyruğa alındı.\nİş: <code>{job_id}</code>")
            await status.edit_text(text, parse_mode="HTML")
        except Exception as exc:
            logger.warning("Quote enqueue failed: %s", summarize_exception(exc, label="quote_enqueue"))
            await status.edit_text("❌ Alıntı kuyruğa alınamadı; biraz sonra tekrar dene.")
