
import sys
import os
import time

# Add src to sys.path to ensure modules can be found
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

import logging
from enum import Enum, auto
from typing import List, Dict

from config import Config

try:
    import unified_logger as UnifiedLoggerModule
    UnifiedLogger = UnifiedLoggerModule.UnifiedLogger
except ImportError:
    UnifiedLogger = None # Fail gracefully

logger = logging.getLogger(__name__)

class KeyStatus(Enum):
    ACTIVE = auto()
    QUOTA_EXHAUSTED = auto()
    TEMPORARY_ERROR = auto()
    SUSPENDED = auto()


class KeyManagerError(Exception):
    """Base error for provider key availability."""


class NoKeysConfiguredError(KeyManagerError):
    """Raised when a provider has no configured keys."""


class ServiceCircuitOpenError(KeyManagerError):
    """Raised while every configured key for a provider is cooling down."""

    def __init__(self, service_type, retry_after: float | None = None):
        self.service_type = service_type
        self.retry_after = retry_after
        message = f"Circuit open for {service_type.value}"
        if retry_after is not None:
            message += f"; retry after {retry_after:.1f}s"
        super().__init__(message)


class ServiceType(Enum):
    GROQ = "groq"
    CLOUDFLARE = "cloudflare"
    GOOGLE_SEARCH = "google_search"
    TAVILY = "tavily"
    FREEMODEL = "freemodel"
    NINEROUTER = "ninerouter"
    CEREBRAS = "cerebras"
    GEMINI = "gemini"
    IAMHC = "iamhc"
    EVREN = "evren"
    # Kept as a compatibility enum for old fixtures and explicitly pinned
    # legacy deployments. Production routing never selects it by default.
    CLEANAPIS = "cleanapis"

class KeyManager:
    QUOTA_COOLDOWN_SECONDS = 300
    # A transient upstream failure must not silence the only AI route for half
    # a minute; the adapter already performs a bounded retry before this gate.
    TEMPORARY_ERROR_COOLDOWN_SECONDS = 10

    def __init__(self, encryptor=None):
        self.encryptor = encryptor
        self.keys = {
            ServiceType.FREEMODEL: self._maybe_decrypt([Config.FREEMODEL_KEY]) if Config.FREEMODEL_KEY else [],
            ServiceType.GROQ: self._maybe_decrypt(Config.GROQ_KEYS),
            ServiceType.IAMHC: self._maybe_decrypt([Config.IAMHC_KEY]) if Config.IAMHC_KEY else [],
            ServiceType.EVREN: self._maybe_decrypt([Config.EVREN_KEY]) if Config.EVREN_KEY else [],
            ServiceType.CLEANAPIS: self._maybe_decrypt([Config.CLEANAPIS_KEY]) if Config.CLEANAPIS_KEY else [],
            ServiceType.NINEROUTER: self._maybe_decrypt([Config.NINEROUTER_KEY]) if Config.NINEROUTER_KEY else [],
            ServiceType.CEREBRAS: self._maybe_decrypt(Config.CEREBRAS_KEYS),
            ServiceType.GEMINI: self._maybe_decrypt(Config.GEMINI_KEYS),
            ServiceType.TAVILY: self._maybe_decrypt(Config.TAVILY_KEYS),
            ServiceType.CLOUDFLARE: self._maybe_decrypt(Config.CLOUDFLARE_ACCOUNTS),
        }
        
        # Initialize Logger safely
        try:
             self.unified_logger = UnifiedLogger()
        except:
             self.unified_logger = None
        
        # Track current index
        self.current_indices = {
            ServiceType.FREEMODEL: 0,
            ServiceType.GROQ: 0,
            ServiceType.IAMHC: 0,
            ServiceType.EVREN: 0,
            ServiceType.CLEANAPIS: 0,
            ServiceType.NINEROUTER: 0,
            ServiceType.CEREBRAS: 0,
            ServiceType.GEMINI: 0,
            ServiceType.TAVILY: 0,
            ServiceType.CLOUDFLARE: 0,
        }
        
        self.key_statuses: Dict[ServiceType, Dict[str, KeyStatus]] = {
            ServiceType.FREEMODEL: {k: KeyStatus.ACTIVE for k in self.keys[ServiceType.FREEMODEL] if k},
            ServiceType.GROQ: {k: KeyStatus.ACTIVE for k in self.keys[ServiceType.GROQ] if k},
            ServiceType.IAMHC: {k: KeyStatus.ACTIVE for k in self.keys[ServiceType.IAMHC] if k},
            ServiceType.EVREN: {k: KeyStatus.ACTIVE for k in self.keys[ServiceType.EVREN] if k},
            ServiceType.CLEANAPIS: {k: KeyStatus.ACTIVE for k in self.keys[ServiceType.CLEANAPIS] if k},
            ServiceType.NINEROUTER: {k: KeyStatus.ACTIVE for k in self.keys[ServiceType.NINEROUTER] if k},
            ServiceType.CEREBRAS: {k: KeyStatus.ACTIVE for k in self.keys[ServiceType.CEREBRAS] if k},
            ServiceType.GEMINI: {k: KeyStatus.ACTIVE for k in self.keys[ServiceType.GEMINI] if k},
            ServiceType.TAVILY: {k: KeyStatus.ACTIVE for k in self.keys[ServiceType.TAVILY]},
            ServiceType.CLOUDFLARE: {k: KeyStatus.ACTIVE for k in self.keys[ServiceType.CLOUDFLARE]},
        }
        
        self.exhaustion_times: Dict[ServiceType, Dict[str, float]] = {
            ServiceType.GROQ: {},
            ServiceType.CLOUDFLARE: {},
            ServiceType.GOOGLE_SEARCH: {},
            ServiceType.TAVILY: {},
            ServiceType.FREEMODEL: {},
            ServiceType.NINEROUTER: {},
            ServiceType.CEREBRAS: {},
            ServiceType.GEMINI: {},
            ServiceType.IAMHC: {},
            ServiceType.EVREN: {},
            ServiceType.CLEANAPIS: {},
        }
    def _maybe_decrypt(self, keys_list: List[str]) -> List[str]:
        """EÄŸer encryptor varsa ve key ÅŸifreliyse deÅŸifre eder."""
        if not self.encryptor or not self.encryptor.is_available:
            return [k.strip() for k in keys_list if k and k.strip()]

        decrypted = []
        for key in keys_list:
            key = key.strip()
            if not key:
                continue
            if self.encryptor.is_encrypted(key):
                dec = self.encryptor.decrypt(key)
                if dec and dec != key:
                    logger.info("Decrypted API key from encrypted storage")
                    decrypted.append(dec)
                    continue
            decrypted.append(key)
        return decrypted


    def _schedule_log(self, message: str) -> None:
        """Schedule remote logging only when an event loop is running."""
        if not self.unified_logger:
            return

        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.debug("Skipping async provider log because no event loop is running.")
            return

        loop.create_task(
            self.unified_logger.log(
                message,
                topic_id=Config.GROUP_LOG_THREAD_ID,
            )
        )

    def get_key(self, service_type: ServiceType) -> str:
        """
        Returns the current active key for the service.
        If the current key is exhausted, it tries to find the next active one.
        Raises Exception if all keys are exhausted.
        """
        keys_list = self.keys.get(service_type, [])
        if not keys_list:
            raise NoKeysConfiguredError(f"No keys configured for {service_type.value}")

        start_index = self.current_indices[service_type]
        current_time = time.time()
        retry_after_values = []
        
        # 1. First Pass: Try to find an ACTIVE key
        for i in range(len(keys_list)):
            idx = (start_index + i) % len(keys_list)
            key = keys_list[idx]
            status = self.key_statuses[service_type].get(key, KeyStatus.ACTIVE)
            
            if status == KeyStatus.QUOTA_EXHAUSTED:
                exhaustion_time = self.exhaustion_times[service_type].get(key, 0)
                elapsed = current_time - exhaustion_time
                if elapsed >= self.QUOTA_COOLDOWN_SECONDS:
                    logger.info("Quota cooldown passed for %s. Reactivating key.", service_type.value)
                    self.key_statuses[service_type][key] = KeyStatus.ACTIVE
                    status = KeyStatus.ACTIVE
                else:
                    retry_after_values.append(self.QUOTA_COOLDOWN_SECONDS - elapsed)
            
            elif status == KeyStatus.TEMPORARY_ERROR:
                exhausted_at = self.exhaustion_times[service_type].get(key, 0)
                elapsed = current_time - exhausted_at
                if elapsed >= self.TEMPORARY_ERROR_COOLDOWN_SECONDS:
                    logger.info("Temporary cooldown passed for %s. Reactivating key.", service_type.value)
                    self.key_statuses[service_type][key] = KeyStatus.ACTIVE
                    status = KeyStatus.ACTIVE
                else:
                    retry_after_values.append(self.TEMPORARY_ERROR_COOLDOWN_SECONDS - elapsed)

            if status == KeyStatus.ACTIVE:
                self.current_indices[service_type] = idx
                return key

        retry_after = min(retry_after_values) if retry_after_values else None
        raise ServiceCircuitOpenError(service_type, retry_after)


    def report_error(
        self,
        service_type: ServiceType,
        key: str | None,
        error_msg: str,
    ) -> bool:
        """Update a known key's circuit state from a provider error."""
        keys_list = self.keys.get(service_type, [])
        if not key or key not in keys_list:
            logger.warning("Ignoring error report for unknown %s key.", service_type.value)
            return False

        error_msg_lower = error_msg.lower()

        if "429" in error_msg_lower or "quota" in error_msg_lower:
            logger.warning("Marking %s key as quota exhausted.", service_type.value)
            self.key_statuses[service_type][key] = KeyStatus.QUOTA_EXHAUSTED
            self.exhaustion_times[service_type][key] = time.time()
            self._schedule_log(
                f"<b>Key quota doldu ({service_type.value})</b>\n"
                "Key: <code>[redacted]</code>\n"
                f"Durum: {self.QUOTA_COOLDOWN_SECONDS} saniye cooldown"
            )
        elif any(
            marker in error_msg_lower
            for marker in ("suspended", "401", "403", "invalid api key")
        ):
            logger.error("Suspending invalid %s key.", service_type.value)
            self.key_statuses[service_type][key] = KeyStatus.SUSPENDED
            self.exhaustion_times[service_type][key] = time.time()
            self._schedule_log(
                "<b>API key devre disi birakildi</b>\n"
                "Key: <code>[redacted]</code>\n"
                f"Servis: {service_type.value}\n"
                "Durum: kalici askida"
            )
        elif "nsfw" in error_msg_lower:
            logger.warning("NSFW rejection did not alter %s key state.", service_type.value)
            return False
        else:
            logger.warning("Marking %s key as temporary error.", service_type.value)
            self.key_statuses[service_type][key] = KeyStatus.TEMPORARY_ERROR
            self.exhaustion_times[service_type][key] = time.time()

        self._advance_index(service_type)
        return True

    def report_success(self, service_type: ServiceType, key: str) -> None:
        """Close the circuit after a successful provider request."""
        if key not in self.keys.get(service_type, []):
            return
        self.key_statuses[service_type][key] = KeyStatus.ACTIVE
        self.exhaustion_times[service_type].pop(key, None)

    def _advance_index(self, service_type: ServiceType):
        keys_list = self.keys[service_type]
        current = self.current_indices[service_type]
        self.current_indices[service_type] = (current + 1) % len(keys_list)
