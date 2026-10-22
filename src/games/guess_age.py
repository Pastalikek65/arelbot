"""Domain model for Arel's group "yaşını tahmin et" game.

The original project is used as a behaviour reference only.  This module is
an independent, Telegram-agnostic implementation: rounds are serialisable,
guesses are idempotent, and no user supplied text is trusted as a target.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
import uuid
from typing import Any, Mapping


SCHEMA_VERSION = 1
GAME_KEY = "guess_age"
MIN_AGE = 1
MAX_AGE = 120
MAX_PLAYERS = 100
DEFAULT_ROUNDS = 5
DEFAULT_ROUND_SECONDS = 30


@dataclass(frozen=True, slots=True)
class AgeAsset:
    key: str
    filename: str
    target_age: int
    person_name: str = ""
    photo_date: str = ""
    source_url: str = ""
    credit: str = ""
    birth_date: str = ""
    license_url: str = ""
    sha256: str = ""
    person_id: str = ""


@dataclass(frozen=True, slots=True)
class GuessOutcome:
    accepted: bool
    code: str
    score: int = 0
    round_complete: bool = False


def builtin_assets(asset_dir: str) -> tuple[AgeAsset, ...]:
    """Curated Commons portraits; ages are calculated at the photo date.

    See assets/guess_age/README.md for the photograph and birth-date sources.
    ``asset_dir`` is retained for the existing service interface.
    """

    from pathlib import Path
    from src.games.age_catalog import load_age_catalog

    downloaded = load_age_catalog(Path(asset_dir))
    if downloaded:
        return downloaded
    if (Path(asset_dir) / "catalog.json").exists():
        return ()
    rows = (
        ("jared_2023", "jared_goff_2023.jpg", 28, "Jared Goff", "2023-02-10", "Jared_Goff_by_Gage_Skidmore.jpg", "3.0"),
        ("cate_2017", "cate_blanchett_2017.jpg", 48, "Cate Blanchett", "2017-07-22", "Cate_Blanchett_by_Gage_Skidmore.jpg", "3.0"),
        ("nile_2023", "nile_rodgers_2023.jpg", 70, "Nile Rodgers", "2023-04-01", "Nile_Rodgers_by_Gage_Skidmore.jpg", "3.0"),
        ("johnny_2018", "johnny_depp_2018.jpg", 55, "Johnny Depp", "2018-07-21", "Johnny_Depp_by_Gage_Skidmore.jpg", "3.0"),
        ("sonia_2017", "sonia_sotomayor_2017.jpg", 62, "Sonia Sotomayor", "2017-01-23", "Sonia_Sotomayor_by_Gage_Skidmore.jpg", "3.0"),
        ("sonequa_2019", "sonequa_martin_green_2019.jpg", 34, "Sonequa Martin-Green", "2019-07-20", "Sonequa_Martin-Green_Gage_Skidmore_2019_%28cropped%29.jpg", "2.0"),
    )
    return tuple(
        AgeAsset(key, filename, age, name, photo_date,
                 f"https://commons.wikimedia.org/wiki/File:{source}",
                 f"Gage Skidmore · CC BY-SA {license_version}")
        for key, filename, age, name, photo_date, source, license_version in rows
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def round_expired(value: Mapping[str, Any], *, now: datetime | None = None) -> bool:
    """Check the persisted round deadline without trusting client clocks."""

    if str(value.get("status") or "") != "playing":
        return False
    if value.get("delivery_pending") or value.get("initial_delivery_pending"):
        return False
    try:
        started = datetime.fromisoformat(str(value.get("round_started_at")))
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        seconds = max(15, min(300, int(value.get("round_seconds", DEFAULT_ROUND_SECONDS))))
        return (current - started).total_seconds() >= seconds
    except (TypeError, ValueError, OverflowError):
        # A malformed timestamp is safer to expire than to accept guesses
        # indefinitely.
        return True


def new_guess_age_state(
    *,
    owner_id: int,
    owner_name: str,
    chat_id: int,
    thread_id: int | None = None,
    rounds: int = DEFAULT_ROUNDS,
    round_seconds: int = DEFAULT_ROUND_SECONDS,
    asset: AgeAsset | None = None,
) -> dict[str, Any]:
    if int(chat_id) >= 0:
        raise ValueError("guess_age_group_only")
    if int(owner_id) <= 0:
        raise ValueError("guess_age_invalid_owner")
    rounds = max(1, min(10, int(rounds)))
    round_seconds = max(15, min(300, int(round_seconds)))
    chosen = asset or builtin_assets(".")[0]
    return {
        "schema_version": SCHEMA_VERSION,
        "game_key": GAME_KEY,
        "round_id": uuid.uuid4().hex[:20],
        "chat_id": int(chat_id),
        "thread_id": int(thread_id) if thread_id is not None else None,
        "owner_id": int(owner_id),
        "owner_name": str(owner_name or "oyuncu")[:80],
        "status": "playing",
        "round": 1,
        "rounds": rounds,
        "round_seconds": round_seconds,
        "round_started_at": _utc_now(),
        "round_deadline": None,
        "initial_delivery_pending": True,
        "asset_key": chosen.key,
        "asset_filename": chosen.filename,
        "target_age": int(chosen.target_age),
        "person_name": chosen.person_name,
        "photo_date": chosen.photo_date,
        "asset_source_url": chosen.source_url,
        "asset_credit": chosen.credit,
        "asset_license_url": chosen.license_url,
        "answers": {},
        "players": {},
        "history": [],
        "prompt_message_id": 0,
        "revision": 0,
        "version": 0,
        "last_action": None,
        "error_code": None,
    }


def validate_guess_age_state(value: Mapping[str, Any]) -> bool:
    try:
        return (
            str(value.get("game_key")) == GAME_KEY
            and len(str(value.get("round_id") or "")) >= 12
            and int(value.get("chat_id")) < 0
            and int(value.get("owner_id")) > 0
            and str(value.get("status")) in {"playing", "finished", "cancelled", "expired", "failed"}
            and 1 <= int(value.get("round")) <= 10
            and 1 <= int(value.get("rounds")) <= 10
            and 1 <= int(value.get("target_age")) <= MAX_AGE
            and isinstance(value.get("answers", {}), Mapping)
        )
    except (TypeError, ValueError):
        return False


def normalize_age(value: object) -> int:
    text = str(value or "").strip()
    if not text.isdecimal():
        raise ValueError("guess_age_invalid_number")
    age = int(text)
    if age < MIN_AGE or age > MAX_AGE:
        raise ValueError("guess_age_out_of_range")
    return age


def score_guess(target_age: int, guess: int) -> int:
    """Score accuracy on a predictable 0..100 scale."""

    target = normalize_age(target_age)
    candidate = normalize_age(guess)
    return max(0, 100 - (abs(target - candidate) * 5))


def submit_guess(state: dict[str, Any], *, user_id: int, display_name: str, guess: object) -> GuessOutcome:
    if not validate_guess_age_state(state) or state.get("status") != "playing":
        return GuessOutcome(False, "guess_age_inactive")
    if state.get("delivery_pending") or state.get("initial_delivery_pending"):
        return GuessOutcome(False, "GUESS_AGE_ROUND_PREPARING")
    if round_expired(state):
        return GuessOutcome(False, "guess_age_round_expired")
    user_id = int(user_id)
    if user_id <= 0:
        return GuessOutcome(False, "guess_age_invalid_user")
    key = str(user_id)
    answers = state.setdefault("answers", {})
    if key in answers:
        return GuessOutcome(False, "guess_age_already_answered")
    players = state.setdefault("players", {})
    if key not in players and len(players) >= MAX_PLAYERS:
        return GuessOutcome(False, "guess_age_full")
    try:
        candidate = normalize_age(guess)
    except ValueError as exc:
        return GuessOutcome(False, str(exc))
    points = score_guess(int(state["target_age"]), candidate)
    answers[key] = {
        "guess": candidate,
        "score": points,
        "display_name": str(display_name or "oyuncu")[:80],
    }
    players.setdefault(key, {"display_name": str(display_name or "oyuncu")[:80], "score": 0})
    players[key]["display_name"] = str(display_name or "oyuncu")[:80]
    players[key]["score"] = int(players[key].get("score", 0) or 0) + points
    state["revision"] = int(state.get("revision", 0)) + 1
    state["last_action"] = f"guess:{user_id}:{candidate}"
    # A round closes on its timer, just like the reference game.  Accepting a
    # guess never ends the round early; this lets late players still see the
    # same panel and keeps the timer deterministic after a restart.
    return GuessOutcome(True, "accepted", score=points, round_complete=False)


def finish_round(state: dict[str, Any], *, next_asset: AgeAsset | None = None) -> dict[str, Any]:
    if not validate_guess_age_state(state) or state.get("status") != "playing":
        raise ValueError("guess_age_inactive")
    answers = state.get("answers", {})
    ranked = sorted(
        (
            {"user_id": int(user_id), **dict(value)}
            for user_id, value in answers.items()
            if isinstance(value, Mapping)
        ),
        key=lambda row: (-int(row.get("score", 0)), abs(int(state["target_age"]) - int(row.get("guess", 0)))),
    )
    state.setdefault("history", []).append(
        {
            "round": int(state["round"]),
            "asset_key": str(state.get("asset_key") or ""),
            "target_age": int(state["target_age"]),
            "person_name": str(state.get("person_name") or ""),
            "photo_date": str(state.get("photo_date") or ""),
            "asset_source_url": str(state.get("asset_source_url") or ""),
            "asset_credit": str(state.get("asset_credit") or ""),
            "asset_license_url": str(state.get("asset_license_url") or ""),
            "asset_filename": str(state.get("asset_filename") or ""),
            "ranked": ranked,
        }
    )
    if int(state["round"]) >= int(state["rounds"]):
        state["status"] = "finished"
        state["revision"] = int(state.get("revision", 0)) + 1
        return state
    if next_asset is None:
        raise ValueError("guess_age_asset_missing")
    state["round"] = int(state["round"]) + 1
    state["answers"] = {}
    state["round_started_at"] = _utc_now()
    state["round_deadline"] = None
    state["asset_key"] = next_asset.key
    state["asset_filename"] = next_asset.filename
    state["target_age"] = int(next_asset.target_age)
    state["person_name"] = next_asset.person_name
    state["photo_date"] = next_asset.photo_date
    state["asset_source_url"] = next_asset.source_url
    state["asset_credit"] = next_asset.credit
    state["asset_license_url"] = next_asset.license_url
    state["revision"] = int(state.get("revision", 0)) + 1
    state["last_action"] = "round_advanced"
    return state


def final_ranking(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    players = state.get("players", {})
    rows = [
        {"user_id": int(user_id), **dict(value)}
        for user_id, value in players.items()
        if isinstance(value, Mapping)
    ]
    return sorted(rows, key=lambda row: (-int(row.get("score", 0)), str(row.get("display_name", ""))))[:MAX_PLAYERS]
