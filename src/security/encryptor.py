"""
Encryptor — API anahtarlarını şifreleme/deşifreleme.

Fernet (symmetric encryption) kullanır.
Master key .env'de ENCRYPTION_KEY olarak saklanır.
"""
import os
import base64
import logging
from typing import Optional

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from src.security.log_redaction import summarize_exception

logger = logging.getLogger(__name__)


class Encryptor:
    """
    API anahtarlarını ve hassas verileri şifreler.

    Kullanım:
        encryptor = Encryptor()
        encrypted = encryptor.encrypt("my-api-key")
        decrypted = encryptor.decrypt(encrypted)
    """

    def __init__(self, master_key: Optional[str] = None):
        self._master_key = master_key or os.getenv("ENCRYPTION_KEY")
        self._fernet = None

        if self._master_key:
            self._init_fernet()
        else:
            logger.warning("ENCRYPTION_KEY bulunamadı. Şifreleme devre dışı.")

    def _init_fernet(self):
        try:
            key = self._master_key.encode()
            if len(key) < 32:
                salt = b'arel_salt_2024'
                kdf = PBKDF2HMAC(
                    algorithm=hashes.SHA256(),
                    length=32,
                    salt=salt,
                    iterations=100000,
                )
                key = base64.urlsafe_b64encode(kdf.derive(key))
            elif len(key) > 32:
                key = base64.urlsafe_b64encode(key[:32].ljust(32, b'\0'))
            else:
                key = base64.urlsafe_b64encode(key.ljust(32, b'\0'))

            self._fernet = Fernet(key)
            logger.info("✅ Encryptor başarıyla başlatıldı.")
        except Exception as e:
            logger.error("Encryptor başlatılamadı: %s", summarize_exception(e, label="encryptor_init"))
            self._fernet = None

    def encrypt(self, plain_text: str) -> Optional[str]:
        """Metni şifreler."""
        if not self._fernet or not plain_text:
            return plain_text
        try:
            return self._fernet.encrypt(plain_text.encode()).decode()
        except Exception as e:
            logger.error("Şifreleme hatası: %s", summarize_exception(e, label="encrypt"))
            return plain_text

    def decrypt(self, encrypted_text: str) -> Optional[str]:
        """Şifrelenmiş metni çözer."""
        if not self._fernet or not encrypted_text:
            return encrypted_text
        try:
            return self._fernet.decrypt(encrypted_text.encode()).decode()
        except Exception as e:
            logger.error("Deşifreleme hatası: %s", summarize_exception(e, label="decrypt"))
            return encrypted_text

    def is_encrypted(self, text: str) -> bool:
        """Metnin şifreli olup olmadığını kontrol eder."""
        if not text:
            return False
        try:
            return text.startswith("gAAAAA")
        except:
            return False

    @property
    def is_available(self) -> bool:
        return self._fernet is not None
