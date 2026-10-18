"""Small, dependency-free observability HTTP endpoint.

The bot deliberately does not expose an unauthenticated web dashboard.  This
server is intended for a reverse proxy or a local Prometheus sidecar and binds
to loopback by default.  It accepts only bounded GET requests, never echoes
request data, and serializes only the supplied metrics/health snapshots.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from src.security.log_redaction import summarize_exception

logger = logging.getLogger(__name__)

SnapshotFactory = Callable[[], Mapping[str, Any] | Awaitable[Mapping[str, Any]]]

_METRIC_NAME = re.compile(r"[^a-zA-Z0-9_:]")
_MAX_REQUEST_BYTES = 8192
_READ_TIMEOUT_SECONDS = 2.0


def _safe_metric_name(value: object) -> str:
    name = _METRIC_NAME.sub("_", str(value)).strip("_")
    return name or "value"


def render_prometheus(snapshot: Mapping[str, Any]) -> str:
    """Render scalar values from a nested snapshot without user labels.

    Queue services expose nested ``counters``, ``gauges`` and ``latency``
    dictionaries.  Recursively flattening those maps keeps the exporter
    generic while guaranteeing that no user/chat/provider text becomes a
    Prometheus label or an unbounded metric dimension.
    """

    rows: list[str] = []
    seen: set[str] = set()

    def visit(parts: tuple[str, ...], value: Any) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                visit(parts + (_safe_metric_name(key),), child)
            return
        if isinstance(value, bool):
            numeric: float | int = int(value)
        elif isinstance(value, (int, float)):
            numeric = value
        else:
            return
        metric = "arelbot_" + "_".join(parts)
        if metric in seen:
            return
        seen.add(metric)
        rows.append(f"{metric} {numeric!r}")

    for key, value in snapshot.items():
        visit((_safe_metric_name(key),), value)
    return "\n".join(rows) + ("\n" if rows else "")


class ObservabilityServer:
    """Authenticated loopback HTTP server for health and metrics snapshots."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        snapshot_factory: SnapshotFactory,
        health_factory: SnapshotFactory | None = None,
        token: str | None = None,
        max_request_bytes: int = _MAX_REQUEST_BYTES,
    ) -> None:
        self.host = str(host)
        self.requested_port = int(port)
        self.snapshot_factory = snapshot_factory
        self.health_factory = health_factory or (lambda: {"ok": True})
        self.token = str(token or "")
        self.max_request_bytes = max(1024, min(_MAX_REQUEST_BYTES, int(max_request_bytes)))
        self._server: asyncio.AbstractServer | None = None

    @property
    def port(self) -> int:
        if self._server is None or not self._server.sockets:
            return self.requested_port
        address = self._server.sockets[0].getsockname()
        return int(address[1])

    async def start(self) -> None:
        if self._server is not None:
            return
        self._server = await asyncio.start_server(self._handle_client, self.host, self.requested_port)
        logger.info("Observability endpoint listening on %s:%s", self.host, self.port)

    async def close(self) -> None:
        server, self._server = self._server, None
        if server is None:
            return
        server.close()
        await server.wait_closed()

    async def _resolve(self, factory: SnapshotFactory) -> Mapping[str, Any]:
        value = factory()
        if hasattr(value, "__await__"):
            value = await value  # type: ignore[misc]
        return value if isinstance(value, Mapping) else {"ok": False, "error": "invalid_snapshot"}

    def _authorized(self, headers: Mapping[str, str]) -> bool:
        if not self.token:
            return True
        # Prometheus uses the standard Authorization header while local
        # operators may use the explicit Arelbot header. Accept only the
        # Bearer scheme; never treat Basic or arbitrary auth text as a token.
        provided = headers.get("x-arelbot-metrics-token", "")
        if not provided:
            authorization = headers.get("authorization", "")
            scheme, _, credential = authorization.partition(" ")
            if scheme.casefold() == "bearer":
                provided = credential.strip()
        elif provided.startswith("Bearer "):
            provided = provided[7:]
        return hmac.compare_digest(provided, self.token)

    @staticmethod
    def _response(status: int, content_type: str, body: bytes) -> bytes:
        reason = {200: "OK", 401: "Unauthorized", 404: "Not Found", 405: "Method Not Allowed", 400: "Bad Request"}.get(status, "Error")
        head = (
            f"HTTP/1.1 {status} {reason}\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Cache-Control: no-store\r\n"
            "Connection: close\r\n\r\n"
        )
        return head.encode("ascii") + body

    @staticmethod
    def _health_http_status(payload: Mapping[str, Any]) -> int:
        """Map the bounded health contract to an HTTP readiness status.

        ``degraded`` deliberately remains available: optional providers can
        be down while the bot and its durable dependencies still accept
        traffic.  A core ``unhealthy`` state must be visible to reverse
        proxies, Docker and orchestration probes as ``503``.
        """

        status = str(payload.get("status", "")).casefold()
        if status == "unhealthy" or payload.get("ok") is False:
            return 503
        return 200

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = bytearray()
            while len(request) < self.max_request_bytes:
                chunk = await asyncio.wait_for(reader.read(1024), timeout=_READ_TIMEOUT_SECONDS)
                if not chunk:
                    break
                request.extend(chunk)
                if b"\r\n\r\n" in request:
                    break
            if len(request) >= self.max_request_bytes and b"\r\n\r\n" not in request:
                writer.write(self._response(400, "text/plain; charset=utf-8", b"bad request\n"))
                await writer.drain()
                return
            try:
                head = bytes(request).split(b"\r\n\r\n", 1)[0].decode("ascii")
                lines = head.split("\r\n")
                method, target, _version = lines[0].split(" ", 2)
                headers = {
                    key.strip().lower(): value.strip()
                    for key, value in (line.split(":", 1) for line in lines[1:] if ":" in line)
                }
            except (UnicodeDecodeError, ValueError):
                writer.write(self._response(400, "text/plain; charset=utf-8", b"bad request\n"))
                await writer.drain()
                return
            if method != "GET":
                writer.write(self._response(405, "text/plain; charset=utf-8", b"method not allowed\n"))
                await writer.drain()
                return
            if not self._authorized(headers):
                writer.write(self._response(401, "text/plain; charset=utf-8", b"unauthorized\n"))
                await writer.drain()
                return
            path = target.split("?", 1)[0]
            if path == "/healthz":
                health = await self._resolve(self.health_factory)
                payload = json.dumps(health, ensure_ascii=False).encode("utf-8")
                content_type = "application/json; charset=utf-8"
                status = self._health_http_status(health)
            elif path == "/metrics":
                payload = render_prometheus(await self._resolve(self.snapshot_factory)).encode("utf-8")
                content_type = "text/plain; version=0.0.4; charset=utf-8"
                status = 200
            else:
                payload = b"not found\n"
                content_type = "text/plain; charset=utf-8"
                status = 404
            writer.write(self._response(status, content_type, payload))
            await writer.drain()
        except (asyncio.TimeoutError, ConnectionError):
            return
        except Exception as exc:
            logger.error("Observability request failed: %s", summarize_exception(exc, label="observability"))
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, asyncio.CancelledError):
                pass
