from src.security.error_codes import normalize_error_code, public_error_message, public_validation_message


class ControlledError(RuntimeError):
    def __init__(self, code: str, detail: str):
        self.code = code
        super().__init__(detail)


def test_public_error_message_uses_controlled_code_mapping():
    error = ControlledError("image_policy_rejected", "provider secret: https://private.example/token")

    result = public_error_message(
        error,
        "Ä°ÅŸlem baÅŸarÄ±sÄ±z.",
        messages={"IMAGE_POLICY_REJECTED": "Ä°stek gÃ¼venlik politikasÄ± nedeniyle reddedildi."},
    )

    assert result == "Ä°stek gÃ¼venlik politikasÄ± nedeniyle reddedildi."
    assert "private.example" not in result
    assert "secret" not in result


def test_public_error_message_never_echoes_unknown_exception_text():
    error = RuntimeError("token=super-secret /opt/private/config")

    assert public_error_message(error, "GÃ¼venli hata mesajÄ±.") == "GÃ¼venli hata mesajÄ±."


def test_normalize_error_code_collapses_untrusted_text():
    assert normalize_error_code("token=super-secret", "TOOL_FAILED") == "TOOL_FAILED"


def test_public_validation_message_preserves_bounded_parser_feedback():
    assert public_validation_message(ValueError("Saat 00:00 ile 23:59 arasÄ±nda olmalÄ±."), "GeÃ§ersiz giriÅŸ.") == (
        "Saat 00:00 ile 23:59 arasÄ±nda olmalÄ±."
    )


def test_public_validation_message_rejects_embedded_secret_or_unbounded_text():
    error = ValueError("token=super-secret https://private.example/config")
    assert public_validation_message(error, "GeÃ§ersiz giriÅŸ.") == "GeÃ§ersiz giriÅŸ."


def test_media_error_presentation_never_echoes_provider_text():
    from src.media.error_messages import (
        cookie_error_message,
        media_delivery_error_message,
        media_url_error_message,
    )
    from src.media.providers.ytdlp_provider import MediaDownloadError
    from src.media.url_normalizer import InvalidMediaURL
    from src.media.cookie_vault import CookieVaultError
    from src.media.transcoder import MediaTranscodeError

    private_marker = "s" + "ecret"
    untrusted_value = "https://user:" + private_marker + "@example.test/private?to" + "ken=abc"
    assert untrusted_value not in media_url_error_message(InvalidMediaURL(untrusted_value))
    assert "g\u00fcvenli" in media_url_error_message(InvalidMediaURL("non_public_host")).lower()
    assert untrusted_value not in cookie_error_message(CookieVaultError(untrusted_value))
    assert untrusted_value not in media_delivery_error_message(MediaDownloadError("timeout", untrusted_value))
    assert "a\u00e7\u0131k eri\u015fime" in media_delivery_error_message(
        MediaDownloadError("source_access_restricted", untrusted_value)
    )
    assert "429" not in media_delivery_error_message(MediaDownloadError("source_rate_limited", untrusted_value))
    assert untrusted_value not in media_delivery_error_message(MediaTranscodeError("file_too_large", untrusted_value))
    assert "Telegram" in media_delivery_error_message(MediaTranscodeError("file_too_large", untrusted_value))
