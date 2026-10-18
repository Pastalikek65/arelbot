"""Safe, user-facing messages for media boundary errors."""

from __future__ import annotations

from src.security.error_codes import normalize_error_code, public_error_message, public_validation_message


_MEDIA_URL_MESSAGES = {
    "UNSUPPORTED_SCHEME": "Bu ba\u011flant\u0131 t\u00fcr\u00fc desteklenmiyor.",
    "INVALID_URL": "Ba\u011flant\u0131 ge\u00e7ersiz.",
    "UNSUPPORTED_PORT": "Bu ba\u011flant\u0131 noktas\u0131 desteklenmiyor.",
    "UNSUPPORTED_PLATFORM": "Bu platform \u015fu anda desteklenmiyor.",
    "HOST_UNAVAILABLE": "Ba\u011flant\u0131 kayna\u011f\u0131na ula\u015f\u0131lamad\u0131.",
    "NON_PUBLIC_HOST": "Bu ba\u011flant\u0131 g\u00fcvenli bir d\u0131\u015f kayna\u011fa y\u00f6nlenmiyor.",
}

_MEDIA_JOB_MESSAGES = {
    "FILE_TOO_LARGE": "Medya dosyas\u0131 Telegram g\u00f6nderim s\u0131n\u0131r\u0131n\u0131 a\u015f\u0131yor.",
    "DURATION_LIMIT": "Video izin verilen s\u00fcre s\u0131n\u0131r\u0131n\u0131 a\u015f\u0131yor.",
    "TIMEOUT": "Kaynak zaman\u0131nda yan\u0131t vermedi. Daha sonra yeniden deneyebilirsin.",
    "NON_PUBLIC_HOST": "Bu ba\u011flant\u0131 g\u00fcvenli bir d\u0131\u015f kayna\u011fa y\u00f6nlenmiyor.",
    "UNSUPPORTED_PLATFORM": "Bu platform \u015fu anda desteklenmiyor.",
    "FFMPEG_UNAVAILABLE": "Bu video bi\u00e7imini Telegram i\u00e7in d\u00f6n\u00fc\u015ft\u00fcrmek \u00fczere FFmpeg gerekli.",
    "ARTIFACT_MISSING": "Medya dosyas\u0131 haz\u0131rlanamad\u0131.",
    "SOURCE_ACCESS_RESTRICTED": "Kaynak herkese a\u00e7\u0131k eri\u015fime izin vermiyor.",
    "SOURCE_RATE_LIMITED": "Kaynak ge\u00e7ici olarak \u00e7ok fazla istek ald\u0131; biraz sonra yeniden dene.",
    "PROBE_FAILED": "Kayna\u011f\u0131n medya bilgileri al\u0131namad\u0131.",
    "DOWNLOAD_FAILED": "Bu ba\u011flant\u0131 \u015fu anda indirilemedi.",
}


def media_url_error_message(error: BaseException | object) -> str:
    """Map URL validation codes without echoing arbitrary exception text."""

    code = normalize_error_code(str(error), "INVALID_URL")
    return _MEDIA_URL_MESSAGES.get(code, "Bu ba\u011flant\u0131 desteklenmiyor veya g\u00fcvenli de\u011fil.")


def cookie_error_message(error: BaseException | object) -> str:
    """Preserve only short parser-owned cookie validation text."""

    return public_validation_message(error, "Cookie i\u015flemi ge\u00e7ersiz.")


def media_delivery_error_message(error: BaseException | object) -> str:
    """Map media/transcode codes to bounded Telegram-safe messages."""

    return public_error_message(error, "Medya i\u015flemi tamamlanamad\u0131.", messages=_MEDIA_JOB_MESSAGES)
