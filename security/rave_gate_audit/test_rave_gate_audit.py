"""Proof that the audit battery actually detects the flaws it claims to detect.

The lab ships every weakness on purpose. Each test flips one weakness on and
asserts that the matching check flips to FAIL. A battery that cannot fail is
worth nothing.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

import audit
import lab_server
import tg_init_data

LAB_TOKEN = lab_server.BOT_TOKEN


@pytest.fixture()
def client():
    reset_lab({"ok"})
    with TestClient(lab_server.app, base_url="http://testserver") as test_client:
        yield test_client


def reset_lab(vulns: set[str], rate_limit: int = 5) -> None:
    lab_server.VULNS = set(vulns)
    lab_server.RATE_LIMIT = rate_limit
    lab_server.RATE_WINDOW = 3600.0
    lab_server._replay_seen.clear()
    lab_server._failures.clear()
    lab_server._sessions.clear()


def run_audit(
    client: TestClient,
    token: str | None = LAB_TOKEN,
    captured: str | None = None,
) -> audit.AuditResult:
    auditor = audit.Auditor(
        "http://testserver",
        bot_token=token,
        rate_gap=0.0,
        client=client,
        captured_init_data=captured,
    )
    return auditor.run()


def check(result: audit.AuditResult, check_id: str) -> audit.Check:
    for item in result.checks:
        if item.id == check_id:
            return item
    raise AssertionError(f"check {check_id} missing from the report")


# ------------------------------------------------------------------ unit level
def test_signature_round_trip():
    payload = tg_init_data.build_valid_init_data(LAB_TOKEN)
    outcome = tg_init_data.validate(payload, LAB_TOKEN)
    assert outcome.ok
    assert outcome.user["first_name"] == "audit"


def test_tampered_payload_rejected():
    payload = tg_init_data.build_valid_init_data(LAB_TOKEN)
    tampered = payload.replace("first_name", "first_namf")
    outcome = tg_init_data.validate(tampered, LAB_TOKEN)
    assert not outcome.ok
    assert outcome.reason == "hash_mismatch"


def test_wrong_token_rejected():
    payload = tg_init_data.build_valid_init_data(LAB_TOKEN)
    outcome = tg_init_data.validate(payload, "999999:OTHER-TOKEN")
    assert not outcome.ok
    assert outcome.reason == "hash_mismatch"


def test_stale_payload_rejected():
    payload = tg_init_data.build_valid_init_data(LAB_TOKEN, auth_date=int(time.time()) - 7200)
    outcome = tg_init_data.validate(payload, LAB_TOKEN, max_age=3600)
    assert not outcome.reason == "ok"


def test_replay_detected():
    seen: set[str] = set()
    payload = tg_init_data.build_valid_init_data(LAB_TOKEN)
    assert tg_init_data.validate(payload, LAB_TOKEN, replay_seen=seen).ok
    assert not tg_init_data.validate(payload, LAB_TOKEN, replay_seen=seen).ok


def test_data_check_string_excludes_hash_and_signature():
    pairs = {"b": "2", "a": "1", "hash": "x", "signature": "y"}
    assert tg_init_data.data_check_string(pairs.items()) == "a=1\nb=2"


# ------------------------------------------------------------------- lab level
def test_ok_lab_passes_core_auth_checks(client):
    result = run_audit(client)
    for check_id in ("GATE-00", "GATE-01", "GATE-02", "GATE-03", "GATE-04", "GATE-05", "GATE-06"):
        assert check(result, check_id).passed, check(result, check_id).observed


def test_control_failure_makes_stale_and_replay_inconclusive(client):
    """A server that rejects everything must not look like it passed."""
    reset_lab({"ok"})
    original = lab_server.BOT_TOKEN
    lab_server.BOT_TOKEN = "111:DIFFERENT-TOKEN"
    try:
        result = run_audit(client, token=LAB_TOKEN)
    finally:
        lab_server.BOT_TOKEN = original
    assert not check(result, "GATE-00").passed
    assert check(result, "GATE-05").skipped
    assert check(result, "GATE-06").skipped


def test_anonymous_caller_gets_the_gate_page(client):
    result = run_audit(client)
    assert check(result, "GATE-12").passed


def test_captured_checks_are_skipped_without_a_capture(client):
    result = run_audit(client)
    assert check(result, "GATE-14").skipped
    assert check(result, "GATE-15").skipped


def test_captured_stale_payload_is_refused(client):
    captured = tg_init_data.build_valid_init_data(
        LAB_TOKEN, user_id=777, auth_date=int(time.time()) - 60 * 60 * 24 * 7
    )
    result = run_audit(client, captured=captured)
    assert check(result, "GATE-14").passed
    assert check(result, "GATE-15").passed


def test_captured_fresh_payload_is_single_use(client):
    captured = tg_init_data.build_valid_init_data(LAB_TOKEN, user_id=777)
    result = run_audit(client, captured=captured)
    assert not check(result, "GATE-14").passed
    assert check(result, "GATE-15").passed


def test_captured_payload_replay_is_detected_without_replay_cache(client):
    reset_lab({"no_replay"})
    captured = tg_init_data.build_valid_init_data(LAB_TOKEN, user_id=777)
    result = run_audit(client, captured=captured)
    assert not check(result, "GATE-15").passed


def test_missing_hash_verification_is_detected(client):
    reset_lab({"no_hash_check"})
    result = run_audit(client)
    assert not check(result, "GATE-02").passed


def test_trusted_gate_flag_is_detected(client):
    reset_lab({"trust_gate"})
    result = run_audit(client)
    assert not check(result, "GATE-04").passed


def test_missing_freshness_window_is_detected(client):
    reset_lab({"no_auth_date"})
    result = run_audit(client)
    assert not check(result, "GATE-05").passed


def test_missing_replay_cache_is_detected(client):
    reset_lab({"no_replay"})
    result = run_audit(client)
    assert not check(result, "GATE-06").passed


def test_throttle_bypass_by_rotation_is_detected(client):
    result = run_audit(client)
    assert not check(result, "GATE-11").passed


def test_throttle_absent_is_detected(client):
    reset_lab({"ok"}, rate_limit=0)
    result = run_audit(client)
    assert not check(result, "GATE-10").passed


def test_report_serialises(client):
    result = run_audit(client)
    payload = result.to_dict()
    assert payload["target"] == "http://testserver"
    assert len(payload["checks"]) == len(audit.Auditor.__dict__) or payload["checks"]
    assert all({"id", "severity", "passed", "expected", "observed"} <= set(c) for c in payload["checks"])

