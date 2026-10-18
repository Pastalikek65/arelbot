"""
Input Validator — Kullanıcı girdilerini temizleme ve doğrulama.

Injection saldırılarına (HTML, SQL, path traversal) karşı koruma.
Tüm kullanıcı girdileri handler'lara gelmeden önce buradan geçer.
"""
import os
import re
import html as html_lib
import logging
from typing import Optional, List, Tuple

logger = logging.getLogger(__name__)


class InputValidator:
    """Kullanıcı girdisi doğrulama ve temizleme yardımcısı."""

    HTML_TAG_PATTERN = re.compile(r'<[^>]*>', re.DOTALL)
    SCRIPT_PATTERN = re.compile(r'<script.*?>.*?</script>', re.DOTALL | re.IGNORECASE)
    SQL_INJECTION_PATTERN = re.compile(
        r"(\b(SELECT|INSERT|UPDATE|DELETE|DROP|ALTER|EXEC|UNION|CREATE)\b|--|;--)",
        re.IGNORECASE
    )
    PATH_TRAVERSAL_PATTERN = re.compile(r'(\.\./|\.\.\\)')
    CONTROL_CHARS_PATTERN = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')
    LINK_PATTERN = re.compile(r'https?://\S+', re.IGNORECASE)

    MAX_TEXT_LENGTH = 5000
    MAX_COMMAND_ARGS_LENGTH = 200
    MAX_USERNAME_LENGTH = 64

    @classmethod
    def sanitize_text(cls, text: str) -> Tuple[str, bool]:
        """
        Kullanıcı mesajını temizler. (True, clean_text) döndürür.

        Returns:
            (is_safe, sanitized_text)
        """
        if not text or not isinstance(text, str):
            return False, ""

        original = text

        text = cls.CONTROL_CHARS_PATTERN.sub('', text)
        text = cls.HTML_TAG_PATTERN.sub('', text)

        if len(text) > cls.MAX_TEXT_LENGTH:
            text = text[:cls.MAX_TEXT_LENGTH]
            logger.warning(f"Text truncated from {len(original)} to {cls.MAX_TEXT_LENGTH} chars")

        is_safe = text == original
        return is_safe, text

    @classmethod
    def sanitize_html_entities(cls, text: str) -> str:
        """HTML entity'leri temizler, safe HTML formatına dönüştürür."""
        if not text:
            return ""
        text = html_lib.escape(text, quote=False)
        safe_tags = {
            'b': r'<b>', '/b': r'</b>',
            'i': r'<i>', '/i': r'</i>',
            'code': r'<code>', '/code': r'</code>',
            'a': r'<a href="', '/a': r'</a>',
        }
        for tag, replacement in safe_tags.items():
            text = text.replace(f'&lt;{tag}&gt;', replacement)
        return text

    @classmethod
    def sanitize_filename(cls, filename: str) -> str:
        """Path traversal ve tehlikeli karakterleri temizler."""
        if not filename:
            return "unnamed"

        filename = re.sub(r'[<>:"/\\|?*\'`]', '_', filename)
        filename = cls.PATH_TRAVERSAL_PATTERN.sub('', filename)

        if len(filename) > 255:
            name, ext = os.path.splitext(filename) if '.' in filename else (filename, '')
            filename = name[:250] + ext[:5]

        return filename or "unnamed"

    @classmethod
    def validate_length(cls, text: str, min_len: int = 1, max_len: int = 5000) -> Tuple[bool, str]:
        """Metin uzunluğunu kontrol eder."""
        if not text or not isinstance(text, str):
            return False, f"Metin {min_len}-{max_len} karakter arasında olmalıdır."
        if len(text) < min_len:
            return False, f"Metin en az {min_len} karakter olmalıdır."
        if len(text) > max_len:
            return False, f"Metin en fazla {max_len} karakter olabilir."
        return True, text

    @classmethod
    def validate_command_args(cls, args: List[str], allowed_count: int = 10) -> Tuple[bool, List[str]]:
        """Komut argümanlarını doğrular."""
        if not args:
            return True, []

        if len(args) > allowed_count:
            return False, args[:allowed_count]

        cleaned = []
        for arg in args[:allowed_count]:
            arg = str(arg)[:cls.MAX_COMMAND_ARGS_LENGTH]
            arg = cls.PATH_TRAVERSAL_PATTERN.sub('', arg)
            cleaned.append(arg)

        return True, cleaned

    @classmethod
    def detect_suspicious_patterns(cls, text: str) -> bool:
        """
        Tehlikeli pattern'leri tespit eder.

        Returns:
            True = şüpheli pattern bulundu
        """
        if not text:
            return False

        checks = [
            (cls.SCRIPT_PATTERN, "script injection"),
            (cls.PATH_TRAVERSAL_PATTERN, "path traversal"),
        ]

        for pattern, name in checks:
            if pattern.search(text):
                logger.warning(f"Suspicious pattern detected: {name}")
                return True

        return False

    @classmethod
    def validate_user_input(cls, text: Optional[str]) -> str:
        """
        Ana doğrulama giriş noktası. Tüm kullanıcı girdileri için kullanılır.

        Returns:
            Temizlenmiş metin
        """
        if text is None:
            return ""

        is_safe, cleaned = cls.sanitize_text(text)

        if cls.detect_suspicious_patterns(cleaned):
            logger.warning("Suspicious input blocked: length=%s", len(cleaned))

        if not cleaned.strip():
            return ""

        return cleaned
