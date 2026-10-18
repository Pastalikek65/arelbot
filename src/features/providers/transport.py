"""Bounded, redirect-free transport shared by typed public providers.

Provider parsers should only receive a bounded payload.  Keeping this policy in
one place prevents a new JSON or RSS adapter from accidentally accepting an
unbounded response, following a redirect to an unexpected host, or returning
an upstream HTML error page as if it were structured data.
"""

from __future__ import annotations

import ipaddress
import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

import httpx


DEFAULT_MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class ProviderTransportError(RuntimeError):
    """Stable, non-sensitive transport failure exposed to provider parsers."""

    def __init__(self, code: str, *, status_code: int | None = None) -> None:
        self.code = str(code)
        self.status_code = int(status_code) if status_code is not None else None
        super().__init__(self.code)

    @property
    def retryable(self) -> bool:
        """Whether repeating the same request can plausibly help."""

        if self.code != "provider_upstream_http":
            return False
        return self.status_code is None or self.status_code in {
            408, 425, 429, 500, 502, 503, 504, 507, 508, 509, 510, 511,
        }


@dataclass(frozen=True)
class ProviderPayload:
    body: bytes
    content_type: str
    encoding: str = "utf-8"
    status_code: int = 200

    @property
    def text(self) -> str:
        return self.body.decode(self.encoding or "utf-8", errors="replace")


def _host_is_private(host: str) -> bool:
    normalized = str(host or "").casefold().rstrip(".")
    if normalized in {"localhost", "localhost.localdomain"}:
        return True
    if normalized.endswith((".local", ".internal", ".localhost")):
        return True
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    return bool(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def validate_provider_url(url: str, *, allowed_hosts: Iterable[str] | None = None) -> str:
    """Validate an upstream URL before any request is made.

    Providers build URLs from fixed endpoints and normalized parameters, but
    this check remains mandatory so configuration and future adapters cannot
    silently introduce credentialed, private, or redirectable destinations.
    """

    candidate = str(url or "").strip()
    parsed = urlsplit(candidate)
    host = (parsed.hostname or "").casefold().rstrip(".")
    if parsed.scheme.casefold() != "https" or not host:
        raise ProviderTransportError("provider_url_https_required")
    if parsed.username or parsed.password:
        raise ProviderTransportError("provider_url_credentials_forbidden")
    if _host_is_private(host):
        raise ProviderTransportError("provider_url_private_host")
    configured = {
        str(value).casefold().rstrip(".")
        for value in (allowed_hosts or ())
        if str(value).strip()
    }
    if configured and host not in configured:
        raise ProviderTransportError("provider_host_not_allowed")
    return candidate


class ProviderHttpTransport:
    """Read bounded HTTP payloads through an injected async client.

    The production client is configured with ``follow_redirects=False`` by
    each provider.  This class also rejects a redirect response explicitly,
    so a custom test/deployment client cannot weaken that invariant.
    """

    def __init__(
        self,
        client: Any,
        *,
        allowed_hosts: Iterable[str] | None = None,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        self.client = client
        self.allowed_hosts = tuple(allowed_hosts or ())
        self.max_response_bytes = max(1, int(max_response_bytes))

    async def get_bytes(
        self,
        url: str,
        *,
        timeout: float,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        expected_content_types: Iterable[str] | None = None,
        max_response_bytes: int | None = None,
    ) -> ProviderPayload:
        candidate = validate_provider_url(url, allowed_hosts=self.allowed_hosts)
        limit = max(1, int(max_response_bytes or self.max_response_bytes))
        expected = {
            str(value).split(";", 1)[0].strip().casefold()
            for value in (expected_content_types or ())
            if str(value).strip()
        }
        request_kwargs: dict[str, Any] = {"timeout": timeout}
        request_kwargs["follow_redirects"] = False
        if params:
            request_kwargs["params"] = dict(params)
        if headers:
            request_kwargs["headers"] = dict(headers)

        stream_factory = getattr(self.client, "stream", None)
        if callable(stream_factory):
            async with stream_factory("GET", candidate, **request_kwargs) as response:
                payload = await self._read_stream(response, limit, expected)
        else:
            response = await self.client.get(candidate, **request_kwargs)
            payload = self._read_response(response, limit, expected)
        return payload

    async def get_json(
        self,
        url: str,
        *,
        timeout: float,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        error_code: str = "provider_invalid_json",
        expected_content_types: Iterable[str] | None = None,
        max_response_bytes: int | None = None,
    ) -> Any:
        content_types = tuple(
            expected_content_types
            or ("application/json", "application/problem+json", "text/json")
        )
        payload = await self.get_bytes(
            url,
            timeout=timeout,
            params=params,
            headers=headers,
            expected_content_types=content_types,
            max_response_bytes=max_response_bytes,
        )
        try:
            return json.loads(payload.body.decode(payload.encoding or "utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ProviderTransportError(error_code) from exc

    async def post_json(
        self,
        url: str,
        *,
        json_body: Mapping[str, Any],
        timeout: float,
        headers: Mapping[str, str] | None = None,
        error_code: str = "provider_invalid_json",
        max_response_bytes: int | None = None,
    ) -> Any:
        candidate = validate_provider_url(url, allowed_hosts=self.allowed_hosts)
        request_kwargs: dict[str, Any] = {
            "json": dict(json_body),
            "timeout": timeout,
            "follow_redirects": False,
        }
        if headers:
            request_kwargs["headers"] = dict(headers)
        limit = max(1, int(max_response_bytes or self.max_response_bytes))
        expected = {"application/json", "application/problem+json", "text/json"}
        stream_factory = getattr(self.client, "stream", None)
        if callable(stream_factory):
            async with stream_factory("POST", candidate, **request_kwargs) as response:
                payload = await self._read_stream(response, limit, expected)
        else:
            # Minimal replay clients may implement only post(); production
            # httpx clients always take the bounded streaming branch above.
            response = await self.client.post(candidate, **request_kwargs)
            payload = self._read_response(response, limit, expected)
        try:
            return json.loads(payload.body.decode(payload.encoding or "utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ProviderTransportError(error_code) from exc

    async def get_text(
        self,
        url: str,
        *,
        timeout: float,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        expected_content_types: Iterable[str] | None = None,
        max_response_bytes: int | None = None,
    ) -> str:
        payload = await self.get_bytes(
            url,
            timeout=timeout,
            params=params,
            headers=headers,
            expected_content_types=expected_content_types,
            max_response_bytes=max_response_bytes,
        )
        return payload.text

    @staticmethod
    def _check_response(response: Any, expected: set[str]) -> tuple[str, str]:
        status = int(getattr(response, "status_code", 500))
        if 300 <= status < 400:
            raise ProviderTransportError("provider_redirect_rejected", status_code=status)
        raise_for_status = getattr(response, "raise_for_status", None)
        if callable(raise_for_status):
            try:
                raise_for_status()
            except Exception as exc:
                raise ProviderTransportError("provider_upstream_http", status_code=status) from exc
        elif status >= 400:
            raise ProviderTransportError("provider_upstream_http", status_code=status)
        headers = getattr(response, "headers", {}) or {}
        content_type = str(headers.get("content-type", "")).split(";", 1)[0].strip().casefold()
        if expected and content_type and content_type not in expected:
            raise ProviderTransportError("provider_content_type_invalid")
        encoding = str(getattr(response, "encoding", None) or "utf-8")
        return content_type, encoding

    @classmethod
    def _read_response(cls, response: Any, limit: int, expected: set[str]) -> ProviderPayload:
        content_type, encoding = cls._check_response(response, expected)
        headers = getattr(response, "headers", {}) or {}
        declared = headers.get("content-length")
        try:
            if declared is not None and int(declared) > limit:
                raise ProviderTransportError("provider_response_too_large")
        except (TypeError, ValueError):
            pass
        try:
            body = bytes(getattr(response, "content"))
        except (AttributeError, TypeError, ValueError):
            # Small fake clients used by contract tests may only expose json().
            value = response.json()
            body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(body) > limit:
            raise ProviderTransportError("provider_response_too_large")
        return ProviderPayload(body=body, content_type=content_type, encoding=encoding, status_code=int(getattr(response, "status_code", 200)))

    @classmethod
    async def _read_stream(cls, response: Any, limit: int, expected: set[str]) -> ProviderPayload:
        content_type, encoding = cls._check_response(response, expected)
        headers = getattr(response, "headers", {}) or {}
        declared = headers.get("content-length")
        try:
            if declared is not None and int(declared) > limit:
                raise ProviderTransportError("provider_response_too_large")
        except (TypeError, ValueError):
            pass
        body = bytearray()
        iterator = getattr(response, "aiter_bytes", None)
        if callable(iterator):
            async for chunk in iterator():
                body.extend(chunk)
                if len(body) > limit:
                    raise ProviderTransportError("provider_response_too_large")
        else:
            value = getattr(response, "content", b"")
            body.extend(bytes(value))
        return ProviderPayload(body=bytes(body), content_type=content_type, encoding=encoding, status_code=int(getattr(response, "status_code", 200)))
