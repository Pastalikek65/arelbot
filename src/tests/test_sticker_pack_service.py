from __future__ import annotations

import pytest

from src.services.sticker_pack_service import StickerPackService


class FakeDatabase:
    def __init__(self):
        self.pack = {
            "pack_name": "arel_fun_by_bot",
            "pack_title": "Eski Başlık",
            "user_id": 42,
            "pack_type": "static",
        }
        self.updates = []
        self.deleted = []
        self.stickers = {}
        self.removed_stickers = []
        self.positions = []

    async def get_sticker_in_pack(self, pack_id, file_id):
        return self.stickers.get((str(pack_id), str(file_id)))

    async def register_sticker(self, pack_id, file_id, emojis, tags=None):
        self.stickers[(str(pack_id), str(file_id))] = {
            "pack_id": pack_id,
            "file_id": file_id,
            "emojis": emojis,
            "tags": tags or [],
        }

    async def get_pack_by_name(self, pack_name, user_id=None):
        if pack_name != self.pack["pack_name"]:
            return None
        if user_id is not None and user_id != self.pack["user_id"]:
            return None
        return dict(self.pack)

    async def update_sticker_pack(self, pack_name, user_id, updates):
        self.updates.append((pack_name, user_id, updates))
        self.pack.update(updates)
        return dict(self.pack)

    async def delete_sticker_pack(self, pack_name, user_id):
        self.deleted.append((pack_name, user_id))
        return True

    async def delete_sticker_from_db(self, pack_id, file_id):
        self.removed_stickers.append((pack_id, file_id))

    async def update_sticker_position(self, pack_name, user_id, file_id, position):
        self.positions.append((pack_name, user_id, file_id, position))


class FakeBot:
    def __init__(self):
        self.calls = []

    async def add_sticker_to_set(self, **kwargs):
        self.calls.append(("add", kwargs))
        return True

    async def set_sticker_set_title(self, name, title):
        self.calls.append(("rename", name, title))
        return True

    async def set_sticker_emoji_list(self, sticker, emoji_list):
        self.calls.append(("emoji", sticker, tuple(emoji_list)))
        return True

    async def set_sticker_position_in_set(self, sticker, position):
        self.calls.append(("position", sticker, position))
        return True

    async def delete_sticker_set(self, name):
        self.calls.append(("delete", name))
        return True

    async def delete_sticker_from_set(self, sticker):
        self.calls.append(("delete_sticker", sticker))
        return True

    async def set_sticker_set_thumbnail(self, **kwargs):
        self.calls.append(("thumbnail", kwargs))
        return True


@pytest.mark.asyncio
async def test_pack_service_requires_owner_and_updates_telegram_then_database():
    database = FakeDatabase()
    bot = FakeBot()
    service = StickerPackService(database)

    renamed = await service.rename(bot, 42, "arel_fun_by_bot", "Yeni Koleksiyon")
    assert renamed["pack_title"] == "Yeni Koleksiyon"
    assert bot.calls == [("rename", "arel_fun_by_bot", "Yeni Koleksiyon")]
    assert database.updates[-1][2] == {"pack_title": "Yeni Koleksiyon"}

    with pytest.raises(PermissionError, match="pack_owner_required"):
        await service.rename(bot, 7, "arel_fun_by_bot", "Başkasının paketi")


@pytest.mark.asyncio
async def test_pack_service_validates_emoji_and_position_before_telegram_call():
    database = FakeDatabase()
    bot = FakeBot()
    service = StickerPackService(database)

    await service.set_sticker_emoji(bot, 42, "arel_fun_by_bot", "sticker-file", "😀 😂")
    assert bot.calls[-1] == ("emoji", "sticker-file", ("😀", "😂"))

    await service.reorder(bot, 42, "arel_fun_by_bot", "sticker-file", 3)
    assert bot.calls[-1] == ("position", "sticker-file", 3)
    assert database.positions == [("arel_fun_by_bot", 42, "sticker-file", 3)]

    with pytest.raises(ValueError, match="sticker_position_invalid"):
        await service.reorder(bot, 42, "arel_fun_by_bot", "sticker-file", 50)
    with pytest.raises(ValueError, match="sticker_emoji_required"):
        await service.set_emoji(bot, 42, "arel_fun_by_bot", "not-an-emoji")


@pytest.mark.asyncio
async def test_pack_service_delete_is_owner_scoped_and_removes_local_record_after_api_success():
    database = FakeDatabase()
    bot = FakeBot()
    service = StickerPackService(database)

    assert await service.delete(bot, 42, "arel_fun_by_bot") is True
    assert bot.calls == [("delete", "arel_fun_by_bot")]
    assert database.deleted == [("arel_fun_by_bot", 42)]


@pytest.mark.asyncio
async def test_pack_service_add_registers_catalog_and_is_idempotent():
    database = FakeDatabase()
    bot = FakeBot()
    service = StickerPackService(database)

    first = await service.add_sticker(
        bot,
        42,
        "arel_fun_by_bot",
        "sticker-file",
        "😀 😂",
        "static",
    )
    second = await service.add_sticker(
        bot,
        42,
        "arel_fun_by_bot",
        "sticker-file",
        "😀 😂",
        "static",
    )

    assert first["status"] == "added"
    assert second["status"] == "already_exists"
    assert [call[0] for call in bot.calls] == ["add"]
    assert ("arel_fun_by_bot", "sticker-file") in database.stickers


@pytest.mark.asyncio
async def test_pack_service_fails_closed_when_a_durable_add_is_already_pending():
    class LeasedDatabase(FakeDatabase):
        async def claim_sticker_add(self, pack_id, file_id, user_id):
            return {"status": "pending", "pack_id": pack_id, "file_id": file_id}

    database = LeasedDatabase()
    bot = FakeBot()
    service = StickerPackService(database)

    result = await service.add_sticker(
        bot,
        42,
        "arel_fun_by_bot",
        "sticker-file",
        "😀",
        "static",
    )

    assert result["status"] == "in_progress"
    assert bot.calls == []


@pytest.mark.asyncio
async def test_pack_service_deletes_one_sticker_only_after_owner_check():
    database = FakeDatabase()
    bot = FakeBot()
    service = StickerPackService(database)

    assert await service.delete_sticker(bot, 42, "arel_fun_by_bot", "sticker-file") is True
    assert ("delete_sticker", "sticker-file") in bot.calls
    assert database.removed_stickers == [("arel_fun_by_bot", "sticker-file")]
    with pytest.raises(PermissionError, match="pack_owner_required"):
        await service.delete_sticker(bot, 7, "arel_fun_by_bot", "sticker-file")


@pytest.mark.asyncio
async def test_pack_service_sets_thumbnail_with_pack_format_and_persists_file_id():
    database = FakeDatabase()
    bot = FakeBot()
    service = StickerPackService(database)

    result = await service.set_thumbnail(bot, 42, "arel_fun_by_bot", "thumb-file")
    assert bot.calls[-1] == (
        "thumbnail",
        {
            "name": "arel_fun_by_bot",
            "user_id": 42,
            "format": "static",
            "thumbnail": "thumb-file",
        },
    )
    assert result["thumbnail_file_id"] == "thumb-file"
