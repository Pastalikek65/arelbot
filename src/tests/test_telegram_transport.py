from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.telegram_transport import TelegramTransport, transport_from_config


def _config(**overrides):
    values = {
        "TELEGRAM_MODE": "webhook",
        "WEBHOOK_URL": "https://bot.example.test",
        "WEBHOOK_LISTEN": "127.0.0.1",
        "WEBHOOK_PORT": 8443,
        "WEBHOOK_PATH": "telegram/webhook",
        "WEBHOOK_SECRET": "s" * 32,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_webhook_options_join_base_url_and_path_once():
    transport = transport_from_config(_config())
    options = transport.webhook_kwargs()

    assert options["url_path"] == "telegram/webhook"
    assert options["webhook_url"] == "https://bot.example.test/telegram/webhook"
    assert options["listen"] == "127.0.0.1"
    assert options["port"] == 8443
    assert options["secret_token"] == "s" * 32
    assert "message" in options["allowed_updates"]


def test_webhook_options_do_not_duplicate_path_when_operator_configures_full_url():
    transport = TelegramTransport.from_config(
        _config(WEBHOOK_URL="https://bot.example.test/telegram/webhook/")
    )

    assert transport.public_webhook_url == "https://bot.example.test/telegram/webhook"


def test_webhook_options_preserve_reverse_proxy_prefix():
    transport = TelegramTransport.from_config(
        _config(WEBHOOK_URL="https://bot.example.test/bot", WEBHOOK_PATH="telegram/webhook")
    )

    assert transport.public_webhook_url == "https://bot.example.test/bot/telegram/webhook"


def test_polling_options_are_explicit_and_do_not_need_webhook_values():
    transport = TelegramTransport.from_config(
        _config(TELEGRAM_MODE="polling", WEBHOOK_URL=None, WEBHOOK_SECRET=None)
    )

    assert "message" in transport.polling_kwargs()["allowed_updates"]
    with pytest.raises(ValueError, match="requires webhook mode"):
        transport.webhook_kwargs()


def test_webhook_url_is_rejected_in_polling_mode():
    transport = TelegramTransport.from_config(_config(TELEGRAM_MODE="polling"))

    with pytest.raises(ValueError, match="only available in webhook mode"):
        _ = transport.public_webhook_url


def test_invalid_mode_is_rejected_before_application_bootstrap():
    with pytest.raises(ValueError, match="TELEGRAM_MODE"):
        TelegramTransport.from_config(_config(TELEGRAM_MODE="long-polling"))


@pytest.mark.parametrize(
    "path",
    ["", "../telegram", "telegram//webhook", "telegram?bad", "telegram/webhook/../x"],
)
def test_transport_rejects_unsafe_webhook_paths(path):
    transport = TelegramTransport.from_config(_config(WEBHOOK_PATH=path))
    with pytest.raises(ValueError, match="WEBHOOK_PATH"):
        _ = transport.public_webhook_url


@pytest.mark.parametrize(
    "url",
    [
        "http://bot.example.test",
        "https://user:secret@bot.example.test",
        "https://bot.example.test?token=leak",
        "https://bot.example.test/../escape",
    ],
)
def test_transport_rejects_unsafe_webhook_urls(url):
    transport = TelegramTransport.from_config(_config(WEBHOOK_URL=url))
    with pytest.raises(ValueError, match="WEBHOOK_URL"):
        _ = transport.public_webhook_url


def test_transport_requires_secret_and_valid_port_before_webhook_start():
    with pytest.raises(ValueError, match="WEBHOOK_SECRET"):
        TelegramTransport.from_config(_config(WEBHOOK_SECRET="short")).webhook_kwargs()
    with pytest.raises(ValueError, match="WEBHOOK_PORT"):
        TelegramTransport.from_config(_config(WEBHOOK_PORT=70000)).webhook_kwargs()
