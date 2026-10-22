"""Durable, idempotent word-chain session orchestration."""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from src.games import word_chain
from src.observability.metrics import MetricsRegistry


class WordChainService:
    GAME_TYPE = "kelime_zinciri"

    def __init__(self, repository: Any, *, leaderboard=None, economy=None, metrics=None) -> None:
        self.repository = repository
        self.leaderboard, self.economy = leaderboard, economy
        self.metrics = metrics if metrics is not None else MetricsRegistry()
        self._locks: dict[tuple[int, int | None], asyncio.Lock] = {}

    @classmethod
    def _key(cls, thread_id: int | None) -> str:
        return cls.GAME_TYPE if thread_id is None else f"{cls.GAME_TYPE}_t{int(thread_id)}"

    def _lock(self, chat_id: int, thread_id: int | None) -> asyncio.Lock:
        return self._locks.setdefault((int(chat_id), thread_id), asyncio.Lock())

    async def current(self, chat_id: int, thread_id: int | None) -> dict | None:
        return await self.repository.get_game_state(int(chat_id), self._key(thread_id))

    async def settle_scores(self, *, chat_id: int, thread_id: int | None) -> None:
        async with self._lock(chat_id, thread_id):
            for _ in range(4):
                state = await self.current(chat_id, thread_id)
                if not state or state.get("phase") != "finished" or not state.get("score_pending"):
                    return
                if self.leaderboard is None:
                    raise RuntimeError("game_score_store_unavailable")
                for player in state["players"]:
                    points = min(100, max(0, int(state["scores"].get(str(player["id"]), 0))))
                    if not points:
                        continue
                    event_id = f"chain:{chat_id}:{state['round_id']}:{player['id']}"
                    await self.leaderboard.record_score(
                        user_id=int(player["id"]), chat_id=int(chat_id), game_type=self.GAME_TYPE,
                        score=points, user_name=player["name"], event_id=event_id,
                    )
                    if self.economy is not None:
                        await self.economy.credit(
                            int(player["id"]), int(chat_id), max(1, points // 2),
                            reason="kelime_zinciri_reward", event_id=f"coin:{event_id}",
                        )
                settled = dict(state)
                settled.update(score_pending=False, version=int(state["version"]) + 1)
                if await self.repository.compare_and_save_game_state(
                    chat_id, self._key(thread_id), settled, expected_version=int(state["version"]),
                ):
                    self.metrics.inc("game_score_settlement_total")
                    return
            raise RuntimeError("game_state_conflict")

    async def expire(
        self, *, chat_id: int, thread_id: int | None, expected_round_id: str, now: float,
    ) -> dict | None:
        key = self._key(thread_id)
        async with self._lock(chat_id, thread_id):
            for _ in range(4):
                state = await self.current(chat_id, thread_id)
                if not state or state.get("round_id") != expected_round_id or state.get("phase") != "playing":
                    return None
                if float(state["turn_expires_at"]) > now:
                    return None
                advanced = word_chain.expire_turn(state, now=now)
                advanced["panel_dirty"] = True
                if await self.repository.compare_and_save_game_state(
                    chat_id, key, advanced, expected_version=int(state["version"]),
                ):
                    return advanced
            raise RuntimeError("game_state_conflict")

    async def claim_panel(self, *, chat_id: int, thread_id: int | None, now: float) -> dict | None:
        """Lease a pending delivery; an interrupted sender can retry after 30s."""
        async with self._lock(chat_id, thread_id):
            for _ in range(4):
                state = await self.current(chat_id, thread_id)
                if not state or not state.get("panel_dirty") or float(state.get("delivery_retry_at", 0)) > now:
                    return None
                claimed = dict(state, delivery_retry_at=now + 30, version=int(state["version"]) + 1)
                if await self.repository.compare_and_save_game_state(
                    chat_id, self._key(thread_id), claimed, expected_version=int(state["version"]),
                ):
                    return claimed
            raise RuntimeError("game_state_conflict")

    async def acknowledge_panel(
        self, *, chat_id: int, thread_id: int | None, round_id: str, expected_version: int,
    ) -> bool:
        key = self._key(thread_id)
        async with self._lock(chat_id, thread_id):
            for _ in range(4):
                state = await self.current(chat_id, thread_id)
                if (not state or state.get("round_id") != round_id or not state.get("panel_dirty")
                        or int(state["version"]) != expected_version):
                    return False
                updated = dict(state)
                updated["panel_dirty"] = False
                updated["version"] = int(state["version"]) + 1
                if await self.repository.compare_and_save_game_state(
                    chat_id, key, updated, expected_version=int(state["version"]),
                ):
                    return True
            raise RuntimeError("game_state_conflict")

    async def open(
        self, *, chat_id: int, thread_id: int | None, user_id: int, user_name: str,
    ) -> tuple[str, dict]:
        key = self._key(thread_id)
        previous = await self.current(chat_id, thread_id)
        if previous and previous.get("score_pending"):
            await self.settle_scores(chat_id=chat_id, thread_id=thread_id)
        async with self._lock(chat_id, thread_id):
            for _ in range(4):
                current = await self.current(chat_id, thread_id)
                if current and current.get("phase") in {"lobby", "playing"}:
                    return "existing", current
                fresh = word_chain.new_lobby(
                    chat_id=chat_id, thread_id=thread_id, owner_id=user_id, owner_name=user_name,
                )
                if current is None:
                    if await self.repository.create_game_state_if_absent(chat_id, key, fresh):
                        self.metrics.inc("game_lobby_created_total")
                        return "created", fresh
                else:
                    if current.get("score_pending"):
                        raise ValueError("word_chain_scores_pending")
                    fresh["version"] = int(current["version"]) + 1
                    fresh["message_watermark"] = int(current.get("message_watermark", 0))
                    if await self.repository.compare_and_save_game_state(
                        chat_id, key, fresh, expected_version=int(current["version"]),
                    ):
                        return "created", fresh
            raise RuntimeError("game_state_conflict")

    async def act(
        self, *, chat_id: int, thread_id: int | None, user_id: int, action_id: str,
        action: str, value: str | None = None, user_name: str = "Oyuncu", moderator: bool = False,
        expected_round_id: str | None = None, expected_turn: int | None = None,
        submitted_at: float | None = None,
    ) -> tuple[str, dict]:
        key = self._key(thread_id)
        async with self._lock(chat_id, thread_id):
            if not action_id or len(action_id) > 180:
                raise ValueError("word_chain_invalid_action")
            message_id = int(action_id.split(":", 1)[1]) if action_id.startswith(("cmd:", "msg:")) and action_id.split(":", 1)[1].isdigit() else None
            try:
                transition: Callable[[dict], dict]
                if action == "join":
                    transition = lambda state: word_chain.join(state, user_id=user_id, user_name=user_name)
                elif action == "leave":
                    transition = lambda state: word_chain.leave(state, user_id=user_id)
                elif action == "start":
                    transition = lambda state: word_chain.start(state, user_id=user_id)
                elif action == "word":
                    transition = lambda state: word_chain.submit(state, user_id=user_id, text=value or "")
                elif action == "pass":
                    transition = lambda state: word_chain.pass_turn(state, user_id=user_id)
                elif action == "stop":
                    transition = lambda state: word_chain.stop(state, user_id=user_id, moderator=moderator)
                else:
                    raise ValueError("word_chain_unknown_action")
                for _ in range(4):
                    state = await self.current(chat_id, thread_id)
                    if state is None:
                        raise ValueError("word_chain_not_found")
                    if (action_id in state.get("processed_actions", []) or
                            message_id is not None and message_id <= int(state.get("message_watermark", 0))):
                        self.metrics.inc("game_duplicate_action_total")
                        return "duplicate", state
                    if expected_round_id is not None and state["round_id"] != expected_round_id:
                        raise ValueError("word_chain_stale_round")
                    if submitted_at is not None and int(submitted_at) < int(state.get("created_at", 0)):
                        raise ValueError("word_chain_stale_round")
                    if expected_turn is not None and int(state["turn_count"]) != int(expected_turn):
                        raise ValueError("word_chain_stale_turn")
                    if submitted_at is not None and action in {"word", "pass"} and int(submitted_at) < int(state.get("turn_started_at", 0)):
                        raise ValueError("word_chain_stale_turn")
                    next_state = transition(state)
                    next_state["panel_dirty"] = True
                    next_state["delivery_retry_at"] = 0
                    next_state["processed_actions"] = [*state.get("processed_actions", []), action_id][-512:]
                    if message_id is not None:
                        next_state["message_watermark"] = message_id
                    if await self.repository.compare_and_save_game_state(
                        chat_id, key, next_state, expected_version=int(state["version"]),
                    ):
                        self.metrics.inc("game_action_total")
                        if action == "start":
                            self.metrics.inc("game_started_total")
                        if next_state.get("phase") == "finished":
                            self.metrics.inc("game_completed_total")
                        return action, next_state
                raise RuntimeError("game_state_conflict")
            except Exception:
                # The receipt and mutation share one CAS write. A lost write
                # acknowledgement cannot turn a retry into a second action.
                self.metrics.inc("game_action_rejected_total")
                raise
