"""
Error Handler birim testleri.
"""
import pytest
from src.core.error_handler import (
    ErrorHandler,
    ArelError,
    AIApiError,
    RateLimitError,
    ToolExecutionError,
    DatabaseError,
    InputValidationError,
    ConfigurationError,
)


class TestErrorClasses:
    def test_arel_error_defaults(self):
        err = ArelError("test error")
        assert err.message == "test error"
        assert err.code == "UNKNOWN_ERROR"
        assert err.user_message == "İşlem tamamlanamadı. Biraz sonra tekrar dene."
        assert err.details == {}

    def test_arel_error_custom(self):
        err = ArelError("custom", code="CUSTOM", details={"key": "val"}, user_message="Kullanıcı mesajı")
        assert err.code == "CUSTOM"
        assert err.details == {"key": "val"}
        assert err.user_message == "Kullanıcı mesajı"

    def test_ai_api_error(self):
        err = AIApiError("API down", service="gemini", status_code=503)
        assert err.code == "AI_API_ERROR"
        assert err.service == "gemini"
        assert err.status_code == 503
        assert err.user_message == "Yanıt alınamadı. Biraz sonra tekrar dene."
        assert "gemini" not in err.user_message

    def test_rate_limit_error(self):
        err = RateLimitError("Too many", service="groq", retry_after=30)
        assert err.code == "RATE_LIMIT"
        assert err.retry_after == 30
        assert "30" in err.user_message

    def test_tool_execution_error(self):
        err = ToolExecutionError("Failed", tool_name="weather")
        assert err.code == "TOOL_EXECUTION_ERROR"
        assert err.tool_name == "weather"
        assert "weather" not in err.user_message
        assert "tekrar dene" in err.user_message

    def test_database_error(self):
        err = DatabaseError("Connection lost", db_name="mongo", operation="find")
        assert err.code == "DATABASE_ERROR"
        assert err.db_name == "mongo"
        assert err.operation == "find"

    def test_input_validation_error(self):
        err = InputValidationError("Invalid format", field="email")
        assert err.code == "INPUT_VALIDATION_ERROR"
        assert err.field == "email"
        assert "email" in err.user_message or "Invalid" in err.message

    def test_configuration_error(self):
        err = ConfigurationError("Missing keys", missing_keys=["TELEGRAM_TOKEN", "GROQ_KEYS"])
        assert err.code == "CONFIG_ERROR"
        assert "TELEGRAM_TOKEN" in err.missing_keys
        assert len(err.missing_keys) == 2

    def test_arel_error_to_dict(self):
        err = AIApiError("test", service="test")
        d = err.to_dict()
        assert d["code"] == "AI_API_ERROR"
        assert d["message"] == "test"
        assert "timestamp" in d

    def test_error_is_exception(self):
        assert issubclass(ArelError, Exception)
        assert issubclass(AIApiError, ArelError)
        assert issubclass(DatabaseError, ArelError)


class TestErrorHandler:
    def test_handle_arel_error(self):
        err = AIApiError("API error", service="test")
        msg = ErrorHandler.handle(err, context={"user": 1})
        assert isinstance(msg, str)
        assert len(msg) > 0
        assert ErrorHandler.get_stats()["total_errors"] == 1

    def test_handle_standard_exception(self):
        err = ValueError("something wrong")
        msg = ErrorHandler.handle(err)
        assert msg == "İşlem tamamlanamadı. Biraz sonra tekrar dene."
        stats = ErrorHandler.get_stats()
        assert stats["total_errors"] >= 1
        assert "ValueError" in stats["errors_by_type"]

    def test_stats_tracking(self):
        ErrorHandler.reset_stats()
        ErrorHandler.handle(AIApiError("e1", service="s1"))
        ErrorHandler.handle(RateLimitError("e2", service="s2"))
        ErrorHandler.handle(ValueError("e3"))

        stats = ErrorHandler.get_stats()
        assert stats["total_errors"] == 3
        assert stats["errors_by_type"]["AIApiError"] == 1
        assert stats["errors_by_type"]["RateLimitError"] == 1
        assert stats["errors_by_type"]["ValueError"] == 1
        assert stats["errors_by_code"]["AI_API_ERROR"] == 1
        assert stats["errors_by_code"]["RATE_LIMIT"] == 1

    def test_reset_stats(self):
        ErrorHandler.handle(ValueError("test"))
        assert ErrorHandler.get_stats()["total_errors"] > 0
        ErrorHandler.reset_stats()
        stats = ErrorHandler.get_stats()
        assert stats["total_errors"] == 0
        assert stats["errors_by_type"] == {}

    def test_handle_with_none_context(self):
        err = RuntimeError("critical")
        msg = ErrorHandler.handle(err, context=None)
        assert msg == "İşlem tamamlanamadı. Biraz sonra tekrar dene."


@pytest.mark.asyncio
class TestRetryDecorator:
    @pytest.mark.asyncio
    async def test_retry_success_on_second_try(self):
        ErrorHandler.reset_stats()
        call_count = 0

        @ErrorHandler.retry(max_retries=3, delay=0.01, exceptions=(ConnectionError,))
        async def test_fn():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("Service unavailable")
            return "success"

        result = await test_fn()
        assert result == "success"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_retry_raises_after_all_attempts(self):
        call_count = 0

        @ErrorHandler.retry(max_retries=2, delay=0.01, exceptions=(ConnectionError,))
        async def test_fn():
            nonlocal call_count
            call_count += 1
            raise ConnectionError("Always fails")

        with pytest.raises(ConnectionError):
            await test_fn()

        assert call_count == 2

    @pytest.mark.asyncio
    async def test_retry_unhandled_exception_raises_immediately(self):
        @ErrorHandler.retry(max_retries=3, delay=0.01, exceptions=(ConnectionError,))
        async def test_fn():
            raise ValueError("Wrong type")

        with pytest.raises(ValueError):
            await test_fn()
