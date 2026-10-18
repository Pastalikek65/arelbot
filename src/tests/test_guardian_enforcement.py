from datetime import datetime, timedelta, timezone

import pytest

from src.moderation.guardian_enforcement import (
    build_guardian_generation_id,
    evaluate_guardian_message,
    new_guardian_challenge_nonce,
    validate_captcha_challenge,
)


def test_global_guardian_ban_is_checked_before_attachment_policy():
    result = evaluate_guardian_message(
        attachment_type="document",
        settings={},
        user={"guardian_gban": {"enabled": True}},
    )
    assert result.allowed is False
    assert result.reason == "guardian_gban"
    assert result.action == "ban"


def test_group_media_lock_is_bounded_and_admins_are_exempt():
    result = evaluate_guardian_message(
        attachment_type="animation",
        settings={"locked_types": ["gif"], "lock_mode": "mute"},
        user={},
    )
    assert (result.allowed, result.action) == (False, "mute")
    assert evaluate_guardian_message(
        attachment_type="animation", settings={"locked_types": ["gif"]}, user={}, is_admin=True
    ).allowed is True


def test_captcha_validation_requires_scope_and_current_timestamp():
    now = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
    challenge = {
        "chat_id": -100123,
        "user_id": 42,
        "type": "text",
        "answer": "  Merhaba  ",
        "created_at": now.isoformat(),
    }

    valid = validate_captcha_challenge(
        challenge,
        chat_id=-100123,
        user_id=42,
        now=now + timedelta(seconds=30),
        supplied_answer="merhaba",
    )
    assert valid.valid is True
    assert valid.reason == "valid"

    assert validate_captcha_challenge(
        challenge, chat_id=-100999, user_id=42, now=now
    ).reason == "wrong_chat"
    assert validate_captcha_challenge(
        challenge, chat_id=-100123, user_id=99, now=now
    ).reason == "wrong_user"


@pytest.mark.parametrize(
    ("challenge", "reason"),
    [
        ({"type": "text", "answer": "ok", "created_at": "not-a-date"}, "created_at_invalid"),
        ({"type": "text", "answer": "ok", "created_at": "2000-01-01T00:00:00Z"}, "expired"),
        ({"type": "unknown", "created_at": "2026-10-17T12:00:00Z"}, "type_invalid"),
        ({"type": "text", "answer": "ok", "created_at": "2026-10-17T12:01:00Z"}, "created_at_future"),
    ],
)
def test_captcha_validation_rejects_stale_or_malformed_state(challenge, reason):
    now = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
    assert validate_captcha_challenge(
        challenge,
        chat_id=-100123,
        user_id=42,
        now=now,
    ).reason == reason


def test_captcha_answer_mismatch_and_consumed_state_are_rejected():
    now = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
    challenge = {"type": "math", "answer": "15", "created_at": now.isoformat()}
    assert validate_captcha_challenge(
        challenge,
        chat_id=-100123,
        user_id=42,
        now=now,
        supplied_answer="14",
    ).reason == "answer_mismatch"
    challenge["consumed"] = True
    assert validate_captcha_challenge(challenge, chat_id=-100123, user_id=42, now=now).reason == "consumed"


def test_guardian_generation_id_is_stable_per_challenge_and_unique_per_rejoin():
    first = build_guardian_generation_id("antiraid", chat_id=-100123, user_id=42, nonce="join-a")
    retry = build_guardian_generation_id("antiraid", chat_id=-100123, user_id=42, nonce="join-a")
    second_join = build_guardian_generation_id("antiraid", chat_id=-100123, user_id=42, nonce="join-b")

    assert first == retry
    assert first != second_join
    assert first.startswith("guardian:antiraid:")
    with pytest.raises(ValueError, match="generation_nonce_required"):
        build_guardian_generation_id("antiraid", chat_id=-100123, user_id=42, nonce="")


def test_new_guardian_challenge_nonce_is_non_empty_and_changes():
    first = new_guardian_challenge_nonce()
    second = new_guardian_challenge_nonce()
    assert first and second and first != second
