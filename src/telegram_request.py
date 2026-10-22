"""Shared Telegram HTTP request construction for bot and worker processes."""

from __future__ import annotations

import json
import logging
import os

from telegram.request import HTTPXRequest

from src.security.tls import tls_verify
from src.telegram.button_theme import decorate_markup
from telegram import Bot

logger = logging.getLogger(__name__)


class _ThemedData:
    """Use RequestData's public interfaces; retain original uploads verbatim."""

    def __init__(self, original, markup):
        self.original = original
        self.markup = markup
        self.contains_files = original.contains_files

    @property
    def parameters(self):
        return dict(self.original.parameters, reply_markup=self.markup)

    @property
    def json_parameters(self):
        return dict(self.original.json_parameters, reply_markup=json.dumps(self.markup, ensure_ascii=False))

    @property
    def multipart_data(self):
        return self.original.multipart_data


def _upload_positions(data):
    positions = []
    for upload in data.multipart_data.values():
        content = upload[1]
        if not hasattr(content, "read"):
            continue
        try:
            position = content.tell()
            content.seek(position)
        except (AttributeError, OSError, ValueError):
            return None
        positions.append((content, position))
    return positions


def _custom_emoji_rejected(status, body):
    if status != 400:
        return False
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return False
    if not isinstance(payload, dict) or payload.get("ok") is not False:
        return False
    description = str(payload.get("description", "")).lower()
    return any(
        marker in description
        for marker in (
            "custom_emoji",
            "custom emoji",
            "button_icon",
            "emoji_not_allowed",
            "emoji invalid",
            "emoji_invalid",
            "button_text_invalid",
            "button text is empty",
        )
    )


class ThemedHTTPXRequest(HTTPXRequest):
    """Style direct inline keyboards with a single safe rejected-request fallback."""

    def __init__(self, *, premium_buttons=True, **kwargs):
        super().__init__(**kwargs)
        self.premium_buttons = bool(premium_buttons)
        self.emoji_fallbacks = 0

    async def do_request(self, url, method, request_data=None, **timeouts):
        parameters = request_data.parameters if request_data is not None else {}
        original = parameters.get("reply_markup")
        if not isinstance(original, dict) or "inline_keyboard" not in original:
            return await super().do_request(url, method, request_data, **timeouts)
        positions = _upload_positions(request_data)
        # Owner Premium permits custom icons only on directly sent messages.
        premium = self.premium_buttons and "chat_id" in parameters and positions is not None
        markup = decorate_markup(original, premium=premium)
        if markup == original:
            return await super().do_request(url, method, request_data, **timeouts)
        status, body = await super().do_request(url, method, _ThemedData(request_data, markup), **timeouts)
        fallback = decorate_markup(original, premium=False)
        if premium and markup != fallback and _custom_emoji_rejected(status, body):
            self.premium_buttons = False
            self.emoji_fallbacks += 1
            logger.warning("Telegram custom button emoji rejected; Unicode fallback enabled")
            # Only an explicit API rejection may be retried, never a network
            # timeout, 429, 5xx or ambiguous accepted delivery.
            for stream, position in positions:
                try:
                    stream.seek(position)
                except (OSError, ValueError):
                    return status, body
            return await super().do_request(url, method, _ThemedData(request_data, fallback), **timeouts)
        return status, body


def _request_kwargs() -> dict:
    kwargs: dict = {"pool_timeout": 30.0}
    kwargs["httpx_kwargs"] = {"verify": tls_verify()}
    return kwargs


def build_request_pair() -> tuple[HTTPXRequest, HTTPXRequest]:
    """Create independent request clients for API calls and getUpdates."""
    kwargs = _request_kwargs()
    premium = os.getenv("TELEGRAM_CUSTOM_BUTTON_EMOJI", "auto").lower() not in {"0", "false", "off"}
    return ThemedHTTPXRequest(premium_buttons=premium, **kwargs), HTTPXRequest(**kwargs)


def build_bot(token: str) -> Bot:
    request, get_updates_request = build_request_pair()
    return Bot(token, request=request, get_updates_request=get_updates_request)
