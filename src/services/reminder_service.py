"""Telegram hatirlatmalarini zamanlayan ve sure metnini ayrıştıran servis."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class ReminderRequest:
    delay_seconds: int
    text: str
    human_delay: str
    due_at: datetime
    recurrence: dict[str, int | str] | None = None
    timezone_name: str = "Europe/Istanbul"


class ReminderService:
    # These spellings must be Unicode characters, not literal ``\u`` text;
    # otherwise ``/remind 1gün`` falls through to the absolute-date parser.
    DURATION_RE = re.compile(
        r"^(?P<num>\d+)\s*(?P<unit>sn|s|dk|m|sa|h|gün|gun|günlük|gunluk|d|hf|hafta|haftalık)$",
        re.IGNORECASE,
    )
    CLOCK_RE = re.compile(r"^(?P<hour>[01]?\d|2[0-3]):(?P<minute>[0-5]\d)$")
    DATE_FORMATS = ("%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d")
    WEEKDAYS = {
        "pazartesi": 0,
        "sal\u0131": 1,
        "sali": 1,
        "\u00e7ar\u015famba": 2,
        "carsamba": 2,
        "per\u015fembe": 3,
        "persembe": 3,
        "cuma": 4,
        "cumartesi": 5,
        "pazar": 6,
    }
    MAX_DELAY_SECONDS = 60 * 60 * 24 * 30

    def parse(
        self,
        args: list[str],
        *,
        now: datetime | None = None,
        timezone_name: str = "Europe/Istanbul",
    ) -> ReminderRequest:
        if not args:
            raise ValueError("Kullan\u0131m: /remind 10dk Su i\u00e7")
        raw_when = args[0].strip().lower()
        match = self.DURATION_RE.match(raw_when)
        now_utc = self._normalise_now(now)
        if raw_when in {"her", "every"}:
            due_at, human_delay, text, recurrence = self._parse_recurring(
                args, now_utc=now_utc, timezone_name=timezone_name
            )
            delay_seconds = max(1, int((due_at - now_utc).total_seconds()))
            return ReminderRequest(
                delay_seconds=delay_seconds,
                text=text[:1000],
                human_delay=human_delay,
                due_at=due_at,
                recurrence=recurrence,
                timezone_name=timezone_name,
            )
        if match:
            value = int(match.group("num"))
            unit = match.group("unit")
            multiplier = (
                1 if unit in {"sn", "s"}
                else 60 if unit in {"dk", "m"}
                else 3600 if unit in {"sa", "h"}
                else 604800 if unit in {"hf", "hafta", "haftalık"}
                else 86400
            )
            delay_seconds = value * multiplier
            self._validate_delay(delay_seconds)
            due_at = now_utc + timedelta(seconds=delay_seconds)
            text = " ".join(args[1:]).strip() or "Hat\u0131rlat\u0131c\u0131"
            human_delay = raw_when
        else:
            due_at, human_delay, text = self._parse_absolute(
                args, now_utc=now_utc, timezone_name=timezone_name
            )
            delay_seconds = max(1, int((due_at - now_utc).total_seconds()))
        return ReminderRequest(
            delay_seconds=delay_seconds,
            text=text[:1000],
            human_delay=human_delay,
            due_at=due_at,
            timezone_name=timezone_name,
        )

    @staticmethod
    def _normalise_now(now: datetime | None) -> datetime:
        value = now or datetime.now(timezone.utc)
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @classmethod
    def _validate_delay(cls, delay_seconds: float) -> None:
        if delay_seconds < 5:
            raise ValueError("Hat\u0131rlat\u0131c\u0131 en az 5 saniye sonras\u0131 i\u00e7in kurulabilir.")
        if delay_seconds > cls.MAX_DELAY_SECONDS:
            raise ValueError("Hat\u0131rlat\u0131c\u0131 en fazla 30 g\u00fcn sonras\u0131 i\u00e7in kurulabilir.")

    @classmethod
    def _parse_absolute(
        cls,
        args: list[str],
        *,
        now_utc: datetime,
        timezone_name: str,
    ) -> tuple[datetime, str, str]:
        if len(args) < 3:
            raise ValueError(
                "S\u00fcre yerine \"yar\u0131n 10:00\" veya \"10.10.2026 14:30\" yazabilirsin."
            )
        try:
            zone = ZoneInfo(timezone_name)
        except (KeyError, ValueError) as exc:
            raise ValueError("Ge\u00e7ersiz saat dilimi.") from exc

        raw_date = args[0].strip().casefold()
        raw_clock = args[1].strip()
        clock_match = cls.CLOCK_RE.match(raw_clock)
        if not clock_match:
            raise ValueError("Ge\u00e7ersiz tarih veya saat. Saat HH:MM bi\u00e7iminde olmal\u0131.")
        local_time = time(int(clock_match.group("hour")), int(clock_match.group("minute")))
        current_local = now_utc.astimezone(zone)

        if raw_date in {"bug\u00fcn", "bugun", "yar\u0131n", "yarin"}:
            offset = 1 if raw_date in {"yar\u0131n", "yarin"} else 0
            target_date = current_local.date() + timedelta(days=offset)
        elif raw_date in cls.WEEKDAYS:
            days = (cls.WEEKDAYS[raw_date] - current_local.weekday()) % 7
            target_date = current_local.date() + timedelta(days=days)
        else:
            target_date = None
            for fmt in cls.DATE_FORMATS:
                try:
                    target_date = datetime.strptime(raw_date, fmt).date()
                    break
                except ValueError:
                    continue
            if target_date is None:
                raise ValueError("Ge\u00e7ersiz tarih. \u00d6rn: yar\u0131n, pazartesi veya 10.10.2026")

        candidate_local = datetime.combine(target_date, local_time, tzinfo=zone)
        if raw_date in cls.WEEKDAYS and candidate_local <= current_local:
            candidate_local += timedelta(days=7)
        elif raw_date in {"bug\u00fcn", "bugun"} and candidate_local <= current_local:
            raise ValueError("Bu saat ge\u00e7mi\u015fte kald\u0131; ileri bir saat se\u00e7.")
        elif candidate_local <= current_local:
            raise ValueError("Bu tarih ge\u00e7mi\u015fte kald\u0131.")

        due_at = candidate_local.astimezone(timezone.utc)
        cls._validate_delay((due_at - now_utc).total_seconds())
        return due_at, f"{args[0]} {args[1]}", " ".join(args[2:]).strip() or "Hat\u0131rlat\u0131c\u0131"

    @classmethod
    def _parse_recurring(
        cls,
        args: list[str],
        *,
        now_utc: datetime,
        timezone_name: str,
    ) -> tuple[datetime, str, str, dict[str, int | str]]:
        if len(args) < 4:
            raise ValueError(
                "Tekrarl\u0131 hat\u0131rlat\u0131c\u0131 i\u00e7in /remind her g\u00fcn 09:00 Mesaj yaz."
            )
        try:
            zone = ZoneInfo(timezone_name)
        except (KeyError, ValueError) as exc:
            raise ValueError("Ge\u00e7ersiz saat dilimi.") from exc

        cadence = args[1].strip().casefold()
        current_local = now_utc.astimezone(zone)
        recurrence: dict[str, int | str]
        if cadence in {"g\u00fcn", "gun", "daily"}:
            recurrence = {"kind": "daily"}
            weekday = None
        elif cadence in {"hafta", "weekly"}:
            recurrence = {"kind": "weekly", "weekday": current_local.weekday()}
            weekday = current_local.weekday()
        elif cadence in cls.WEEKDAYS:
            weekday = cls.WEEKDAYS[cadence]
            recurrence = {"kind": "weekly", "weekday": weekday}
        else:
            raise ValueError("Tekrar aral\u0131\u011f\u0131 g\u00fcn, hafta veya T\u00fcrk\u00e7e bir hafta g\u00fcn\u00fc olmal\u0131.")

        clock_match = cls.CLOCK_RE.match(args[2].strip())
        if not clock_match:
            raise ValueError("Ge\u00e7ersiz saat. Saat HH:MM bi\u00e7iminde olmal\u0131.")
        local_time = time(int(clock_match.group("hour")), int(clock_match.group("minute")))

        if weekday is None:
            target_date = current_local.date()
        else:
            target_date = current_local.date() + timedelta(
                days=(weekday - current_local.weekday()) % 7
            )
        candidate_local = datetime.combine(target_date, local_time, tzinfo=zone)
        if candidate_local <= current_local:
            candidate_local += timedelta(days=1 if weekday is None else 7)
        due_at = candidate_local.astimezone(timezone.utc)
        cls._validate_delay((due_at - now_utc).total_seconds())
        return (
            due_at,
            " ".join(args[:3]),
            " ".join(args[3:]).strip() or "Hat\u0131rlat\u0131c\u0131",
            recurrence,
        )

    @classmethod
    def next_occurrence(
        cls,
        due_at: datetime,
        recurrence: dict[str, int | str] | None,
        *,
        timezone_name: str = "Europe/Istanbul",
    ) -> datetime | None:
        if not recurrence:
            return None
        try:
            zone = ZoneInfo(timezone_name)
        except (KeyError, ValueError) as exc:
            raise ValueError("Ge\u00e7ersiz saat dilimi.") from exc
        current_local = due_at.astimezone(zone)
        kind = recurrence.get("kind")
        if kind == "daily":
            target_date = current_local.date() + timedelta(days=1)
        elif kind == "weekly":
            weekday = int(recurrence.get("weekday", current_local.weekday()))
            target_date = current_local.date() + timedelta(
                days=(weekday - current_local.weekday()) % 7 or 7
            )
        else:
            raise ValueError("Bilinmeyen hat\u0131rlat\u0131c\u0131 tekrar kural\u0131.")
        next_local = datetime.combine(target_date, current_local.timetz().replace(tzinfo=None), tzinfo=zone)
        return next_local.astimezone(timezone.utc)

    def schedule(
        self,
        context,
        callback,
        chat_id: int,
        user_id: int,
        request: ReminderRequest,
        reminder_id: str | None = None,
    ):
        if not context.job_queue:
            raise RuntimeError("JobQueue aktif de\u011fil.")
        suffix = reminder_id or str(int(request.due_at.timestamp()))
        job_name = f"reminder_{chat_id}_{user_id}_{suffix}"
        payload = {"text": request.text, "user_id": user_id, "due_at": request.due_at.isoformat()}
        if reminder_id:
            payload["reminder_id"] = reminder_id
        if request.recurrence:
            payload["recurrence"] = dict(request.recurrence)
            payload["timezone"] = request.timezone_name
        return context.job_queue.run_once(
            callback,
            request.delay_seconds,
            chat_id=chat_id,
            name=job_name,
            data=payload,
        )
