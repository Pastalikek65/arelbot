from __future__ import annotations

from pathlib import Path

import pytest

from src.webhook_proxy_contract import (
    WebhookProxyContract,
    validate_nginx_webhook_config,
)


def _nginx(path: str, upstream_path: str, *, secret_header: bool = True) -> str:
    secret_line = (
        "        proxy_set_header X-Telegram-Bot-Api-Secret-Token $http_x_telegram_bot_api_secret_token;\n"
        if secret_header
        else ""
    )
    return f"""
upstream arelbot_bot {{
    server 127.0.0.1:8443;
}}
server {{
    listen 443 ssl http2;
    location = {path} {{
        proxy_pass http://arelbot_bot{upstream_path};
{secret_line}    }}
    location / {{ return 404; }}
}}
"""


def test_contract_rewrites_public_prefix_to_internal_webhook_path():
    contract = WebhookProxyContract.from_values(
        "https://bot.example.test/bot",
        "telegram/webhook",
    )

    assert contract.public_path == "/bot/telegram/webhook"
    assert contract.upstream_path == "/telegram/webhook"


def test_contract_accepts_exact_location_and_secret_forwarding():
    contract = WebhookProxyContract.from_values(
        "https://bot.example.test/bot",
        "telegram/webhook",
    )

    result = validate_nginx_webhook_config(
        _nginx(contract.public_path, contract.upstream_path),
        contract,
    )

    assert result == []


@pytest.mark.parametrize(
    ("nginx", "code"),
    [
        (_nginx("/telegram/webhook", "/telegram/webhook"), "webhook_location_mismatch"),
        (_nginx("/bot/telegram/webhook", "/bot/telegram/webhook"), "webhook_upstream_path_mismatch"),
        (_nginx("/bot/telegram/webhook", "/telegram/webhook", secret_header=False), "webhook_secret_header_missing"),
    ],
)
def test_contract_rejects_proxy_drift(nginx: str, code: str):
    contract = WebhookProxyContract.from_values(
        "https://bot.example.test/bot",
        "telegram/webhook",
    )

    assert code in validate_nginx_webhook_config(nginx, contract)


def test_contract_rejects_non_loopback_upstream_and_missing_default_deny():
    contract = WebhookProxyContract.from_values(
        "https://bot.example.test",
        "telegram/webhook",
    )
    nginx = _nginx(contract.public_path, contract.upstream_path).replace(
        "http://arelbot_bot", "https://external.example.test"
    )
    assert "webhook_upstream_target_invalid" in validate_nginx_webhook_config(nginx, contract)

    nginx = _nginx(contract.public_path, contract.upstream_path).replace(
        "    location / { return 404; }\n", ""
    )
    assert "webhook_default_deny_missing" in validate_nginx_webhook_config(nginx, contract)


def test_contract_rejects_upstream_definition_that_is_not_loopback():
    contract = WebhookProxyContract.from_values(
        "https://bot.example.test",
        "telegram/webhook",
    )
    nginx = _nginx(contract.public_path, contract.upstream_path).replace(
        "server 127.0.0.1:8443;", "server public-bot.example.test:8443;"
    )
    assert "webhook_upstream_definition_invalid" in validate_nginx_webhook_config(nginx, contract)


def test_contract_rejects_proxy_target_port_override():
    contract = WebhookProxyContract.from_values(
        "https://bot.example.test",
        "telegram/webhook",
    )
    nginx = _nginx(contract.public_path, contract.upstream_path).replace(
        "http://arelbot_bot/", "http://arelbot_bot:9999/"
    )
    assert "webhook_upstream_target_invalid" in validate_nginx_webhook_config(nginx, contract)


def test_contract_rejects_unsafe_webhook_path():
    with pytest.raises(ValueError, match="webhook_path_invalid"):
        WebhookProxyContract.from_values("https://bot.example.test", "../telegram/webhook")


def test_checked_in_nginx_example_matches_default_transport_contract():
    contract = WebhookProxyContract.from_values(
        "https://bot.example.test",
        "telegram/webhook",
    )
    errors = validate_nginx_webhook_config(
        Path("deploy/nginx/arelbot.conf.example").read_text(encoding="utf-8"),
        contract,
    )

    assert errors == []
