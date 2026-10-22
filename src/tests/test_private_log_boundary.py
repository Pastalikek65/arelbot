from src.security.log_redaction import summarize_exception, summarize_private_text
from types import SimpleNamespace
import io
import logging

import pytest

from src.handlers.handler_utils import log_event
from src.handlers.message_handler import MessageHandler
from src.core.error_handler import ErrorHandler
from src.security.log_redaction import RedactionFilter
from unified_logger import UnifiedLogger


def test_private_event_summary_does_not_emit_user_payload_or_identity() -> None:
    payload = "Ali'nin özel notu: https://example.test/private?token=secret"

    summary = summarize_private_text(payload, label="user_input")

    assert payload not in summary
    assert "Ali" not in summary
    assert "secret" not in summary
    assert summary.startswith("user_input")
    assert "length=" in summary


def test_private_event_summary_is_stable_for_empty_payload() -> None:
    assert summarize_private_text("", label="assistant_output") == "assistant_output length=0"


def test_exception_summary_does_not_emit_private_exception_message() -> None:
    error = ValueError("secret user text https://example.test/private")

    summary = summarize_exception(error, label="provider_failure")

    assert summary == "provider_failure type=ValueError"
    assert "secret" not in summary
    assert "example.test" not in summary


def test_redaction_filter_does_not_emit_exception_traceback_payload() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactionFilter())
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("arelbot.test.private-exception")
    logger.handlers[:] = [handler]
    logger.setLevel(logging.ERROR)
    logger.propagate = False

    try:
        raise RuntimeError("private user payload https://example.test/secret")
    except RuntimeError:
        logger.exception("provider failed")

    output = stream.getvalue()
    assert "private user payload" not in output
    assert "example.test" not in output
    assert "provider failed" in output


def test_redaction_filter_replaces_exception_arguments_with_type_only() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="provider failed: %s",
        args=(RuntimeError("credential=https://user:secret@example.test/private"),),
        exc_info=None,
    )

    assert RedactionFilter().filter(record) is True
    assert record.args == ("exception type=RuntimeError",)
    assert "secret" not in str(record.args)


def test_redaction_filter_masks_nvidia_api_keys() -> None:
    leaked = "nvapi-abcdefghijklmnopqrstuvwx1234567890"
    record = logging.LogRecord(
        name="test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="provider key=%s",
        args=(leaked,),
        exc_info=None,
    )

    assert RedactionFilter().filter(record) is True
    assert leaked not in str(record.msg)
    assert leaked not in str(record.args)
    assert "<nvidia-api-key>" in str(record.args)


@pytest.mark.asyncio
async def test_retry_logging_never_interpolates_exception_message() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactionFilter())
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("src.core.error_handler")
    original_handlers = logger.handlers[:]
    original_level = logger.level
    original_propagate = logger.propagate
    logger.handlers[:] = [handler]
    logger.setLevel(logging.ERROR)
    logger.propagate = False

    @ErrorHandler.retry(max_retries=1)
    async def failing_operation():
        raise RuntimeError("private payload https://user:secret@example.test/path")

    try:
        with pytest.raises(RuntimeError):
            await failing_operation()
    finally:
        logger.handlers[:] = original_handlers
        logger.setLevel(original_level)
        logger.propagate = original_propagate

    output = stream.getvalue()
    assert "private payload" not in output
    assert "example.test" not in output
    assert "exception" not in output
    assert "retry type=RuntimeError" in output


@pytest.mark.asyncio
async def test_handler_log_event_does_not_send_private_payload(monkeypatch) -> None:
    payload = "gizli kullanıcı mesajı https://example.test/private"
    sent: list[str] = []

    class Bot:
        async def send_message(self, *, text, **kwargs):
            sent.append(text)

    from config import Config

    monkeypatch.setattr(Config, "LOG_CHANNEL_ID", -100123, raising=False)
    monkeypatch.setattr(Config, "DM_LOG_THREAD_ID", 1, raising=False)
    context = SimpleNamespace(bot=Bot())

    await log_event(context, message=payload, user_info="Ali Kullanıcı", chat_type="private")

    assert len(sent) == 1
    assert payload not in sent[0]
    assert "Ali Kullanıcı" not in sent[0]
    assert "length=" in sent[0]


@pytest.mark.asyncio
async def test_message_handler_log_event_does_not_send_ai_payload(monkeypatch) -> None:
    payload = "AI yanıtındaki kişisel içerik ve https://youtube.com/watch?v=secret"
    sent: list[str] = []

    class Bot:
        async def send_message(self, **kwargs):
            sent.append(kwargs["text"])

    from config import Config

    monkeypatch.setattr(Config, "LOG_CHANNEL_ID", -100123, raising=False)
    monkeypatch.setattr(Config, "DM_LOG_THREAD_ID", 1, raising=False)
    handler = MessageHandler.__new__(MessageHandler)
    context = SimpleNamespace(bot=Bot())

    await handler._log_event(
        context,
        message=payload,
        user_info="Sistem Yanıtı",
        chat_type="private",
    )

    assert len(sent) == 1
    assert payload not in sent[0]
    assert "secret" not in sent[0]
    assert "assistant_output" in sent[0]


@pytest.mark.asyncio
async def test_message_handler_log_event_does_not_upload_private_attachment(monkeypatch, tmp_path) -> None:
    attachment = tmp_path / "private-photo.jpg"
    attachment.write_bytes(b"private image bytes")
    sent: list[str] = []
    uploaded = False

    class Bot:
        async def send_message(self, **kwargs):
            sent.append(kwargs["text"])

        async def send_photo(self, **kwargs):
            nonlocal uploaded
            uploaded = True

    from config import Config

    monkeypatch.setattr(Config, "LOG_CHANNEL_ID", -100123, raising=False)
    monkeypatch.setattr(Config, "DM_LOG_THREAD_ID", 1, raising=False)
    handler = MessageHandler.__new__(MessageHandler)
    context = SimpleNamespace(bot=Bot())

    await handler._log_event(
        context,
        photo_path=str(attachment),
        user_info="user",
        chat_type="private",
    )

    assert uploaded is False
    assert len(sent) == 1
    assert str(attachment) not in sent[0]
    assert "private image bytes" not in sent[0]
    assert "attachment" in sent[0]


@pytest.mark.asyncio
async def test_unified_logger_uses_exception_summary(monkeypatch):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactionFilter())
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("unified_logger")
    original_handlers = logger.handlers[:]
    original_level = logger.level
    original_propagate = logger.propagate

    class Bot:
        async def send_message(self, **_kwargs):
            raise RuntimeError("private payload https://example.test/secret")

    instance = object.__new__(UnifiedLogger)
    instance.bot = Bot()
    instance.last_log_time = 0
    instance.log_cooldown = 0
    monkeypatch.setattr("unified_logger.Config.LOG_CHANNEL_ID", -100123, raising=False)
    logger.handlers[:] = [handler]
    logger.setLevel(logging.ERROR)
    logger.propagate = False
    try:
        await instance.log("safe event")
    finally:
        logger.handlers[:] = original_handlers
        logger.setLevel(original_level)
        logger.propagate = original_propagate

    output = stream.getvalue()
    assert "private payload" not in output
    assert "example.test" not in output
    assert "unified_logger type=RuntimeError" in output


@pytest.mark.asyncio
async def test_handler_log_event_is_suppressed_in_errors_only_mode(monkeypatch) -> None:
    sent: list[str] = []

    class Bot:
        async def send_message(self, *, text, **kwargs):
            sent.append(text)

    from config import Config

    monkeypatch.setattr(Config, "LOG_CHANNEL_ERRORS_ONLY", True, raising=False)
    await log_event(SimpleNamespace(bot=Bot()), message="normal event", chat_type="private")

    assert sent == []


@pytest.mark.asyncio
async def test_message_handler_log_event_is_suppressed_in_errors_only_mode(monkeypatch) -> None:
    sent: list[str] = []

    class Bot:
        async def send_message(self, **kwargs):
            sent.append(kwargs["text"])

    from config import Config

    monkeypatch.setattr(Config, "LOG_CHANNEL_ERRORS_ONLY", True, raising=False)
    handler = MessageHandler.__new__(MessageHandler)
    await handler._log_event(SimpleNamespace(bot=Bot()), message="normal event", chat_type="private")

    assert sent == []
