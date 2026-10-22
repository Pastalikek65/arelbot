from __future__ import annotations

import pytest

from src.moderation import ModerationEngine, ModerationService
from src.moderation.contracts import ModerationEvent


class FakeModerationDB:
    def __init__(self, count=0):
        self.count = count
        self.events = set()
        self.audits = []

    async def get_moderation_summary(self, chat_id, *, since, limit):
        return {
            "total": 4,
            "active_users": 2,
            "by_reason": [{"reason": "flood", "count": 3}, {"reason": "keyword_filter", "count": 1}],
            "top_users": [{"user_id": 20, "name": "<admin?&>", "count": 3}],
        }

    async def clear_moderation_strikes(self, chat_id, user_id):
        return 2

    async def record_moderation_violation(self, **payload):
        event_id = payload["event_id"]
        if event_id not in self.events:
            self.events.add(event_id)
            self.count += 1
        return self.count

    async def add_audit_log(self, actor_id, action, **data):
        self.audits.append((actor_id, action, data))


@pytest.mark.asyncio
async def test_moderation_service_persists_idempotent_strikes_and_escalates():
    db = FakeModerationDB(count=2)
    service = ModerationService(db, ModerationEngine(window_seconds=10))
    settings = {"moderation_enabled": True, "flood_limit": 0, "auto_ban": True}

    first = await service.evaluate(10, 20, "one", settings, event_id="m-1")
    duplicate = await service.evaluate(10, 20, "two", settings, event_id="m-1")

    assert first.action == "ban"
    assert first.strike_count == 3
    assert first.persistent is True
    assert duplicate.strike_count == 3
    assert len(db.events) == 1
    assert db.audits and db.audits[-1][1] == "moderation_violation"
    assert "text" not in db.audits[-1][2]


@pytest.mark.asyncio
async def test_moderation_service_recovers_persistent_strikes_after_engine_restart():
    db = FakeModerationDB(count=3)
    service = ModerationService(db, ModerationEngine(window_seconds=10))
    decision = await service.evaluate(
        10,
        20,
        "new flood",
        {"moderation_enabled": True, "flood_limit": 99, "auto_ban": True},
        event_id="m-recovered",
    )

    assert decision.allowed is True  # no new flood violation to persist
    assert decision.action == "none"

    db.count = 3
    service = ModerationService(db, ModerationEngine(window_seconds=10))
    decision = await service.evaluate(
        10,
        20,
        "one",
        {"moderation_enabled": True, "flood_limit": 0, "auto_ban": True},
        event_id="m-new",
    )
    assert decision.action == "ban"
    assert decision.strike_count == 4


@pytest.mark.asyncio
async def test_admin_decision_does_not_persist_personal_violation():
    db = FakeModerationDB()
    service = ModerationService(db)
    decision = await service.evaluate(
        10,
        20,
        "http://example.test",
        {"moderation_enabled": True, "link_filter": True},
        is_admin=True,
        event_id="admin-1",
    )

    assert decision.allowed is True
    assert db.events == set()
    assert db.audits == []


@pytest.mark.asyncio
async def test_moderation_summary_is_bounded_and_escapes_admin_report():
    db = FakeModerationDB()
    service = ModerationService(db)
    rendered = await service.render_summary(-100, days=7)

    assert "İhlal: <b>4</b>" in rendered
    assert "&lt;admin?&amp;&gt;" in rendered
    assert "mesaj" not in rendered.casefold()


@pytest.mark.asyncio
async def test_moderation_strike_clear_is_audited():
    db = FakeModerationDB()
    service = ModerationService(db)
    deleted = await service.clear_strikes(-100, 20, actor_id=1)

    assert deleted == 2
    assert db.audits[-1][1] == "moderation_strikes_cleared"
    assert db.audits[-1][2]["chat_id"] == -100


@pytest.mark.asyncio
async def test_moderation_service_evaluates_attachment_event_caption():
    db = FakeModerationDB()
    service = ModerationService(db, ModerationEngine(window_seconds=10))
    event = ModerationEvent(
        chat_id=-100,
        message_id=44,
        user_id=7,
        event_kind="animation",
        caption="https://bad.example",
        attachment_type="animation",
    )
    decision = await service.evaluate_event(
        event,
        {"moderation_enabled": True, "link_filter": True},
    )
    assert decision.allowed is False
    assert decision.reason == "link_filter"


@pytest.mark.asyncio
async def test_moderation_service_uses_active_guardian_deny_before_local_policy():
    class Guardian:
        async def evaluate(self, event):
            return type("GuardianResult", (), {
                "decision": "deny",
                "enforce": True,
                "actions": ("mute",),
                "reason_code": "guardian_rule",
            })()

    service = ModerationService(FakeModerationDB(), guardian_adapter=Guardian())
    event = ModerationEvent(chat_id=-100, message_id=45, user_id=7, event_kind="text", text="normal")
    decision = await service.evaluate_event(event, {"moderation_enabled": True})
    assert decision.allowed is False
    assert decision.action == "mute"
    assert decision.reason == "guardian_rule"


@pytest.mark.asyncio
async def test_moderation_service_fails_closed_on_guardian_unavailable_quarantine():
    class Guardian:
        async def evaluate(self, event):
            return type("GuardianResult", (), {
                "decision": "unavailable",
                "enforce": True,
                "enforcement_required": True,
                "actions": ("quarantine",),
                "reason_code": "guardian_unavailable",
            })()

    service = ModerationService(FakeModerationDB(), guardian_adapter=Guardian())
    event = ModerationEvent(chat_id=-100, message_id=46, user_id=7, event_kind="text", text="normal")
    decision = await service.evaluate_event(event, {"moderation_enabled": True})
    assert decision.allowed is False
    assert decision.action == "mute"
    assert decision.reason == "guardian_unavailable"
