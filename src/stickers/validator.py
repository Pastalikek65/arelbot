"""Telegram sticker constraints kept independent from handler code."""

from __future__ import annotations

import re


class StickerValidationError(ValueError):
    pass


_SHORT_NAME = re.compile(r"^[a-z][a-z0-9_]{2,63}$")


def validate_short_name(value: str) -> str:
    value = (value or "").strip().lower()
    if not _SHORT_NAME.fullmatch(value):
        raise StickerValidationError("Paket adı 3-64 karakter olmalı; küçük harf, sayı ve alt çizgi kullanın.")
    return value


def make_pack_name(short_name: str, bot_username: str) -> str:
    short_name = validate_short_name(short_name)
    username = (bot_username or "arelbot").lstrip("@").lower()
    if not re.fullmatch(r"[a-z0-9_]{3,64}", username):
        raise StickerValidationError("Bot kullanıcı adı geçersiz.")
    suffix = f"_by_{username}"
    if len(short_name) + len(suffix) > 64:
        short_name = short_name[:64 - len(suffix)]
    return f"{short_name}{suffix}"


def validate_emoji(value: str) -> str:
    value = (value or "").strip()
    if not value or len(value) > 16 or any(ch.isspace() for ch in value):
        raise StickerValidationError("En az bir geçerli emoji girin.")
    return value
