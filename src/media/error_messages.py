"""Safe, user-facing messages for media boundary errors."""

from __future__ import annotations

from src.security.error_codes import normalize_error_code, public_error_message, public_validation_message


_MEDIA_URL_MESSAGES = {
    "UNSUPPORTED_SCHEME": "Bu bağlantı türü desteklenmiyor. Bir HTTPS bağlantısı gönder.",
    "INVALID_URL": "Bağlantı okunamadı. Tam adresi yeniden gönder.",
    "UNSUPPORTED_PORT": "Bu bağlantı adresi kabul edilmiyor. Platformun paylaşım bağlantısını gönder.",
    "UNSUPPORTED_PLATFORM": "Bu platform desteklenmiyor. Desteklenen bir platformun bağlantısını gönder.",
    "HOST_UNAVAILABLE": "Bağlantının kaynağına ulaşılamadı. Adresi kontrol edip sonra tekrar dene.",
    "NON_PUBLIC_HOST": "Bu bağlantı güvenli bir dış kaynağa yönlenmiyor. Herkese açık bir platform bağlantısı gönder.",
}

_MEDIA_JOB_MESSAGES = {
    "FILE_TOO_LARGE": "Dosya Telegram'ın gönderim sınırını aşıyor. Daha küçük bir dosya dene.",
    "DURATION_LIMIT": "Video süre sınırını aşıyor. Daha kısa bir video dene.",
    "TIMEOUT": "Kaynak zamanında yanıt vermedi. Biraz sonra tekrar dene.",
    "NON_PUBLIC_HOST": "Bu bağlantı güvenli bir dış kaynağa yönlenmiyor. Herkese açık bir platform bağlantısı gönder.",
    "UNSUPPORTED_PLATFORM": "Bu platform desteklenmiyor. Desteklenen bir platformun bağlantısını gönder.",
    "FFMPEG_UNAVAILABLE": "Video dönüştürme şu anda kullanılamıyor. Daha sonra tekrar dene.",
    "ARTIFACT_MISSING": "Medya hazırlanamadı. Biraz sonra tekrar dene.",
    "SOURCE_ACCESS_RESTRICTED": "Kaynak herkese açık erişime izin vermiyor. Herkese açık başka bir bağlantı gönder.",
    "SOURCE_RATE_LIMITED": "Kaynak şu anda çok fazla istek alıyor. Biraz sonra tekrar dene.",
    "PROBE_FAILED": "Kaynağın medya bilgileri okunamadı. Farklı bir bağlantı deneyebilirsin.",
    "DOWNLOAD_FAILED": "Medya indirilemedi. Bağlantıyı kontrol edip sonra tekrar dene.",
}


def media_url_error_message(error: BaseException | object) -> str:
    """Map URL validation codes without echoing arbitrary exception text."""

    code = normalize_error_code(str(error), "INVALID_URL")
    return _MEDIA_URL_MESSAGES.get(code, "Bu bağlantı desteklenmiyor veya güvenli değil. Başka bir bağlantı gönder.")


def cookie_error_message(error: BaseException | object) -> str:
    """Preserve only short parser-owned cookie validation text."""

    return public_validation_message(error, "Oturum bilgisi kabul edilmedi. Biçimini kontrol edip tekrar dene.")


def media_delivery_error_message(error: BaseException | object) -> str:
    """Map media/transcode codes to bounded Telegram-safe messages."""

    return public_error_message(error, "Medya hazırlanamadı. Biraz sonra tekrar dene.", messages=_MEDIA_JOB_MESSAGES)
