"""Persistent, versioned clue arcade sessions and idempotent score settlement."""
from __future__ import annotations

import asyncio
import secrets
import time

from src.games import clue_arcade as rules
from src.observability.metrics import MetricsRegistry


def _stage(state):
    return (state["phase"], state["phase_seq"], state.get("deadline"),
            state.get("panel_wait_until"), state.get("lobby_expires_at"))


class ClueArcadeService:
    GAME_KEYS = rules.GAME_KEYS

    def __init__(self, repository, leaderboard=None, economy=None, metrics=None):
        self.repository, self.leaderboard, self.economy = repository, leaderboard, economy
        self.metrics = metrics if metrics is not None else MetricsRegistry()
        self._locks = {}

    @staticmethod
    def key(game_key, thread_id=None):
        if game_key not in rules.GAME_KEYS:
            raise ValueError("ca_invalid_game")
        return game_key if thread_id is None else f"{game_key}_t{int(thread_id)}"

    def _lock(self, game_key, chat_id, thread_id):
        return self._locks.setdefault((game_key, int(chat_id), thread_id), asyncio.Lock())

    async def current(self, *, game_key, chat_id, thread_id=None):
        return await self.repository.get_game_state(int(chat_id), self.key(game_key, thread_id))

    async def open(self, *, game_key, chat_id, thread_id=None, user_id, user_name,
                   solo=False, now=None, seed=None):
        key = self.key(game_key, thread_id)
        for _ in range(10):
            async with self._lock(game_key, chat_id, thread_id):
                prior = await self.current(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
                if prior and prior["phase"] not in rules.TERMINAL:
                    return "existing", prior
                if not prior or not prior.get("score_pending"):
                    fresh = rules.new(game_key=game_key, chat_id=chat_id,
                        thread_id=thread_id, owner_id=user_id, owner_name=user_name,
                        round_id=secrets.token_hex(6), prior=prior, solo=solo,
                        now=now, seed=seed)
                    fresh.update(leaderboard_required=self.leaderboard is not None,
                                 economy_required=self.economy is not None)
                    if prior is None:
                        saved = await self.repository.create_game_state_if_absent(
                            int(chat_id), key, fresh)
                    else:
                        fresh["version"] = prior["version"] + 1
                        saved = await self.repository.compare_and_save_game_state(
                            int(chat_id), key, fresh, expected_version=prior["version"])
                    if saved:
                        self.metrics.inc("clue_arcade_open_total")
                        return "created", fresh
            await self.settle_scores(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
        raise RuntimeError("game_state_conflict")

    async def _apply(self, *, game_key, chat_id, thread_id, round_id, action_id,
                     transition, phase_seq=None):
        if not action_id or len(str(action_id)) > 180:
            raise ValueError("ca_invalid_action")
        async with self._lock(game_key, chat_id, thread_id):
            for _ in range(8):
                state = await self.current(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
                if not state:
                    raise ValueError("ca_not_found")
                if state["round_id"] != round_id:
                    raise ValueError("ca_stale_round")
                if phase_seq is not None and state["phase_seq"] != int(phase_seq):
                    raise ValueError("ca_stale_phase")
                if str(action_id) in state.get("processed_actions", []):
                    self.metrics.inc("clue_arcade_duplicate_total")
                    return "duplicate", state
                result = transition(state)
                if result is state:
                    return "ignored", state
                if result["version"] != state["version"] + 1:
                    raise RuntimeError("ca_invalid_transition")
                before, after = rules.public_state(state), rules.public_state(result)
                for view in (before, after):
                    for key in ("version", "last_activity_at", "attempts"):
                        view.pop(key, None)
                changed = before != after
                result["panel_dirty"] = bool(state.get("panel_dirty") or changed)
                if changed:
                    result["delivery_retry_at"] = 0
                result["processed_actions"] = [*state.get("processed_actions", []), str(action_id)][-1024:]
                if await self.repository.compare_and_save_game_state(
                    int(chat_id), self.key(game_key, thread_id), result,
                    expected_version=state["version"]):
                    self.metrics.inc("clue_arcade_action_total")
                    return "applied", result
        raise RuntimeError("game_state_conflict")

    async def act(self, *, game_key, chat_id, thread_id=None, user_id, action_id,
                  action, round_id, phase_seq=None, user_name="Oyuncu", text="",
                  moderator=False, message_id=None, submitted_at=None, now=None):
        moment = time.time() if now is None else float(now)

        def transition(state):
            if action == "join":
                return rules.join(state, user_id=user_id, user_name=user_name, now=moment)
            if action == "start":
                return rules.start(state, user_id=user_id, now=moment)
            if action == "leave":
                return rules.leave(state, user_id=user_id, now=moment)
            if action == "stop":
                return rules.cancel(state, user_id=user_id, moderator=moderator, now=moment)
            if action == "pass":
                return rules.pass_clue(state, user_id=user_id, now=moment)
            if action == "text":
                if (message_id is None or submitted_at is None
                        or int(message_id) <= int(state.get("input_floor") or 0)
                        or int(submitted_at) < int(
                            state.get("input_opened_at") or state["created_at"])):
                    raise ValueError("ca_stale_message")
                return rules.guess(state, user_id=user_id, user_name=user_name,
                                   text=text, now=moment)
            raise ValueError("ca_invalid_action")

        return await self._apply(game_key=game_key, chat_id=chat_id, thread_id=thread_id,
            round_id=round_id, action_id=action_id, transition=transition, phase_seq=phase_seq)

    async def advance(self, *, game_key, chat_id, thread_id=None, round_id, now=None):
        moment = time.time() if now is None else float(now)
        observed = await self.current(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
        if not observed:
            raise ValueError("ca_not_found")
        stage = _stage(observed)
        return await self._apply(game_key=game_key, chat_id=chat_id,
            thread_id=thread_id, round_id=round_id,
            action_id=f"clock:{round_id}:{stage[1]}:{secrets.token_hex(6)}",
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
                    if not state.get("panel_dirty"):
                        repair = dict(state, version=state["version"] + 1,
                                      panel_dirty=True, delivery_retry_at=0)
                        if await self.repository.compare_and_save_game_state(
                            int(chat_id), self.key(game_key, thread_id), repair,
                            expected_version=state["version"]):
                            return False
                        continue
                    return False
                result = dict(state, version=state["version"] + 1,
                              panel_message_id=int(message_id), panel_dirty=False,
                              delivery_retry_at=0)
                if (state.get("panel_wait_until") is not None and
                        moment >= state["panel_wait_until"]):
                    result = rules.cancel(state, user_id=state["owner_id"],
                                          now=moment, reason="panel_timeout")
                    result.update(panel_message_id=int(message_id), panel_dirty=True,
                                  delivery_retry_at=0)
                    accepted = False
                else:
                    accepted = True
                    if state["phase"] in {"playing", "reveal"} and state["deadline"] is None:
                        duration = (rules.REVEAL_SECONDS if state["phase"] == "reveal" else
                                    rules.CLUE_SECONDS if game_key == rules.BES_IPUCU else
                                    rules.EMOJI_SECONDS)
                        result.update(deadline=moment + duration, panel_wait_until=None,
                                      input_opened_at=moment, input_floor=int(message_id))
                if await self.repository.compare_and_save_game_state(
                    int(chat_id), self.key(game_key, thread_id), result,
                    expected_version=state["version"]):
                    return accepted
        raise RuntimeError("game_state_conflict")

    async def settle_scores(self, *, game_key, chat_id, thread_id=None):
        async with self._lock(game_key, chat_id, thread_id):
            for _ in range(8):
                state = await self.current(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
                if not state or state["phase"] != "finished" or not state.get("score_pending"):
                    return
                if (state.get("leaderboard_required") is not False and self.leaderboard is None or
                        state.get("economy_required") is not False and self.economy is None):
                    raise RuntimeError("game_score_store_unavailable")
                for player in state["players"]:
                    if not player.get("active", True) or int(player["score"]) <= 0:
                        continue
                    points = int(player["score"])
                    event = f"{game_key}:{chat_id}:{state['round_id']}:{player['id']}"
                    if state.get("leaderboard_required") is not False and self.leaderboard:
                        await self.leaderboard.record_score(
                            user_id=player["id"], chat_id=int(chat_id), game_type=game_key,
                            score=points, user_name=player["name"], event_id=event)
                    if state.get("economy_required") is not False and self.economy and player["id"] in state["winner_ids"]:
                        await self.economy.credit(player["id"], int(chat_id),
                            max(1, points // 20), reason=f"{game_key}_reward",
                            event_id=f"coin:{event}")
                result = dict(state, version=state["version"] + 1,
                              score_pending=False, panel_dirty=True, delivery_retry_at=0)
                if await self.repository.compare_and_save_game_state(
                    int(chat_id), self.key(game_key, thread_id), result,
                    expected_version=state["version"]):
                    self.metrics.inc("clue_arcade_settled_total")
                    return
        raise RuntimeError("game_state_conflict")
