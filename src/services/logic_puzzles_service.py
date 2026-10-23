"""Durable, compare-and-swap sessions for three deterministic puzzle games."""
from __future__ import annotations

import asyncio
import secrets
import time

from src.games import logic_puzzles as rules
from src.observability.metrics import MetricsRegistry


class LogicPuzzlesService:
    def __init__(self, repository, leaderboard=None, economy=None, metrics=None):
        self.repository = repository
        self.leaderboard = leaderboard
        self.economy = economy
        self.metrics = metrics if metrics is not None else MetricsRegistry()
        self._locks = {}

    @staticmethod
    def key(game_key, thread_id=None):
        if game_key not in rules.GAME_KEYS:
            raise ValueError("lp_invalid_game")
        return game_key if thread_id is None else f"{game_key}_t{int(thread_id)}"

    def _lock(self, game_key, chat_id, thread_id):
        return self._locks.setdefault((game_key, int(chat_id), thread_id), asyncio.Lock())

    async def current(self, *, game_key, chat_id, thread_id=None):
        return await self.repository.get_game_state(int(chat_id), self.key(game_key, thread_id))

    async def open(self, *, game_key, chat_id, thread_id=None, user_id, user_name, size=4, now=None):
        key = self.key(game_key, thread_id)
        for _ in range(12):
            prior = await self.current(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
            if prior and prior["phase"] not in rules.TERMINAL:
                return "existing", prior
            if prior and prior.get("score_pending"):
                await self.settle_scores(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
                continue
            fresh = rules.new(game_key=game_key, chat_id=chat_id, thread_id=thread_id,
                              owner_id=user_id, owner_name=user_name,
                              round_id=secrets.token_hex(6), size=size, now=now)
            fresh["processed_actions"] = []
            fresh.update(leaderboard_required=self.leaderboard is not None,
                         economy_required=self.economy is not None)
            if prior is None:
                saved = await self.repository.create_game_state_if_absent(int(chat_id), key, fresh)
            else:
                fresh["version"] = prior["version"] + 1
                saved = await self.repository.compare_and_save_game_state(
                    int(chat_id), key, fresh, expected_version=prior["version"])
            if saved:
                return "created", fresh
        raise RuntimeError("game_state_conflict")

    async def _apply(self, *, game_key, chat_id, thread_id, round_id, action_id, transition,
                     expected_version=None, round_no=None):
        if not action_id or len(str(action_id)) > 180:
            raise ValueError("lp_invalid_action")
        async with self._lock(game_key, chat_id, thread_id):
            for _ in range(8):
                state = await self.current(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
                if state is None:
                    raise ValueError("lp_not_found")
                if state["round_id"] != round_id:
                    raise ValueError("lp_stale_round")
                if round_no is not None and int(state.get("round_no", 0)) != int(round_no):
                    raise ValueError("lp_stale_round")
                if expected_version is not None and state["version"] != expected_version:
                    raise ValueError("lp_stale_panel")
                if action_id in state.get("processed_actions", []):
                    self.metrics.inc("logic_puzzles_duplicate_action_total")
                    return "duplicate", state
                result = transition(state)
                if result is state:
                    return "ignored", state
                if result["version"] != state["version"] + 1:
                    raise RuntimeError("lp_invalid_transition")
                before, after = rules.public_state(state), rules.public_state(result)
                before.pop("version", None)
                after.pop("version", None)
                result["panel_dirty"] = bool(state.get("panel_dirty") or before != after)
                if before != after:
                    result["delivery_retry_at"] = 0
                result["processed_actions"] = [*state.get("processed_actions", []), action_id][-2048:]
                if await self.repository.compare_and_save_game_state(
                    int(chat_id), self.key(game_key, thread_id), result,
                    expected_version=state["version"]):
                    self.metrics.inc("logic_puzzles_action_total")
                    return "applied", result
        raise RuntimeError("game_state_conflict")

    async def act(self, *, game_key, chat_id, thread_id=None, user_id, action_id, action,
                  round_id, round_no=None, user_name="Oyuncu", size=None, row=None, col=None,
                  value=None, symbol=None, moderator=False, message_id=None,
                  submitted_at=None, now=None, secret=None, delay=None):
        moment = time.time() if now is None else float(now)

        def transition(state):
            if (state["game_key"] == "sudoku" and state["phase"] == "active"
                    and state.get("deadline") is not None and moment >= state["deadline"]
                    and action != "stop"):
                raise ValueError("lp_expired")
            if action == "join":
                return rules.join(state, user_id=user_id, user_name=user_name, now=moment)
            if action == "start":
                return rules.start(state, user_id=user_id, now=moment, secret=secret,
                                   delay=delay, symbol=symbol)
            if action == "select":
                return rules.select(state, user_id=user_id, row=int(row), col=int(col), now=moment)
            if action == "focus_row":
                return rules.focus_row(state, user_id=user_id, row=int(row), now=moment)
            if action == "focus_band":
                return rules.focus_band(state, user_id=user_id, band=int(row), now=moment)
            if action == "digit":
                return rules.digit(state, user_id=user_id, value=int(value),
                                   expected_cell=(int(row), int(col)) if row is not None and col is not None else None,
                                   now=moment)
            if action == "undo":
                return rules.undo(state, user_id=user_id, now=moment)
            if action == "hint":
                if state["game_key"] == "sifre_tahmin":
                    return rules.code_hint(state, user_id=user_id, now=moment)
                if state["game_key"] == "fark_bulmaca":
                    return rules.spot_hint(state, user_id=user_id, now=moment)
                return rules.hint(state, user_id=user_id, now=moment)
            if action == "code_digit":
                return rules.code_digit(state, user_id=user_id, digit=str(value), now=moment)
            if action == "code_clear":
                return rules.code_clear(state, user_id=user_id, now=moment)
            if action == "code_submit":
                return rules.code_submit(state, user_id=user_id, now=moment)
            if action == "spot_pick":
                return rules.spot_pick(state, user_id=user_id, index=int(value), now=moment)
            if action == "guess":
                if submitted_at is not None and float(submitted_at) < float(state.get("turn_started_at") or 0):
                    raise ValueError("lp_stale_input")
                if message_id is not None and int(message_id) <= int(state.get("input_floor") or 0):
                    raise ValueError("lp_stale_input")
                return rules.number_guess(state, user_id=user_id, value=value, now=moment)
            if action == "press":
                return rules.reflex_press(state, user_id=user_id, symbol=symbol, now=moment)
            if action == "next":
                raise ValueError("lp_wait_for_next_round")
            if action == "stop":
                return rules.cancel(state, user_id=user_id, moderator=moderator, now=moment)
            raise ValueError("lp_invalid_action")

        try:
            return await self._apply(game_key=game_key, chat_id=chat_id, thread_id=thread_id,
                                     round_id=round_id, round_no=round_no, action_id=action_id,
                                     transition=transition)
        except ValueError:
            self.metrics.inc("logic_puzzles_rejected_action_total")
            raise

    async def advance(self, *, game_key, chat_id, thread_id=None, round_id, now=None,
                      expected_phase=None, expected_deadline=None, expected_round_no=None,
                      expected_version=None):
        observed = await self.current(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
        if observed is None:
            raise ValueError("lp_not_found")
        stage = (observed["phase"], observed.get("round_no", 0),
                 observed.get("turn_index", 0), observed.get("deadline"),
                 observed.get("panel_wait_until"), observed.get("lobby_expires_at"))

        def transition(state):
            current_stage = (state["phase"], state.get("round_no", 0),
                             state.get("turn_index", 0), state.get("deadline"),
                             state.get("panel_wait_until"), state.get("lobby_expires_at"))
            if (current_stage != stage or
                    (expected_version is not None and state["version"] != expected_version) or
                    (expected_phase is not None and state["phase"] != expected_phase) or
                    (expected_deadline is not None and state.get("deadline") != expected_deadline) or
                    (expected_round_no is not None and state.get("round_no", 0) != expected_round_no)):
                return state
            return rules.advance(state, now=now)
        return await self._apply(game_key=game_key, chat_id=chat_id, thread_id=thread_id,
                                 round_id=round_id,
                                 action_id=f"clock:{round_id}:{stage[0]}:{stage[1]}:{stage[2]}:{stage[3]}:{stage[4]}:{stage[5]}",
                                 transition=transition)

    async def claim_panel(self, *, game_key, chat_id, thread_id=None, now=None, force=False):
        moment = time.time() if now is None else float(now)
        async with self._lock(game_key, chat_id, thread_id):
            for _ in range(8):
                state = await self.current(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
                if not state or (not force and not state.get("panel_dirty")) or float(state.get("delivery_retry_at") or 0) > moment:
                    return None
                result = dict(state, version=state["version"] + 1,
                              delivery_retry_at=moment + 30)
                if await self.repository.compare_and_save_game_state(
                    int(chat_id), self.key(game_key, thread_id), result,
                    expected_version=state["version"]):
                    return result
        raise RuntimeError("game_state_conflict")

    async def acknowledge_panel(self, *, game_key, chat_id, thread_id=None, round_id,
                                expected_version, message_id, now=None):
        moment = time.time() if now is None else float(now)
        async with self._lock(game_key, chat_id, thread_id):
            for _ in range(8):
                state = await self.current(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
                if not state or state["round_id"] != round_id:
                    return False
                # A stale edit cannot arm a timer or replace the active panel.
                if state["version"] != expected_version:
                    if not state.get("panel_dirty") or not state.get("panel_message_id"):
                        repair = dict(state, version=state["version"] + 1,
                                      panel_dirty=True, delivery_retry_at=0,
                                      panel_message_id=state.get("panel_message_id") or int(message_id))
                        if await self.repository.compare_and_save_game_state(
                            int(chat_id), self.key(game_key, thread_id), repair,
                            expected_version=state["version"]):
                            return False
                        continue
                    return False
                result = dict(state, version=state["version"] + 1,
                              panel_message_id=int(message_id), panel_dirty=False,
                              delivery_retry_at=0)
                if state.get("panel_wait_until") is not None and moment >= state["panel_wait_until"]:
                    result.update(phase="cancelled", deadline=None, panel_wait_until=None,
                                  score_pending=False, panel_dirty=True)
                elif state["phase"] == "active" and game_key == "sudoku":
                    result.update(panel_ready=True, panel_wait_until=None,
                                  deadline=state.get("deadline") or moment + rules.SUDOKU_SECONDS)
                elif state["phase"] == "turn_pending":
                    result.update(phase="turn", deadline=None,
                                  panel_dirty=True, delivery_retry_at=0)
                elif state["phase"] == "turn" and state.get("deadline") is None:
                    result.update(deadline=moment + (60 if game_key == "sifre_tahmin" else rules.TURN_SECONDS),
                                  turn_started_at=moment, input_floor=int(message_id),
                                  panel_wait_until=None)
                elif state["phase"] == "spot_pending":
                    result.update(phase="spot", deadline=None,
                                  panel_dirty=True, delivery_retry_at=0)
                elif state["phase"] == "spot" and state.get("deadline") is None:
                    result.update(deadline=moment + 90, panel_wait_until=None)
                elif state["phase"] == "go_pending":
                    result.update(phase="go", deadline=None,
                                  panel_dirty=True, delivery_retry_at=0)
                elif state["phase"] == "go" and state.get("deadline") is None:
                    result.update(deadline=moment + rules.REFLEX_SECONDS,
                                  panel_wait_until=None)
                elif state["phase"] == "waiting" and state.get("go_at") is None:
                    result.update(go_at=moment + float(state["delay_seconds"]),
                                  deadline=moment + float(state["delay_seconds"]),
                                  panel_wait_until=None, panel_ready=True)
                elif state["phase"] == "round_result":
                    result.update(panel_wait_until=None,
                                  deadline=moment + rules.RESULT_SECONDS)
                if await self.repository.compare_and_save_game_state(
                    int(chat_id), self.key(game_key, thread_id), result,
                    expected_version=state["version"]):
                    return True
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
                    if game_key == "sudoku":
                        points = max(10, 100 - 20 * state["hint_count"] -
                                     2 * max(0, state["moves"] - sum(v == 0 for r in state["givens"] for v in r)))
                    else:
                        points = min(100, max(0, int(state["scores"][str(player["id"])]) * 10))
                    event = f"{game_key}:{chat_id}:{state['round_id']}:{player['id']}"
                    if state.get("leaderboard_required") is not False and self.leaderboard:
                        await self.leaderboard.record_score(
                            user_id=player["id"], chat_id=int(chat_id), game_type=game_key,
                            score=points, user_name=player["name"], event_id=event)
                    if state.get("economy_required") is not False and self.economy and points:
                        await self.economy.credit(player["id"], int(chat_id), max(1, points // 20),
                                                  reason=f"{game_key}_reward", event_id=f"coin:{event}")
                result = dict(state, score_pending=False, panel_dirty=True,
                              delivery_retry_at=0, version=state["version"] + 1)
                if await self.repository.compare_and_save_game_state(
                    int(chat_id), self.key(game_key, thread_id), result,
                    expected_version=state["version"]):
                    self.metrics.inc("logic_puzzles_settled_total")
                    return
        raise RuntimeError("game_state_conflict")
