from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.handlers.media_handler import MediaHandler
from src.core.error_handler import ErrorHandler
from src.jobs.retry_policy import RetryPolicy
from src.media.service import MediaDownloadService
from src.observability import MetricsRegistry
from src.orchestrator import Orchestrator


class _Status:
    message_id = 91

    def __init__(self):
        self.deleted = False
        self.edits = []

    async def delete(self):
        self.deleted = True

    async def edit_text(self, text, **kwargs):
        self.edits.append(text)


@pytest.mark.asyncio
async def test_passive_animation_is_ignored_without_group_error():
    replies = []

    class Message:
        animation = SimpleNamespace(file_id="gif-1")
        caption = None

        async def reply_text(self, text, **kwargs):
            replies.append(text)

    orchestrator = object.__new__(Orchestrator)
    orchestrator.media_handler = SimpleNamespace()
    orchestrator._queue_media_download = lambda *_args, **_kwargs: pytest.fail("passive GIF was queued")
    update = SimpleNamespace(effective_message=Message())

    await orchestrator.handle_animation(update, SimpleNamespace())

    assert replies == []


@pytest.mark.asyncio
async def test_gif_document_is_ignored_without_explicit_processing():
    replies = []

    class Message:
        document = SimpleNamespace(file_name="reaction.gif", mime_type="image/gif")
        text = None
        caption = None
        reply_to_message = None

        async def reply_text(self, text, **kwargs):
            replies.append(text)

    handler = MediaHandler(
        chat_engine=None,
        sudo_manager=SimpleNamespace(is_blacklisted=lambda *_: False),
        tool_manager=None,
        document_service=SimpleNamespace(submit=pytest.fail),
    )
    update = SimpleNamespace(
        message=Message(),
        effective_message=Message(),
        effective_chat=SimpleNamespace(id=10, type="group"),
        effective_user=SimpleNamespace(id=42),
    )
    context = SimpleNamespace(bot_info=SimpleNamespace(username="arelbot", id=99))

    await handler.handle_document(update, context)

    assert replies == []


@pytest.mark.asyncio
async def test_unsupported_document_is_ignored_without_error_message():
    """Arbitrary Telegram files must stay passive unless they are ingestible documents."""
    replies = []

    class Message:
        document = SimpleNamespace(file_name="archive.zip", mime_type="application/zip")
        text = None
        caption = None
        reply_to_message = None

        async def reply_text(self, text, **kwargs):
            replies.append(text)

    class DocumentService:
        async def submit(self, *args, **kwargs):
            pytest.fail("unsupported Telegram file was sent to the document queue")

    handler = MediaHandler(
        chat_engine=None,
        sudo_manager=SimpleNamespace(is_blacklisted=lambda *_: False),
        tool_manager=None,
        document_service=DocumentService(),
    )
    update = SimpleNamespace(
        message=Message(),
        effective_message=Message(),
        effective_chat=SimpleNamespace(id=10, type="private"),
        effective_user=SimpleNamespace(id=42),
    )
    context = SimpleNamespace(bot_info=SimpleNamespace(username="arelbot", id=99))

    await handler.handle_document(update, context)

    assert replies == []


@pytest.mark.asyncio
async def test_passive_group_document_does_not_enter_response_router():
    """Forwarded arbitrary files must stay silent even if routing is unavailable."""
    replies = []

    class Message:
        document = SimpleNamespace(file_name="forwarded.zip", mime_type="application/zip")
        text = None
        caption = None
        reply_to_message = None

        async def reply_text(self, text, **kwargs):
            replies.append(text)

    handler = MediaHandler(
        chat_engine=None,
        sudo_manager=SimpleNamespace(is_blacklisted=lambda *_: False),
        tool_manager=None,
        document_service=SimpleNamespace(submit=pytest.fail),
    )

    async def should_not_be_called(*_args, **_kwargs):
        pytest.fail("passive documents must not enter group response routing")

    handler._should_respond = should_not_be_called
    update = SimpleNamespace(
        message=Message(),
        effective_message=Message(),
        effective_chat=SimpleNamespace(id=-100, type="supergroup"),
        effective_user=SimpleNamespace(id=42),
    )

    await handler.handle_document(update, SimpleNamespace())

    assert replies == []


@pytest.mark.asyncio
async def test_group_enqueue_failure_deletes_status_without_error_message():
    status = _Status()
    replies = []

    class Message:
        async def reply_text(self, text, **kwargs):
            replies.append(text)
            return status

    class Media:
        async def submit(self, *args, **kwargs):
            raise ValueError("rate_limited")

    orchestrator = object.__new__(Orchestrator)
    orchestrator.sudo_manager = SimpleNamespace(is_blacklisted=lambda *_: False)
    orchestrator.media_service = Media()
    update = SimpleNamespace(
        effective_message=Message(),
        effective_chat=SimpleNamespace(id=-100, type="supergroup"),
        effective_user=SimpleNamespace(id=42),
    )

    await orchestrator._queue_media_download(update, "https://www.youtube.com/shorts/fixture")

    assert replies == ["🔎 Bağlantı analiz ediliyor…"]
    assert status.deleted is True


@pytest.mark.asyncio
async def test_group_worker_failure_is_neutral_during_retry_and_cleared_after_final_attempt():
    class Bot:
        def __init__(self):
            self.edits = []
            self.deleted = []

        async def edit_message_text(self, **kwargs):
            self.edits.append(kwargs["text"])

        async def delete_message(self, **kwargs):
            self.deleted.append(kwargs)

    service = object.__new__(MediaDownloadService)
    service.queue = SimpleNamespace(retry_policy=RetryPolicy(max_attempts=3))
    service.bot = Bot()
    service.metrics_registry = MetricsRegistry()

    payload = {
        "job_id": "group-failure",
        "chat_id": -100,
        "status_message_id": 91,
        "chat_type": "supergroup",
        "attempt": 0,
    }
    await service._report_failure(payload, "source_rate_limited", RuntimeError("secret upstream detail"))
    assert service.bot.edits == ["Medya kaynağı yanıt vermedi; başka bir yöntem deneniyor…"]
    assert service.bot.deleted == []

    payload["attempt"] = 2
    await service._report_failure(payload, "source_rate_limited", RuntimeError("secret upstream detail"))
    assert len(service.bot.deleted) == 1
    assert "secret" not in " ".join(service.bot.edits)


def test_media_retry_policy_includes_bounded_source_recovery_codes():
    policy = RetryPolicy(max_attempts=3)
    assert policy.should_retry("source_rate_limited", 0)
    assert policy.should_retry("source_access_restricted", 1)
    assert not policy.should_retry("source_rate_limited", 2)


@pytest.mark.asyncio
async def test_global_error_handler_does_not_echo_animation_failure_to_group():
    ErrorHandler.reset_stats()
    replies = []

    class Message:
        animation = SimpleNamespace(file_id="gif-1")

        async def reply_text(self, text, **kwargs):
            replies.append(text)

    update = SimpleNamespace(
        update_id=7,
        effective_message=Message(),
        effective_chat=SimpleNamespace(id=-100, type="supergroup"),
        effective_user=SimpleNamespace(id=42),
    )
    context = SimpleNamespace(error=RuntimeError("gif worker failed"), bot=None)

    await ErrorHandler.handle_telegram_error(update, context)

    assert replies == []
    ErrorHandler.reset_stats()


@pytest.mark.asyncio
async def test_global_error_handler_does_not_echo_passive_document_failure_to_group():
    ErrorHandler.reset_stats()
    replies = []

    class Message:
        document = SimpleNamespace(file_name="forwarded.zip", mime_type="application/zip")

        async def reply_text(self, text, **kwargs):
            replies.append(text)

    update = SimpleNamespace(
        update_id=8,
        effective_message=Message(),
        effective_chat=SimpleNamespace(id=-100, type="supergroup"),
        effective_user=SimpleNamespace(id=42),
    )
    context = SimpleNamespace(error=RuntimeError("passive document failure"), bot=None)

    await ErrorHandler.handle_telegram_error(update, context)

    assert replies == []
    ErrorHandler.reset_stats()

