"""Bounded Unix-socket bridge for the Kek Guardian provider.

The Arel process normally acts as a *client* of the existing Kek Guardian
service.  This module is a small, optional server-side bridge for deployments
where Guardian's decision engine is exposed through a local Unix socket.  It
does not contain Telegram logic and it intentionally accepts an injected,
typed evaluator rather than importing Guardian's private implementation.

The bridge is fail-closed: malformed, expired, replayed, unauthorized, or
provider-mismatched requests are discarded without returning untrusted error
details.  A production process should run it under a dedicated service account
with an explicit peer UID/GID allow-list and a pre-created ``0660`` socket.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import stat
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

from .guardian_protocol import (
    GuardianEvent,
    GuardianProtocolError,
    MAX_FRAME_BYTES,
    ReplayCache,
    build_response,
    parse_request,
    parse_response,
    peer_credentials,
    safe_error_code,
    socket_path_is_safe,
    validate_peer_credentials,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GuardianEvaluation:
    """Provider output accepted by :class:`GuardianUnixSocketServer`.

    ``expires_at`` is optional because the bridge assigns a short response
    lifetime bounded by the request expiry.  A provider may supply an earlier
    expiry, but it may never extend a caller's request.
    """

    decision: str
    actions: tuple[str, ...] = ()
    rule_ids: tuple[str, ...] = ()
    provider: str = "kek_guardian"
    reason_code: str | None = None
    expires_at: datetime | None = None


GuardianEvaluator = Callable[
    [GuardianEvent],
    GuardianEvaluation | Mapping[str, Any] | Awaitable[GuardianEvaluation | Mapping[str, Any]],
]


class GuardianBridgeError(RuntimeError):
    """Stable bounded bridge error; never contains provider/user payloads."""

    def __init__(self, code: str):
        self.code = safe_error_code(code, "guardian_bridge_error")
        super().__init__(self.code)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_expiry(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise GuardianBridgeError("response_expiry_invalid")
    return value.astimezone(timezone.utc)


def _normalize_evaluation(
    value: GuardianEvaluation | Mapping[str, Any], *, expected_provider: str
) -> GuardianEvaluation:
    if isinstance(value, GuardianEvaluation):
        result = value
    elif isinstance(value, Mapping):
        allowed = {"decision", "actions", "rule_ids", "provider", "reason_code", "expires_at"}
        if set(value) - allowed:
            raise GuardianBridgeError("provider_response_fields_invalid")
        result = GuardianEvaluation(
            decision=str(value.get("decision", "")),
            actions=tuple(value.get("actions", ()) or ()),
            rule_ids=tuple(value.get("rule_ids", ()) or ()),
            provider=str(value.get("provider", expected_provider)),
            reason_code=value.get("reason_code"),
            expires_at=value.get("expires_at"),
        )
    else:
        raise GuardianBridgeError("provider_response_invalid")

    if result.provider != expected_provider:
        raise GuardianBridgeError("provider_mismatch")
    if not isinstance(result.actions, tuple):
        raise GuardianBridgeError("actions_invalid")
    if not isinstance(result.rule_ids, tuple):
        raise GuardianBridgeError("rule_ids_invalid")
    expiry = _safe_expiry(result.expires_at)
    if expiry is not None and expiry <= _utc_now():
        raise GuardianBridgeError("response_expired")
    # The protocol builder performs the final bounded identifier/action checks.
    return GuardianEvaluation(
        decision=result.decision,
        actions=result.actions,
        rule_ids=result.rule_ids,
        provider=result.provider,
        reason_code=result.reason_code,
        expires_at=expiry,
    )


class GuardianUnixSocketServer:
    """Serve one bounded Guardian request per Unix-socket connection.

    The server intentionally has no default evaluator.  A caller must inject
    Guardian's decision engine, making it impossible for a deployment to
    accidentally enable a permissive placeholder policy.
    """

    def __init__(
        self,
        *,
        socket_path: str,
        evaluator: GuardianEvaluator,
        provider: str = "kek_guardian",
        allowed_peer_uids: set[int] | frozenset[int] | None = None,
        allowed_peer_gids: set[int] | frozenset[int] | None = None,
        require_peer_credentials: bool = True,
        request_timeout_seconds: float = 0.15,
        response_ttl_seconds: float = 1.0,
        max_connections: int = 32,
        replay_cache: ReplayCache | None = None,
    ):
        if not socket_path_is_safe(socket_path):
            raise ValueError("guardian_socket_path_invalid")
        if not callable(evaluator):
            raise TypeError("guardian_evaluator_required")
        safe_provider_chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.:-"
        if not provider or len(provider) > 128 or any(char not in safe_provider_chars for char in provider):
            raise ValueError("guardian_provider_invalid")
        self.socket_path = socket_path
        self.evaluator = evaluator
        self.provider = provider
        self.allowed_peer_uids = frozenset(int(item) for item in (allowed_peer_uids or set()))
        self.allowed_peer_gids = frozenset(int(item) for item in (allowed_peer_gids or set()))
        self.require_peer_credentials = bool(require_peer_credentials)
        self.request_timeout_seconds = max(0.05, min(float(request_timeout_seconds), 10.0))
        self.response_ttl_seconds = max(0.05, min(float(response_ttl_seconds), 30.0))
        self.max_connections = max(1, min(int(max_connections), 256))
        self.replay_cache = replay_cache or ReplayCache()
        self._server: asyncio.AbstractServer | None = None
        self._bound = False
        self._active_connections = 0
        self._metrics: dict[str, int] = {
            "accepted": 0,
            "connection_limit": 0,
            "completed": 0,
            "rejected": 0,
            "protocol_errors": 0,
            "peer_errors": 0,
            "provider_errors": 0,
            "expired": 0,
        }

    @property
    def is_running(self) -> bool:
        return self._server is not None and self._bound

    def metrics(self) -> dict[str, int]:
        """Return a copy of bounded counters; no request data is retained."""

        return dict(self._metrics)

    async def start(self) -> None:
        if os.name == "nt":
            raise GuardianBridgeError("unix_socket_unsupported")
        if not socket_path_is_safe(self.socket_path):
            raise GuardianBridgeError("guardian_socket_path_invalid")
        path = Path(self.socket_path)
        if not path.parent.exists() or path.parent.is_symlink():
            raise GuardianBridgeError("socket_parent_invalid")
        if path.exists():
            # Never unlink an arbitrary path.  Operators must remove stale
            # sockets after verifying ownership, or let systemd do so.
            try:
                if not stat.S_ISSOCK(path.stat().st_mode):
                    raise GuardianBridgeError("socket_path_occupied")
            except OSError as exc:
                raise GuardianBridgeError("socket_path_unavailable") from exc
            raise GuardianBridgeError("socket_already_exists")
        try:
            self._server = await asyncio.start_unix_server(
                self._handle_client,
                path=self.socket_path,
                limit=MAX_FRAME_BYTES + 1,
            )
            os.chmod(self.socket_path, 0o660)
            self._bound = True
        except (OSError, ValueError) as exc:
            self._server = None
            raise GuardianBridgeError("socket_bind_failed") from exc

    async def stop(self) -> None:
        server, self._server = self._server, None
        self._bound = False
        if server is not None:
            server.close()
            await server.wait_closed()
        path = Path(self.socket_path)
        # Only remove a socket file, never a regular file or symlink.
        try:
            if path.exists() and not path.is_symlink() and stat.S_ISSOCK(path.stat().st_mode):
                path.unlink()
        except OSError:
            logger.warning("Guardian bridge socket cleanup failed: code=socket_cleanup_failed")

    async def serve_forever(self) -> None:
        if self._server is None:
            await self.start()
        assert self._server is not None
        await self._server.serve_forever()

    def _validate_peer(self, writer: asyncio.StreamWriter) -> None:
        if not self.require_peer_credentials:
            return
        sock = writer.get_extra_info("socket")
        if sock is None:
            raise GuardianBridgeError("peer_credentials_unavailable")
        try:
            _pid, uid, gid = peer_credentials(sock)
            validate_peer_credentials(
                uid=uid,
                gid=gid,
                allowed_uids=self.allowed_peer_uids,
                allowed_gids=self.allowed_peer_gids,
            )
        except GuardianProtocolError as exc:
            raise GuardianBridgeError(exc.code) from exc

    async def _evaluate(self, event: GuardianEvent) -> GuardianEvaluation:
        try:
            value = self.evaluator(event)
            if inspect.isawaitable(value):
                value = await asyncio.wait_for(value, timeout=self.request_timeout_seconds)
            return _normalize_evaluation(value, expected_provider=self.provider)
        except asyncio.TimeoutError as exc:
            raise GuardianBridgeError("provider_timeout") from exc
        except GuardianBridgeError:
            raise
        except Exception as exc:
            # Do not propagate evaluator messages, URLs, prompts, or secrets.
            raise GuardianBridgeError("provider_failed") from exc

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        # The callback is entered on the event loop before its first await, so
        # this counter is an atomic admission gate.  Reject excess sockets
        # immediately instead of allowing an unbounded semaphore backlog.
        if self._active_connections >= self.max_connections:
            self._metrics["connection_limit"] += 1
            self._metrics["rejected"] += 1
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return
        self._active_connections += 1
        self._metrics["accepted"] += 1
        try:
            self._validate_peer(writer)
            try:
                raw = await asyncio.wait_for(
                    reader.readuntil(b"\n"),
                    timeout=self.request_timeout_seconds,
                )
            except asyncio.LimitOverrunError as exc:
                raise GuardianBridgeError("frame_too_large") from exc
            except (asyncio.IncompleteReadError, asyncio.TimeoutError) as exc:
                raise GuardianBridgeError("frame_incomplete") from exc
            request = parse_request(raw, replay_cache=self.replay_cache)
            if request.expires_at <= _utc_now():
                raise GuardianBridgeError("request_expired")
            evaluation = await self._evaluate(request.event)
            now = _utc_now()
            if request.expires_at <= now:
                raise GuardianBridgeError("request_expired")
            # Never extend the caller's deadline.  The request parser
            # already bounded this timestamp; an injected evaluator may
            # provide an earlier deadline for an internal policy.
            response_expiry = evaluation.expires_at or min(
                request.expires_at,
                now + timedelta(seconds=self.response_ttl_seconds),
            )
            if response_expiry > request.expires_at:
                raise GuardianBridgeError("response_expiry_invalid")
            if response_expiry <= now:
                raise GuardianBridgeError("response_expired")
            if evaluation.decision != "deny" and evaluation.actions:
                raise GuardianBridgeError("actions_decision_invalid")
            payload = build_response(
                request.request_id,
                decision=evaluation.decision,
                actions=evaluation.actions,
                rule_ids=evaluation.rule_ids,
                provider=self.provider,
                reason_code=evaluation.reason_code,
                expires_at=response_expiry,
            )
            # Round-trip validation ensures provider/action/expiry checks
            # are applied before bytes leave the trust boundary.
            parse_response(payload, expected_request_id=request.request_id, now=now)
            writer.write(payload)
            await asyncio.wait_for(writer.drain(), timeout=self.request_timeout_seconds)
            self._metrics["completed"] += 1
        except GuardianBridgeError as exc:
            self._metrics["rejected"] += 1
            if exc.code.startswith("provider_") or exc.code in {
                "provider_mismatch",
                "actions_decision_invalid",
            }:
                self._metrics["provider_errors"] += 1
            elif exc.code in {"request_expired", "response_expired", "response_expiry_invalid"}:
                self._metrics["expired"] += 1
            elif exc.code.startswith("peer_"):
                self._metrics["peer_errors"] += 1
            else:
                self._metrics["protocol_errors"] += 1
            logger.warning("Guardian bridge rejected request: code=%s", exc.code)
        except GuardianProtocolError as exc:
            self._metrics["rejected"] += 1
            if exc.code in {"request_expired", "response_expired", "response_expiry_invalid"}:
                self._metrics["expired"] += 1
            else:
                self._metrics["protocol_errors"] += 1
            logger.warning("Guardian bridge rejected request: code=%s", safe_error_code(exc.code))
        except (ConnectionError, OSError):
            self._metrics["rejected"] += 1
        finally:
            self._active_connections = max(0, self._active_connections - 1)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass


__all__ = [
    "GuardianBridgeError",
    "GuardianEvaluation",
    "GuardianEvaluator",
    "GuardianUnixSocketServer",
]
