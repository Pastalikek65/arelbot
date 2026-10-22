"""
Hata Yönetim Sistemi — Hiyerarşik Hata Sınıfları + Global Error Handler

Tüm hatalar yakalanır, loglanır ve kullanıcıya mesajlarla iletilir.

Kullanım:
    raise AIApiError("DeepSeek yanıt vermedi", service="iamhc", status_code=503)
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
    """Tüm özel hataların base sınıfı."""
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
        self.user_message = user_message or "İşlem tamamlanamadı. Biraz sonra tekrar dene."
        self.timestamp = datetime.now(timezone.utc)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
            "timestamp": self.timestamp.isoformat()
        }


class AIApiError(ArelError):
    """AI API (IAMHC, FreeModel, NineRouter) hataları."""
    def __init__(
        self,
        message: str,
        service: str = "unknown",
        status_code: Optional[int] = None,
        details: Optional[Dict[str, Any]] = None
    ):
        user_msg = "Yanıt alınamadı. Biraz sonra tekrar dene."
        super().__init__(
            message=message,
            code="AI_API_ERROR",
            details={"service": service, "status_code": status_code, **(details or {})},
            user_message=user_msg
        )
        self.service = service
        self.status_code = status_code


class RateLimitError(ArelError):
    """Rate limit / kota aşımı hataları."""
    def __init__(
        self,
        message: str,
        service: str = "unknown",
        retry_after: Optional[int] = None,
        details: Optional[Dict[str, Any]] = None
    ):
        retry_msg = f" {retry_after} saniye sonra tekrar dene." if isinstance(retry_after, int) and retry_after > 0 else " Biraz sonra tekrar dene."
        user_msg = f"Şu anda yeni istek alınamıyor.{retry_msg}"
        super().__init__(
            message=message,
            code="RATE_LIMIT",
            details={"service": service, "retry_after": retry_after, **(details or {})},
            user_message=user_msg
        )
        self.service = service
        self.retry_after = retry_after


class ToolExecutionError(ArelError):
    """Tool çalıştırma hataları."""
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
            user_message="İşlem tamamlanamadı. Biraz sonra tekrar dene."
        )
        self.tool_name = tool_name


class DatabaseError(ArelError):
    """Veritabanı (MongoDB/Redis) hataları."""
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
            user_message="İşlem tamamlanamadı. Biraz sonra tekrar dene."
        )
        self.db_name = db_name
        self.operation = operation


class InputValidationError(ArelError):
    """Kullanıcı girdisi doğrulama hataları."""
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
            user_message="Gönderdiğin bilgi kabul edilmedi. Biçimini kontrol edip tekrar dene."
        )
        self.field = field


# Geriye dönük uyumluluk: bazı eski importlar ValidationError adını bekliyor.
ValidationError = InputValidationError


class ConfigurationError(ArelError):
    """Yapılandırma eksik/hatalı olduğunda."""
    def __init__(self, message: str, missing_keys: Optional[list] = None):
        super().__init__(
            message=message,
            code="CONFIG_ERROR",
            details={"missing_keys": missing_keys or []},
            user_message="Bu işlem şu anda kullanılamıyor. Bot yöneticisine bildirebilirsin."
        )
        self.missing_keys = missing_keys or []


# ============================================
# GLOBAL ERROR HANDLER
# ============================================

class ErrorHandler:
    """
    Global hata yönetim merkezi.

    - Hataları log kanalına iletir
    - İstatistik tutar
    - Retry decorator sağlar
    - Telegram error handler olarak kullanılabilir
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
        Bir hatayı işler, loglar ve kullanıcı mesajı döndürür.

        Args:
            error: Yakalanan exception
            context: Ek bağlam bilgisi (örn: {"user_id": 123, "command": "/test"})
            notify: Log kanalına bildirim gönderilsin mi?
            bot: telegram.Bot instance (notify=True ise gerekli)
            chat_id: Bildirimin gideceği chat ID

        Returns:
            Kullanıcıya gösterilecek mesaj
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
            user_msg = "İşlem tamamlanamadı. Biraz sonra tekrar dene."
            log_msg = f"[UNHANDLED] {error_type}"

        logger.error("%s (%s)", log_msg, summarize_exception(error, label="handled_error"))

        if notify and bot and chat_id:
            try:
                error_text = (
                    f"🚨 <b>HATA</b>\n\n"
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
        """Hata istatistiklerini döndürür."""
        return dict(cls._stats)

    @classmethod
    def reset_stats(cls):
        """İstatistikleri sıfırlar."""
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
        Decorator: Bir fonksiyonu hata durumunda belirtilen sayıda tekrar dener.

        Kullanım:
            @ErrorHandler.retry(max_retries=5, delay=2, exceptions=(AIApiError,))
            async def get_ai_response(prompt):
                ...

        Args:
            max_retries: Maksimum deneme sayısı
            delay: İlk bekleme süresi (saniye)
            backoff: Her denemede delay çarpanı
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
        """python-telegram-bot için global error handler callback."""
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
                is_document = bool(getattr(message, "document", None))
                caption_or_text = getattr(message, "caption", None) or getattr(message, "text", None) or ""
                # Passive GIFs and media-link updates must never turn an
                # internal exception into a public group error message.
                is_media_update = (
                    is_animation
                    or is_document
                    or "http://" in caption_or_text
                    or "https://" in caption_or_text
                )
                if not (is_group and is_media_update):
                    await message.reply_text(user_msg, parse_mode='HTML')
        except Exception:
            pass
