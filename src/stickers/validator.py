"""Telegram sticker constraints kept independent from handler code."""

from __future__ import annotations

import re


class StickerValidationError(ValueError):
    pass


_SHORT_NAME = re.compile(r"^[a-z][a-z0-9_]{2,63}$")


def validate_short_name(value: str) -> str:
    value = (value or "").strip().lower()
    if not _SHORT_NAME.fullmatch(value):
        raise StickerValidationError("Paket adÄ± 3-64 karakter olmalÄ±; kÃ¼Ã§Ã¼k harf, sayÄ± ve alt Ã§izgi kullanÄ±n.")
    return value


def make_pack_name(short_name: str, bot_username: str) -> str:
    short_name = validate_short_name(short_name)
    username = (bot_username or "arelbot").lstrip("@").lower()
    if not re.fullmatch(r"[a-z0-9_]{3,64}", username):
        raise StickerValidationError("Bot kullanÄ±cÄ± adÄ± geÃ§ersiz.")
    suffix = f"_by_{username}"
    if len(short_name) + len(suffix) > 64:
        short_name = short_name[:64 - len(suffix)]
    return f"{short_name}{suffix}"


def validate_emoji(value: str) -> str:
    value = (value or "").strip()
    if not value or len(value) > 16 or any(ch.isspace() for ch in value):
        raise StickerValidationError("En az bir geÃ§erli emoji girin.")
    return value
