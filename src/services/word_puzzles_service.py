"""CAS sessions and idempotent effects for three Turkish word games."""
from __future__ import annotations

import asyncio
import secrets
import time
from copy import deepcopy

from src.games import word_puzzles as games
from src.observability.metrics import MetricsRegistry


def _stage(state):
    return (state["phase"], state["phase_seq"], state.get("deadline"),
            state.get("panel_wait_until"), state.get("lobby_expires_at"))


class WordPuzzlesService:
    GAME_KEYS = games.GAME_KEYS

    def __init__(self, repository, leaderboard=None, economy=None, metrics=None):
        self.repository, self.leaderboard, self.economy = repository, leaderboard, economy
        self.metrics = metrics if metrics is not None else MetricsRegistry()
        self._locks = {}

    @staticmethod
    def key(game_key, thread_id=None):
        if game_key not in games.GAME_KEYS:
            raise ValueError("word_puzzles_invalid_game")
        return game_key if thread_id is None else f"{game_key}_t{int(thread_id)}"

    def _lock(self, game_key, chat_id, thread_id):
        return self._locks.setdefault((game_key, int(chat_id), thread_id), asyncio.Lock())

    async def current(self, *, game_key, chat_id, thread_id=None):
        return await self.repository.get_game_state(int(chat_id), self.key(game_key, thread_id))

    async def _apply(self, *, game_key, chat_id, thread_id, action_id, transition,
                     round_id=None, phase_seq=None):
        if not action_id or len(str(action_id)) > 180:
            raise ValueError("word_puzzles_invalid_action")
        async with self._lock(game_key, chat_id, thread_id):
            for _ in range(6):
                state = await self.current(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
                if state is None:
                    raise ValueError("word_puzzles_not_found")
                if round_id is not None and state["round_id"] != round_id:
                    raise ValueError("word_puzzles_stale_round")
                if phase_seq is not None and state["phase_seq"] != int(phase_seq):
                    raise ValueError("word_puzzles_stale_phase")
                if action_id in state.get("processed_actions", []):
                    self.metrics.inc("game_duplicate_action_total")
                    return "duplicate", state
                result = transition(state)
                if result is state:
                    return "ignored", state
                if result["version"] != state["version"] + 1:
                    raise RuntimeError("word_puzzles_invalid_transition")
                before, after = games.public_state(state), games.public_state(result)
                before.pop("version", None)
                after.pop("version", None)
                result["panel_dirty"] = bool(state.get("panel_dirty") or before != after)
                if before != after:
                    result["delivery_retry_at"] = 0
                if (result["phase"] in {"playing", "reveal"}
                        and result["deadline"] is None
                        and (state["phase"] != result["phase"] or
                             state["phase_seq"] != result["phase_seq"] or
                             state["deadline"] is not None)):
                    result["panel_wait_until"] = float(result["last_activity_at"]) + games.PANEL_WAIT_SECONDS
                result["processed_actions"] = [*state.get("processed_actions", []), action_id][-2048:]
                if await self.repository.compare_and_save_game_state(
                    int(chat_id), self.key(game_key, thread_id), result,
                    expected_version=state["version"]):
                    self.metrics.inc("game_action_total")
                    return "applied", result
            raise RuntimeError("game_state_conflict")

    async def open(self, *, game_key, chat_id, thread_id=None, user_id, user_name):
        self.key(game_key, thread_id)
        for _ in range(12):
            pending = False
            async with self._lock(game_key, chat_id, thread_id):
                current = await self.current(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
                if current and current["phase"] not in {"finished", "cancelled"}:
                    return "existing", current
                if current and current.get("score_pending"):
                    pending = True
                else:
                    fresh = games.new_lobby(game_key=game_key, chat_id=chat_id, thread_id=thread_id,
                                             owner_id=user_id, owner_name=user_name)
                    fresh.update(leaderboard_required=self.leaderboard is not None,
                                 economy_required=self.economy is not None)
                    if current is None:
                        ok = await self.repository.create_game_state_if_absent(
                            int(chat_id), self.key(game_key, thread_id), fresh)
                    else:
                        fresh["version"] = current["version"] + 1
                        ok = await self.repository.compare_and_save_game_state(
                            int(chat_id), self.key(game_key, thread_id), fresh,
                            expected_version=current["version"])
                    if ok:
                        self.metrics.inc("game_lobby_created_total")
                        return "created", fresh
            if pending:
                await self.settle_scores(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
        raise RuntimeError("game_state_conflict")

    async def act(self, *, game_key, chat_id, thread_id=None, user_id, action_id, action,
                  round_id=None, phase_seq=None, user_name="Oyuncu", text="", moderator=False,
                  message_id=None, submitted_at=None, target=None, card_order=None,
                  word_order=None, now=None):
        clock = time.time() if now is None else float(now)

        def transition(state):
            if submitted_at is not None and float(submitted_at) < state["created_at"]:
                raise ValueError("word_puzzles_stale_round")
            if action == "join":
                return games.join(state, user_id=user_id, user_name=user_name, now=clock)
            if action == "start":
                return games.start(state, user_id=user_id, target=target,
                                   card_order=card_order, word_order=word_order, now=clock)
            if action == "leave":
                return games.leave(state, user_id=user_id, now=clock)
            if action == "stop":
                return games.cancel(state, user_id=user_id, moderator=moderator, now=clock)
            if action == "hint":
                return games.hint(state, user_id=user_id, now=clock)
            if action == "text":
                if state["phase"] != "playing":
                    raise ValueError("word_puzzles_wrong_phase")
                if state["deadline"] is None:
                    raise ValueError("word_puzzles_waiting_panel")
                if message_id is None or int(message_id) <= int(state.get("input_floor") or 0):
                    raise ValueError("word_puzzles_stale_message")
                if (submitted_at is not None and
                        float(submitted_at) < float(state.get("input_opened_at") or state["created_at"])):
                    raise ValueError("word_puzzles_stale_message")
                result = games.submit_text(state, user_id=user_id, text=text, now=clock)
                if result is state:
                    result = deepcopy(state)
                    result["version"] += 1
                    result["last_activity_at"] = clock
                result["input_floor"] = int(message_id)
                return result
            raise ValueError("word_puzzles_invalid_action")

        return await self._apply(game_key=game_key, chat_id=chat_id, thread_id=thread_id,
                                 action_id=action_id, transition=transition,
                                 round_id=round_id, phase_seq=phase_seq)

    async def advance(self, *, game_key, chat_id, thread_id=None, round_id, now=None):
        clock = time.time() if now is None else float(now)
        observed = await self.current(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
        if observed is None:
            raise ValueError("word_puzzles_not_found")
        stage = _stage(observed)

        def transition(state):
            if _stage(state) != stage:
                return state
            if (state["phase"] in {"playing", "reveal"} and state["deadline"] is None
                    and state.get("panel_wait_until") is not None
                    and clock >= state["panel_wait_until"]):
                result = deepcopy(state)
                result["version"] += 1
                result.update(phase="cancelled", phase_seq=state["phase_seq"] + 1,
                              panel_wait_until=None, score_pending=False,
                              reason="panel_delivery_timeout")
                return result
            if state["phase"] == "lobby" and clock >= state["lobby_expires_at"]:
                return games.expire(state, now=clock)
            if (state["phase"] in {"playing", "reveal"} and state["deadline"] is not None
                    and clock >= state["deadline"]):
                return games.expire(state, now=clock)
            return state

        action_id = (f"clock:{round_id}:{stage[0]}:{stage[1]}:"
                     f"{secrets.token_hex(6)}")
        return await self._apply(game_key=game_key, chat_id=chat_id, thread_id=thread_id,
                                 action_id=action_id, transition=transition, round_id=round_id)

    async def claim_panel(self, *, game_key, chat_id, thread_id=None, now=None, force=False):
        clock = time.time() if now is None else float(now)
        async with self._lock(game_key, chat_id, thread_id):
            for _ in range(6):
                state = await self.current(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
                if (not state or (not state.get("panel_dirty") and not force) or
                        float(state.get("delivery_retry_at", 0)) > clock):
                    return None
                result = dict(state, panel_dirty=True, delivery_retry_at=clock + 30,
                              version=state["version"] + 1)
                if await self.repository.compare_and_save_game_state(
                    int(chat_id), self.key(game_key, thread_id), result,
                    expected_version=state["version"]):
                    return result
            raise RuntimeError("game_state_conflict")

    async def acknowledge_panel(self, *, game_key, chat_id, thread_id=None,
                                round_id, expected_version, message_id, now=None):
        moment = time.time() if now is None else float(now)
        async with self._lock(game_key, chat_id, thread_id):
            for _ in range(6):
                state = await self.current(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
                if not state or state["round_id"] != round_id:
                    return False
                result = dict(state, version=state["version"] + 1)
                if state["version"] == int(expected_version):
                    result["panel_message_id"] = int(message_id)
                    result["panel_dirty"] = False
                    result["delivery_retry_at"] = 0
                    if state["phase"] in {"playing", "reveal"} and state["deadline"] is None:
                        if (state.get("panel_wait_until") is not None and
                                moment >= state["panel_wait_until"]):
                            result.update(phase="cancelled", phase_seq=state["phase_seq"] + 1,
                                          panel_wait_until=None, score_pending=False,
                                          panel_dirty=True, delivery_retry_at=0,
                                          reason="panel_delivery_timeout")
                        else:
                            duration = (games.WORDLE_SECONDS if state["game_key"] == "wordle_tr" else
                                        games.REVEAL_SECONDS if state["phase"] == "reveal" else
                                        games.ROUND_SECONDS)
                            result.update(deadline=moment + duration, panel_wait_until=None,
                                          panel_dirty=True, delivery_retry_at=0)
                            if state["phase"] == "playing":
                                result["input_opened_at"] = moment
                                result["input_floor"] = int(message_id)
                else:
                    # The delivered image is older than the current state. Keep a
                    # newer canonical panel ID and schedule a repair edit.
                    if not state.get("panel_message_id"):
                        result["panel_message_id"] = int(message_id)
                    result["panel_dirty"] = True
                    result["delivery_retry_at"] = 0
                if await self.repository.compare_and_save_game_state(
                    int(chat_id), self.key(game_key, thread_id), result,
                    expected_version=state["version"]):
                    return True
            raise RuntimeError("game_state_conflict")

    async def settle_scores(self, *, game_key, chat_id, thread_id=None):
        async with self._lock(game_key, chat_id, thread_id):
            for _ in range(6):
                state = await self.current(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
                if not state or state["phase"] != "finished" or not state.get("score_pending"):
                    return
                if (state.get("leaderboard_required") is not False and self.leaderboard is None or
                        state.get("economy_required") is not False and self.economy is None):
                    raise RuntimeError("game_score_store_unavailable")
                for player in state["players"]:
                    points = int(state["scores"].get(str(player["id"]), 0))
                    if not player["active"] or points <= 0:
                        continue
                    event_id = f"{game_key}:{chat_id}:{state['round_id']}:{player['id']}"
                    if state.get("leaderboard_required") is not False and self.leaderboard:
                        await self.leaderboard.record_score(
                            user_id=player["id"], chat_id=int(chat_id), game_type=game_key,
                            score=points, user_name=player["name"], event_id=event_id)
                    if state.get("economy_required") is not False and self.economy is not None:
                        await self.economy.credit(player["id"], int(chat_id), max(1, points // 5),
                                                  reason=f"{game_key}_reward", event_id=f"coin:{event_id}")
                result = dict(state, score_pending=False, panel_dirty=True,
                              delivery_retry_at=0, version=state["version"] + 1)
                if await self.repository.compare_and_save_game_state(
                    int(chat_id), self.key(game_key, thread_id), result,
                    expected_version=state["version"]):
                    self.metrics.inc("game_score_settlement_total")
                    return
            raise RuntimeError("game_state_conflict")
