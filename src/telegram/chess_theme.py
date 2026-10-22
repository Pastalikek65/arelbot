"""Verified native chess tiles; no network or file I/O while formatting a request."""
from __future__ import annotations

import json
from pathlib import Path
from types import MappingProxyType

_SYMBOLS = dict(zip("♙♘♗♖♕♔♟♞♝♜♛♚", "PNBRQKpnbrqk", strict=True))


def _catalog():
    data = json.loads((Path(__file__).resolve().parents[2] / "assets/telegram/chess_icons.json").read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or set(data["pieces"]) != set("PNBRQKpnbrqk"):
        raise ValueError("chess_icon_catalog_invalid")
    tiles = {key: MappingProxyType(value) for key, value in data["pieces"].items()}
    tiles["empty"] = MappingProxyType(data["empty"])
    for variants in tiles.values():
        if set(variants) != {"light", "dark"} or any(
            not isinstance(v, str) or not v.isascii() or not v.isdecimal() or not 0 < int(v) < 2**64
            for v in variants.values()
        ):
            raise ValueError("chess_icon_catalog_identifier")
    return MappingProxyType(tiles)


CHESS_TILES = _catalog()


def decorate_chess_square(button: dict, *, premium: bool) -> bool:
    """Recognize only a real chess square; retain the original Unicode fallback."""
    parts = str(button.get("callback_data", "")).split(":")
    if not premium or len(parts) != 4 or parts[0] != "chess" or parts[2] != "sq":
        return False
    square = parts[3]
    if len(square) != 2 or square[0] not in "abcdefgh" or square[1] not in "12345678":
        return False
    label = str(button.get("text", "")).replace("\ufe0e", "").replace("\ufe0f", "")
    piece = _SYMBOLS.get(label)
    if piece is None and label not in {"⬜", "⬛", "•"}:
        return False
    shade = "dark" if (ord(square[0]) - ord("a") + int(square[1]) - 1) % 2 == 0 else "light"
    button["icon_custom_emoji_id"] = CHESS_TILES[piece or "empty"][shade]
    # A nonempty blank keeps a 20px tile centered without duplicating its piece.
    button["text"] = "\u2800"
    return True
