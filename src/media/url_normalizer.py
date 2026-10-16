"""URL extraction and public host validation for media jobs."""

import asyncio
import ipaddress
import re
import socket
from urllib.parse import urlsplit, urlunsplit

_URL_RE = re.compile(r"https?://[^\s<>\]\[(){}\"']+", re.IGNORECASE)
_ALLOWED_HOSTS = (
    "instagram.com", "instagr.am", "tiktok.com", "youtube.com", "youtu.be",
    "twitter.com", "x.com", "facebook.com", "fb.watch", "reddit.com", "redd.it",
)


class InvalidMediaURL(ValueError):
    """Raised when a URL is unsupported or unsafe to fetch."""


def normalize_media_url(url: str) -> str:
    candidate = (url or "").strip().rstrip(".,!?;:")
    parts = urlsplit(candidate)
    if parts.scheme.lower() not in {"http", "https"}:
        raise InvalidMediaURL("unsupported_scheme")
    if not parts.hostname or parts.username or parts.password:
        raise InvalidMediaURL("invalid_url")
    if parts.port not in {None, 80, 443}:
        raise InvalidMediaURL("unsupported_port")
    host = parts.hostname.rstrip(".").lower()
    if not any(host == allowed or host.endswith("." + allowed) for allowed in _ALLOWED_HOSTS):
        raise InvalidMediaURL("unsupported_platform")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", parts.query, ""))


def extract_media_url(text: str) -> str | None:
    for match in _URL_RE.finditer(text or ""):
        try:
            return normalize_media_url(match.group(0))
        except InvalidMediaURL:
            continue
    return None


async def ensure_public_host(url: str) -> None:
    """Reject DNS answers that point at local or reserved networks."""
    host = urlsplit(url).hostname
    if not host:
        raise InvalidMediaURL("invalid_url")
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            loop = asyncio.get_running_loop()
            records = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
            addresses = [ipaddress.ip_address(record[4][0]) for record in records]
        except (OSError, socket.gaierror) as exc:
            raise InvalidMediaURL("host_unavailable") from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise InvalidMediaURL("non_public_host")


def platform_for_url(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    if host.endswith(("instagram.com", "instagr.am")):
        return "instagram"
    if host.endswith("tiktok.com"):
        return "tiktok"
    if host.endswith(("youtube.com", "youtu.be")):
        return "youtube"
    if host.endswith(("twitter.com", "x.com")):
        return "x"
    if host.endswith(("facebook.com", "fb.watch")):
        return "facebook"
    return "reddit"
