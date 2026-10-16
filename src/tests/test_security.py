"""
InputValidator, RBAC iÃ§in testler.
"""
import io
import logging

import pytest
from src.security.input_validator import InputValidator
from src.security.rbac import RBACManager, Role, Permission
from src.security.log_redaction import redact


class TestInputValidator:
    def test_sanitize_text_clean(self):
        is_safe, text = InputValidator.sanitize_text("Merhaba dÃ¼nya!")
        assert is_safe is True
        assert text == "Merhaba dÃ¼nya!"

    def test_sanitize_text_html(self):
        is_safe, text = InputValidator.sanitize_text("Hello <b>world</b>")
        assert is_safe is False
        assert "Hello" in text
        assert "<b>" not in text

    def test_sanitize_text_script(self):
        is_safe, text = InputValidator.sanitize_text("<script>alert('xss')</script>")
        assert is_safe is False
        assert "script" not in text
        assert "alert" in text

    def test_sanitize_text_truncate(self):
        long_text = "A" * 6000
        is_safe, text = InputValidator.sanitize_text(long_text)
        assert len(text) == 5000

    def test_sanitize_text_control_chars(self):
        is_safe, text = InputValidator.sanitize_text("test\x00\x01")
        assert is_safe is False
        assert "\x00" not in text

    def test_sanitize_text_none(self):
        is_safe, text = InputValidator.sanitize_text(None)
        assert is_safe is False
        assert text == ""

    def test_sanitize_text_empty(self):
        is_safe, text = InputValidator.sanitize_text("")
        assert text == ""

    def test_sanitize_text_non_string(self):
        is_safe, text = InputValidator.sanitize_text(12345)
        assert is_safe is False
        assert text == ""

    def test_sanitize_filename_clean(self):
        result = InputValidator.sanitize_filename("test.jpg")
        assert result == "test.jpg"

    def test_sanitize_filename_path_traversal(self):
        result = InputValidator.sanitize_filename("../../etc/passwd")
        assert "../" not in result

    def test_sanitize_filename_dangerous_chars(self):
        result = InputValidator.sanitize_filename("file:name<>.txt")
        assert ":" not in result
        assert "<" not in result
        assert "_" in result

    def test_sanitize_filename_empty(self):
        result = InputValidator.sanitize_filename("")
        assert result == "unnamed"

    def test_validate_length_valid(self):
        ok, text = InputValidator.validate_length("hello", min_len=1, max_len=100)
        assert ok is True
        assert text == "hello"

    def test_validate_length_too_short(self):
        ok, msg = InputValidator.validate_length("", min_len=2)
        assert ok is False

    def test_validate_length_too_long(self):
        ok, msg = InputValidator.validate_length("A" * 100, max_len=10)
        assert ok is False

    def test_validate_command_args(self):
        ok, args = InputValidator.validate_command_args(["arg1", "arg2"])
        assert ok is True
        assert args == ["arg1", "arg2"]

    def test_validate_command_args_too_many(self):
        ok, args = InputValidator.validate_command_args(["a"] * 20, allowed_count=5)
        assert ok is False
        assert len(args) == 5

    def test_detect_suspicious_patterns_clean(self):
        result = InputValidator.detect_suspicious_patterns("normal text")
        assert result is False

    def test_detect_suspicious_patterns_script(self):
        result = InputValidator.detect_suspicious_patterns("<script>alert(1)</script>")
        assert result is True

    def test_detect_suspicious_none(self):
        result = InputValidator.detect_suspicious_patterns(None)
        assert result is False

    def test_validate_user_input_normal(self):
        result = InputValidator.validate_user_input("Hello World!")
        assert result == "Hello World!"

    def test_validate_user_input_html(self):
        result = InputValidator.validate_user_input("<p>test</p>")
        assert "<p>" not in result
        assert "</p>" not in result

    def test_validate_user_input_none(self):
        result = InputValidator.validate_user_input(None)
        assert result == ""


class TestRBAC:
    def test_owner_has_max_role(self):
        rbac = RBACManager(owner_id=100)
        assert rbac.get_role(100) == Role.OWNER
        assert rbac.has_role(100, Role.OWNER)
        assert rbac.has_role(100, Role.ADMIN)
        assert rbac.has_role(100, Role.MODERATOR)

    def test_unknown_user_is_user_role(self):
        rbac = RBACManager(owner_id=100)
        assert rbac.get_role(999) == Role.USER
        assert rbac.has_role(999, Role.USER)
        assert not rbac.has_role(999, Role.MODERATOR)

    def test_admin_can_assign_moderator(self):
        rbac = RBACManager(owner_id=100)
        rbac.set_role(200, Role.ADMIN, requester_id=100)
        success = rbac.set_role(300, Role.MODERATOR, requester_id=200)
        assert success is True
        assert rbac.get_role(300) == Role.MODERATOR

    def test_admin_cannot_assign_admin(self):
        rbac = RBACManager(owner_id=100)
        rbac.set_role(200, Role.ADMIN, requester_id=100)
        success = rbac.set_role(300, Role.ADMIN, requester_id=200)
        assert success is False

    def test_cannot_change_owner(self):
        rbac = RBACManager(owner_id=100)
        success = rbac.set_role(100, Role.MODERATOR, requester_id=200)
        assert success is False

    def test_user_cannot_assign_roles(self):
        rbac = RBACManager(owner_id=100)
        success = rbac.set_role(300, Role.MODERATOR, requester_id=400)
        assert success is False

    def test_remove_role(self):
        rbac = RBACManager(owner_id=100)
        rbac.set_role(200, Role.ADMIN, requester_id=100)
        assert rbac.get_role(200) == Role.ADMIN
        success = rbac.remove_role(200, requester_id=100)
        assert success is True
        assert rbac.get_role(200) == Role.USER

    def test_has_permission(self):
        rbac = RBACManager(owner_id=100)
        assert rbac.has_permission(100, Permission.MANAGE_BOT_CONFIG) is True
        assert rbac.has_permission(100, Permission.BAN_UNBAN) is True

    def test_user_has_no_permissions(self):
        rbac = RBACManager(owner_id=100)
        assert rbac.has_permission(999, Permission.BAN_UNBAN) is False

    def test_list_roles(self):
        rbac = RBACManager(owner_id=100)
        rbac.set_role(200, Role.ADMIN, requester_id=100)
        rbac.set_role(300, Role.MODERATOR, requester_id=100)

        roles = rbac.list_roles()
        assert roles[100] == "OWNER"
        assert roles[200] == "ADMIN"
        assert roles[300] == "MODERATOR"

    def test_get_all_with_min_role(self):
        rbac = RBACManager(owner_id=100)
        rbac.set_role(200, Role.ADMIN, requester_id=100)
        rbac.set_role(300, Role.MODERATOR, requester_id=100)
        rbac.set_role(400, Role.MODERATOR, requester_id=200)

        admins_plus = rbac.get_all_with_min_role(Role.ADMIN)
        assert 100 in admins_plus
        assert 200 in admins_plus
        assert 300 not in admins_plus
        assert 400 not in admins_plus

    def test_require_decorator_role_check(self):
        rbac = RBACManager(owner_id=100)

        @rbac.require(Role.ADMIN)
        async def admin_test(self_or_handler, update, context=None):
            return "admin_ok"

        assert rbac.has_role(100, Role.ADMIN) is True


def test_log_redaction_removes_credentials_and_media_query_urls():
    value = redact("Bearer super-secret https://youtube.com/watch?v=private-token")
    assert "super-secret" not in value
    assert "private-token" not in value
    assert "<media-url>" in value


def test_log_redaction_preserves_numeric_format_arguments():
    """Redaction must not turn a ``%d`` status code into a string."""

    from src.security.log_redaction import RedactionFilter

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactionFilter())
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("arelbot.test.redaction")
    logger.handlers[:] = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False

    logger.info(
        "HTTP Request: %s %s %s %d %s",
        "POST",
        "https://api.telegram.org/bot123456:abcdefghijklmnopqrstuvwxyz/getMe",
        "HTTP/1.1",
        200,
        "OK",
    )

    output = stream.getvalue()
    assert " 200 OK" in output
    assert "bot123456" not in output
    assert "<telegram-token>" in output
