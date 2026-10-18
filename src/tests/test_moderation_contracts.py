from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.moderation.contracts import (
    ModerationAction,
    ModerationDecision,
    ModerationEvent,
)


def test_moderation_event_keeps_bounded_telegram_identity_and_payload():
    event = ModerationEvent(
        chat_id=-100,
        thread_id=7,
        message_id=42,
        user_id=99,
        event_kind="message",
        text="merhaba",
        created_at=datetime.now(timezone.utc),
    )

    assert event.chat_id == -100
    assert event.message_id == 42
    assert event.text == "merhaba"
    assert len(event.correlation_id) <= 128


def test_moderation_event_rejects_unbounded_text():
    with pytest.raises(ValueError, match="text_too_long"):
        ModerationEvent(
            chat_id=-100,
            message_id=42,
            user_id=99,
            event_kind="message",
            text="x" * 4097,
        )


def test_moderation_decision_normalizes_rule_ids_and_action():
    decision = ModerationDecision(
        allowed=False,
        action="mute",
        reason="flood",
        rule_ids=["flood", "flood"],
        severity=4,
    )

    assert decision.rule_ids == ("flood",)
    assert decision.severity == 4


def test_moderation_action_derives_stable_idempotency_key():
    first = ModerationAction(
        action="delete_message",
        chat_id=-100,
        actor_id=7,
        target_message_id=42,
        reason="link_filter",
    )
    second = ModerationAction(
        action="delete_message",
        chat_id=-100,
        actor_id=7,
        target_message_id=42,
        reason="link_filter",
    )

    assert first.idempotency_key == second.idempotency_key
    assert len(first.idempotency_key) <= 128


def test_moderation_action_requires_target_for_destructive_action():
    with pytest.raises(ValueError, match="target_message_required"):
        ModerationAction(action="delete_message", chat_id=-100, actor_id=7)


def test_moderation_action_accepts_legacy_delete_decision_name():
    action = ModerationAction(
        action="delete",
        chat_id=-100,
        actor_id=7,
        target_message_id=42,
    )

    assert action.action == "delete_message"


def test_manual_action_generation_can_be_retried_as_a_new_command():
    first = ModerationAction(
        action="ban", chat_id=-100, actor_id=7, target_user_id=42,
        reason="manual_group_ban", metadata={"generation_id": "update:1"},
    )
    second = ModerationAction(
        action="ban", chat_id=-100, actor_id=7, target_user_id=42,
        reason="manual_group_ban", metadata={"generation_id": "update:2"},
    )
    assert first.idempotency_key != second.idempotency_key
