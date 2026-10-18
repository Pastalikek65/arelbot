from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import pytest

from src.moderation.guardian_adapter import (
    GuardianAdapter,
    GuardianMode,
    GuardianResult,
    GuardianUnavailable,
)
from src.moderation.guardian_protocol import (
    GuardianEvent,
    GuardianProtocolError,
    ReplayCache,
    build_request,
    parse_request,
)
from src.moderation.contracts import ModerationEvent


def _event(**overrides):
    values = {
        "chat_id": -100123,
        "user_id": 42,
        "message_id": 7,
        "event_kind": "text",
        "text": "merhaba",
        "is_admin": False,
        "settings_version": 1,
        "correlation_id": "corr-123",
    }
    values.update(overrides)
    return GuardianEvent(**values)


def test_request_round_trip_is_bounded_and_typed():
    raw = build_request(_event(), ttl_seconds=2)
    parsed = parse_request(raw, replay_cache=ReplayCache())

    assert parsed.version == 1
    assert parsed.operation == "evaluate"
    assert parsed.event.chat_id == -100123
    assert parsed.event.text == "merhaba"
    assert len(raw) <= 16 * 1024


def test_unknown_request_fields_are_rejected_before_evaluation():
    raw = json.loads(build_request(_event()))
    raw["unexpected"] = "value"

    with pytest.raises(GuardianProtocolError, match="unknown_field"):
        parse_request(json.dumps(raw).encode(), replay_cache=ReplayCache())


def test_expired_request_and_replayed_request_are_rejected():
    raw = json.loads(build_request(_event(), ttl_seconds=1))
    raw["expires_at"] = "2000-01-01T00:00:00+00:00"

    with pytest.raises(GuardianProtocolError, match="request_expired"):
        parse_request(json.dumps(raw).encode(), replay_cache=ReplayCache())

    fresh = build_request(_event())
    cache = ReplayCache()
    parse_request(fresh, replay_cache=cache)
    with pytest.raises(GuardianProtocolError, match="request_replayed"):
        parse_request(fresh, replay_cache=cache)


def test_raw_event_data_is_bounded():
    raw = json.loads(build_request(_event()))
    raw["event"]["text"] = "x" * 5000
    with pytest.raises(GuardianProtocolError, match="text_too_large"):
        parse_request(json.dumps(raw).encode(), replay_cache=ReplayCache())


def test_raw_event_control_characters_are_rejected():
    raw = json.loads(build_request(_event()))
    raw["event"]["text"] = "ok\x00bad"

    with pytest.raises(GuardianProtocolError, match="control_character"):
        parse_request(json.dumps(raw).encode(), replay_cache=ReplayCache())


def test_disabled_mode_never_returns_an_allow_decision():
    adapter = GuardianAdapter(socket_path="/run/kek-guardian/guardian.sock", mode="disabled")

    result = asyncio.run(adapter.evaluate(_event()))

    assert isinstance(result, GuardianResult)
    assert result.decision == "unavailable"
    assert result.error_code == "guardian_disabled"
    assert result.enforce is False


def test_adapter_coerces_shared_moderation_event_contract():
    event = ModerationEvent(
        chat_id=-100123,
        user_id=42,
        message_id=7,
        event_kind="photo",
        caption="altyazı",
        attachment_type="photo",
        attachment_mime="image/jpeg",
    )
    converted = GuardianAdapter._coerce_event(event)

    assert converted.chat_id == event.chat_id
    assert converted.attachment_type == "photo"
    assert converted.caption == "altyazı"


def test_invalid_mode_defaults_to_disabled(monkeypatch):
    monkeypatch.setenv("GUARDIAN_MODE", "anything-else")

    adapter = GuardianAdapter.from_environment()

    assert adapter.mode is GuardianMode.DISABLED


@pytest.mark.asyncio
async def test_circuit_breaker_opens_after_bounded_failures(monkeypatch):
    adapter = GuardianAdapter(
        socket_path="/run/kek-guardian/guardian.sock",
        mode="active",
        failure_threshold=2,
        cooldown_seconds=60,
    )

    async def fail(_event):
        raise GuardianUnavailable("socket_unavailable")

    monkeypatch.setattr(adapter, "_request", fail)
    first = await adapter.evaluate(_event())
    second = await adapter.evaluate(_event(message_id=8))
    third = await adapter.evaluate(_event(message_id=9))

    assert first.error_code == "socket_unavailable"
    assert second.error_code == "socket_unavailable"
    assert third.error_code == "guardian_circuit_open"
    assert third.decision == "unavailable"
    assert third.enforce is True
    assert third.actions == ("quarantine",)
    assert third.failure_mode == "fail_closed"
    assert third.enforcement_required is True


@pytest.mark.asyncio
async def test_shadow_mode_suppresses_actions_and_keeps_decision_metadata(monkeypatch):
    adapter = GuardianAdapter(socket_path="/run/kek-guardian/guardian.sock", mode="shadow")

    async def response(_event):
        return {
            "version": 1,
            "request_id": "req-12345678",
            "provider": "kek_guardian",
            "decision": "deny",
            "actions": ["delete", "mute"],
            "rule_ids": ["flood-1"],
            "expires_at": "2999-01-01T00:00:00+00:00",
        }

    monkeypatch.setattr(adapter, "_request", response)
    result = await adapter.evaluate(_event())

    assert result.decision == "deny"
    assert result.actions == ()
    assert result.enforce is False
    assert result.rule_ids == ("flood-1",)


@pytest.mark.asyncio
async def test_active_bridge_failure_can_be_explicitly_observe_only(monkeypatch):
    adapter = GuardianAdapter(
        socket_path="/run/kek-guardian/guardian.sock",
        mode="active",
        fail_closed_on_error=False,
    )

    async def fail(_event):
        raise GuardianUnavailable("socket_unavailable")

    monkeypatch.setattr(adapter, "_request", fail)
    result = await adapter.evaluate(_event())

    assert result.decision == "unavailable"
    assert result.enforce is False
    assert result.actions == ()
    assert result.failure_mode == "observe_only"
    assert result.enforcement_required is False


def test_environment_defaults_active_mode_to_fail_closed(monkeypatch):
    monkeypatch.setenv("GUARDIAN_MODE", "active")
    monkeypatch.delenv("GUARDIAN_FAIL_CLOSED", raising=False)

    adapter = GuardianAdapter.from_environment()

    assert adapter.fail_closed_on_error is True


def test_environment_can_disable_fail_closed_only_explicitly(monkeypatch):
    monkeypatch.setenv("GUARDIAN_MODE", "active")
    monkeypatch.setenv("GUARDIAN_FAIL_CLOSED", "false")

    adapter = GuardianAdapter.from_environment()

    assert adapter.fail_closed_on_error is False


@pytest.mark.skipif(os.name == "nt", reason="Unix socket peer credentials are unavailable on Windows")
@pytest.mark.asyncio
async def test_unix_socket_adapter_rejects_oversized_response(tmp_path: Path):
    socket_path = str(tmp_path / "guardian.sock")

    async def handler(reader, writer):
        await reader.read(1024)
        writer.write(("{" + "x" * (16 * 1024) + "}\n").encode())
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_unix_server(handler, path=socket_path)
    try:
        # This fixture is a deliberately unauthenticated fake peer.  Disable
        # the production peer-credential gate so the test reaches the framed
        # response-size boundary it is intended to exercise.
        adapter = GuardianAdapter(
            socket_path=socket_path,
            mode="active",
            timeout_seconds=0.2,
            require_peer_credentials=False,
        )
        result = await adapter.evaluate(_event())
        assert result.decision == "unavailable"
        assert result.error_code in {"response_too_large", "protocol_error"}
    finally:
        server.close()
        await server.wait_closed()


def test_peer_credentials_are_required_when_allowlist_is_configured():
    from src.moderation.guardian_protocol import validate_peer_credentials

    with pytest.raises(GuardianProtocolError, match="peer_unauthorized"):
        validate_peer_credentials(uid=1000, gid=1000, allowed_uids={2000}, allowed_gids=None)


def test_protocol_error_codes_cannot_carry_payloads():
    error = GuardianProtocolError("token=super-secret https://private.invalid")

    assert error.code == "protocol_error"
    assert "secret" not in str(error)

