"""
Handler Utils - TÃ¼m handler'lar tarafÄ±ndan kullanÄ±lan ortak fonksiyonlar.

Code duplication'Ä± azaltmak iÃ§in merkezi yardÄ±mcÄ± fonksiyonlar.
"""
import logging
from typing import Optional

from src.security.log_redaction import summarize_exception, summarize_private_text

logger = logging.getLogger(__name__)


async def is_admin(update, context, sudo_manager=None) -> bool:
    """
    KullanÄ±cÄ±nÄ±n admin olup olmadÄ±ÄŸÄ±nÄ± kontrol eder.
    
    Args:
        update: Telegram Update nesnesi
        context: Telegram Context nesnesi
        sudo_manager: SudoManager nesnesi (opsiyonel)
    
    Returns:
        bool: KullanÄ±cÄ± admin mi?
    """
    user_id = update.effective_user.id
    
    # Owner her zaman admin'dir
    if sudo_manager and user_id == sudo_manager.owner_id:
        return True
    
    try:
        member = await context.bot.get_chat_member(update.effective_chat.id, user_id)
        return member.status in ['creator', 'administrator']
    except Exception:
        return False


def remove_job_by_name(context, name: str) -> bool:
    """
    Ä°simli job'Ä± kuyruktan kaldÄ±rÄ±r.
    
    Args:
        context: Telegram Context nesnesi
        name: KaldÄ±rÄ±lacak job'Ä±n adÄ±
    
    Returns:
        bool: Job kaldÄ±rÄ±ldÄ± mÄ±?
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
    OlayÄ± log kanalÄ±na kaydeder.
    
    Args:
        context: Telegram Context nesnesi
        message: Log mesajÄ±
        chat_id: Chat ID (opsiyonel)
        user_info: KullanÄ±cÄ± bilgisi (opsiyonel)
        chat_type: Chat tÃ¼rÃ¼ (private/group/supergroup)
    """
    try:
        from config import Config

        if getattr(Config, "LOG_CHANNEL_ERRORS_ONLY", False):
            return
        
        log_chat_id = Config.LOG_CHANNEL_ID
        thread_id = Config.DM_LOG_THREAD_ID if chat_type == "private" else Config.GROUP_LOG_THREAD_ID
        
        # Log channels are operational sinks; never copy private message
        # bodies or names/usernames into them.
        log_text = f"ğŸ“‹ {summarize_private_text(message, label='event')}"
        if user_info:
            log_text += "\nğŸ‘¤ actor=present"
        
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
        log_text = f"âš ï¸ <b>HATA</b>\n{summarize_private_text(message, label='error')}"
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
    Mesaj iÃ§in yanÄ±t verilip verilmeyeceÄŸini belirler.
    
    Args:
        update: Telegram Update nesnesi
        context: Telegram Context nesnesi
        observation_mode: GÃ¶zlem modu aÃ§Ä±k mÄ±?
    
    Returns:
        bool: YanÄ±t verilmeli mi?
    """
    if not update.message or not update.message.text:
        return False
    
    # Komutlar her zaman iÅŸlenir
    if update.message.text.startswith('/'):
        return True
    
    # Ã–zel mesajlar (DM) her zaman yanÄ±tlanÄ±r
    chat_type = update.effective_chat.type
    if chat_type == 'private':
        return True
    
    # GÃ¶zlem modunda tÃ¼m mesajlar yanÄ±tlanÄ±r
    if observation_mode:
        return True
    
    # Gruplarda sadece mention veya reply'lara yanÄ±t ver
    if update.message.text:
        # Bot mention kontrolÃ¼
        if context.bot.username and f"@{context.bot.username}" in update.message.text:
            return True
        
        # Reply kontrolÃ¼
        if update.message.reply_to_message and update.message.reply_to_message.from_user:
            if update.message.reply_to_message.from_user.id == context.bot.id:
                return True
    
    return False
