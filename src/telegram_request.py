"""Shared Telegram HTTP request construction for bot and worker processes."""

from __future__ import annotations

from telegram import Bot
from telegram.request import HTTPXRequest

from src.security.tls import tls_verify


def _request_kwargs() -> dict:
    kwargs: dict = {"pool_timeout": 30.0}
    kwargs["httpx_kwargs"] = {"verify": tls_verify()}
    return kwargs


def build_request_pair() -> tuple[HTTPXRequest, HTTPXRequest]:
    """Create independent request clients for API calls and getUpdates."""
    kwargs = _request_kwargs()
    return HTTPXRequest(**kwargs), HTTPXRequest(**kwargs)


def build_bot(token: str) -> Bot:
    request, get_updates_request = build_request_pair()
    return Bot(token, request=request, get_updates_request=get_updates_request)
