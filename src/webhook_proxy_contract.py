"""Pure reverse-proxy contract for Telegram webhooks.

The Telegram application and the HTTPS reverse proxy must agree on two
different paths: the public path Telegram calls and the internal path the
Python webhook server receives.  Keeping that relationship in a small pure
module lets deployment preflight validate an Nginx file without starting the
bot or exposing any secret values.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit


_PATH_RE = re.compile(r"^[A-Za-z0-9_/-]{1,128}$")
_LOCATION_RE = re.compile(
    r"(?m)^\s*location\s*=\s*(?P<path>/[^\s{;]+)\s*\{(?P<body>.*?)^\s*\}",
    re.DOTALL,
)
_PROXY_PASS_RE = re.compile(r"(?m)^\s*proxy_pass\s+(?P<target>[^;\s]+)\s*;")
_UPSTREAM_RE = re.compile(
    r"(?ms)^\s*upstream\s+arelbot_bot\s*\{(?P<body>.*?)^\s*\}"
)
_LOOPBACK_SERVER_RE = re.compile(r"(?m)^\s*server\s+127\.0\.0\.1:\d+\s*;")
_DEFAULT_DENY_RE = re.compile(
    r"(?ms)^\s*location\s+/\s*\{.*?\breturn\s+404\s*;.*?^\s*\}"
)
_SECRET_HEADER_RE = re.compile(
    r"(?m)^\s*proxy_set_header\s+X-Telegram-Bot-Api-Secret-Token\s+"
    r"\$http_x_telegram_bot_api_secret_token\s*;\s*$"
)


def _normalize_path(raw: str, *, error_code: str) -> str:
    value = str(raw).strip().strip("/")
    if not value or not _PATH_RE.fullmatch(value):
        raise ValueError(error_code)
    segments = value.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise ValueError(error_code)
    return "/" + "/".join(segments)


@dataclass(frozen=True, slots=True)
class WebhookProxyContract:
    """The public and upstream paths for one webhook deployment."""

    public_path: str
    upstream_path: str

    @classmethod
    def from_values(cls, webhook_url: str, webhook_path: str) -> "WebhookProxyContract":
        configured = urlsplit(str(webhook_url).strip())
        if (
            configured.scheme != "https"
            or not configured.hostname
            or configured.username
            or configured.password
            or configured.query
            or configured.fragment
        ):
            raise ValueError("webhook_url_invalid")

        upstream_path = _normalize_path(webhook_path, error_code="webhook_path_invalid")
        prefix = configured.path.strip("/")
        if prefix:
            if not _PATH_RE.fullmatch(prefix) or any(
                segment in {"", ".", ".."} for segment in prefix.split("/")
            ):
                raise ValueError("webhook_url_path_invalid")
            prefix_path = "/" + "/".join(prefix.split("/"))
        else:
            prefix_path = ""

        if prefix_path == upstream_path or prefix_path.endswith(upstream_path):
            public_path = prefix_path
        elif prefix_path:
            public_path = prefix_path + upstream_path
        else:
            public_path = upstream_path
        return cls(public_path=public_path, upstream_path=upstream_path)


def validate_nginx_webhook_config(
    nginx_text: str,
    contract: WebhookProxyContract,
) -> list[str]:
    """Return stable error codes when an Nginx file drifts from the contract."""

    errors: list[str] = []
    if not re.search(r"(?m)^\s*listen\s+443\s+ssl(?:\s|;)", nginx_text):
        errors.append("webhook_tls_listener_missing")
    upstream = _UPSTREAM_RE.search(nginx_text)
    if upstream is None or _LOOPBACK_SERVER_RE.search(upstream.group("body")) is None:
        errors.append("webhook_upstream_definition_invalid")

    locations = list(_LOCATION_RE.finditer(nginx_text))
    matching = next((item for item in locations if item.group("path") == contract.public_path), None)
    if matching is None:
        errors.append("webhook_location_mismatch")
        return errors

    body = matching.group("body")
    proxy_pass = _PROXY_PASS_RE.search(body)
    if proxy_pass is None:
        errors.append("webhook_upstream_path_mismatch")
    else:
        target = urlsplit(proxy_pass.group("target"))
        try:
            target_port = target.port
        except ValueError:
            target_port = -1
        if (
            target.scheme != "http"
            or target.hostname != "arelbot_bot"
            or target_port is not None
            or target.username
            or target.password
            or target.query
            or target.fragment
        ):
            errors.append("webhook_upstream_target_invalid")
        elif target.path != contract.upstream_path:
            errors.append("webhook_upstream_path_mismatch")
    if _SECRET_HEADER_RE.search(body) is None:
        errors.append("webhook_secret_header_missing")
    if _DEFAULT_DENY_RE.search(nginx_text) is None:
        errors.append("webhook_default_deny_missing")
    return errors
