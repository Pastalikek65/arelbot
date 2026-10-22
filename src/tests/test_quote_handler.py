from types import SimpleNamespace

import pytest

from src.handlers.quote_handler import QuoteHandler


def _telegram_message(*, text, user_id=1, name="Ali", message_id=1, reply_to_message=None):
    return SimpleNamespace(
        text=text,
        caption=None,
        from_user=SimpleNamespace(id=user_id, full_name=name),
        sender_chat=None,
        entities=None,
        caption_entities=None,
        message_id=message_id,
        reply_to_message=reply_to_message,
    )


class FakeDatabase:
    available = True

    def __init__(self, rows):
        self.rows = rows

    async def find_latest_message_for_quote(self, chat_id, user_id, content):
        return {"_id": "reply-db", "timestamp": "2026-01-01T00:00:00Z"}

    async def get_sequential_messages(self, chat_id, start_timestamp, limit):
        return self.rows[:limit]


class FakeEngine:
    def __init__(self, db):
        self.db = db


@pytest.mark.asyncio
async def test_reply_quote_collects_requested_sequential_messages_without_duplicates():
    replied = _telegram_message(text="Birinci", user_id=7, message_id=42)
    update = SimpleNamespace(message=SimpleNamespace(reply_to_message=replied))
    rows = [
        {"_id": "reply-db", "content": "Birinci", "user_id": 7, "user_info": {"first_name": "Ali"}},
        {"_id": "second", "content": "İkinci", "user_id": 8, "user_info": {"first_name": "Ayşe"}},
        {"_id": "third", "content": "Üçüncü", "user_id": 9, "user_info": {"first_name": "Can"}},
        {"_id": "fourth", "content": "Dördüncü", "user_id": 10, "user_info": {"first_name": "Deniz"}},
    ]
    handler = QuoteHandler(FakeEngine(FakeDatabase(rows)), None)

    result = await handler._get_reply_messages(
        update,
        {"count": 3, "show_reply": False},
        chat_id=100,
    )

    assert [item["text"] for item in result] == ["Birinci", "İkinci", "Üçüncü"]
    assert len(result) == 3


@pytest.mark.asyncio
async def test_reply_quote_falls_back_to_one_message_when_storage_is_unavailable():
    replied = _telegram_message(text="Yerel mesaj", user_id=7)
    update = SimpleNamespace(message=SimpleNamespace(reply_to_message=replied))
    database = FakeDatabase([])
    database.available = False
    handler = QuoteHandler(FakeEngine(database), None)

    result = await handler._get_reply_messages(
        update,
        {"count": 5, "show_reply": False},
        chat_id=100,
    )

    assert [item["text"] for item in result] == ["Yerel mesaj"]


@pytest.mark.asyncio
async def test_reply_quote_show_reply_adds_parent_context_once():
    parent = _telegram_message(text="Önceki mesaj", user_id=3, message_id=10)
    replied = _telegram_message(text="Yanıt", user_id=7, message_id=11, reply_to_message=parent)
    update = SimpleNamespace(message=SimpleNamespace(reply_to_message=replied))
    database = FakeDatabase([])
    database.available = False
    handler = QuoteHandler(FakeEngine(database), None)

    result = await handler._get_reply_messages(
        update,
        {"count": 1, "show_reply": True},
        chat_id=100,
    )

    assert [item["text"] for item in result] == ["Önceki mesaj", "Yanıt"]
