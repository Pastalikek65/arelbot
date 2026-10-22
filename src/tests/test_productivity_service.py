from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from src.services.productivity_service import ProductivityService
from src.services.reminder_service import ReminderService
from src.handlers.callback_handler import CallbackHandler
from src.handlers.command_handler import CommandHandler


def test_reminder_parser_accepts_turkish_absolute_datetime():
    service = ReminderService()
    now = datetime(2026, 10, 5, 8, 0, tzinfo=timezone.utc)

    request = service.parse(
        ["yarın", "10:30", "Doktor", "randevusu"],
        now=now,
        timezone_name="Europe/Istanbul",
    )

    expected = datetime(2026, 10, 6, 10, 30, tzinfo=ZoneInfo("Europe/Istanbul"))
    assert request.due_at == expected.astimezone(timezone.utc)
    assert request.human_delay == "yarın 10:30"
    assert request.text == "Doktor randevusu"


def test_reminder_parser_accepts_turkish_duration_words():
    service = ReminderService()
    now = datetime(2026, 10, 5, 8, 0, tzinfo=timezone.utc)

    daily = service.parse(["1g\u00fcn", "Su iç"], now=now)
    assert daily.delay_seconds == 24 * 60 * 60

    weekly = service.parse(["1haftal\u0131k", "Haftalık plan"], now=now)
    assert weekly.delay_seconds == 7 * 24 * 60 * 60


def test_reminder_parser_accepts_weekday_and_numeric_date():
    service = ReminderService()
    now = datetime(2026, 10, 5, 8, 0, tzinfo=timezone.utc)

    monday = service.parse(
        ["pazartesi", "09:00", "Haftalık", "plan"],
        now=now,
        timezone_name="Europe/Istanbul",
    )
    assert monday.due_at > now
    assert monday.text == "Haftalık plan"

    dated = service.parse(
        ["10.10.2026", "14:15", "Proje", "teslimi"],
        now=now,
        timezone_name="Europe/Istanbul",
    )
    expected = datetime(2026, 10, 10, 14, 15, tzinfo=ZoneInfo("Europe/Istanbul"))
    assert dated.due_at == expected.astimezone(timezone.utc)


def test_reminder_parser_rejects_past_and_invalid_absolute_datetime():
    service = ReminderService()
    now = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="geçmiş"):
        service.parse(["bugün", "10:00", "Geçmiş"], now=now)

    with pytest.raises(ValueError, match="tarih"):
        service.parse(["yarın", "25:00", "Geçersiz"], now=now)


def test_reminder_parser_supports_daily_and_weekly_recurrence():
    service = ReminderService()
    now = datetime(2026, 10, 5, 8, 0, tzinfo=timezone.utc)

    daily = service.parse(
        ["her", "gün", "10:30", "Günlük", "plan"],
        now=now,
        timezone_name="Europe/Istanbul",
    )
    assert daily.recurrence == {"kind": "daily"}
    assert daily.text == "Günlük plan"

    weekly = service.parse(
        ["her", "pazartesi", "09:00", "Haftalık", "plan"],
        now=now,
        timezone_name="Europe/Istanbul",
    )
    assert weekly.recurrence == {"kind": "weekly", "weekday": 0}
    assert weekly.due_at > now


@pytest.mark.asyncio
async def test_productivity_service_reschedules_recurring_reminder_without_marking_sent():
    service = ProductivityService(db=None)
    due = datetime(2026, 10, 5, 7, 30, tzinfo=timezone.utc)
    reminder = await service.add_reminder(
        user_id=7,
        chat_id=7,
        text="Günlük plan",
        due_at=due,
        recurrence={"kind": "daily"},
        timezone_name="Europe/Istanbul",
    )
    next_due = due + timedelta(days=1)
    assert await service.reschedule_reminder(reminder["reminder_id"], next_due) is True
    saved = service._reminders[reminder["reminder_id"]]
    assert saved["sent"] is False
    assert saved["due_at"] == next_due
    assert saved["recurrence"] == {"kind": "daily"}


def test_next_occurrence_preserves_local_time_across_daily_and_weekly_rules():
    service = ReminderService()
    due = datetime(2026, 10, 5, 7, 30, tzinfo=timezone.utc)

    daily = service.next_occurrence(due, {"kind": "daily"}, timezone_name="Europe/Istanbul")
    weekly = service.next_occurrence(
        due,
        {"kind": "weekly", "weekday": 0},
        timezone_name="Europe/Istanbul",
    )

    assert daily == due + timedelta(days=1)
    assert weekly == due + timedelta(days=7)


@pytest.mark.asyncio
async def test_productivity_export_and_bulk_delete_are_user_scoped():
    service = ProductivityService(db=None)
    await service.add_note(1, 1, "Birinci not")
    await service.add_note(2, 2, "Başka kullanıcı")
    await service.add_task(1, 1, "Birinci görev")
    reminder = await service.add_reminder(1, 1, "Birinci hatırlatıcı", datetime.now(timezone.utc))

    exported = await service.export_user_data(1)
    assert len(exported["notes"]) == 1
    assert len(exported["tasks"]) == 1
    assert len(exported["reminders"]) == 1

    deleted = await service.clear_user_data(1, sections={"notes", "tasks", "reminders"})
    assert deleted == {"notes": 1, "tasks": 1, "reminders": 1}
    assert await service.list_notes(1) == []
    assert await service.list_tasks(1) == []
    assert await service.list_reminders(1) == []
    assert await service.list_notes(2)
    assert reminder["reminder_id"] not in service._reminders


@pytest.mark.asyncio
async def test_reminder_command_escapes_user_text_before_html_delivery():
    replies = []

    class Message:
        async def reply_text(self, text, **kwargs):
            replies.append((text, kwargs))

    class JobQueue:
        def run_once(self, *args, **kwargs):
            return object()

    productivity = ProductivityService(db=None)
    handler = CommandHandler(
        chat_engine=SimpleNamespace(),
        sudo_manager=None,
        image_router=None,
        tool_manager=None,
        reminder_service=ReminderService(),
        productivity_service=productivity,
    )
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42),
        effective_chat=SimpleNamespace(id=42, type="private"),
        message=Message(),
    )
    context = SimpleNamespace(args=["10dk", "<b>gizli</b>"], job_queue=JobQueue())

    await handler.handle_remind_command(update, context)

    assert replies
    assert "&lt;b&gt;gizli&lt;/b&gt;" in replies[0][0]
    assert "<b>gizli</b>" not in replies[0][0]


@pytest.mark.asyncio
async def test_reminder_claim_is_atomic_until_lease_expires():
    service = ProductivityService(db=None)
    reminder = await service.add_reminder(
        user_id=7,
        chat_id=-100,
        text="Toplantı",
        due_at=datetime.now(timezone.utc) + timedelta(minutes=1),
    )

    assert await service.claim_reminder(reminder["reminder_id"]) is True
    assert await service.claim_reminder(reminder["reminder_id"]) is False

    # Simulate a worker crash after claim.  A later worker may reclaim the
    # lease, while a still-live claim remains protected from duplicate sends.
    service._reminders[reminder["reminder_id"]]["claimed_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=6)
    )
    assert await service.claim_reminder(reminder["reminder_id"], claim_timeout_seconds=300) is True


@pytest.mark.asyncio
async def test_reminder_delivery_clears_lease_and_release_is_retryable():
    service = ProductivityService(db=None)
    reminder = await service.add_reminder(
        user_id=8,
        chat_id=-101,
        text="Su iç",
        due_at=datetime.now(timezone.utc),
    )
    reminder_id = reminder["reminder_id"]

    assert await service.claim_reminder(reminder_id) is True
    await service.release_reminder(reminder_id)
    assert await service.claim_reminder(reminder_id) is True

    await service.mark_reminder_sent(reminder_id)
    assert service._reminders[reminder_id]["sent"] is True
    assert "claimed_at" not in service._reminders[reminder_id]
    assert await service.claim_reminder(reminder_id) is False


@pytest.mark.asyncio
async def test_productivity_delete_operations_are_user_scoped():
    service = ProductivityService(db=None)
    note = await service.add_note(1, 1, "Özel not")
    task = await service.add_task(1, 1, "Sunum")
    reminder = await service.add_reminder(1, 1, "Ara", datetime.now(timezone.utc))

    assert await service.delete_note(2, note["note_id"]) is False
    assert await service.delete_task(2, task["task_id"]) is False
    assert await service.delete_reminder(2, reminder["reminder_id"]) is False
    assert await service.delete_note(1, note["note_id"]) is True
    assert await service.delete_task(1, task["task_id"]) is True
    assert await service.delete_reminder(1, reminder["reminder_id"]) is True


@pytest.mark.asyncio
async def test_productivity_callback_rejects_other_user_and_completes_owned_task():
    service = ProductivityService(db=None)
    task = await service.add_task(1, 1, "Sunum")
    handler = CallbackHandler(None, None, None, productivity_service=service)
    query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())

    other_user = SimpleNamespace(effective_user=SimpleNamespace(id=2))
    await handler._handle_productivity_callback(
        other_user, query, f"prod:td:1:{task['task_id']}"
    )
    query.answer.assert_awaited_once()
    assert service._tasks[task["task_id"]]["completed"] is False

    query.answer.reset_mock()
    owner = SimpleNamespace(effective_user=SimpleNamespace(id=1))
    await handler._handle_productivity_callback(
        owner, query, f"prod:td:1:{task['task_id']}"
    )
    assert service._tasks[task["task_id"]]["completed"] is True
    query.edit_message_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_productivity_callback_requires_confirmation_for_bulk_clear():
    service = ProductivityService(db=None)
    await service.add_note(1, 1, "Silinecek not")
    handler = CallbackHandler(None, None, None, productivity_service=service)
    query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())
    owner = SimpleNamespace(effective_user=SimpleNamespace(id=1))

    await handler._handle_productivity_callback(owner, query, "prod:confirmclear:1:notes")
    query.edit_message_text.assert_awaited_once()
    assert await service.list_notes(1)

    query.edit_message_text.reset_mock()
    await handler._handle_productivity_callback(owner, query, "prod:clear:1:notes")
    assert await service.list_notes(1) == []
    query.edit_message_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_daily_summary_splits_overdue_and_local_window():
    service = ProductivityService(db=None)
    now = datetime(2026, 10, 5, 8, 0, tzinfo=timezone.utc)
    await service.add_task(1, 1, "Bugünün işi", priority="high")
    await service.add_reminder(1, 1, "Geciken", now - timedelta(hours=1))
    await service.add_reminder(1, 1, "Bugünkü toplantı", now + timedelta(hours=2))
    await service.add_reminder(1, 1, "Gelecek hafta", now + timedelta(days=8))

    summary = await service.daily_summary(1, days=1, timezone_name="Europe/Istanbul", now=now)

    assert summary["timezone"] == "Europe/Istanbul"
    assert summary["days"] == 1
    assert summary["open_task_count"] == 1
    assert summary["overdue_count"] == 1
    assert summary["upcoming_count"] == 1
    assert summary["overdue_reminders"][0]["text"] == "Geciken"
    assert summary["upcoming_reminders"][0]["text"] == "Bugünkü toplantı"


@pytest.mark.asyncio
async def test_daily_summary_counts_records_beyond_display_limit():
    service = ProductivityService(db=None)
    for index in range(27):
        await service.add_task(1, 1, f"Görev {index}")
    now = datetime(2026, 10, 5, 8, 0, tzinfo=timezone.utc)

    summary = await service.daily_summary(1, now=now, limit=5)

    assert len(summary["tasks"]) == 5
    assert summary["open_task_count"] == 27
    assert summary["tasks_truncated"] is True


@pytest.mark.asyncio
async def test_daily_summary_week_view_uses_seven_day_window_and_falls_back_timezone():
    service = ProductivityService(db=None)
    now = datetime(2026, 10, 5, 8, 0, tzinfo=timezone.utc)
    await service.add_reminder(1, 1, "Haftalık iş", now + timedelta(days=6))
    await service.add_reminder(1, 1, "Pencere dışı", now + timedelta(days=8))

    summary = await service.daily_summary(1, days=7, timezone_name="Invalid/Zone", now=now)

    assert summary["timezone"] == "Europe/Istanbul"
    assert summary["days"] == 7
    assert summary["upcoming_count"] == 1
    assert summary["upcoming_reminders"][0]["text"] == "Haftalık iş"


@pytest.mark.asyncio
async def test_plan_command_renders_owner_scoped_action_buttons():
    service = ProductivityService(db=None)
    task = await service.add_task(1, 1, "Sunumu tamamla")
    await service.add_reminder(1, 1, "Toplantı", datetime.now(timezone.utc) + timedelta(minutes=10))
    replies = []

    class Message:
        async def reply_text(self, text, **kwargs):
            replies.append((text, kwargs))

    handler = CommandHandler(
        chat_engine=SimpleNamespace(),
        sudo_manager=None,
        image_router=None,
        tool_manager=None,
        productivity_service=service,
    )
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1),
        effective_chat=SimpleNamespace(id=1, type="private"),
        effective_message=Message(),
    )
    await handler.handle_plan_command(update, SimpleNamespace(args=[]))

    assert replies and "Açık görevler" in replies[0][0]
    callbacks = [
        button.callback_data
        for row in replies[0][1]["reply_markup"].inline_keyboard
        for button in row
    ]
    assert f"prod:td:1:{task['task_id']}" in callbacks


@pytest.mark.asyncio
async def test_plan_command_refuses_to_disclose_personal_data_in_groups():
    class PrivateDataMustNotBeRead:
        async def daily_summary(self, *args, **kwargs):
            raise AssertionError("group command must not read private productivity data")

    replies = []

    class Message:
        async def reply_text(self, text, **kwargs):
            replies.append(text)

    handler = CommandHandler(
        chat_engine=SimpleNamespace(),
        sudo_manager=None,
        image_router=None,
        tool_manager=None,
        productivity_service=PrivateDataMustNotBeRead(),
    )
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1),
        effective_chat=SimpleNamespace(id=-100, type="supergroup"),
        effective_message=Message(),
    )

    await handler.handle_plan_command(update, SimpleNamespace(args=[]))

    assert replies and "yalnızca özel sohbetinde" in replies[0]
