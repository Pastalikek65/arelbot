from __future__ import annotations

import pytest

from src.services.group_analytics_service import GroupAnalyticsService
from src.handlers.command_handler import CommandHandler


@pytest.mark.asyncio
async def test_group_analytics_renders_weekly_summary_with_safe_names():
    class FakeDatabase:
        async def get_group_analytics(self, chat_id, since, limit=5):
            assert chat_id == -1001
            assert since is not None
            return {
                "total_messages": 128,
                "active_users": 9,
                "peak_hour": 21,
                "top_users": [
                    {"name": "<Admin>", "count": 44},
                    {"name": "Ayşe", "count": 21},
                ],
            }

    service = GroupAnalyticsService(FakeDatabase())

    result = await service.render(chat_id=-1001, days=7)

    assert "HAFTALIK GRUP ANALİZİ" in result
    assert "128" in result
    assert "9" in result
    assert "21:00–22:00" in result
    assert "&lt;Admin&gt;" in result
    assert "<Admin>" not in result


@pytest.mark.asyncio
async def test_group_analytics_rejects_unsafe_periods_before_repository_call():
    class FakeDatabase:
        async def get_group_analytics(self, *args, **kwargs):
            raise AssertionError("repository must not be called")

    service = GroupAnalyticsService(FakeDatabase())

    with pytest.raises(ValueError, match="days_out_of_range"):
        await service.summary(chat_id=1, days=0)


@pytest.mark.asyncio
async def test_group_analytics_command_rechecks_admin_and_only_then_reads_data():
    class Message:
        def __init__(self):
            self.sent = []

        async def reply_text(self, text, **kwargs):
            self.sent.append(text)

    class Bot:
        async def get_chat_member(self, chat_id, user_id):
            class Member:
                status = "administrator"
            return Member()

    class Service:
        def __init__(self):
            self.calls = []

        async def render(self, chat_id, days):
            self.calls.append((chat_id, days))
            return "📊 Özet"

    class Engine:
        db = object()

    class Sudo:
        owner_id = 99

    class Chat:
        id = -1001
        type = "supergroup"

    class User:
        id = 5

    message = Message()
    service = Service()

    class Update:
        effective_chat = Chat()
        effective_user = User()
        effective_message = message

    class Context:
        bot = Bot()
        args = ["30"]

    handler = CommandHandler(
        chat_engine=Engine(), sudo_manager=Sudo(), image_router=None, tool_manager=None,
        group_analytics_service=service,
    )

    await handler.handle_group_analytics_command(Update(), Context())

    assert service.calls == [(-1001, 30)]
    assert message.sent == ["📊 Özet"]


@pytest.mark.asyncio
async def test_group_analytics_command_blocks_non_admin_before_service_call():
    class Message:
        def __init__(self):
            self.sent = []

        async def reply_text(self, text, **kwargs):
            self.sent.append(text)

    class Bot:
        async def get_chat_member(self, chat_id, user_id):
            class Member:
                status = "member"
            return Member()

    class Service:
        async def render(self, *args, **kwargs):
            raise AssertionError("analytics service must not be called")

    class Engine:
        db = object()

    class Sudo:
        owner_id = 99

    class Chat:
        id = -1001
        type = "supergroup"

    class User:
        id = 5

    message = Message()

    class Update:
        effective_chat = Chat()
        effective_user = User()
        effective_message = message

    class Context:
        bot = Bot()
        args = []

    handler = CommandHandler(
        chat_engine=Engine(), sudo_manager=Sudo(), image_router=None, tool_manager=None,
        group_analytics_service=Service(),
    )

    await handler.handle_group_analytics_command(Update(), Context())

    assert len(message.sent) == 1
    assert "yalnızca yöneticiler" in message.sent[0]
