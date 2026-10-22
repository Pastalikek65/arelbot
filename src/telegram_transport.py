"""Pure Telegram transport configuration.

The application bootstrap owns the Telegram Application instance, while this
module owns the transport contract.  Keeping the contract pure makes webhook
and polling behavior testable without opening a socket or importing the bot
bootstrap (which has deliberately expensive dependencies).
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from telegram import Update


TransportMode = Literal["polling", "webhook"]
_PATH_RE = re.compile(r"^[A-Za-z0-9_/-]{1,128}$")
_SECRET_RE = re.compile(r"^[A-Za-z0-9_-]{16,256}$")


def _normalize_path(raw: str, *, allow_empty: bool = False) -> str:
    value = str(raw or "").strip().strip("/")
    if not value:
        if allow_empty:
            return ""
        raise ValueError("WEBHOOK_PATH must not be empty")
    if not _PATH_RE.fullmatch(value):
        raise ValueError("WEBHOOK_PATH contains unsafe characters")
    segments = value.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise ValueError("WEBHOOK_PATH contains unsafe segments")
    return "/" + "/".join(segments)


@dataclass(frozen=True, slots=True)
class TelegramTransport:
    """Validated values needed by python-telegram-bot's run methods.

    Validation of secrets and the full environment remains in ``Config``.
    This object only normalizes values that affect the public transport URL,
    ensuring a URL which already contains ``WEBHOOK_PATH`` is not given the
    path twice.
    """

    mode: TransportMode
    webhook_url: str | None = None
    webhook_listen: str = "0.0.0.0"
    webhook_port: int = 8443
    webhook_path: str = "telegram/webhook"
    webhook_secret: str | None = None

    @classmethod
    def from_config(cls, config: Any) -> "TelegramTransport":
        mode = str(config.TELEGRAM_MODE).strip().lower()
        if mode not in {"polling", "webhook"}:
            raise ValueError("TELEGRAM_MODE must be 'polling' or 'webhook'")
        return cls(
            mode=mode,
            webhook_url=getattr(config, "WEBHOOK_URL", None),
            webhook_listen=str(getattr(config, "WEBHOOK_LISTEN", "0.0.0.0")),
            webhook_port=int(getattr(config, "WEBHOOK_PORT", 8443)),
            webhook_path=str(getattr(config, "WEBHOOK_PATH", "telegram/webhook")),
            webhook_secret=getattr(config, "WEBHOOK_SECRET", None),
        )

    @property
    def normalized_webhook_path(self) -> str:
        return _normalize_path(self.webhook_path).lstrip("/")

    @property
    def public_webhook_url(self) -> str:
        """Return the URL Telegram should call, without duplicate paths."""

        if self.mode != "webhook":
            raise ValueError("A public webhook URL is only available in webhook mode")
        if not self.webhook_url:
            raise ValueError("WEBHOOK_URL is required when TELEGRAM_MODE=webhook")

        configured = urlsplit(self.webhook_url.strip())
        if (
            configured.scheme.casefold() != "https"
            or not configured.hostname
            or configured.username
            or configured.password
            or configured.query
            or configured.fragment
        ):
            raise ValueError("WEBHOOK_URL must be a credential-free HTTPS URL")
        try:
            configured_port = configured.port
        except ValueError as exc:
            raise ValueError("WEBHOOK_URL port is invalid") from exc
        if configured_port is not None and not 1 <= configured_port <= 65535:
            raise ValueError("WEBHOOK_URL port is invalid")
        try:
            configured_path = _normalize_path(configured.path, allow_empty=True)
        except ValueError as exc:
            raise ValueError("WEBHOOK_URL path is invalid") from exc
        transport_path = "/" + self.normalized_webhook_path
        if configured_path == transport_path or configured_path.endswith(transport_path):
            final_path = configured_path
        elif configured_path:
            final_path = configured_path + transport_path
        else:
            final_path = transport_path
        return urlunsplit((configured.scheme, configured.netloc, final_path, "", ""))

    def webhook_kwargs(self) -> dict[str, Any]:
        if self.mode != "webhook":
            raise ValueError("webhook_kwargs() requires webhook mode")
        if not self.webhook_secret or not _SECRET_RE.fullmatch(str(self.webhook_secret)):
            raise ValueError("WEBHOOK_SECRET must contain 16-256 URL-safe characters")
        if not str(self.webhook_listen).strip():
            raise ValueError("WEBHOOK_LISTEN must not be empty")
        if not 1 <= int(self.webhook_port) <= 65535:
            raise ValueError("WEBHOOK_PORT must be between 1 and 65535")
        return {
            "listen": self.webhook_listen,
            "port": self.webhook_port,
            "url_path": self.normalized_webhook_path,
            "webhook_url": self.public_webhook_url,
            "secret_token": self.webhook_secret,
            "allowed_updates": list(Update.ALL_TYPES),
        }

    def polling_kwargs(self) -> dict[str, Any]:
        if self.mode != "polling":
            raise ValueError("polling_kwargs() requires polling mode")
        return {"allowed_updates": list(Update.ALL_TYPES)}


def transport_from_config(config: Any) -> TelegramTransport:
    """Small dependency-injection seam used by bootstrap and tests."""

    return TelegramTransport.from_config(config)
