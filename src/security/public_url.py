"""Shared validation for user-supplied public HTTP(S) URLs."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit


async def validate_public_url(value: str, *, resolve_host: bool = True) -> str:
    """Validate a public URL and optionally resolve every DNS answer.

    The caller must repeat this validation for every redirect target.  The
    DNS check is intentionally conservative: any non-global answer rejects
    the request, including loopback, link-local, private and reserved ranges.
    """

    candidate = str(value or "").strip()
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("web_url_invalid") from exc
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("web_url_invalid")
    if parsed.username or parsed.password:
        raise ValueError("web_url_credentials")
    if port not in {None, 80, 443}:
        raise ValueError("web_url_port_unsupported")
    host = parsed.hostname.rstrip(".").casefold()
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not literal.is_global:
            raise ValueError("web_url_private_host")
    elif host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".internal")):
        raise ValueError("web_url_private_host")
    elif resolve_host:
        try:
            loop = asyncio.get_running_loop()
            records = await loop.getaddrinfo(host, port or (443 if parsed.scheme.casefold() == "https" else 80), type=socket.SOCK_STREAM)
        except (OSError, socket.gaierror) as exc:
            raise ValueError("web_url_host_unavailable") from exc
        addresses = {record[4][0] for record in records if record and len(record) > 4 and record[4]}
        if not addresses:
            raise ValueError("web_url_host_unavailable")
        try:
            parsed_addresses = [ipaddress.ip_address(address) for address in addresses]
        except ValueError as exc:
            raise ValueError("web_url_host_invalid") from exc
        if any(not address.is_global for address in parsed_addresses):
            raise ValueError("web_url_private_host")
    return candidate
