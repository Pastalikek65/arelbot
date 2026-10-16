from datetime import datetime, timedelta, timezone
import json

import pytest

from src.group_replies import (
    GroupMessageRef,
    GroupReplyPlan,
    GroupReplyRouter,
    GroupTurnContext,
)


UTC = timezone.utc


def message(
    message_id: int,
    user_id: int,
    *,
    text: str = "mesaj",
    seconds_ago: int = 0,
    chat_id: int = -100,
    thread_id: int | None = None,
    display_name: str = "KullanÄ±cÄ±",
    message_kind: str = "text",
    already_replied: bool = False,
) -> GroupMessageRef:
    now = datetime.now(UTC)
    return GroupMessageRef(
        chat_id=chat_id,
        thread_id=thread_id,
        message_id=message_id,
        user_id=user_id,
        display_name=display_name,
        text=text,
        created_at=now - timedelta(seconds=seconds_ago),
        message_kind=message_kind,
        already_replied=already_replied,
    )


def context(current: GroupMessageRef, *candidates: GroupMessageRef, **kwargs) -> GroupTurnContext:
    return GroupTurnContext(
        current_message=current,
        candidates=(current, *candidates),
        trigger_kind="mention",
        **kwargs,
    )


def test_candidate_window_is_scoped_and_latest_message_per_user_is_selected():
    current = message(99, 8, seconds_ago=0, display_name="Tetikleyen")
    newest_alice = message(20, 1, seconds_ago=20, display_name="AyÅŸe")
    old_alice = message(19, 1, seconds_ago=30, display_name="AyÅŸe")
    wrong_thread = message(30, 2, seconds_ago=10, thread_id=4, display_name="Kemal")
    stale = message(40, 3, seconds_ago=301, display_name="Deniz")
    command = message(50, 4, seconds_ago=5, display_name="Ece", message_kind="command")
    other_chat = message(60, 5, seconds_ago=5, chat_id=-200, display_name="FÄ±rat")
    ctx = context(current, newest_alice, old_alice, wrong_thread, stale, command, other_chat)

    selected = GroupReplyRouter().select_candidates(ctx)

    assert [item.message_id for item in selected] == [99, 20]


def test_candidate_selection_is_bounded_to_three_users_and_is_deterministic():
    current = message(100, 9, display_name="Tetikleyen")
    candidates = [message(10 + i, i, seconds_ago=i + 1, display_name=f"U{i}") for i in range(1, 8)]
    selected = GroupReplyRouter().select_candidates(context(current, *candidates))

    assert len(selected) == 3
    assert [item.user_id for item in selected] == [9, 1, 2]


def test_valid_structured_response_creates_individual_reply_segments():
    current = message(101, 1, display_name="AyÅŸe")
    kemal = message(202, 2, seconds_ago=10, display_name="Kemal")
    ctx = context(current, kemal)

    plan = GroupReplyRouter().build_plan(
        ctx,
        json.dumps({"replies": [
            {"target_message_id": 101, "text": "iyiyim ya"},
            {"target_message_id": 202, "text": "sana da selam"},
        ]}),
    )

    assert isinstance(plan, GroupReplyPlan)
    assert plan.rejected_reason is None
    assert [(segment.target_message.message_id, segment.text) for segment in plan.segments] == [
        (101, "iyiyim ya"),
        (202, "sana da selam"),
    ]


@pytest.mark.parametrize(
    "payload,reason",
    [
        ({"replies": [{"target_message_id": 999, "text": "selam"}]}, "unknown_target"),
        ({"replies": [{"target_message_id": 101, "text": "bir"}, {"target_message_id": 101, "text": "iki"}]}, "duplicate_target"),
        ({"replies": [{"target_message_id": 101, "text": "AyÅŸe: selam\nKemal: nasÄ±lsÄ±n"}]}, "multi_address_prose"),
        ({"replies": [{"target_message_id": 101, "text": ""}]}, "malformed_response"),
    ],
)
def test_invalid_model_plans_are_rejected(payload, reason):
    current = message(101, 1, display_name="AyÅŸe")
    kemal = message(202, 2, seconds_ago=10, display_name="Kemal")
    plan = GroupReplyRouter().build_plan(context(current, kemal), json.dumps(payload))

    assert plan.segments == ()
    assert plan.rejected_reason == reason


def test_invalid_plan_falls_back_only_to_current_trigger_when_safe_text_is_given():
    current = message(101, 1, display_name="AyÅŸe")
    ctx = context(current)

    plan = GroupReplyRouter().build_plan(ctx, "not json", safe_fallback_text="ÅŸu an buradayÄ±m")

    assert [(part.target_message.message_id, part.text) for part in plan.segments] == [(101, "ÅŸu an buradayÄ±m")]
    assert plan.rejected_reason == "malformed_json"

    no_fallback = GroupReplyRouter().build_plan(ctx, "not json")
    assert no_fallback.segments == ()


def test_fallback_with_addressing_is_not_considered_safe():
    current = message(101, 1, display_name="AyÅŸe")
    plan = GroupReplyRouter().build_plan(
        context(current),
        "not json",
        safe_fallback_text="AyÅŸe: sana cevap veriyorum",
    )
    assert plan.segments == ()


def test_stale_current_message_cannot_be_used_as_target():
    current = message(101, 1, seconds_ago=301, display_name="AyÅŸe")
    plan = GroupReplyRouter().build_plan(
        context(current),
        json.dumps({"replies": [{"target_message_id": 101, "text": "selam"}]}),
    )
    assert plan.segments == ()
    assert plan.rejected_reason == "current_target_not_eligible"


def test_router_emits_bounded_metric_names_without_user_data():
    class Metrics:
        def __init__(self):
            self.events = []

        def increment(self, name: str, value: int = 1):
            self.events.append((name, value))

    metrics = Metrics()
    current = message(101, 1)
    GroupReplyRouter(metrics=metrics).build_plan(context(current), "not json")

    assert metrics.events
    assert all(name.startswith("group_reply_") for name, _ in metrics.events)
    assert all("101" not in name for name, _ in metrics.events)


def test_router_works_with_production_metrics_registry():
    from src.observability.metrics import MetricsRegistry

    metrics = MetricsRegistry()
    current = message(101, 1)
    GroupReplyRouter(metrics=metrics).build_plan(context(current), "not json")

    assert metrics.counters["group_reply_requests_total"] == 1
    assert metrics.counters["group_reply_plan_rejected"] == 1
