"""CAS-backed Spy sessions, private invitations and retryable effects."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import time
from copy import deepcopy

from src.games import spy
from src.observability.metrics import MetricsRegistry


def _b36(value):
    value = int(value)
    result = ""
    while value:
        value, rem = divmod(value, 36)
        result = "0123456789abcdefghijklmnopqrstuvwxyz"[rem] + result
    return result or "0"


def _role_retry_at(state):
    if state["phase"] != "delivery":
        return None
    pending = [float(state.get(f"role_retry_{p['id']}", 0))
               for p in state["players"] if p["active"] and not p["delivered"]]
    return min(pending) if pending else None


def _clock_stage(state):
    """Identify the phase a due worker observed before its CAS attempt."""
    return (state["phase"], state.get("turn_index"), state.get("vote_round"),
            state.get("deadline"), state.get("panel_wait_until"))


class SpyService:
    GAME_TYPE = "casus"

    def __init__(self, repository, leaderboard=None, economy=None, metrics=None):
        self.repository, self.leaderboard, self.economy = repository, leaderboard, economy
        self.metrics = metrics if metrics is not None else MetricsRegistry()
        self._locks = {}

    @staticmethod
    def key(thread_id):
        return "casus" if thread_id is None else f"casus_t{int(thread_id)}"

    def _lock(self, chat_id, thread_id):
        return self._locks.setdefault((int(chat_id), thread_id), asyncio.Lock())

    async def current(self, chat_id, thread_id=None):
        return await self.repository.get_game_state(int(chat_id), self.key(thread_id))

    async def _apply(self, chat_id, thread_id, action_id, transition, *, round_id=None,
                     expected_version=None):
        if not action_id or len(str(action_id)) > 180:
            raise ValueError("spy_invalid_action")
        async with self._lock(chat_id, thread_id):
            for _ in range(6):
                state = await self.current(chat_id, thread_id)
                if state is None:
                    raise ValueError("spy_not_found")
                if round_id is not None and round_id != state["round_id"]:
                    raise ValueError("spy_stale_round")
                if expected_version is not None and int(expected_version) != state["version"]:
                    raise ValueError("spy_stale_panel")
                if action_id in state.get("processed_actions", []):
                    self.metrics.inc("game_duplicate_action_total")
                    return "duplicate", state
                result = transition(state)
                if result is state:
                    return "ignored", state
                if result["version"] != state["version"] + 1:
                    raise RuntimeError("spy_invalid_transition")
                before, after = spy.public_state(state), spy.public_state(result)
                for view in (before, after):
                    view.pop("version", None)
                result["panel_dirty"] = bool(state.get("panel_dirty") or before != after)
                result["role_retry_at"] = _role_retry_at(result)
                if (result["phase"] in {"question", "discussion", "vote"}
                        and result.get("deadline") is None
                        and (state["phase"] != result["phase"]
                             or state.get("deadline") is not None
                             or state.get("vote_round") != result.get("vote_round"))):
                    result["panel_wait_until"] = float(result["last_activity_at"]) + 300
                if before != after:
                    result["panel_retry_at"] = 0
                result["processed_actions"] = [*state.get("processed_actions", []), action_id][-2048:]
                if await self.repository.compare_and_save_game_state(
                    int(chat_id), self.key(thread_id), result, expected_version=state["version"]
                ):
                    self.metrics.inc("game_action_total")
                    return "applied", result
            raise RuntimeError("game_state_conflict")

    async def open(self, *, chat_id, thread_id=None, user_id, user_name):
        for _ in range(12):
            pending = False
            async with self._lock(chat_id, thread_id):
                current = await self.current(chat_id, thread_id)
                if current and current["phase"] not in {"finished", "cancelled"}:
                    return "existing", current
                if current and current.get("score_pending"):
                    pending = True
                else:
                    fresh = spy.new_lobby(chat_id=chat_id, thread_id=thread_id, owner_id=user_id,
                                          owner_name=user_name, round_id=secrets.token_hex(6))
                    fresh["panel_dirty"] = True
                    fresh["role_retry_at"] = None
                    if current is None:
                        ok = await self.repository.create_game_state_if_absent(
                            int(chat_id), self.key(thread_id), fresh)
                    else:
                        fresh["version"] = current["version"] + 1
                        ok = await self.repository.compare_and_save_game_state(
                            int(chat_id), self.key(thread_id), fresh, expected_version=current["version"])
                    if ok:
                        self.metrics.inc("game_lobby_created_total")
                        return "created", fresh
            if pending:
                # Settle outside the local lock, then reread before replacement.
                await self.settle_scores(chat_id=chat_id, thread_id=thread_id)
        raise RuntimeError("game_state_conflict")

    async def act(self, *, chat_id, thread_id=None, user_id, action_id, action,
                  round_id=None, user_name="Oyuncu", target_id=None, text="",
                  moderator=False, submitted_at=None, message_id=None, now=None,
                  expected_version=None):
        clock = time.time() if now is None else float(now)

        def transition(state):
            if submitted_at is not None and float(submitted_at) < state["created_at"]:
                raise ValueError("spy_stale_round")
            if action == "join":
                return spy.join(state, user_id=user_id, user_name=user_name, now=clock)
            if action == "start":
                active_ids = {p["id"] for p in state["players"] if p["active"]}
                if not active_ids.issubset(set(state.get("dm_ready", []))):
                    raise ValueError("spy_dm_not_ready")
                return spy.start(state, user_id=user_id, now=clock)
            if action == "leave":
                return spy.leave(state, user_id=user_id, now=clock)
            if action == "stop":
                return spy.cancel(state, user_id=user_id, moderator=moderator, now=clock)
            if action == "delivered":
                result = spy.delivered(state, user_id=user_id, now=clock)
                if message_id is None or int(message_id) <= 0:
                    raise ValueError("spy_invalid_delivery")
                next(p for p in result["players"] if p["id"] == int(user_id))["dm_message_id"] = int(message_id)
                return result
            if action == "confirm":
                return spy.confirm(state, user_id=user_id, now=clock)
            if action == "target":
                return spy.question_target(state, user_id=user_id, target_id=target_id, now=clock)
            if action == "text":
                if state["phase"] != "question":
                    return state
                if message_id is None or int(message_id) <= int(state.get("input_floor") or 0):
                    raise ValueError("spy_stale_message")
                if submitted_at is not None and float(submitted_at) < state.get("question_started_at", state["created_at"]):
                    raise ValueError("spy_stale_message")
                if submitted_at is not None and float(submitted_at) < state.get("target_chosen_at", state["created_at"]):
                    raise ValueError("spy_stale_message")
                result = spy.group_text(state, user_id=user_id, text=text, now=clock)
                result["input_floor"] = int(message_id)
                return result
            if action == "vote":
                return spy.vote(state, user_id=user_id, target_id=target_id, now=clock)
            if action in {"guess_menu", "select_guess"}:
                if (state["phase"] not in {"question", "discussion", "vote"} or
                        state["deadline"] is None or int(user_id) != state["spy_id"] or
                        state.get("guess_used")):
                    raise ValueError("spy_guess_forbidden")
                player = next((p for p in state["players"] if p["id"] == int(user_id) and p["active"]), None)
                if not player or int(player.get("dm_message_id") or 0) != int(message_id or 0):
                    raise ValueError("spy_stale_panel")
                if action == "select_guess" and (target_id is None or not 0 <= int(target_id) < len(spy.LOCATIONS)):
                    raise ValueError("spy_invalid_action")
                result = deepcopy(state)
                result["version"] += 1
                result["guess_selection"] = (int(target_id) if action == "select_guess" else None)
                return result
            if action == "guess":
                if (state.get("guess_selection") is None or target_id is None or
                        int(state["guess_selection"]) != int(target_id)):
                    raise ValueError("spy_guess_not_selected")
                player = next((p for p in state["players"] if p["id"] == int(user_id) and p["active"]), None)
                if not player or int(player.get("dm_message_id") or 0) != int(message_id or 0):
                    raise ValueError("spy_stale_panel")
                return spy.guess(state, user_id=user_id, location=text, now=clock)
            raise ValueError("spy_invalid_action")

        status, state = await self._apply(chat_id, thread_id, action_id, transition,
                                          round_id=round_id, expected_version=expected_version)
        if status == "applied" and action == "confirm" and state["phase"] == "question":
            # The group message floor is updated by the panel send/ack path.
            pass
        return status, state

    async def mint_invite(self, *, chat_id, thread_id=None, user_id, round_id, request_id=None):
        token = secrets.token_urlsafe(12)
        digest = hashlib.sha256(token.encode()).hexdigest()
        clock = time.time()

        def transition(state):
            if state["phase"] != "lobby" or not any(p["id"] == int(user_id) for p in state["players"]):
                raise ValueError("spy_not_player")
            prior = state.get("dm_invites", {}).get(str(user_id), {})
            if clock - prior.get("issued_at", 0) < 15:
                raise ValueError("spy_recent_invite")
            result = dict(state, version=state["version"] + 1)
            result["dm_invites"] = dict(state.get("dm_invites", {}))
            result["dm_invites"][str(user_id)] = {"hash": digest, "expires_at": clock + 600,
                                                   "issued_at": clock}
            return result

        status, _ = await self._apply(chat_id, thread_id, f"invite:{request_id or digest}", transition,
                                      round_id=round_id)
        if status == "duplicate":
            raise ValueError("spy_recent_invite")
        return f"sp_{_b36(abs(chat_id))}_{_b36(thread_id or 0)}_{_b36(user_id)}_{token}"

    async def accept_invite(self, payload, *, user_id):
        try:
            prefix, chat, thread, owner, token = str(payload).split("_", 4)
            if prefix != "sp" or int(owner, 36) != int(user_id):
                raise ValueError()
            chat_id, thread_id = -int(chat, 36), int(thread, 36) or None
        except (ValueError, TypeError):
            raise ValueError("spy_invalid_invite") from None
        digest = hashlib.sha256(token.encode()).hexdigest()

        def transition(state):
            invitation = state.get("dm_invites", {}).get(str(user_id), {})
            if (time.time() >= invitation.get("expires_at", 0) or
                    not hmac.compare_digest(str(invitation.get("hash", "")), digest)):
                raise ValueError("spy_invalid_invite")
            if state["phase"] != "lobby":
                raise ValueError("spy_wrong_phase")
            result = dict(state, version=state["version"] + 1)
            result["dm_invites"] = dict(state.get("dm_invites", {}))
            result["dm_invites"].pop(str(user_id), None)
            result["dm_ready"] = list(set(state.get("dm_ready", [])) | {int(user_id)})
            return result

        _, state = await self._apply(chat_id, thread_id, f"ready:{digest}", transition)
        return chat_id, thread_id, state

    async def advance(self, *, chat_id, thread_id=None, round_id, now=None):
        clock = time.time() if now is None else float(now)
        observed = await self.current(chat_id, thread_id)
        if observed is None:
            raise ValueError("spy_not_found")
        stage = _clock_stage(observed)

        def transition(state):
            if _clock_stage(state) != stage:
                return state
            if (state.get("panel_wait_until") is not None and state["deadline"] is None
                    and clock >= float(state["panel_wait_until"])):
                result = deepcopy(state)
                result["version"] += 1
                result.update(phase="cancelled", reason="panel_delivery_timeout",
                              panel_wait_until=None, score_pending=False)
                return result
            if state["phase"] in {"finished", "cancelled"} or state["deadline"] is None or clock < state["deadline"]:
                if state["phase"] == "vote" and len(state["votes"]) == sum(p["active"] for p in state["players"]):
                    return spy.close_vote(state, now=clock)
                return state
            return spy.expire(state, now=clock)
        action_id = (f"clock:{round_id}:{stage[0]}:{stage[1]}:{stage[2]}:"
                     f"{secrets.token_hex(6)}")
        return await self._apply(chat_id, thread_id, action_id, transition,
                                 round_id=round_id)

    async def claim_delivery(self, *, chat_id, thread_id=None, kind, user_id=None, now=None):
        clock = time.time() if now is None else float(now)
        if kind not in {"panel", "role"}:
            raise ValueError("spy_invalid_delivery")
        field = "panel_retry_at" if kind == "panel" else f"role_retry_{user_id}"
        async with self._lock(chat_id, thread_id):
            for _ in range(6):
                state = await self.current(chat_id, thread_id)
                if state is None:
                    return None
                if kind == "panel":
                    eligible = state.get("panel_dirty")
                else:
                    player = next((p for p in state["players"] if p["id"] == int(user_id) and p["active"]), None)
                    eligible = state["phase"] == "delivery" and player and not player["delivered"]
                if not eligible or state.get(field, 0) > clock:
                    return None
                result = dict(state, version=state["version"] + 1)
                result[field] = clock + 30
                if kind == "role":
                    result["role_retry_at"] = _role_retry_at(result)
                if await self.repository.compare_and_save_game_state(
                    int(chat_id), self.key(thread_id), result, expected_version=state["version"]):
                    return result
            raise RuntimeError("game_state_conflict")

    async def acknowledge_panel(self, *, chat_id, thread_id=None, round_id, expected_version, message_id):
        async with self._lock(chat_id, thread_id):
            for _ in range(6):
                state = await self.current(chat_id, thread_id)
                if not state or state["round_id"] != round_id:
                    return False
                result = dict(state, version=state["version"] + 1,
                              panel_message_id=int(message_id))
                if state["version"] == expected_version:
                    result["panel_dirty"] = False
                    if state["phase"] in {"question", "discussion", "vote"} and state["deadline"] is None:
                        duration = (spy.TURN_SECONDS if state["phase"] == "question" else
                                    spy.DISCUSSION_SECONDS if state["phase"] == "discussion" else
                                    spy.REVOTE_SECONDS if state["vote_round"] == 2 else spy.VOTE_SECONDS)
                        moment = time.time()
                        if (state.get("panel_wait_until") is not None and
                                moment >= float(state["panel_wait_until"])):
                            result.update(phase="cancelled", reason="panel_delivery_timeout",
                                          panel_wait_until=None, score_pending=False,
                                          panel_dirty=True, panel_retry_at=0)
                        else:
                            result["deadline"] = moment + duration
                            result["panel_wait_until"] = None
                            result["panel_dirty"] = True
                            result["panel_retry_at"] = 0
                            if state["phase"] == "question":
                                result["question_started_at"] = moment
                                result["input_floor"] = int(message_id)
                if await self.repository.compare_and_save_game_state(
                    int(chat_id), self.key(thread_id), result, expected_version=state["version"]):
                    return True
            raise RuntimeError("game_state_conflict")

    async def settle_scores(self, *, chat_id, thread_id=None):
        async with self._lock(chat_id, thread_id):
            for _ in range(6):
                state = await self.current(chat_id, thread_id)
                if not state or state["phase"] != "finished" or not state.get("score_pending"):
                    return
                if state.get("reason") == "spy_left":
                    # A player can leave before any fair vote or accusation.
                    # Keep the public outcome, but never award an abandonment.
                    result = dict(state, score_pending=False, panel_dirty=True,
                                  panel_retry_at=0, version=state["version"] + 1)
                    if await self.repository.compare_and_save_game_state(
                        int(chat_id), self.key(thread_id), result,
                        expected_version=state["version"]):
                        return
                    continue
                if self.leaderboard is None:
                    raise RuntimeError("game_score_store_unavailable")
                for player in state["players"]:
                    if not player["active"]:
                        continue
                    won = (player["id"] == state["spy_id"]) == (state["winner"] == "spy")
                    points = 10 if won else 0
                    event = f"casus:{chat_id}:{state['round_id']}:{player['id']}"
                    await self.leaderboard.record_score(
                        user_id=player["id"], chat_id=int(chat_id), game_type="casus",
                        score=points, user_name=player["name"], event_id=event)
                    if self.economy and points:
                        await self.economy.credit(player["id"], int(chat_id), 2,
                                                  reason="casus_reward", event_id=f"coin:{event}")
                result = dict(state, score_pending=False, panel_dirty=True,
                              panel_retry_at=0, version=state["version"] + 1)
                if await self.repository.compare_and_save_game_state(
                    int(chat_id), self.key(thread_id), result, expected_version=state["version"]):
                    return
            raise RuntimeError("game_state_conflict")
