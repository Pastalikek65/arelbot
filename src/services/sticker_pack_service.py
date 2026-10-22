"""Owner-scoped Telegram sticker pack operations."""

from __future__ import annotations

import unicodedata
import re
import logging
from typing import Any

from telegram import InputSticker
from src.security.log_redaction import summarize_exception

logger = logging.getLogger(__name__)


class StickerPackService:
    MAX_TITLE_LENGTH = 64
    MAX_POSITION = 49
    PACK_NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")

    def __init__(self, database: Any) -> None:
        self.database = database

    async def _owned_pack(self, user_id: int, pack_name: str) -> dict[str, Any]:
        normalized_name = str(pack_name or "").strip()
        if not self.PACK_NAME_RE.fullmatch(normalized_name):
            raise ValueError("pack_name_invalid")
        pack = await self.database.get_pack_by_name(normalized_name, user_id=int(user_id))
        if not pack or int(pack.get("user_id", -1)) != int(user_id):
            raise PermissionError("pack_owner_required")
        return pack

    @classmethod
    def _title(cls, title: str) -> str:
        value = " ".join(str(title or "").split())
        if not value or len(value) > cls.MAX_TITLE_LENGTH or any(ord(ch) < 32 for ch in value):
            raise ValueError("pack_title_invalid")
        return value

    @staticmethod
    def _emoji_list(value: str) -> list[str]:
        tokens = [token for token in str(value or "").split() if token]
        if not tokens or len(tokens) > 3:
            raise ValueError("sticker_emoji_required")
        for token in tokens:
            if len(token) > 16 or not any(
                ord(char) >= 0x1F000 or unicodedata.category(char) in {"So", "Sk"}
                for char in token
            ):
                raise ValueError("sticker_emoji_required")
        return tokens

    async def rename(self, bot: Any, user_id: int, pack_name: str, title: str) -> dict[str, Any]:
        pack = await self._owned_pack(user_id, pack_name)
        normalized = self._title(title)
        await bot.set_sticker_set_title(name=pack["pack_name"], title=normalized)
        return await self.database.update_sticker_pack(
            pack["pack_name"], int(user_id), {"pack_title": normalized}
        )

    async def set_emoji(self, bot: Any, user_id: int, pack_name: str, emojis: str) -> list[str]:
        pack = await self._owned_pack(user_id, pack_name)
        values = self._emoji_list(emojis)
        # This operation targets the current sticker in the command surface;
        # callers pass a file id through ``set_sticker_emoji`` below.
        await self.database.update_sticker_pack(
            pack["pack_name"], int(user_id), {"default_emojis": values}
        )
        return values

    async def set_sticker_emoji(
        self, bot: Any, user_id: int, pack_name: str, sticker_file_id: str, emojis: str
    ) -> list[str]:
        await self._owned_pack(user_id, pack_name)
        values = self._emoji_list(emojis)
        await bot.set_sticker_emoji_list(sticker_file_id, values)
        updater = getattr(self.database, "update_sticker_emojis", None)
        if updater is not None:
            await updater(pack_name, int(user_id), sticker_file_id, values)
        return values

    async def add_sticker(
        self,
        bot: Any,
        user_id: int,
        pack_name: str,
        sticker_file_id: str,
        emojis: str | list[str],
        sticker_format: Any,
    ) -> dict[str, Any]:
        """Add a sticker to an owned pack and register it in the catalog.

        The catalog is used as the durable idempotency boundary. Telegram
        callback queries can be delivered more than once; once the same
        ``pack_id/file_id`` pair is registered, a retry does not call the
        remote API again.
        """
        pack = await self._owned_pack(user_id, pack_name)
        file_id = str(sticker_file_id or "").strip()
        if not file_id:
            raise ValueError("sticker_file_required")
        emoji_text = " ".join(emojis) if isinstance(emojis, list) else str(emojis or "")
        values = self._emoji_list(emoji_text)
        pack_id = pack.get("_id") or pack["pack_name"]

        existing_getter = getattr(self.database, "get_sticker_in_pack", None)
        if existing_getter is not None:
            existing = await existing_getter(pack_id, file_id)
            if existing:
                return {"status": "already_exists", "pack": pack, "sticker": existing}

        # A durable operation claim closes the race between two workers. A
        # pending claim is deliberately treated as in-progress: after an
        # ambiguous Telegram timeout, retrying blindly could create a second
        # remote sticker even though the first request succeeded.
        claim = getattr(self.database, "claim_sticker_add", None)
        if claim is not None:
            reservation = await claim(pack_id, file_id, int(user_id))
            reservation_status = str((reservation or {}).get("status", "pending"))
            if reservation_status == "completed":
                return {"status": "already_exists", "pack": pack, "sticker": reservation}
            if reservation_status != "claimed":
                return {"status": "in_progress", "pack": pack}

        await bot.add_sticker_to_set(
            user_id=int(user_id),
            name=pack["pack_name"],
            sticker=InputSticker(
                sticker=file_id,
                emoji_list=values,
                format=sticker_format,
            ),
        )

        catalog_registered = True
        register = getattr(self.database, "register_sticker", None)
        if register is not None:
            try:
                await register(pack_id, file_id, values, tags=[])
            except Exception as exc:
                # Telegram already accepted the sticker. Keep the operation
                # completed so a retry cannot duplicate it; health/admin
                # reconciliation can repair the catalog entry later.
                catalog_registered = False
                logger.error("Sticker catalog registration failed after Telegram add: %s", summarize_exception(exc, label="sticker_catalog"))
        complete = getattr(self.database, "complete_sticker_add", None)
        if complete is not None:
            await complete(pack_id, file_id, catalog_registered=catalog_registered)
        return {
            "status": "added",
            "pack": pack,
            "sticker_file_id": file_id,
            "emojis": values,
            "catalog_registered": catalog_registered,
        }

    async def reorder(
        self, bot: Any, user_id: int, pack_name: str, sticker_file_id: str, position: int
    ) -> bool:
        pack = await self._owned_pack(user_id, pack_name)
        try:
            position = int(position)
        except (TypeError, ValueError) as exc:
            raise ValueError("sticker_position_invalid") from exc
        if position < 0 or position > self.MAX_POSITION:
            raise ValueError("sticker_position_invalid")
        moved = bool(await bot.set_sticker_position_in_set(sticker_file_id, position))
        if moved:
            updater = getattr(self.database, "update_sticker_position", None)
            if updater is not None:
                await updater(
                    pack["pack_name"], int(user_id), str(sticker_file_id), position
                )
        return moved

    async def delete_sticker(
        self, bot: Any, user_id: int, pack_name: str, sticker_file_id: str
    ) -> bool:
        """Delete one sticker after re-checking pack ownership."""

        pack = await self._owned_pack(user_id, pack_name)
        file_id = str(sticker_file_id or "").strip()
        if not file_id or len(file_id) > 512:
            raise ValueError("sticker_file_required")
        deleted = bool(await bot.delete_sticker_from_set(sticker=file_id))
        if deleted:
            remover = getattr(self.database, "delete_sticker_from_db", None)
            if remover is not None:
                await remover(pack.get("_id") or pack["pack_name"], file_id)
        return deleted

    async def set_thumbnail(
        self, bot: Any, user_id: int, pack_name: str, thumbnail_file_id: str
    ) -> dict[str, Any]:
        """Set a Telegram pack thumbnail and persist its file id."""

        pack = await self._owned_pack(user_id, pack_name)
        thumbnail = str(thumbnail_file_id or "").strip()
        if not thumbnail or len(thumbnail) > 512:
            raise ValueError("sticker_thumbnail_required")
        sticker_format = str(pack.get("pack_type") or "static").casefold()
        if sticker_format not in {"static", "video", "animated"}:
            raise ValueError("sticker_format_invalid")
        await bot.set_sticker_set_thumbnail(
            name=pack["pack_name"],
            user_id=int(user_id),
            format=sticker_format,
            thumbnail=thumbnail,
        )
        updater = getattr(self.database, "update_sticker_pack", None)
        if updater is None:
            return {**pack, "thumbnail_file_id": thumbnail}
        return await updater(
            pack["pack_name"], int(user_id), {"thumbnail_file_id": thumbnail}
        )

    async def delete(self, bot: Any, user_id: int, pack_name: str) -> bool:
        pack = await self._owned_pack(user_id, pack_name)
        deleted = bool(await bot.delete_sticker_set(pack["pack_name"]))
        if deleted:
            await self.database.delete_sticker_pack(pack["pack_name"], int(user_id))
        return deleted
