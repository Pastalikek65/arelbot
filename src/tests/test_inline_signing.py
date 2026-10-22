import pytest

from src.services.inline_signing import signing_key
from src.services.uno_inline_service import UnoInlineService


def test_inline_boot_without_optional_cookie_key():
    key = signing_key("123456:valid-bot-token-independent-of-cookies")
    assert len(key) == 32
    assert key == signing_key("123456:valid-bot-token-independent-of-cookies")
    assert key != signing_key("654321:another-bot-token")
    UnoInlineService(object(), secret=key, file_ids={})


def test_empty_token_is_rejected():
    with pytest.raises(ValueError, match="inline_bot_token_required"):
        signing_key("")
