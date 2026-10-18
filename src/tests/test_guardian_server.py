from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.moderation.guardian_protocol import GuardianEvent, build_request, parse_response
from src.moderation.guardian_server import (
    GuardianBridgeError,
    GuardianEvaluation,
    GuardianUnixSocketServer,
)


def _event(message_id: int = 1) -> GuardianEvent:
    return GuardianEvent(
        chat_id=-100123,
        user_id=42,
        message_id=message_id,
        event_kind="text",
        text="merhaba",
        is_admin=False,
        settings_version=1,
        correlation_id=f"server-test-{message_id}",
    )


async def _send(path: str, payload: bytes) -> bytes:
    reader, writer = await asyncio.open_unix_connection(path, limit=17 * 1024)
    try:
        writer.write(payload)
        await writer.drain()
        return await asyncio.wait_for(reader.readuntil(b"\n"), timeout=1)
    finally:
        writer.close()
        await writer.wait_closed()


async def _send_maybe_empty(path: str, payload: bytes) -> bytes:
    try:
        return await _send(path, payload)
    except (asyncio.IncompleteReadError, ConnectionError, OSError):
        return b""


@pytest.mark.skipif(os.name == "nt", reason="Unix socket bridge is Linux-only")
@pytest.mark.asyncio
async def test_guardian_server_round_trip_validates_provider_and_response(tmp_path: Path):
    socket_path = str(tmp_path / "guardian.sock")

    async def evaluator(event: GuardianEvent) -> GuardianEvaluation:
        assert event.message_id == 1
        return GuardianEvaluation(
            decision="deny",
            actions=("delete_message",),
            rule_ids=("flood",),
        )

    server = GuardianUnixSocketServer(
        socket_path=socket_path,
        evaluator=evaluator,
        allowed_peer_uids={os.getuid()},
        request_timeout_seconds=0.5,
    )
    await server.start()
    try:
        raw = build_request(_event(), ttl_seconds=2)
        response = await _send(socket_path, raw)
        parsed = parse_response(response, expected_request_id=json.loads(raw.decode())["request_id"])
        assert parsed.provider == "kek_guardian"
        assert parsed.decision == "deny"
        assert parsed.actions == ("delete_message",)
        assert server.metrics()["completed"] == 1
    finally:
        await server.stop()


@pytest.mark.skipif(os.name == "nt", reason="Unix socket bridge is Linux-only")
@pytest.mark.asyncio
async def test_guardian_server_rejects_peer_before_provider_call(tmp_path: Path):
    socket_path = str(tmp_path / "guardian.sock")
    called = False

    async def evaluator(_event: GuardianEvent) -> GuardianEvaluation:
        nonlocal called
        called = True
        return GuardianEvaluation(decision="allow")

    server = GuardianUnixSocketServer(
        socket_path=socket_path,
        evaluator=evaluator,
        allowed_peer_uids={os.getuid() + 100000},
        request_timeout_seconds=0.5,
    )
    await server.start()
    try:
        assert await _send_maybe_empty(socket_path, build_request(_event())) == b""
        assert called is False
        assert server.metrics()["peer_errors"] == 1
    finally:
        await server.stop()


@pytest.mark.skipif(os.name == "nt", reason="Unix socket bridge is Linux-only")
@pytest.mark.asyncio
async def test_guardian_server_replay_is_rejected_without_second_evaluation(tmp_path: Path):
    socket_path = str(tmp_path / "guardian.sock")
    calls = 0

    async def evaluator(_event: GuardianEvent) -> GuardianEvaluation:
        nonlocal calls
        calls += 1
        return GuardianEvaluation(decision="allow")

    server = GuardianUnixSocketServer(
        socket_path=socket_path,
        evaluator=evaluator,
        allowed_peer_uids={os.getuid()},
        request_timeout_seconds=0.5,
    )
    await server.start()
    try:
        raw = build_request(_event(), ttl_seconds=2)
        first = await _send(socket_path, raw)
        assert parse_response(first).decision == "allow"
        assert await _send_maybe_empty(socket_path, raw) == b""
        assert calls == 1
        assert server.metrics()["protocol_errors"] >= 1
    finally:
        await server.stop()


@pytest.mark.skipif(os.name == "nt", reason="Unix socket bridge is Linux-only")
@pytest.mark.asyncio
async def test_guardian_server_rejects_expired_request_and_provider_mismatch(tmp_path: Path):
    socket_path = str(tmp_path / "guardian.sock")

    async def evaluator(_event: GuardianEvent) -> GuardianEvaluation:
        return GuardianEvaluation(decision="allow", provider="untrusted-provider")

    server = GuardianUnixSocketServer(
        socket_path=socket_path,
        evaluator=evaluator,
        allowed_peer_uids={os.getuid()},
        request_timeout_seconds=0.5,
    )
    await server.start()
    try:
        expired = build_request(
            _event(),
            now=datetime.now(timezone.utc) - timedelta(seconds=3),
            ttl_seconds=1,
        )
        assert await _send_maybe_empty(socket_path, expired) == b""
        assert server.metrics()["expired"] >= 1

        valid = build_request(_event(2), ttl_seconds=2)
        assert await _send_maybe_empty(socket_path, valid) == b""
        assert server.metrics()["provider_errors"] >= 1
    finally:
        await server.stop()


def test_guardian_server_requires_injected_evaluator(tmp_path: Path):
    with pytest.raises(TypeError, match="guardian_evaluator_required"):
        GuardianUnixSocketServer(socket_path=str(tmp_path / "x.sock"), evaluator=None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_guardian_server_normalizes_provider_output_without_socket(tmp_path: Path):
    server = GuardianUnixSocketServer(
        socket_path=str(tmp_path / "guardian.sock"),
        evaluator=lambda _event: {"decision": "allow", "provider": "kek_guardian"},
        require_peer_credentials=False,
    )
    result = await server._evaluate(_event())
    assert result.decision == "allow"
    assert result.provider == "kek_guardian"


@pytest.mark.asyncio
async def test_guardian_server_rejects_bad_provider_output_without_socket(tmp_path: Path):
    server = GuardianUnixSocketServer(
        socket_path=str(tmp_path / "guardian.sock"),
        evaluator=lambda _event: {"decision": "allow", "provider": "other"},
        require_peer_credentials=False,
    )
    with pytest.raises(GuardianBridgeError, match="provider_mismatch"):
        await server._evaluate(_event())


@pytest.mark.skipif(os.name == "nt", reason="Unix socket bridge is Linux-only")
@pytest.mark.asyncio
async def test_guardian_server_does_not_replace_occupied_path(tmp_path: Path):

    occupied = tmp_path / "occupied.sock"
    occupied.write_text("regular file", encoding="utf-8")
    server = GuardianUnixSocketServer(
        socket_path=str(occupied),
        evaluator=lambda _event: GuardianEvaluation(decision="allow"),
        require_peer_credentials=False,
    )
    with pytest.raises(GuardianBridgeError, match="socket_path_occupied"):
        await server.start()
    assert occupied.read_text(encoding="utf-8") == "regular file"
