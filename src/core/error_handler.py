"""
Hata YÃ¶netim Sistemi â€” HiyerarÅŸik Hata SÄ±nÄ±flarÄ± + Global Error Handler

TÃ¼m hatalar yakalanÄ±r, loglanÄ±r ve kullanÄ±cÄ±ya mesajlarla iletilir.

KullanÄ±m:
    raise AIApiError("DeepSeek yanÄ±t vermedi", service="iamhc", status_code=503)
    ErrorHandler.handle(exception, context)
"""
import logging
import asyncio
from typing import Optional, Dict, Any, Callable
from datetime import datetime, timezone
from src.security.log_redaction import summarize_exception

logger = logging.getLogger(__name__)


# ============================================
# HATA SINIFLARI
# ============================================

class ArelError(Exception):
    """TÃ¼m Ã¶zel hatalarÄ±n base sÄ±nÄ±fÄ±."""
    def __init__(
        self,
        message: str,
        code: str = "UNKNOWN_ERROR",
        details: Optional[Dict[str, Any]] = None,
        user_message: Optional[str] = None
    ):
        self.message = message
        self.code = code
        self.details = details or {}
        self.user_message = user_message or "Bir hata oluÅŸtu. LÃ¼tfen tekrar deneyin."
        self.timestamp = datetime.now(timezone.utc)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
            "timestamp": self.timestamp.isoformat()
        }


class AIApiError(ArelError):
    """AI API (IAMHC, FreeModel, NineRouter) hatalarÄ±."""
    def __init__(
        self,
        message: str,
        service: str = "unknown",
        status_code: Optional[int] = None,
        details: Optional[Dict[str, Any]] = None
    ):
        user_msg = (
            f"ğŸ˜” {service} servisi ÅŸu an yanÄ±t vermiyor. "
            f"DiÄŸer servislere yÃ¶nlendiriyorum..."
        )
        super().__init__(
            message=message,
            code="AI_API_ERROR",
            details={"service": service, "status_code": status_code, **(details or {})},
            user_message=user_msg
        )
        self.service = service
        self.status_code = status_code


class RateLimitError(ArelError):
    """Rate limit / kota aÅŸÄ±mÄ± hatalarÄ±."""
    def __init__(
        self,
        message: str,
        service: str = "unknown",
        retry_after: Optional[int] = None,
        details: Optional[Dict[str, Any]] = None
    ):
        retry_msg = f" {retry_after} saniye sonra tekrar dene." if retry_after else ""
        user_msg = f"â³ {service} servisi ÅŸu an yoÄŸun.{retry_msg}"
        super().__init__(
            message=message,
            code="RATE_LIMIT",
            details={"service": service, "retry_after": retry_after, **(details or {})},
            user_message=user_msg
        )
        self.service = service
        self.retry_after = retry_after


class ToolExecutionError(ArelError):
    """Tool Ã§alÄ±ÅŸtÄ±rma hatalarÄ±."""
    def __init__(
        self,
        message: str,
        tool_name: str = "unknown",
        args: Optional[Dict[str, Any]] = None,
        details: Optional[Dict[str, Any]] = None
    ):
        super().__init__(
            message=message,
            code="TOOL_EXECUTION_ERROR",
            details={"tool": tool_name, "args": args, **(details or {})},
            user_message=f"ğŸ”§ '{tool_name}' Ã§alÄ±ÅŸÄ±rken bir sorun Ã§Ä±ktÄ±. LÃ¼tfen tekrar dene."
        )
        self.tool_name = tool_name


class DatabaseError(ArelError):
    """VeritabanÄ± (MongoDB/Redis) hatalarÄ±."""
    def __init__(
        self,
        message: str,
        db_name: str = "unknown",
        operation: str = "unknown",
        details: Optional[Dict[str, Any]] = None
    ):
        super().__init__(
            message=message,
            code="DATABASE_ERROR",
            details={"db": db_name, "operation": operation, **(details or {})},
            user_message=f"ğŸ—„ï¸ VeritabanÄ± iÅŸlemi sÄ±rasÄ±nda bir sorun oluÅŸtu. LÃ¼tfen tekrar dene."
        )
        self.db_name = db_name
        self.operation = operation


class InputValidationError(ArelError):
    """KullanÄ±cÄ± girdisi doÄŸrulama hatalarÄ±."""
    def __init__(
        self,
        message: str,
        field: str = "unknown",
        details: Optional[Dict[str, Any]] = None
    ):
        super().__init__(
            message=message,
            code="INPUT_VALIDATION_ERROR",
            details={"field": field, **(details or {})},
            user_message=f"âš ï¸ GeÃ§ersiz girdi: {message}"
        )
        self.field = field


# Geriye dÃ¶nÃ¼k uyumluluk: bazÄ± eski importlar ValidationError adÄ±nÄ± bekliyor.
ValidationError = InputValidationError


class ConfigurationError(ArelError):
    """YapÄ±landÄ±rma eksik/hatalÄ± olduÄŸunda."""
    def __init__(self, message: str, missing_keys: Optional[list] = None):
        super().__init__(
            message=message,
            code="CONFIG_ERROR",
            details={"missing_keys": missing_keys or []},
            user_message="âš™ï¸ Bot yapÄ±landÄ±rmasÄ±nda bir sorun var. LÃ¼tfen yÃ¶neticiye baÅŸvurun."
        )
        self.missing_keys = missing_keys or []


# ============================================
# GLOBAL ERROR HANDLER
# ============================================

class ErrorHandler:
    """
    Global hata yÃ¶netim merkezi.

    - HatalarÄ± log kanalÄ±na iletir
    - Ä°statistik tutar
    - Retry decorator saÄŸlar
    - Telegram error handler olarak kullanÄ±labilir
    """

    _stats = {
        "total_errors": 0,
        "errors_by_type": {},
        "errors_by_code": {},
        "last_error": None,
        "last_error_time": None
    }

    @classmethod
    def handle(
        cls,
        error: Exception,
        context: Optional[Dict[str, Any]] = None,
        notify: bool = False,
        bot=None,
        chat_id: Optional[int] = None
    ) -> str:
        """
        Bir hatayÄ± iÅŸler, loglar ve kullanÄ±cÄ± mesajÄ± dÃ¶ndÃ¼rÃ¼r.

        Args:
            error: Yakalanan exception
            context: Ek baÄŸlam bilgisi (Ã¶rn: {"user_id": 123, "command": "/test"})
            notify: Log kanalÄ±na bildirim gÃ¶nderilsin mi?
            bot: telegram.Bot instance (notify=True ise gerekli)
            chat_id: Bildirimin gideceÄŸi chat ID

        Returns:
            KullanÄ±cÄ±ya gÃ¶sterilecek mesaj
        """
        cls._stats["total_errors"] += 1
        error_type = type(error).__name__
        cls._stats["errors_by_type"][error_type] = cls._stats["errors_by_type"].get(error_type, 0) + 1
        cls._stats["last_error"] = error_type
        cls._stats["last_error_time"] = datetime.now(timezone.utc).isoformat()

        if isinstance(error, ArelError):
            cls._stats["errors_by_code"][error.code] = cls._stats["errors_by_code"].get(error.code, 0) + 1
            code = error.code
            user_msg = error.user_message
            log_msg = f"[{code}] {error_type}"
        else:
            code = "UNHANDLED"
            user_msg = "Beklenmedik bir hata oluÅŸtu. LÃ¼tfen tekrar deneyin."
            log_msg = f"[UNHANDLED] {error_type}"

        logger.error("%s (%s)", log_msg, summarize_exception(error, label="handled_error"))

        if notify and bot and chat_id:
            try:
                error_text = (
                    f"ğŸš¨ <b>HATA</b>\n\n"
                    f"<b>Tip:</b> {error_type}\n"
                    f"<b>Kod:</b> {code}\n"
                    f"<b>Mesaj:</b> {user_msg[:200]}\\n"
                    f"<b>Kapsam:</b> {len(context or {})} alan"
                )
                asyncio.ensure_future(bot.send_message(
                    chat_id=chat_id,
                    text=error_text,
                    parse_mode='HTML'
                ))
            except Exception as log_e:
                logger.error("Error notification failed: %s", summarize_exception(log_e, label="error_notification"))

        return user_msg

    @classmethod
    def get_stats(cls) -> Dict[str, Any]:
        """Hata istatistiklerini dÃ¶ndÃ¼rÃ¼r."""
        return dict(cls._stats)

    @classmethod
    def reset_stats(cls):
        """Ä°statistikleri sÄ±fÄ±rlar."""
        for key in cls._stats:
            if isinstance(cls._stats[key], (int, float)):
                cls._stats[key] = 0
            elif isinstance(cls._stats[key], dict):
                cls._stats[key] = {}
            else:
                cls._stats[key] = None

    @staticmethod
    def retry(
        max_retries: int = 3,
        delay: float = 1.0,
        backoff: float = 2.0,
        exceptions: tuple = (Exception,)
    ) -> Callable:
        """
        Decorator: Bir fonksiyonu hata durumunda belirtilen sayÄ±da tekrar dener.

        KullanÄ±m:
            @ErrorHandler.retry(max_retries=5, delay=2, exceptions=(AIApiError,))
            async def get_ai_response(prompt):
                ...

        Args:
            max_retries: Maksimum deneme sayÄ±sÄ±
            delay: Ä°lk bekleme sÃ¼resi (saniye)
            backoff: Her denemede delay Ã§arpanÄ±
            exceptions: Yakalanacak exception tipleri

        Returns:
            Decorated function
        """
        def decorator(func: Callable) -> Callable:
            async def wrapper(*args, **kwargs):
                current_delay = delay
                last_exception = None

                for attempt in range(1, max_retries + 1):
                    try:
                        return await func(*args, **kwargs)
                    except exceptions as e:
                        last_exception = e
                        if attempt < max_retries:
                            logger.warning(
                                "Retry %s/%s for %s (%s). Waiting %.2fs...",
                                attempt,
                                max_retries,
                                func.__name__,
                                summarize_exception(e, label="retry"),
                                current_delay,
                            )
                            await asyncio.sleep(current_delay)
                            current_delay *= backoff
                        else:
                            logger.error(
                                "All %s retries failed for %s (%s)",
                                max_retries,
                                func.__name__,
                                summarize_exception(e, label="retry"),
                            )
                    except Exception as e:
                        raise e

                raise last_exception

            return wrapper
        return decorator

    @staticmethod
    async def handle_telegram_error(update, context):
        """python-telegram-bot iÃ§in global error handler callback."""
        error = context.error
        update_info = {}

        if update:
            update_info["update_id"] = update.update_id
            if update.effective_user:
                update_info["user_id"] = update.effective_user.id
            if update.effective_chat:
                update_info["chat_id"] = update.effective_chat.id

        user_msg = ErrorHandler.handle(
            error=error,
            context=update_info,
            notify=True,
            bot=context.bot if hasattr(context, 'bot') and context.bot else None,
            chat_id=None
        )

        try:
            if update and update.effective_message:
                message = update.effective_message
                chat = getattr(update, "effective_chat", None)
                is_group = getattr(chat, "type", None) in {"group", "supergroup"}
                is_animation = bool(getattr(message, "animation", None))
                caption_or_text = getattr(message, "caption", None) or getattr(message, "text", None) or ""
                # Passive GIFs and media-link updates must never turn an
                # internal exception into a public group error message.
                is_media_update = is_animation or "http://" in caption_or_text or "https://" in caption_or_text
                if not (is_group and is_media_update):
                    await message.reply_text(user_msg, parse_mode='HTML')
        except Exception:
            pass
