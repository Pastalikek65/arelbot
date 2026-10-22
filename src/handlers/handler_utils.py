"""
Handler Utils - Tüm handler'lar tarafından kullanılan ortak fonksiyonlar.

Code duplication'ı azaltmak için merkezi yardımcı fonksiyonlar.
"""
import logging
from typing import Optional

from src.moderation.authorization import TelegramModerationAuthorization
from src.security.log_redaction import summarize_exception, summarize_private_text

logger = logging.getLogger(__name__)


def _resolve_authorization(update, context, authorization=None):
    """Resolve the transport-backed authorization boundary.

    Production composition injects one instance from ``Orchestrator``.  The
    small compatibility fallback keeps isolated handler fixtures working while
    still routing the Telegram lookup through the moderation authorization
    service rather than issuing a ``get_chat_member`` call in a handler.
    """

    if authorization is not None:
        return authorization
    candidate = getattr(context, "arel_authorization", None)
    if candidate is not None:
        return candidate
    bot = getattr(context, "bot", None)
    if bot is None:
        return None
    return TelegramModerationAuthorization(bot, bot_id=getattr(bot, "id", None))


async def is_admin(update, context, sudo_manager=None, *, authorization=None) -> bool:
    """
    Kullanıcının admin olup olmadığını kontrol eder.
    
    Args:
        update: Telegram Update nesnesi
        context: Telegram Context nesnesi
        sudo_manager: SudoManager nesnesi (opsiyonel)
    
    Returns:
        bool: Kullanıcı admin mi?
    """
    user_id = update.effective_user.id
    
    # Owner her zaman admin'dir
    if sudo_manager and user_id == sudo_manager.owner_id:
        return True
    
    auth = _resolve_authorization(update, context, authorization)
    if auth is None:
        return False
    try:
        return bool(await auth.is_admin(update.effective_chat.id, user_id))
    except Exception:
        return False


async def is_group_admin(update, context, *, authorization=None) -> bool:
    """Check the current Telegram member through the auth service.

    Unlike :func:`is_admin`, this intentionally does not apply the bot owner
    shortcut: shared group controls require the actor's current Telegram role.
    """

    chat = getattr(update, "effective_chat", None)
    user = getattr(update, "effective_user", None)
    if getattr(chat, "type", None) not in {"group", "supergroup"}:
        return False
    if user is None or getattr(user, "id", None) is None:
        return False
    auth = _resolve_authorization(update, context, authorization)
    if auth is None:
        return False
    try:
        return bool(await auth.is_admin(chat.id, user.id))
    except Exception:
        return False


def remove_job_by_name(context, name: str) -> bool:
    """
    İsimli job'ı kuyruktan kaldırır.
    
    Args:
        context: Telegram Context nesnesi
        name: Kaldırılacak job'ın adı
    
    Returns:
        bool: Job kaldırıldı mı?
    """
    current_jobs = context.job_queue.get_jobs_by_name(name)
    if not current_jobs:
        return False
    
    for job in current_jobs:
        job.schedule_removal()
    
    return True


async def log_event(context, message: str, chat_id: int = None, user_info: str = None, 
                    chat_type: str = "unknown") -> None:
    """
    Olayı log kanalına kaydeder.
    
    Args:
        context: Telegram Context nesnesi
        message: Log mesajı
        chat_id: Chat ID (opsiyonel)
        user_info: Kullanıcı bilgisi (opsiyonel)
        chat_type: Chat türü (private/group/supergroup)
    """
    try:
        from config import Config

        if getattr(Config, "LOG_CHANNEL_ERRORS_ONLY", False):
            return
        
        log_chat_id = Config.LOG_CHANNEL_ID
        thread_id = Config.DM_LOG_THREAD_ID if chat_type == "private" else Config.GROUP_LOG_THREAD_ID
        
        # Log channels are operational sinks; never copy private message
        # bodies or names/usernames into them.
        log_text = f"📋 {summarize_private_text(message, label='event')}"
        if user_info:
            log_text += "\n👤 actor=present"
        
        await context.bot.send_message(
            chat_id=log_chat_id,
            text=log_text,
            message_thread_id=thread_id,
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error("Log event failed: %s", summarize_exception(e, label="log_event"))


async def log_error_event(context, message: str, *, chat_type: str = "unknown") -> None:
    """Send a bounded operational error record to the configured log topic."""

    try:
        from config import Config

        log_chat_id = Config.LOG_CHANNEL_ID
        thread_id = Config.DM_LOG_THREAD_ID if chat_type == "private" else Config.GROUP_LOG_THREAD_ID
        log_text = f"⚠️ <b>HATA</b>\n{summarize_private_text(message, label='error')}"
        await context.bot.send_message(
            chat_id=log_chat_id,
            text=log_text,
            message_thread_id=thread_id,
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error("Error log failed: %s", summarize_exception(e, label="error_log"))


def should_respond(update, context, observation_mode: bool = False) -> bool:
    """
    Mesaj için yanıt verilip verilmeyeceğini belirler.
    
    Args:
        update: Telegram Update nesnesi
        context: Telegram Context nesnesi
        observation_mode: Gözlem modu açık mı?
    
    Returns:
        bool: Yanıt verilmeli mi?
    """
    if not update.message or not update.message.text:
        return False
    
    # Komutlar her zaman işlenir
    if update.message.text.startswith('/'):
        return True
    
    # Özel mesajlar (DM) her zaman yanıtlanır
    chat_type = update.effective_chat.type
    if chat_type == 'private':
        return True
    
    # Gözlem modunda tüm mesajlar yanıtlanır
    if observation_mode:
        return True
    
    # Gruplarda sadece mention veya reply'lara yanıt ver
    if update.message.text:
        # Bot mention kontrolü
        if context.bot.username and f"@{context.bot.username}" in update.message.text:
            return True
        
        # Reply kontrolü
        if update.message.reply_to_message and update.message.reply_to_message.from_user:
            if update.message.reply_to_message.from_user.id == context.bot.id:
                return True
    
    return False
