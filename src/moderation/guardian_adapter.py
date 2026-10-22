"""Fail-closed Unix-socket adapter for the external Kek Guardian service."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from .guardian_protocol import (
    GuardianEvent,
    GuardianProtocolError,
    GuardianResponse,
    MAX_FRAME_BYTES,
    build_request,
    parse_response,
    peer_credentials,
    safe_error_code,
    socket_path_is_safe,
    validate_peer_credentials,
)

logger = logging.getLogger(__name__)


class GuardianMode(str, Enum):
    DISABLED = "disabled"
    SHADOW = "shadow"
    ACTIVE = "active"

    @classmethod
    def parse(cls, value: object) -> "GuardianMode":
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value or "disabled").strip().casefold())
        except ValueError:
            return cls.DISABLED


class GuardianUnavailable(RuntimeError):
    """Bounded bridge failure that must never be interpreted as an allow."""

    def __init__(self, code: str):
        self.code = safe_error_code(code, "guardian_unavailable")
        super().__init__(self.code)


@dataclass(frozen=True)
class GuardianResult:
    decision: str
    actions: tuple[str, ...] = ()
    rule_ids: tuple[str, ...] = ()
    provider: str = "kek_guardian"
    reason_code: str | None = None
    error_code: str | None = None
    enforce: bool = False
    latency_ms: float | None = None
    failure_mode: str = "none"

    @property
    def available(self) -> bool:
        return self.decision in {"allow", "deny"} and self.error_code is None

    @property
    def fail_closed(self) -> bool:
        """True when Guardian could not establish a trusted decision."""

        return not self.available

    @property
    def enforcement_required(self) -> bool:
        """Whether the caller must apply the returned action plan.

        In active mode an outage is represented as ``decision=unavailable``
        with ``enforce=True`` and a bounded ``quarantine`` action.  Callers
        should check this property instead of testing only ``decision ==
        'deny'``; otherwise a transport failure silently becomes a local
        fail-open path.
        """

        return bool(self.enforce) and self.decision in {"deny", "unavailable"}


class BoundedCircuitBreaker:
    """Small monotonic circuit breaker with no unbounded state."""

    def __init__(self, *, failure_threshold: int = 3, cooldown_seconds: float = 30.0):
        self.failure_threshold = max(1, min(int(failure_threshold), 20))
        self.cooldown_seconds = max(1.0, min(float(cooldown_seconds), 3600.0))
        self.failures = 0
        self.opened_until = 0.0

    @property
    def state(self) -> str:
        if self.opened_until > time.monotonic():
            return "open"
        if self.opened_until:
            return "half_open"
        return "closed"

    def allow(self) -> bool:
        return self.state != "open"

    def record_success(self) -> None:
        self.failures = 0
        self.opened_until = 0.0

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.failure_threshold:
            self.opened_until = time.monotonic() + self.cooldown_seconds


class GuardianAdapter:
    """Call Guardian over a local Unix socket with bounded fail-closed state.

    ``mode=shadow`` records the provider decision but never authorizes an
    action.  ``mode=active`` fails closed by default when the bridge is
    unavailable: the result is ``decision=unavailable``, ``enforce=True`` and
    a single ``quarantine`` action.  This keeps the transport failure visible
    while giving the caller a safe, deterministic action.  Operators may
    explicitly opt out with ``GUARDIAN_FAIL_CLOSED=false`` during a controlled
    migration; that choice is exposed in the health/config snapshot.
    """

    def __init__(
        self,
        *,
        socket_path: str,
        mode: GuardianMode | str = GuardianMode.DISABLED,
        timeout_seconds: float = 0.15,
        failure_threshold: int = 3,
        cooldown_seconds: float = 30.0,
        allowed_peer_uids: set[int] | frozenset[int] | None = None,
        allowed_peer_gids: set[int] | frozenset[int] | None = None,
        require_peer_credentials: bool = True,
        fail_closed_on_error: bool | None = None,
    ):
        if not socket_path_is_safe(socket_path):
            raise ValueError("guardian_socket_path_invalid")
        self.socket_path = socket_path
        self.mode = GuardianMode.parse(mode)
        self.timeout_seconds = max(0.05, min(float(timeout_seconds), 10.0))
        self.allowed_peer_uids = frozenset(int(item) for item in (allowed_peer_uids or set()))
        self.allowed_peer_gids = frozenset(int(item) for item in (allowed_peer_gids or set()))
        self.require_peer_credentials = bool(require_peer_credentials)
        self.fail_closed_on_error = self.mode is GuardianMode.ACTIVE and (
            self.mode is GuardianMode.ACTIVE if fail_closed_on_error is None else bool(fail_closed_on_error)
        )
        self.breaker = BoundedCircuitBreaker(
            failure_threshold=failure_threshold,
            cooldown_seconds=cooldown_seconds,
        )

    @classmethod
    def from_environment(cls) -> "GuardianAdapter":
        def _int(name: str, default: int) -> int:
            try:
                return int(os.getenv(name, str(default)) or default)
            except (TypeError, ValueError):
                return default

        def _float(name: str, default: float) -> float:
            try:
                return float(os.getenv(name, str(default)) or default)
            except (TypeError, ValueError):
                return default

        def _ids(name: str) -> set[int]:
            values: set[int] = set()
            for raw in os.getenv(name, "").split(","):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    value = int(raw)
                except ValueError:
                    continue
                if value >= 0:
                    values.add(value)
            return values

        def _bool(name: str, default: bool) -> bool:
            raw = os.getenv(name)
            if raw is None:
                return default
            return raw.strip().casefold() in {"1", "true", "yes", "on"}

        timeout = _float("GUARDIAN_TIMEOUT_SECONDS", 0.15)
        require_peer = os.getenv("GUARDIAN_REQUIRE_PEER_CREDENTIALS", "true").casefold() in {
            "1",
            "true",
            "yes",
            "on",
        }
        return cls(
            socket_path=os.getenv("GUARDIAN_SOCKET_PATH", "/run/kek-guardian/guardian.sock"),
            mode=GuardianMode.parse(os.getenv("GUARDIAN_MODE", "disabled")),
            timeout_seconds=timeout,
            failure_threshold=_int("GUARDIAN_FAILURE_THRESHOLD", 3),
            cooldown_seconds=_float("GUARDIAN_COOLDOWN_SECONDS", 30),
            allowed_peer_uids=_ids("GUARDIAN_PEER_UIDS"),
            allowed_peer_gids=_ids("GUARDIAN_PEER_GIDS"),
            require_peer_credentials=require_peer,
            fail_closed_on_error=_bool("GUARDIAN_FAIL_CLOSED", True),
        )

    def _unavailable_result(self, error_code: str, *, latency_ms: float | None = None) -> GuardianResult:
        """Create a transport-failure result without leaking exception data."""

        fail_closed = self.mode is GuardianMode.ACTIVE and self.fail_closed_on_error
        return GuardianResult(
            decision="unavailable",
            actions=("quarantine",) if fail_closed else (),
            reason_code="guardian_unavailable" if fail_closed else None,
            error_code=str(error_code)[:96],
            enforce=fail_closed,
            latency_ms=latency_ms,
            failure_mode="fail_closed" if fail_closed else "observe_only",
        )

    @staticmethod
    def _coerce_event(event: GuardianEvent | Any) -> GuardianEvent:
        """Accept the shared moderation contract without importing it here.

        Keeping the protocol module transport-only avoids a dependency cycle;
        the application-level ``ModerationEvent`` is structurally converted at
        this boundary and no extra fields cross into Guardian.
        """

        if isinstance(event, GuardianEvent):
            return event
        try:
            return GuardianEvent(
                chat_id=int(event.chat_id),
                user_id=int(event.user_id),
                message_id=int(event.message_id),
                event_kind=str(event.event_kind),
                thread_id=getattr(event, "thread_id", None),
                text=getattr(event, "text", None) or None,
                caption=getattr(event, "caption", None),
                attachment_type=getattr(event, "attachment_type", None),
                attachment_mime=getattr(event, "attachment_mime", None),
                is_admin=bool(getattr(event, "is_admin", False)),
                settings_version=int(getattr(event, "settings_version", 0) or 0),
                correlation_id=str(getattr(event, "correlation_id", "") or "") or None,
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise GuardianProtocolError("event_invalid") from exc

    async def evaluate(self, event: GuardianEvent | Any) -> GuardianResult:
        event = self._coerce_event(event)
        if self.mode is GuardianMode.DISABLED:
            return GuardianResult(
                decision="unavailable",
                error_code="guardian_disabled",
                enforce=False,
                failure_mode="disabled",
            )
        if not self.breaker.allow():
            return self._unavailable_result("guardian_circuit_open")
        started = time.perf_counter()
        try:
            response = await asyncio.wait_for(self._request(event), timeout=self.timeout_seconds)
            self.breaker.record_success()
            if not isinstance(response, GuardianResponse):
                response = self._parse_mapping_response(response)
            enforce = self.mode is GuardianMode.ACTIVE and response.decision == "deny"
            return GuardianResult(
                decision=response.decision,
                actions=response.actions if enforce else (),
                rule_ids=response.rule_ids,
                provider=response.provider,
                reason_code=response.reason_code,
                enforce=enforce,
                latency_ms=round((time.perf_counter() - started) * 1000.0, 2),
                failure_mode="none",
            )
        except asyncio.TimeoutError:
            self.breaker.record_failure()
            return self._unavailable_result(
                "guardian_timeout",
                latency_ms=round((time.perf_counter() - started) * 1000.0, 2),
            )
        except (GuardianUnavailable, GuardianProtocolError, OSError, ValueError) as exc:
            self.breaker.record_failure()
            code = getattr(exc, "code", None) or "guardian_unavailable"
            logger.warning("Guardian bridge unavailable: code=%s", str(code)[:64])
            return self._unavailable_result(
                str(code)[:64],
                latency_ms=round((time.perf_counter() - started) * 1000.0, 2),
            )
        except Exception as exc:
            self.breaker.record_failure()
            logger.warning("Guardian bridge failed: type=%s", type(exc).__name__[:64])
            return self._unavailable_result(
                "guardian_unavailable",
                latency_ms=round((time.perf_counter() - started) * 1000.0, 2),
            )

    @staticmethod
    def _parse_mapping_response(value: Mapping[str, Any] | Any) -> GuardianResponse:
        if isinstance(value, GuardianResponse):
            return value
        if not isinstance(value, Mapping):
            raise GuardianProtocolError("response_object_required")
        try:
            raw = (json.dumps(dict(value), ensure_ascii=True, separators=(",", ":")) + "\n").encode()
        except (TypeError, ValueError) as exc:
            raise GuardianProtocolError("response_json_invalid") from exc
        return parse_response(raw, expected_provider="kek_guardian")

    def _verify_peer(self, writer: asyncio.StreamWriter) -> None:
        if not self.require_peer_credentials and not self.allowed_peer_uids and not self.allowed_peer_gids:
            return
        sock = writer.get_extra_info("socket")
        if sock is None:
            raise GuardianUnavailable("peer_credentials_unavailable")
        try:
            _pid, uid, gid = peer_credentials(sock)
            validate_peer_credentials(
                uid=uid,
                gid=gid,
                allowed_uids=self.allowed_peer_uids,
                allowed_gids=self.allowed_peer_gids,
            )
        except GuardianProtocolError as exc:
            raise GuardianUnavailable(exc.code) from exc

    async def _request(self, event: GuardianEvent) -> GuardianResponse:
        if os.name == "nt":
            raise GuardianUnavailable("unix_socket_unsupported")
        if not socket_path_is_safe(self.socket_path):
            raise GuardianUnavailable("guardian_socket_path_invalid")
        request = build_request(event, ttl_seconds=self.timeout_seconds)
        try:
            reader, writer = await asyncio.open_unix_connection(
                self.socket_path,
                limit=MAX_FRAME_BYTES + 1,
            )
        except (FileNotFoundError, ConnectionRefusedError, PermissionError, OSError) as exc:
            raise GuardianUnavailable("socket_unavailable") from exc
        try:
            self._verify_peer(writer)
            writer.write(request)
            await writer.drain()
            try:
                raw = await reader.readuntil(b"\n")
            except asyncio.LimitOverrunError as exc:
                raise GuardianProtocolError("response_too_large") from exc
            return parse_response(
                raw,
                expected_request_id=json.loads(request.decode())["request_id"],
                expected_provider="kek_guardian",
            )
        except GuardianProtocolError:
            raise
        except (asyncio.IncompleteReadError, ConnectionError, OSError) as exc:
            raise GuardianUnavailable("socket_read_failed") from exc
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def health_check(self) -> dict[str, Any]:
        if self.mode is GuardianMode.DISABLED:
            return {
                "provider": "kek_guardian",
                "mode": self.mode.value,
                "health": "disabled",
                "health_state": "disabled",
                "circuit": self.breaker.state,
                "circuit_state": self.breaker.state,
                "fail_closed_on_error": self.fail_closed_on_error,
            }
        health = "unknown" if self.breaker.allow() else "degraded"
        return {
            "provider": "kek_guardian",
            "mode": self.mode.value,
            "health": health,
            "health_state": health,
            "circuit": self.breaker.state,
            "circuit_state": self.breaker.state,
            "timeout_ms": int(self.timeout_seconds * 1000),
            "fail_closed_on_error": self.fail_closed_on_error,
        }


__all__ = [
    "BoundedCircuitBreaker",
    "GuardianAdapter",
    "GuardianMode",
    "GuardianResult",
    "GuardianUnavailable",
]
