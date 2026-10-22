"""Read only manifest-listed corresponding source parts for private delivery."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from pathlib import Path


class UnoSourceOffer:
    def __init__(self, root: str | Path = "."):
        self.root = Path(root).resolve()

    def _metadata(self):
        source = self.root / "SOURCE_OFFER.json"
        if source.is_symlink():
            raise ValueError("UNO_SOURCE_UNAVAILABLE")
        value = json.loads(source.read_text(encoding="utf-8"))
        if not re.fullmatch(r"[0-9a-f]{40}", str(value.get("commit", ""))):
            raise ValueError("UNO_SOURCE_UNAVAILABLE")
        if not isinstance(value.get("parts"), list) or not 1 <= len(value["parts"]) <= 20:
            raise ValueError("UNO_SOURCE_UNAVAILABLE")
        return value

    def _read(self, part):
        name = str(part.get("file", ""))
        if not re.fullmatch(r"source/arel-source\.tar\.gz\.part[0-9]{3}", name):
            raise ValueError("UNO_SOURCE_UNAVAILABLE")
        path = self.root / name
        if path.is_symlink() or self.root not in path.resolve().parents:
            raise ValueError("UNO_SOURCE_UNAVAILABLE")
        if not 0 < path.stat().st_size <= 20 * 1024 * 1024:
            raise ValueError("UNO_SOURCE_UNAVAILABLE")
        data = path.read_bytes()
        if len(data) != part["bytes"] or hashlib.sha256(data).hexdigest() != part["sha256"]:
            raise ValueError("UNO_SOURCE_UNAVAILABLE")
        return Path(name).name, data

    async def parts(self):
        metadata = await asyncio.to_thread(self._metadata)
        for part in metadata["parts"]:
            yield await asyncio.to_thread(self._read, part)
