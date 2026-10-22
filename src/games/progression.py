"""Pure game progression, achievement, and leaderboard domain contracts.

This module deliberately has no Telegram, MongoDB, Redis, or network dependency.
A game result is an immutable event.  :class:`ProgressionEngine` accepts each
identity once, applies bounded scoring and anti-farming policy, then projects
accepted events into deterministic player and leaderboard views.  A durable
repository can persist ``export_records()`` and reconstruct the same projection
with ``ProgressionEngine.replay``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from enum import Enum
import hashlib
import json
import math
import unicodedata
from typing import Any, Iterable, Mapping, Sequence


UTC = timezone.utc
_MAX_ID_LENGTH = 180
_MAX_NAME_LENGTH = 128
_MAX_GAME_TYPE_LENGTH = 64
_ALLOWED_KEY_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_-")


class ProgressionStatus(str, Enum):
    """Stable result codes for a progression event."""

    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    CONFLICT = "conflict"
    INVALID = "invalid"
    FUTURE_EVENT = "future_event"
    COOLDOWN = "cooldown"
    WINDOW_CAP = "window_cap"
    DAILY_CAP = "daily_cap"


@dataclass(frozen=True, slots=True)
class ProgressionPolicy:
    """Bounds and anti-farming limits shared by all games.

    Limits are intentionally conservative.  A game may submit a score that is
    lower than these values, but no handler can bypass the common cap by adding
    a new game type or replaying an event.
    """

    score_cap: int = 1000
    max_base_score: int = 1000
    max_combo: int = 20
    max_hints: int = 20
    cooldown_seconds: int = 2
    window_seconds: int = 60
    max_events_per_window: int = 20
    daily_score_cap: int = 5000
    future_skew_seconds: int = 30

    def __post_init__(self) -> None:
        integer_fields = (
            "score_cap",
            "max_base_score",
            "max_combo",
            "max_hints",
            "cooldown_seconds",
            "window_seconds",
            "max_events_per_window",
            "daily_score_cap",
            "future_skew_seconds",
        )
        for name in integer_fields:
            value = getattr(self, name)
            if isinstance(value, bool) or int(value) != value:
                raise ValueError("invalid_progression_policy")
        if not 1 <= self.score_cap <= 100_000:
            raise ValueError("invalid_progression_policy")
        if not 1 <= self.max_base_score <= 100_000:
            raise ValueError("invalid_progression_policy")
        if not 0 <= self.max_combo <= 1000 or not 0 <= self.max_hints <= 1000:
            raise ValueError("invalid_progression_policy")
        if not 0 <= self.cooldown_seconds <= 86_400:
            raise ValueError("invalid_progression_policy")
        if not 1 <= self.window_seconds <= 86_400:
            raise ValueError("invalid_progression_policy")
        if not 1 <= self.max_events_per_window <= 100_000:
            raise ValueError("invalid_progression_policy")
        if not 1 <= self.daily_score_cap <= 100_000:
            raise ValueError("invalid_progression_policy")
        if not 0 <= self.future_skew_seconds <= 86_400:
            raise ValueError("invalid_progression_policy")


_DIFFICULTY_MULTIPLIERS: dict[str, float] = {
    "easy": 0.8,
    "kolay": 0.8,
    "normal": 1.0,
    "orta": 1.0,
    "medium": 1.0,
    "hard": 1.25,
    "zor": 1.25,
    "expert": 1.5,
    "uzman": 1.5,
}


def _fold(value: object) -> str:
    text = str(value or "").strip().casefold()
    text = text.replace("ı", "i")
    return "".join(char for char in unicodedata.normalize("NFKD", text) if not unicodedata.combining(char))


def _normalize_datetime(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if not isinstance(value, datetime):
        raise ValueError("occurred_at_invalid")
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def season_id_for(value: datetime | None = None) -> str:
    """Return the UTC calendar quarter used by the seasonal projection."""

    moment = _normalize_datetime(value)
    return f"{moment.year}-Q{((moment.month - 1) // 3) + 1}"


def calculate_awarded_score(
    base_score: int | float,
    *,
    difficulty: object = "normal",
    combo: int = 0,
    hints_used: int = 0,
    timed_out: bool = False,
    score_cap: int = 1000,
) -> int:
    """Calculate a bounded score from one completed game result.

    The function is deterministic and side-effect free.  Combo bonus is capped
    at 2x, difficulty is a fixed allowlisted multiplier, and hints reduce the
    result before the final cap.  Invalid values fail closed instead of
    silently granting a reward.
    """

    if isinstance(base_score, bool):
        raise ValueError("base_score_invalid")
    try:
        raw = float(base_score)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("base_score_invalid") from None
    if not math.isfinite(raw) or raw < 0:
        raise ValueError("base_score_invalid")
    try:
        combo_value = int(combo)
        hint_value = int(hints_used)
        cap = int(score_cap)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("score_input_invalid") from None
    if combo_value < 0 or combo_value > 1000 or hint_value < 0 or hint_value > 1000:
        raise ValueError("score_input_invalid")
    if cap < 1 or cap > 100_000:
        raise ValueError("score_cap_invalid")
    key = _fold(difficulty)
    multiplier = _DIFFICULTY_MULTIPLIERS.get(key)
    if multiplier is None:
        raise ValueError("difficulty_invalid")
    if timed_out:
        return 0
    combo_bonus = 1.0 + min(combo_value, 20) * 0.1
    hint_penalty = hint_value * 2
    awarded = max(0.0, (raw * multiplier * combo_bonus) - hint_penalty)
    return max(0, min(cap, int(round(awarded))))


def _validate_event_key(value: object, *, field_name: str, max_length: int = _MAX_ID_LENGTH) -> str:
    text = str(value or "").strip()
    if not text or len(text) > max_length or any(
        ord(char) < 32 or ord(char) == 127 or char.isspace() for char in text
    ):
        raise ValueError(f"{field_name}_invalid")
    return text


def _validate_game_type(value: object) -> str:
    text = str(value or "").strip().casefold()
    if not text or len(text) > _MAX_GAME_TYPE_LENGTH or any(char not in _ALLOWED_KEY_CHARS for char in text):
        raise ValueError("game_type_invalid")
    return text


@dataclass(frozen=True, slots=True)
class ProgressionEvent:
    """Immutable result event accepted from a game boundary."""

    event_id: str
    user_id: int
    chat_id: int
    game_type: str
    base_score: int | float
    won: bool = False
    difficulty: str = "normal"
    combo: int = 0
    hints_used: int = 0
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    user_name: str | None = None
    timed_out: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _validate_event_key(self.event_id, field_name="event_id"))
        if isinstance(self.user_id, bool) or not isinstance(self.user_id, int) or self.user_id <= 0:
            raise ValueError("user_id_invalid")
        if isinstance(self.chat_id, bool) or not isinstance(self.chat_id, int) or self.chat_id == 0:
            raise ValueError("chat_id_invalid")
        object.__setattr__(self, "game_type", _validate_game_type(self.game_type))
        if isinstance(self.base_score, bool):
            raise ValueError("base_score_invalid")
        try:
            score = float(self.base_score)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("base_score_invalid") from None
        if not math.isfinite(score) or score < 0 or score > 100_000:
            raise ValueError("base_score_invalid")
        object.__setattr__(self, "base_score", int(score) if score.is_integer() else score)
        if not isinstance(self.won, bool) or not isinstance(self.timed_out, bool):
            raise ValueError("event_flag_invalid")
        object.__setattr__(self, "difficulty", _validate_event_key(self.difficulty, field_name="difficulty", max_length=32).casefold())
        try:
            combo = int(self.combo)
            hints = int(self.hints_used)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("score_input_invalid") from None
        if combo < 0 or combo > 1000 or hints < 0 or hints > 1000:
            raise ValueError("score_input_invalid")
        object.__setattr__(self, "combo", combo)
        object.__setattr__(self, "hints_used", hints)
        object.__setattr__(self, "occurred_at", _normalize_datetime(self.occurred_at))
        if self.user_name is not None:
            name = str(self.user_name).strip()
            if len(name) > _MAX_NAME_LENGTH or any(ord(char) < 32 or ord(char) == 127 for char in name):
                raise ValueError("user_name_invalid")
            object.__setattr__(self, "user_name", name or None)

    def to_record(self) -> dict[str, Any]:
        """Serialize a replay-safe, JSON-compatible event record."""

        return {
            "event_id": self.event_id,
            "user_id": self.user_id,
            "chat_id": self.chat_id,
            "game_type": self.game_type,
            "base_score": self.base_score,
            "won": self.won,
            "difficulty": self.difficulty,
            "combo": self.combo,
            "hints_used": self.hints_used,
            "occurred_at": self.occurred_at.isoformat(),
            "user_name": self.user_name,
            "timed_out": self.timed_out,
        }

    @classmethod
    def from_record(cls, value: Mapping[str, Any]) -> "ProgressionEvent":
        if not isinstance(value, Mapping):
            raise ValueError("event_record_invalid")
        return cls(
            event_id=value.get("event_id"),
            user_id=value.get("user_id"),
            chat_id=value.get("chat_id"),
            game_type=value.get("game_type"),
            base_score=value.get("base_score"),
            won=bool(value.get("won", False)),
            difficulty=value.get("difficulty", "normal"),
            combo=value.get("combo", 0),
            hints_used=value.get("hints_used", 0),
            occurred_at=_normalize_datetime(value.get("occurred_at")) if not isinstance(value.get("occurred_at"), str) else _parse_iso_datetime(value.get("occurred_at")),
            user_name=value.get("user_name"),
            timed_out=bool(value.get("timed_out", False)),
        )

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(self.to_record(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parse_iso_datetime(value: str) -> datetime:
    try:
        return _normalize_datetime(datetime.fromisoformat(value))
    except (TypeError, ValueError):
        raise ValueError("occurred_at_invalid") from None


@dataclass(frozen=True, slots=True)
class ProgressionReceipt:
    event_id: str
    status: ProgressionStatus
    accepted: bool
    replayed: bool
    awarded_score: int
    total_score: int
    season_id: str
    combo: int
    achievements: tuple[str, ...]
    reason: str = ""


@dataclass(frozen=True, slots=True)
class LeaderboardRow:
    """Stable player projection; sorting is deterministic across replays."""

    rank: int
    user_id: int
    user_name: str
    chat_id: int
    season_id: str
    game_type: str | None
    total_score: int
    games_played: int
    wins: int
    best_score: int
    max_combo: int
    achievement_count: int


@dataclass(frozen=True, slots=True)
class _AcceptedRecord:
    event: ProgressionEvent
    awarded_score: int
    season_id: str


_ACHIEVEMENT_ORDER = (
    "first_game",
    "first_win",
    "score_50",
    "score_500",
    "streak_3",
    "quiz_master",
    "versatile",
)


def _achievement_keys(records: Sequence[_AcceptedRecord]) -> tuple[str, ...]:
    if not records:
        return ()
    ordered = sorted(records, key=lambda item: (item.event.occurred_at, item.event.event_id))
    games = len(ordered)
    wins = sum(1 for item in ordered if item.event.won)
    total = sum(item.awarded_score for item in ordered)
    unique_games = {item.event.game_type for item in ordered}
    max_combo = max(item.event.combo for item in ordered)
    available = {
        "first_game": games >= 1,
        "first_win": wins >= 1,
        "score_50": total >= 50,
        "score_500": total >= 500,
        "streak_3": max_combo >= 3,
        "quiz_master": sum(1 for item in ordered if item.event.game_type == "quiz" and item.event.won) >= 5,
        "versatile": len(unique_games) >= 3,
    }
    return tuple(key for key in _ACHIEVEMENT_ORDER if available[key])


def _event_sort_key(item: _AcceptedRecord) -> tuple[datetime, str]:
    return item.event.occurred_at, item.event.event_id


def _projection_rows(
    records: Iterable[_AcceptedRecord],
    *,
    chat_id: int,
    season_id: str,
    game_type: str | None,
    limit: int,
    include_achievements: bool = True,
) -> tuple[LeaderboardRow, ...]:
    grouped: dict[int, list[_AcceptedRecord]] = {}
    normalized_game = _validate_game_type(game_type) if game_type else None
    for item in records:
        event = item.event
        if event.chat_id != int(chat_id) or item.season_id != season_id:
            continue
        if normalized_game and event.game_type != normalized_game:
            continue
        grouped.setdefault(event.user_id, []).append(item)
    prepared: list[tuple[int, str, int, int, int, int, int, int]] = []
    for user_id, values in grouped.items():
        ordered = sorted(values, key=_event_sort_key)
        latest_name = next((item.event.user_name for item in reversed(ordered) if item.event.user_name), None) or f"Oyuncu {user_id}"
        total = sum(item.awarded_score for item in ordered)
        wins = sum(1 for item in ordered if item.event.won)
        best = max(item.awarded_score for item in ordered)
        max_combo = max(item.event.combo for item in ordered)
        achievements = len(_achievement_keys(ordered)) if include_achievements else 0
        prepared.append((user_id, latest_name, total, len(ordered), wins, best, max_combo, achievements))
    prepared.sort(key=lambda row: (-row[2], -row[4], -row[5], -row[3], row[0]))
    bounded_limit = max(1, min(100, int(limit)))
    return tuple(
        LeaderboardRow(
            rank=index,
            user_id=row[0],
            user_name=row[1],
            chat_id=int(chat_id),
            season_id=season_id,
            game_type=normalized_game,
            total_score=row[2],
            games_played=row[3],
            wins=row[4],
            best_score=row[5],
            max_combo=row[6],
            achievement_count=row[7],
        )
        for index, row in enumerate(prepared[:bounded_limit], start=1)
    )


def project_leaderboard(
    events: Iterable[ProgressionEvent],
    *,
    chat_id: int,
    season_id: str,
    game_type: str | None = None,
    limit: int = 10,
    policy: ProgressionPolicy | None = None,
) -> tuple[LeaderboardRow, ...]:
    """Project a deterministic leaderboard from raw accepted result events.

    Callers replaying persisted events should pass the same policy used at
    ingestion.  Events that would violate anti-farming constraints are ignored
    by the replay engine; this helper treats its input as an already accepted
    event stream and only applies the scoring formula.
    """

    if isinstance(chat_id, bool) or not isinstance(chat_id, int) or chat_id == 0:
        raise ValueError("chat_id_invalid")
    season = _validate_event_key(season_id, field_name="season_id", max_length=32)
    selected_policy = policy or ProgressionPolicy()
    accepted = tuple(
        _AcceptedRecord(
            event=item,
            awarded_score=calculate_awarded_score(
                item.base_score,
                difficulty=item.difficulty,
                combo=min(item.combo, selected_policy.max_combo),
                hints_used=min(item.hints_used, selected_policy.max_hints),
                timed_out=item.timed_out,
                score_cap=selected_policy.score_cap,
            ),
            season_id=season_id_for(item.occurred_at),
        )
        for item in events
        if isinstance(item, ProgressionEvent)
    )
    return _projection_rows(accepted, chat_id=chat_id, season_id=season, game_type=game_type, limit=limit)


class ProgressionEngine:
    """Idempotent progression ledger and deterministic projection engine."""

    def __init__(self, policy: ProgressionPolicy | None = None) -> None:
        self.policy = policy or ProgressionPolicy()
        self._ledger: dict[str, tuple[str, ProgressionReceipt]] = {}
        self._accepted: dict[str, _AcceptedRecord] = {}

    @classmethod
    def replay(
        cls,
        records: Iterable[Mapping[str, Any] | ProgressionEvent],
        *,
        policy: ProgressionPolicy | None = None,
    ) -> "ProgressionEngine":
        engine = cls(policy=policy)
        events = [item if isinstance(item, ProgressionEvent) else ProgressionEvent.from_record(item) for item in records]
        for event in sorted(events, key=lambda item: (item.occurred_at, item.event_id)):
            engine.apply(event, now=event.occurred_at)
        return engine

    def _accepted_for_scope(self, event: ProgressionEvent) -> list[_AcceptedRecord]:
        return sorted(
            (item for item in self._accepted.values() if item.event.chat_id == event.chat_id and item.event.user_id == event.user_id),
            key=_event_sort_key,
        )

    @staticmethod
    def _day(moment: datetime) -> date:
        return moment.astimezone(UTC).date()

    def _receipt(
        self,
        event: ProgressionEvent,
        status: ProgressionStatus,
        *,
        awarded_score: int = 0,
        replayed: bool = False,
        reason: str = "",
    ) -> ProgressionReceipt:
        season = season_id_for(event.occurred_at)
        records = [item for item in self._accepted.values() if item.event.user_id == event.user_id and item.event.chat_id == event.chat_id and item.season_id == season]
        total = sum(item.awarded_score for item in records) + (awarded_score if status is ProgressionStatus.ACCEPTED else 0)
        achievements = _achievement_keys([*records, *([_AcceptedRecord(event, awarded_score, season)] if status is ProgressionStatus.ACCEPTED else [])])
        combo = min(event.combo, self.policy.max_combo)
        return ProgressionReceipt(
            event_id=event.event_id,
            status=status,
            accepted=status is ProgressionStatus.ACCEPTED,
            replayed=replayed,
            awarded_score=awarded_score,
            total_score=total,
            season_id=season,
            combo=combo,
            achievements=achievements,
            reason=reason,
        )

    def apply(self, event: ProgressionEvent, *, now: datetime | None = None) -> ProgressionReceipt:
        if not isinstance(event, ProgressionEvent):
            raise ValueError("event_invalid")
        fingerprint = event.fingerprint
        previous = self._ledger.get(event.event_id)
        if previous is not None:
            previous_fingerprint, previous_receipt = previous
            if previous_fingerprint != fingerprint:
                conflict = replace(previous_receipt, status=ProgressionStatus.CONFLICT, accepted=False, replayed=True, reason="event_payload_conflict")
                return conflict
            return replace(previous_receipt, status=ProgressionStatus.DUPLICATE, accepted=False, replayed=True, reason="event_already_recorded")
        moment = _normalize_datetime(now)
        if event.occurred_at > moment + timedelta(seconds=self.policy.future_skew_seconds):
            receipt = self._receipt(event, ProgressionStatus.FUTURE_EVENT, reason="event_is_in_the_future")
            self._ledger[event.event_id] = (fingerprint, receipt)
            return receipt
        try:
            awarded = calculate_awarded_score(
                event.base_score,
                difficulty=event.difficulty,
                combo=min(event.combo, self.policy.max_combo),
                hints_used=min(event.hints_used, self.policy.max_hints),
                timed_out=event.timed_out,
                score_cap=self.policy.score_cap,
            )
        except ValueError as exc:
            receipt = self._receipt(event, ProgressionStatus.INVALID, reason=str(exc))
            self._ledger[event.event_id] = (fingerprint, receipt)
            return receipt
        scope = self._accepted_for_scope(event)
        if scope and self.policy.cooldown_seconds:
            latest = scope[-1].event.occurred_at
            if event.occurred_at < latest + timedelta(seconds=self.policy.cooldown_seconds):
                receipt = self._receipt(event, ProgressionStatus.COOLDOWN, reason="anti_farming_cooldown")
                self._ledger[event.event_id] = (fingerprint, receipt)
                return receipt
        window_start = event.occurred_at - timedelta(seconds=self.policy.window_seconds)
        in_window = [item for item in scope if window_start <= item.event.occurred_at <= event.occurred_at]
        if len(in_window) >= self.policy.max_events_per_window:
            receipt = self._receipt(event, ProgressionStatus.WINDOW_CAP, reason="anti_farming_window_cap")
            self._ledger[event.event_id] = (fingerprint, receipt)
            return receipt
        current_day = self._day(event.occurred_at)
        day_total = sum(item.awarded_score for item in scope if self._day(item.event.occurred_at) == current_day)
        if day_total + awarded > self.policy.daily_score_cap:
            receipt = self._receipt(event, ProgressionStatus.DAILY_CAP, reason="anti_farming_daily_cap")
            self._ledger[event.event_id] = (fingerprint, receipt)
            return receipt
        season = season_id_for(event.occurred_at)
        accepted_record = _AcceptedRecord(event=event, awarded_score=awarded, season_id=season)
        self._accepted[event.event_id] = accepted_record
        receipt = self._receipt(event, ProgressionStatus.ACCEPTED, awarded_score=awarded)
        self._ledger[event.event_id] = (fingerprint, receipt)
        return receipt

    def export_records(self) -> list[dict[str, Any]]:
        """Return accepted events in canonical order for durable replay."""

        values = sorted(self._accepted.values(), key=_event_sort_key)
        return [item.event.to_record() for item in values]

    def accepted_events(self) -> tuple[ProgressionEvent, ...]:
        return tuple(item.event for item in sorted(self._accepted.values(), key=_event_sort_key))

    def player_snapshot(self, user_id: int, chat_id: int, *, season_id: str | None = None) -> dict[str, Any]:
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
            raise ValueError("user_id_invalid")
        if isinstance(chat_id, bool) or not isinstance(chat_id, int) or chat_id == 0:
            raise ValueError("chat_id_invalid")
        selected = season_id
        records = [
            item
            for item in self._accepted.values()
            if item.event.user_id == user_id and item.event.chat_id == chat_id and (selected is None or item.season_id == selected)
        ]
        ordered = sorted(records, key=_event_sort_key)
        return {
            "user_id": user_id,
            "chat_id": chat_id,
            "season_id": selected,
            "total_score": sum(item.awarded_score for item in ordered),
            "games_played": len(ordered),
            "wins": sum(1 for item in ordered if item.event.won),
            "best_score": max((item.awarded_score for item in ordered), default=0),
            "max_combo": max((item.event.combo for item in ordered), default=0),
            "achievements": _achievement_keys(ordered),
        }

    def achievements(self, user_id: int, chat_id: int, *, season_id: str | None = None) -> tuple[str, ...]:
        return tuple(self.player_snapshot(user_id, chat_id, season_id=season_id)["achievements"])

    def leaderboard(
        self,
        chat_id: int,
        *,
        season_id: str | None = None,
        game_type: str | None = None,
        limit: int = 10,
    ) -> tuple[LeaderboardRow, ...]:
        season = season_id or season_id_for()
        _validate_event_key(season, field_name="season_id", max_length=32)
        return _projection_rows(
            self._accepted.values(),
            chat_id=chat_id,
            season_id=season,
            game_type=game_type,
            limit=limit,
        )
