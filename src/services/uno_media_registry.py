"""Load the bot-specific sticker registry during application bootstrap."""
from __future__ import annotations

import json
from pathlib import Path


def load_sticker_ids(path: str = "assets/uno_maildian/telegram_stickers.json") -> dict[str, str]:
    source = Path(path)
    if not source.is_file():
        return {}
    value = json.loads(source.read_text(encoding="utf-8"))
    entries = value.get("file_ids", {})
    if not isinstance(entries, dict) or any(not isinstance(k, str) or not isinstance(v, str) or not v for k, v in entries.items()):
        raise ValueError("UNO_STICKER_REGISTRY_INVALID")
    faces = {f"{color}_{kind}" for color in "bgry" for kind in [*"0123456789", "draw", "skip", "reverse"]}
    faces |= {"colorchooser", "draw_four"}
    required = {f"{prefix}:{face}" for prefix in ("colored", "grey") for face in faces}
    required |= {f"option_{kind}" for kind in ("draw", "pass", "bluff", "info")}
    if not required <= entries.keys():
        raise ValueError("UNO_STICKER_REGISTRY_INCOMPLETE")
    return entries
