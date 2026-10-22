"""Persistent service for the group-based Arel Yaş Tahmini game."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path
import random
import time
import unicodedata
import weakref
from datetime import datetime, timezone
from typing import Any

from src.games.guess_age import (
    AgeAsset,
    DEFAULT_ROUND_SECONDS,
    DEFAULT_ROUNDS,
    builtin_assets,
    final_ranking,
    finish_round,
    new_guess_age_state,
    round_expired,
    submit_guess,
    validate_guess_age_state,
)


class GuessAgeServiceError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


class GuessAgeService:
    """Own game state and persistence; Telegram handlers only render results."""

    GAME_TYPE = "guess_age"

    def __init__(
        self,
        database: Any,
        *,
        asset_dir: str | Path = "assets/guess_age",
        rounds: int = DEFAULT_ROUNDS,
        round_seconds: int = DEFAULT_ROUND_SECONDS,
        rng: random.Random | None = None,
        leaderboard_service: Any | None = None,
    ) -> None:
        self.database = database
        self.asset_dir = Path(asset_dir)
        self.rounds = max(1, min(10, int(rounds)))
        self.round_seconds = max(15, min(300, int(round_seconds)))
        self.rng = rng or random.Random()
        self.leaderboard_service = leaderboard_service
        self.assets = builtin_assets(str(self.asset_dir))
        self._locks: dict[int, asyncio.Lock] = {}
        self._panel_locks = weakref.WeakValueDictionary()

    def presentation_lock(self, chat_id):
        return self._panel_locks.setdefault(int(chat_id), asyncio.Lock())

    def _lock_for(self, chat_id: int) -> asyncio.Lock:
        return self._locks.setdefault(int(chat_id), asyncio.Lock())

    async def _get(self, chat_id: int) -> dict[str, Any] | None:
        getter = getattr(self.database, "get_game_state", None)
        if getter is None or getattr(self.database, "available", True) is False:
            raise GuessAgeServiceError("GUESS_AGE_STORAGE_UNAVAILABLE")
        try:
            value = await getter(int(chat_id), self.GAME_TYPE)
            if isinstance(value, dict) and "version" not in value:
                initializer = getattr(self.database, "initialize_game_state_version_if_missing", None)
                if initializer is None:
                    raise GuessAgeServiceError("GUESS_AGE_STORAGE_UNAVAILABLE")
                await initializer(int(chat_id), self.GAME_TYPE)
                value = await getter(int(chat_id), self.GAME_TYPE)
                if isinstance(value, dict) and "version" not in value:
                    raise GuessAgeServiceError("GUESS_AGE_STORAGE_UNAVAILABLE")
        except GuessAgeServiceError:
            raise
        except Exception:
            raise GuessAgeServiceError("GUESS_AGE_STORAGE_UNAVAILABLE") from None
        return deepcopy(value) if isinstance(value, dict) else None

    async def _cas(self, chat_id: int, previous: dict[str, Any], state: dict[str, Any]) -> bool:
        saver = getattr(self.database, "compare_and_save_game_state", None)
        if saver is None or getattr(self.database, "available", True) is False:
            raise GuessAgeServiceError("GUESS_AGE_STORAGE_UNAVAILABLE")
        expected = int(previous["version"])
        state["version"] = expected + 1
        try:
            return bool(await saver(int(chat_id), self.GAME_TYPE, state, expected_version=expected))
        except GuessAgeServiceError:
            raise
        except Exception:
            raise GuessAgeServiceError("GUESS_AGE_STORAGE_UNAVAILABLE") from None

    async def _apply(self, chat_id: int, transform):
        async with self._lock_for(chat_id):
            for _ in range(4):
                previous = await self._get(chat_id)
                state, extra = transform(previous, deepcopy(previous) if previous is not None else None)
                if state is previous or state is None:
                    return previous, extra
                if await self._cas(chat_id, previous, state):
                    return state, extra
            raise GuessAgeServiceError("GUESS_AGE_CONFLICT")

    async def _delete(self, chat_id: int) -> None:
        deleter = getattr(self.database, "delete_game_state", None)
        if deleter is None or getattr(self.database, "available", True) is False:
            raise GuessAgeServiceError("GUESS_AGE_STORAGE_UNAVAILABLE")
        try:
            await deleter(int(chat_id), self.GAME_TYPE)
        except GuessAgeServiceError:
            raise
        except Exception:
            raise GuessAgeServiceError("GUESS_AGE_STORAGE_UNAVAILABLE") from None

    def asset_path(self, state: dict[str, Any]) -> Path:
        filename = Path(str(state.get("asset_filename") or "")).name
        if not filename or filename != str(state.get("asset_filename")):
            raise GuessAgeServiceError("GUESS_AGE_ASSET_INVALID")
        path = (self.asset_dir / filename).resolve()
        root = self.asset_dir.resolve()
        if root not in path.parents or not path.is_file():
            raise GuessAgeServiceError("GUESS_AGE_ASSET_MISSING")
        return path

    @staticmethod
    def _credit_identifies_subject(asset: AgeAsset) -> bool:
        def plain(value: str) -> str:
            return "".join(char for char in unicodedata.normalize("NFKC", value).casefold() if char.isalnum())

        subject = plain(asset.person_name)
        return len(subject) > 1 and subject in plain(asset.credit)

    def _playable_assets(self) -> list[AgeAsset]:
        return [item for item in self.assets
                if (self.asset_dir / item.filename).is_file() and not self._credit_identifies_subject(item)]

    def _next_asset(self, state: dict[str, Any]) -> AgeAsset:
        used = set(state.get("recent_asset_keys", [])) | {str(item.get("asset_key")) for item in state.get("history", []) if isinstance(item, dict)}
        present = self._playable_assets()
        available = [item for item in present if item.key not in used and item.key != state.get("asset_key")]
        if not present:
            raise GuessAgeServiceError("GUESS_AGE_ASSET_MISSING")
        return self._pick_asset(available or [item for item in present if item.key != state.get("asset_key")] or present)

    def _pick_asset(self, assets):
        buckets = {}
        for asset in assets:
            buckets.setdefault(asset.target_age // 10, []).append(asset)
        return self.rng.choice(buckets[self.rng.choice(list(buckets))])

    async def current(self, chat_id: int) -> dict[str, Any] | None:
        def backfill(previous, state):
            if (state and state.get("status") == "playing" and not state.get("delivery_pending")
                    and not state.get("initial_delivery_pending") and state.get("round_deadline") is None):
                try:
                    started = datetime.fromisoformat(str(state["round_started_at"]))
                    if started.tzinfo is None:
                        started = started.replace(tzinfo=timezone.utc)
                    state["round_deadline"] = started.timestamp() + int(state["round_seconds"])
                except (KeyError, ValueError, TypeError):
                    state["round_deadline"] = 0
                return state, None
            return previous, None

        state, _ = await self._apply(chat_id, backfill)
        if state and state.get("status") == "cancelled":
            return None
        if state and validate_guess_age_state(state) and state.get("status") == "playing":
            return state
        return state if state and validate_guess_age_state(state) else None

    async def start(self, *, chat_id: int, owner_id: int, owner_name: str, thread_id: int | None = None) -> dict[str, Any]:
        existing = await self._get(chat_id)
        if existing and existing.get("pending_scores"):
            await self.settle_scores(chat_id)
        async with self._lock_for(chat_id):
            available = self._playable_assets()
            if not available:
                raise GuessAgeServiceError("GUESS_AGE_ASSET_MISSING")
            for _ in range(4):
                existing = await self._get(chat_id)
                if existing and validate_guess_age_state(existing) and existing.get("status") == "playing":
                    raise GuessAgeServiceError("GUESS_AGE_ACTIVE")
                if existing and existing.get("pending_scores"):
                    raise GuessAgeServiceError("GUESS_AGE_REWARDS_PENDING")
                if existing and existing.get("delivery_pending"):
                    raise GuessAgeServiceError("GUESS_AGE_ROUND_PREPARING")
                recent = list(dict.fromkeys((existing or {}).get("recent_asset_keys", [])))
                recent = recent[-max(1, len(available) - 1):]
                asset = self._pick_asset([item for item in available if item.key not in recent] or available)
                state = new_guess_age_state(
                    owner_id=owner_id, owner_name=owner_name, chat_id=chat_id,
                    thread_id=thread_id, rounds=self.rounds,
                    round_seconds=self.round_seconds, asset=asset,
                )
                self.asset_path(state)
                state["recent_asset_keys"] = [*recent, asset.key][-max(1, len(available) - 1):]
                state["catalog_size"] = len(available)
                try:
                    if existing is None:
                        created = await self.database.create_game_state_if_absent(chat_id, self.GAME_TYPE, state)
                    else:
                        created = await self._cas(chat_id, existing, state)
                except GuessAgeServiceError:
                    raise
                except Exception:
                    raise GuessAgeServiceError("GUESS_AGE_STORAGE_UNAVAILABLE") from None
                if created:
                    return state
            raise GuessAgeServiceError("GUESS_AGE_CONFLICT")

    async def submit(self, *, chat_id: int, user_id: int, display_name: str, guess: object, expected_round_id: str | None = None, expected_round: int | None = None) -> tuple[dict[str, Any], Any]:
        def transition(previous, state):
            if not state or not validate_guess_age_state(state):
                raise GuessAgeServiceError("GUESS_AGE_NOT_FOUND")
            if ((expected_round_id is not None and state.get("round_id") != expected_round_id)
                    or (expected_round is not None and int(state["round"]) != int(expected_round))):
                raise GuessAgeServiceError("GUESS_AGE_STALE_ROUND")
            if state.get("delivery_pending") or state.get("initial_delivery_pending"):
                raise GuessAgeServiceError("GUESS_AGE_ROUND_PREPARING")
            if round_expired(state):
                state["status"] = "expired"
                return state, GuessAgeServiceError("GUESS_AGE_ROUND_EXPIRED")
            outcome = submit_guess(state, user_id=user_id, display_name=display_name, guess=guess)
            if not outcome.accepted:
                raise GuessAgeServiceError(outcome.code)
            return state, outcome

        state, outcome = await self._apply(chat_id, transition)
        if isinstance(outcome, GuessAgeServiceError):
            raise outcome
        return state, outcome

    async def close_round(self, *, chat_id: int, expected_round_id: str | None = None, expected_round: int | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        def transition(previous, state):
            if not state or not validate_guess_age_state(state):
                raise GuessAgeServiceError("GUESS_AGE_NOT_FOUND")
            if expected_round_id and str(state.get("round_id")) != str(expected_round_id):
                raise GuessAgeServiceError("GUESS_AGE_STALE_ROUND")
            if expected_round is not None and int(state.get("round", 0)) != int(expected_round):
                raise GuessAgeServiceError("GUESS_AGE_STALE_ROUND")
            if state.get("delivery_pending") or state.get("initial_delivery_pending"):
                raise GuessAgeServiceError("GUESS_AGE_ROUND_PREPARING")
            if state.get("status") == "expired":
                # Expiration closes the current round but still allows the
                # owner/scheduler to record its ranking and advance safely.
                state["status"] = "playing"
            previous_round = int(state.get("round", 1))
            next_asset = self._next_asset(state)
            finish_round(state, next_asset=next_asset)
            state["last_closed_round"] = previous_round
            state.update(delivery_pending=True, delivery_stage=0,
                         delivery_id=f"{state['round_id']}:{previous_round}",
                         previous_prompt_message_id=state.get("prompt_message_id", 0))
            state["recent_asset_keys"] = list(dict.fromkeys([*state.get("recent_asset_keys", []), state["asset_key"]]))[-max(1, int(state.get("catalog_size") or len(self._playable_assets())) - 1):]
            history = state.get("history", [])
            if len(history) >= 2 and not any(item.get("ranked") for item in history[-2:]):
                state.update(status="finished", finish_reason="inactive")
            for row in history[-1].get("ranked", []):
                state.setdefault("pending_scores", []).append({
                    "user_id": int(row.get("user_id", 0)), "chat_id": int(chat_id), "game_type": self.GAME_TYPE,
                    "score": max(0, min(100, int(row.get("score", 0)))),
                    "user_name": str(row.get("display_name") or "Oyuncu")[:80],
                    "event_id": f"guess_age:{chat_id}:{state['round_id']}:{previous_round}:{row.get('user_id')}",
                })
            return state, list(state.get("history", [])[-1].get("ranked", []))

        state, ranked = await self._apply(chat_id, transition)
        await self.settle_scores(chat_id)
        return await self._get(chat_id), ranked

    async def settle_scores(self, chat_id):
        async with self._lock_for(chat_id):
            for _ in range(4):
                state = await self._get(chat_id)
                if not state or not state.get("pending_scores"):
                    return
                if self.leaderboard_service is None:
                    return
                pending = []
                for row in state["pending_scores"]:
                    try:
                        await self.leaderboard_service.record_score(**row)
                    except Exception:
                        pending.append(row)
                if len(pending) == len(state["pending_scores"]):
                    return
                updated = deepcopy(state)
                updated["pending_scores"] = pending
                if await self._cas(chat_id, state, updated):
                    return
            raise GuessAgeServiceError("GUESS_AGE_CONFLICT")

    async def mark_delivery(self, chat_id, delivery_id, stage, message_id=0):
        def transition(previous, state):
            if not state or state.get("delivery_id") != delivery_id:
                raise GuessAgeServiceError("GUESS_AGE_STALE_ROUND")
            if int(stage) not in {1, 2}:
                raise GuessAgeServiceError("GUESS_AGE_STALE_ROUND")
            if int(state.get("delivery_stage", 0)) >= int(stage):
                return previous, None
            state["delivery_stage"] = int(stage)
            state.update(delivery_retry_at=0, delivery_failures=0)
            if stage == 1:
                visible_until = time.time() + 8
                state["result_visible_until"] = visible_until
                state["delivery_retry_at"] = visible_until
            if stage == 2 and message_id:
                state["prompt_message_id"] = int(message_id)
            if stage >= 2:
                state["delivery_pending"] = False
                state["choice_prompts"] = {}
                if state.get("status") == "playing":
                    state["round_started_at"] = datetime.now(timezone.utc).isoformat()
                    state["round_deadline"] = datetime.now(timezone.utc).timestamp() + int(state["round_seconds"])
            return state, None

        state, _ = await self._apply(chat_id, transition)
        return state

    async def defer_delivery(self, chat_id):
        def transition(previous, state):
            if state:
                failures = int(state.get("delivery_failures", 0)) + 1
                state.update(delivery_failures=failures, delivery_retry_at=datetime.now(timezone.utc).timestamp() + min(300, 5 * 2 ** min(failures, 6)))
                return state, None
            return previous, None

        await self._apply(chat_id, transition)

    async def cancel(self, chat_id: int, *, expected_round_id: str | None = None,
                     expected_round: int | None = None) -> bool:
        def transition(previous, state):
            if not state:
                return previous, False
            if ((expected_round_id is not None and str(state.get("round_id")) != str(expected_round_id))
                    or (expected_round is not None and int(state.get("round", 0)) != int(expected_round))):
                raise GuessAgeServiceError("GUESS_AGE_STALE_ROUND")
            state["status"] = "cancelled"
            state["delivery_pending"] = False
            state["initial_delivery_pending"] = False
            return state, True

        _, cancelled = await self._apply(chat_id, transition)
        return cancelled

    async def set_prompt_message(self, chat_id: int, message_id: int, *, reset_clock: bool = True,
                                 expected_round_id: str | None = None, expected_round: int | None = None) -> dict[str, Any]:
        def transition(previous, state):
            if not state or not validate_guess_age_state(state):
                raise GuessAgeServiceError("GUESS_AGE_NOT_FOUND")
            if ((expected_round_id is not None and state["round_id"] != expected_round_id)
                    or (expected_round is not None and int(state["round"]) != int(expected_round))):
                raise GuessAgeServiceError("GUESS_AGE_STALE_ROUND")
            if state.get("status") != "playing" or state.get("delivery_pending"):
                raise GuessAgeServiceError("GUESS_AGE_STALE_ROUND")
            state["prompt_message_id"] = int(message_id)
            if reset_clock:
                state["initial_delivery_pending"] = False
                state["round_started_at"] = datetime.now(timezone.utc).isoformat()
                state["round_deadline"] = datetime.now(timezone.utc).timestamp() + int(state["round_seconds"])
            return state, None

        state, _ = await self._apply(chat_id, transition)
        return state

    async def set_choice_prompt(self, chat_id, user_id, message_id, *, round_id, round_no):
        def transition(previous, state):
            if not state or state.get("status") != "playing":
                raise GuessAgeServiceError("GUESS_AGE_NOT_FOUND")
            if state.get("round_id") != round_id or int(state["round"]) != int(round_no):
                raise GuessAgeServiceError("GUESS_AGE_STALE_ROUND")
            if state.get("delivery_pending") or state.get("initial_delivery_pending") or round_expired(state):
                raise GuessAgeServiceError("GUESS_AGE_ROUND_PREPARING")
            previous = state.setdefault("choice_prompts", {}).get(str(user_id))
            state["choice_prompts"][str(user_id)] = int(message_id)
            return state, previous

        _, previous = await self._apply(chat_id, transition)
        return previous

    @staticmethod
    def ranking(state: dict[str, Any]) -> list[dict[str, Any]]:
        return final_ranking(state)

