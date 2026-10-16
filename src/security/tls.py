"""Secure, platform-aware TLS verification for outbound HTTP clients.

The bot must never weaken certificate verification to make a provider work.  A
deployment may explicitly provide an organisation CA bundle through
``SSL_CERT_FILE``; otherwise Windows uses the operating-system trust store and
other platforms use the normal Python/HTTPX trust configuration.
"""

from __future__ import annotations

import os
import ssl
from pathlib import Path


def tls_verify(
    *,
    cert_file: str | None = None,
    is_windows: bool | None = None,
) -> str | ssl.SSLContext | bool:
    """Return a safe ``httpx`` ``verify`` value.

    ``cert_file`` and ``is_windows`` are injectable to keep policy tests
    deterministic.  An explicitly configured bundle must exist; silently
    ignoring a bad path would make staging/production trust ambiguous.
    """

    configured = cert_file if cert_file is not None else os.getenv("SSL_CERT_FILE", "")
    configured = configured.strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_file():
            raise ValueError("SSL_CERT_FILE must point to a readable certificate bundle")
        return str(path)

    windows = os.name == "nt" if is_windows is None else is_windows
    if windows:
        # On Windows CPython loads the native system root store into this
        # context.  Verification remains enabled; no insecure fallback exists.
        return ssl.create_default_context()
    return True


def httpx_verify_kwargs() -> dict[str, str | ssl.SSLContext | bool]:
    """Return kwargs shared by all outbound ``httpx`` clients."""

    return {"verify": tls_verify()}

