"""Durable CAS orchestration for memory pairs and minesweeper."""

from __future__ import annotations

import asyncio
import secrets
import time

from src.games import grid_puzzles as grid
from src.observability.metrics import MetricsRegistry


def base36(value):
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    value = int(value)
    answer = ""
    while value:
        value, digit = divmod(value, 36)
        answer = digits[digit] + answer
    return answer or "0"


class GridPuzzlesService:
    GAME_KEYS = grid.GAME_KEYS

    def __init__(self, repository, leaderboard=None, economy=None, metrics=None):
        self.repository = repository
        self.leaderboard = leaderboard
        self.economy = economy
        self.metrics = metrics if metrics is not None else MetricsRegistry()
        self._locks = {}

    @staticmethod
    def key(game_key, thread_id=None):
        if game_key not in grid.GAME_KEYS:
            raise ValueError("grid_invalid_game")
        return game_key if thread_id is None else f"{game_key}_t{int(thread_id)}"

    def _lock(self, game_key, chat_id, thread_id):
        return self._locks.setdefault((game_key, int(chat_id), thread_id), asyncio.Lock())

    async def current(self, *, game_key, chat_id, thread_id=None):
        return await self.repository.get_game_state(int(chat_id), self.key(game_key, thread_id))

    async def _apply(self, *, game_key, chat_id, thread_id=None, action_id, transform,
                     round_id=None, phase_seq=None, turn_seq=None):
        if not action_id or len(str(action_id)) > 180:
            raise ValueError("grid_invalid_action")
        async with self._lock(game_key, chat_id, thread_id):
            for _ in range(8):
                state = await self.current(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
                if not state:
                    raise ValueError("grid_not_found")
                if round_id is not None and state["round_id"] != round_id:
                    raise ValueError("grid_stale_round")
                if phase_seq is not None and int(state["phase_seq"]) != int(phase_seq):
                    raise ValueError("grid_stale_phase")
                if turn_seq is not None and int(state["turn_seq"]) != int(turn_seq):
                    raise ValueError("grid_stale_turn")
                if str(action_id) in state.get("processed_actions", []):
                    self.metrics.inc("game_duplicate_action_total")
                    return "duplicate", state
                result = transform(state)
                if result is state or int(result["version"]) == int(state["version"]):
                    return "ignored", state
                before, after = grid.public_view(state), grid.public_view(result)
                for view in (before, after):
                    view.pop("version", None)
                changed = before != after
                result["panel_dirty"] = bool(state.get("panel_dirty") or changed)
                if changed:
                    result["delivery_retry_at"] = 0
                result["version"] = int(state["version"]) + 1
                result["processed_actions"] = [*state.get("processed_actions", []), str(action_id)][-1024:]
                if await self.repository.compare_and_save_game_state(
                    int(chat_id), self.key(game_key, thread_id), result,
                    expected_version=int(state["version"])):
                    self.metrics.inc("game_action_total")
                    return "applied", result
            raise RuntimeError("game_state_conflict")

    async def open(self, *, game_key, chat_id, thread_id=None, user_id, user_name):
        if game_key not in grid.GAME_KEYS:
            raise ValueError("grid_invalid_game")
        async with self._lock(game_key, chat_id, thread_id):
            for _ in range(8):
                current = await self.current(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
                if current and current["phase"] not in grid.TERMINAL:
                    return "existing", current
                if current and current.get("score_pending"):
                    raise ValueError("grid_rewards_pending")
                common = dict(chat_id=chat_id, thread_id=thread_id, owner_id=user_id,
                              owner_name=user_name, round_id=secrets.token_hex(6))
                fresh = (grid.new_memory(**common) if game_key == grid.MEMORY_KEY else
                         grid.new_mines(**common))
                fresh.update(leaderboard_required=self.leaderboard is not None,
                             economy_required=self.economy is not None)
                if current is None:
                    saved = await self.repository.create_game_state_if_absent(
                        int(chat_id), self.key(game_key, thread_id), fresh)
                else:
                    fresh["version"] = int(current["version"]) + 1
                    saved = await self.repository.compare_and_save_game_state(
                        int(chat_id), self.key(game_key, thread_id), fresh,
                        expected_version=int(current["version"]))
                if saved:
                    self.metrics.inc("game_lobby_created_total")
                    return "created", fresh
            raise RuntimeError("game_state_conflict")

    async def act(self, *, game_key, chat_id, thread_id=None, user_id, action_id,
                  action, user_name="Oyuncu", round_id=None, phase_seq=None,
                  turn_seq=None, index=None, moderator=False, now=None):
        clock = time.time() if now is None else float(now)

        def transition(state):
            if action == "join":
                return grid.memory_join(state, user_id=user_id, user_name=user_name, now=clock)
            if action == "start":
                return grid.memory_start(state, user_id=user_id, now=clock)
            if action == "pick":
                return grid.memory_pick(state, user_id=user_id, index=index, now=clock)
            if action == "open":
                return grid.mine_open(state, user_id=user_id, index=index, now=clock)
            if action == "flag":
                return grid.mine_flag(state, user_id=user_id, index=index, now=clock)
            if action == "mode":
                return grid.mine_mode(state, user_id=user_id,
                                      enabled=index == 1 if index in {0, 1} else None,
                                      now=clock)
            if action == "cancel":
                return grid.cancel(state, user_id=user_id, moderator=moderator)
            raise ValueError("grid_invalid_action")

        return await self._apply(game_key=game_key, chat_id=chat_id, thread_id=thread_id,
            action_id=action_id, transform=transition, round_id=round_id,
            phase_seq=phase_seq, turn_seq=turn_seq)

    async def advance(self, *, game_key, chat_id, thread_id=None, round_id, now=None):
        clock = time.time() if now is None else float(now)
        for _ in range(4):
            state = await self.current(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
            if not state:
                raise ValueError("grid_not_found")
            if state["round_id"] != round_id:
                raise ValueError("grid_stale_round")
            seq = int(state["phase_seq"])
            try:
                return await self._apply(game_key=game_key, chat_id=chat_id,
                    thread_id=thread_id, action_id=f"clock:{round_id}:{seq}:{clock}",
                    transform=lambda row: grid.advance(row, now=clock),
                    round_id=round_id, phase_seq=seq)
            except ValueError as exc:
                if str(exc) != "grid_stale_phase":
                    raise
        raise RuntimeError("game_state_conflict")

    async def claim_delivery(self, *, game_key, chat_id, thread_id=None, now=None, force=False):
        clock = time.time() if now is None else float(now)
        async with self._lock(game_key, chat_id, thread_id):
            for _ in range(8):
                state = await self.current(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
                if not state or (not state.get("panel_dirty") and not force) or float(state.get("delivery_retry_at", 0)) > clock:
                    return None
                updated = dict(state, version=int(state["version"]) + 1,
                               delivery_retry_at=clock + 30)
                if await self.repository.compare_and_save_game_state(
                    int(chat_id), self.key(game_key, thread_id), updated,
                    expected_version=int(state["version"])):
                    return updated
            raise RuntimeError("game_state_conflict")

    async def acknowledge_panel(self, *, game_key, chat_id, thread_id=None,
                                round_id, expected_version, message_id, now=None):
        clock = time.time() if now is None else float(now)
        async with self._lock(game_key, chat_id, thread_id):
            for _ in range(8):
                state = await self.current(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
                if not state or state["round_id"] != round_id:
                    return False
                if (state["phase"] in {"preview", "turn", "mismatch", "playing"}
                        and not state["phase_ready"] and state["panel_wait_until"] is not None
                        and clock >= state["panel_wait_until"]):
                    closed = grid.cancel(state, reason="panel_timeout")
                    closed["version"] = int(state["version"]) + 1
                    closed["panel_dirty"] = True
                    closed["delivery_retry_at"] = 0
                    if int(state["version"]) == int(expected_version):
                        closed["panel_message_id"] = int(message_id)
                    if await self.repository.compare_and_save_game_state(
                        int(chat_id), self.key(game_key, thread_id), closed,
                        expected_version=int(state["version"])):
                        return False
                    continue
                if int(state["version"]) != int(expected_version):
                    if not state.get("panel_dirty"):
                        repair = dict(state, version=int(state["version"]) + 1,
                                      panel_dirty=True, delivery_retry_at=0)
                        if await self.repository.compare_and_save_game_state(
                            int(chat_id), self.key(game_key, thread_id), repair,
                            expected_version=int(state["version"])):
                            return False
                        continue
                    return False
                updated = dict(state, version=int(state["version"]) + 1,
                               panel_message_id=int(message_id))
                updated["panel_dirty"] = False
                if (state["phase"] in {"preview", "turn", "mismatch", "playing"}
                        and not state["phase_ready"] and state["panel_wait_until"] is not None):
                    active = grid.activate(state, now=clock)
                    updated.update(phase_ready=True, deadline=active["deadline"],
                                   panel_wait_until=None, panel_dirty=True,
                                   delivery_retry_at=0)
                if await self.repository.compare_and_save_game_state(
                    int(chat_id), self.key(game_key, thread_id), updated,
                    expected_version=int(state["version"])):
                    return True
            raise RuntimeError("game_state_conflict")

    async def settle_scores(self, *, game_key, chat_id, thread_id=None):
        async with self._lock(game_key, chat_id, thread_id):
            for _ in range(8):
                state = await self.current(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
                if not state or state["phase"] != "finished" or not state["score_pending"]:
                    return
                if (state.get("leaderboard_required") is not False and self.leaderboard is None or
                        state.get("economy_required") is not False and self.economy is None):
                    raise RuntimeError("game_score_store_unavailable")
                for player in state["players"]:
                    score = (int(player["score"]) if game_key == grid.MEMORY_KEY else 10)
                    event = f"{game_key}:{chat_id}:{state['round_id']}:{player['id']}"
                    if state.get("leaderboard_required") is not False and self.leaderboard:
                        await self.leaderboard.record_score(user_id=player["id"], chat_id=int(chat_id),
                            game_type=game_key, score=score, user_name=player["name"], event_id=event)
                    if state.get("economy_required") is not False and self.economy and score > 0 and player["id"] in state["winner_ids"]:
                        await self.economy.credit(player["id"], int(chat_id), 5,
                            reason=f"{game_key}_reward", event_id=f"coin:{event}")
                updated = dict(state, version=int(state["version"]) + 1,
                               score_pending=False, panel_dirty=True, delivery_retry_at=0)
                if await self.repository.compare_and_save_game_state(
                    int(chat_id), self.key(game_key, thread_id), updated,
                    expected_version=int(state["version"])):
                    return
            raise RuntimeError("game_state_conflict")
