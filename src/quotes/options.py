"""Typed, bounded quote options shared by handlers, storage and rendering."""

from __future__ import annotations

import math
import re
from typing import Any, Mapping

from .themes import THEMES


class QuoteOptionsError(ValueError):
    """Stable validation error for quote options and template names."""


_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_TEMPLATE_NAME = re.compile(r"^[\w-]{1,32}$", re.UNICODE)


def normalize_template_name(value: str) -> str:
    name = " ".join(str(value or "").split()).strip().casefold()
    if not name or not _TEMPLATE_NAME.fullmatch(name):
        raise QuoteOptionsError("template_name_invalid")
    return name


def _optional_color(value: Any, key: str) -> str | None:
    if value in (None, "", "default"):
        return None
    color = str(value).strip()
    if not color.startswith("#"):
        color = f"#{color}"
    if not _COLOR.fullmatch(color):
        raise QuoteOptionsError(f"{key}_invalid")
    return color.upper()


def _boolean(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().casefold()
    if normalized in {"1", "true", "on", "yes", "evet", "açık", "acik"}:
        return True
    if normalized in {"0", "false", "off", "no", "hayır", "hayir", "kapalı", "kapali"}:
        return False
    raise QuoteOptionsError("boolean_option_invalid")


def normalize_quote_options(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return only renderer-supported, bounded quote options."""

    source = dict(value or {})
    theme = str(source.get("theme") or "dark").strip().casefold()
    if theme not in THEMES:
        raise QuoteOptionsError("theme_invalid")
    try:
        scale = float(source.get("scale", 1.0))
    except (TypeError, ValueError) as exc:
        raise QuoteOptionsError("scale_invalid") from exc
    if not math.isfinite(scale) or not 0.5 <= scale <= 2.0:
        raise QuoteOptionsError("scale_invalid")

    emoji = str(source.get("emoji") or "✨").strip()
    if not emoji or len(emoji) > 16 or any(ord(char) < 32 for char in emoji):
        raise QuoteOptionsError("emoji_invalid")

    return {
        "theme": theme,
        "bg_color": _optional_color(source.get("bg_color"), "bg_color"),
        "name_color": _optional_color(source.get("name_color"), "name_color"),
        "text_color": _optional_color(source.get("text_color"), "text_color"),
        "scale": scale,
        "is_png": _boolean(source.get("is_png"), False),
        "as_file": _boolean(source.get("as_file"), False),
        "show_reply": _boolean(source.get("show_reply"), False),
        "crop": _boolean(source.get("crop"), False),
        "rate_enabled": _boolean(source.get("rate_enabled"), True),
        "emoji": emoji,
    }

