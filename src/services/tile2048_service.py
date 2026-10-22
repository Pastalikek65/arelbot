"""CAS persistence, panel delivery, and idempotent scores for 2048."""

from __future__ import annotations

import asyncio
import secrets
import time

from src.games import tile2048 as rules
from src.observability.metrics import MetricsRegistry


def _stage(state):
    return (state["phase"], state["phase_seq"], state.get("deadline"),
            state.get("panel_wait_until"))


class Tile2048Service:
    GAME_KEYS = rules.GAME_KEYS

    def __init__(self, repository, leaderboard=None, economy=None, metrics=None):
        self.repository = repository
        self.leaderboard = leaderboard
        self.economy = economy
        self.metrics = metrics if metrics is not None else MetricsRegistry()
        self._locks = {}

    @staticmethod
    def key(game_key, thread_id=None):
        if game_key not in rules.GAME_KEYS:
            raise ValueError("t2_invalid_game")
        return game_key if thread_id is None else f"{game_key}_t{int(thread_id)}"

    def _lock(self, game_key, chat_id, thread_id):
        return self._locks.setdefault((game_key, int(chat_id), thread_id), asyncio.Lock())

    async def current(self, *, game_key, chat_id, thread_id=None):
        return await self.repository.get_game_state(int(chat_id), self.key(game_key, thread_id))

    async def open(self, *, game_key, chat_id, thread_id=None, user_id, user_name,
                   now=None, rng=None):
        key = self.key(game_key, thread_id)
        for _ in range(12):
            async with self._lock(game_key, chat_id, thread_id):
                prior = await self.current(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
                if prior and prior["phase"] not in rules.TERMINAL:
                    return "existing", prior
                if not prior or not prior.get("score_pending"):
                    fresh = rules.new(chat_id=chat_id, thread_id=thread_id, owner_id=user_id,
                                      owner_name=user_name, round_id=secrets.token_hex(6),
                                      now=now, rng=rng)
                    fresh.update(leaderboard_required=self.leaderboard is not None,
                                 economy_required=self.economy is not None)
                    if prior is None:
                        saved = await self.repository.create_game_state_if_absent(int(chat_id), key, fresh)
                    else:
                        fresh["version"] = prior["version"] + 1
                        saved = await self.repository.compare_and_save_game_state(
                            int(chat_id), key, fresh, expected_version=prior["version"])
                    if saved:
                        self.metrics.inc("tile2048_open_total")
                        return "created", fresh
            await self.settle_scores(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
        raise RuntimeError("game_state_conflict")

    async def _apply(self, *, game_key, chat_id, thread_id, round_id,
                     action_id, transition, phase_seq=None):
        if not action_id or len(str(action_id)) > 180:
            raise ValueError("t2_invalid_action")
        async with self._lock(game_key, chat_id, thread_id):
            for _ in range(8):
                state = await self.current(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
                if not state:
                    raise ValueError("t2_not_found")
                if state["round_id"] != round_id:
                    raise ValueError("t2_stale_round")
                if str(action_id) in state.get("processed_actions", []):
                    self.metrics.inc("tile2048_duplicate_total")
                    return "duplicate", state
                if phase_seq is not None and int(phase_seq) != state["phase_seq"]:
                    raise ValueError("t2_stale_phase")
                result = transition(state)
                if result is state:
                    return "ignored", state
                if result["version"] != state["version"] + 1:
                    raise RuntimeError("t2_invalid_transition")
                before, after = rules.public_state(state), rules.public_state(result)
                for view in (before, after):
                    view.pop("version", None)
                    view.pop("last_activity_at", None)
                changed = before != after
                result["panel_dirty"] = bool(state.get("panel_dirty") or changed)
                if changed:
                    result["delivery_retry_at"] = 0
                result["processed_actions"] = [*state.get("processed_actions", []), str(action_id)][-2048:]
                if await self.repository.compare_and_save_game_state(
                    int(chat_id), self.key(game_key, thread_id), result,
                    expected_version=state["version"]):
                    self.metrics.inc("tile2048_action_total")
                    return "applied", result
        raise RuntimeError("game_state_conflict")

    async def act(self, *, game_key, chat_id, thread_id=None, user_id, action_id,
                  action, round_id, phase_seq=None, direction=None, moderator=False,
                  now=None, rng=None):
        moment = time.time() if now is None else float(now)

        def transition(state):
            if action == "move":
                return rules.move(state, user_id=user_id, direction=direction,
                                  now=moment, rng=rng)
            if action == "cashout":
                return rules.cashout(state, user_id=user_id, now=moment)
            if action == "stop":
                return rules.cancel(state, user_id=user_id, moderator=moderator, now=moment)
            raise ValueError("t2_invalid_action")

        return await self._apply(game_key=game_key, chat_id=chat_id,
            thread_id=thread_id, round_id=round_id, action_id=action_id,
            phase_seq=phase_seq, transition=transition)

    async def advance(self, *, game_key, chat_id, thread_id=None, round_id, now=None):
        moment = time.time() if now is None else float(now)
        observed = await self.current(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
        if not observed:
            raise ValueError("t2_not_found")
        stage = _stage(observed)
        return await self._apply(game_key=game_key, chat_id=chat_id,
            thread_id=thread_id, round_id=round_id,
            action_id=f"clock:{round_id}:{stage[1]}:{stage[0]}",
            transition=lambda state: rules.advance(state, now=moment)
            if _stage(state) == stage else state)

    async def claim_panel(self, *, game_key, chat_id, thread_id=None, now=None, force=False):
        moment = time.time() if now is None else float(now)
        async with self._lock(game_key, chat_id, thread_id):
            for _ in range(8):
                state = await self.current(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
                if (not state or (not state.get("panel_dirty") and not force)
                        or float(state.get("delivery_retry_at") or 0) > moment):
                    return None
                result = dict(state, version=state["version"] + 1,
                              delivery_retry_at=moment + 30)
                if await self.repository.compare_and_save_game_state(
                    int(chat_id), self.key(game_key, thread_id), result,
                    expected_version=state["version"]):
                    return result
        raise RuntimeError("game_state_conflict")

    async def acknowledge_panel(self, *, game_key, chat_id, thread_id=None,
                                round_id, expected_version, message_id, now=None):
        moment = time.time() if now is None else float(now)
        async with self._lock(game_key, chat_id, thread_id):
            for _ in range(8):
                state = await self.current(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
                if not state or state["round_id"] != round_id:
                    return False
                if state["version"] != int(expected_version):
                    repair = dict(state, version=state["version"] + 1,
                                  panel_dirty=True, delivery_retry_at=0)
                    if await self.repository.compare_and_save_game_state(
                        int(chat_id), self.key(game_key, thread_id), repair,
                        expected_version=state["version"]):
                        return False
                    continue
                result = dict(state, version=state["version"] + 1,
                              panel_message_id=int(message_id), panel_dirty=False,
                              delivery_retry_at=0)
                if state["phase"] == "playing" and state["deadline"] is None:
                    if moment >= min(float(state["panel_wait_until"]),
                                     float(state["session_expires_at"])):
                        result = rules.cancel(state, user_id=state["owner_id"],
                                              now=moment, reason="panel_timeout")
                        result.update(panel_message_id=int(message_id), panel_dirty=True,
                                      delivery_retry_at=0)
                        accepted = False
                    else:
                        result.update(deadline=min(moment + rules.TURN_SECONDS,
                                                   state["session_expires_at"]),
                                      panel_wait_until=None)
                        accepted = True
                else:
                    accepted = True
                if await self.repository.compare_and_save_game_state(
                    int(chat_id), self.key(game_key, thread_id), result,
                    expected_version=state["version"]):
                    return accepted
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
                event = f"2048:{chat_id}:{state['round_id']}:{state['owner_id']}"
                if state["score"] > 0:
                    if state.get("leaderboard_required") is not False and self.leaderboard:
                        await self.leaderboard.record_score(
                            user_id=state["owner_id"], chat_id=int(chat_id),
                            game_type=game_key, score=int(state["score"]),
                            user_name=state["players"][0]["name"], event_id=event)
                    if state.get("economy_required") is not False and self.economy and state["won"]:
                        await self.economy.credit(state["owner_id"], int(chat_id),
                            max(1, int(state["score"]) // 200), reason="2048_reward",
                            event_id=f"coin:{event}")
                result = dict(state, version=state["version"] + 1,
                              score_pending=False, panel_dirty=True, delivery_retry_at=0)
                if await self.repository.compare_and_save_game_state(
                    int(chat_id), self.key(game_key, thread_id), result,
                    expected_version=state["version"]):
                    self.metrics.inc("tile2048_settled_total")
                    return
        raise RuntimeError("game_state_conflict")
