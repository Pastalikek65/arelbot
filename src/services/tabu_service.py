"""Durable team Tabu sessions and user-bound private-chat invitations."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import time
from typing import Any, Callable

from src.games import tabu
from src.observability.metrics import MetricsRegistry


def _base36(value: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    result = ""
    while value:
        value, remainder = divmod(value, 36)
        result = digits[remainder] + result
    return result or "0"


class TabuService:
    GAME_TYPE = "tabu"

    def __init__(self, repository: Any, *, leaderboard=None, economy=None, metrics=None):
        self.repository, self.leaderboard, self.economy = repository, leaderboard, economy
        self.metrics = metrics if metrics is not None else MetricsRegistry()
        self._locks: dict[tuple[int, int | None], asyncio.Lock] = {}

    @staticmethod
    def key(thread_id: int | None) -> str:
        return "tabu" if thread_id is None else f"tabu_t{int(thread_id)}"

    def _lock(self, chat_id, thread_id):
        return self._locks.setdefault((int(chat_id), thread_id), asyncio.Lock())

    async def current(self, chat_id, thread_id=None):
        return await self.repository.get_game_state(int(chat_id), self.key(thread_id))

    async def _apply(self, chat_id, thread_id, action_id: str, transform: Callable, *, round_id=None, card_id=None):
        if not action_id or len(action_id) > 180:
            raise ValueError("tabu_invalid_action")
        async with self._lock(chat_id, thread_id):
            for _ in range(4):
                state = await self.current(chat_id, thread_id)
                if not state:
                    raise ValueError("tabu_not_found")
                if round_id is not None and state["round_id"] != round_id:
                    raise ValueError("tabu_stale_round")
                if action_id in state.get("processed_actions", []):
                    self.metrics.inc("game_duplicate_action_total")
                    return "duplicate", state
                if card_id is not None and state.get("card_id") != card_id:
                    raise ValueError("tabu_stale_card")
                result = transform(state)
                if int(result["version"]) == int(state["version"]):
                    return "ignored", state
                before, after = tabu.public_state(state), tabu.public_state(result)
                for view in (before, after):
                    view.pop("version", None)
                    view.pop("last_activity_at", None)
                result["panel_dirty"] = bool(state.get("panel_dirty") or before != after)
                if before != after:
                    result["panel_retry_at"] = 0
                if state.get("card_id") != result.get("card_id"):
                    result["card_retry_at"] = 0
                    result["ack_retry_at"] = 0
                    result["active_message_floor"] = None
                elif state.get("narrator_id") != result.get("narrator_id"):
                    result["card_retry_at"] = 0
                    result["ack_retry_at"] = 0
                result["version"] = int(state["version"]) + 1
                result["processed_actions"] = [*state.get("processed_actions", []), action_id][-1024:]
                if await self.repository.compare_and_save_game_state(
                    int(chat_id), self.key(thread_id), result, expected_version=int(state["version"]),
                ):
                    self.metrics.inc("game_action_total")
                    return "applied", result
            raise RuntimeError("game_state_conflict")

    async def open(self, *, chat_id, thread_id=None, user_id, user_name):
        existing = await self.current(chat_id, thread_id)
        if existing and existing.get("score_pending"):
            await self.settle_scores(chat_id=chat_id, thread_id=thread_id)
        async with self._lock(chat_id, thread_id):
            for _ in range(4):
                current = await self.current(chat_id, thread_id)
                if current and current["phase"] not in {"finished", "cancelled"}:
                    return "existing", current
                fresh = tabu.new_lobby(
                    chat_id=int(chat_id), thread_id=thread_id, owner_id=int(user_id),
                    owner_name=user_name, round_id=secrets.token_hex(6), deck_seed=secrets.randbits(128),
                )
                fresh["panel_dirty"] = True
                if current is None:
                    saved = await self.repository.create_game_state_if_absent(int(chat_id), self.key(thread_id), fresh)
                else:
                    fresh["version"] = int(current["version"]) + 1
                    saved = await self.repository.compare_and_save_game_state(
                        int(chat_id), self.key(thread_id), fresh, expected_version=int(current["version"]),
                    )
                if saved:
                    self.metrics.inc("game_lobby_created_total")
                    return "created", fresh
            raise RuntimeError("game_state_conflict")

    async def act(self, *, chat_id, thread_id=None, user_id, action_id, action, user_name="Oyuncu",
                  round_id=None, card_id=None, team=None, text="", moderator=False,
                  decision="uphold", submitted_at=None, input_floor=None, now=None):
        clock = time.time() if now is None else float(now)

        def transition(state):
            if submitted_at is not None and int(submitted_at) < int(state["created_at"]):
                raise ValueError("tabu_stale_round")
            if action == "join":
                return tabu.join(state, user_id=user_id, user_name=user_name, team=team)
            if action == "team":
                return tabu.change_team(state, user_id=user_id, team=team, now=clock)
            if action == "start":
                return tabu.start(state, user_id=user_id, now=clock)
            if action == "leave":
                return tabu.leave(state, user_id=user_id, now=clock)
            if action == "stop":
                return tabu.cancel(state, user_id=user_id, moderator=moderator, now=clock)
            if action == "ack":
                if not isinstance(input_floor, int) or isinstance(input_floor, bool) or input_floor <= 0:
                    raise ValueError("tabu_input_floor_required")
                result = tabu.ack_card(state, user_id=user_id, card_id=card_id, now=clock)
                result["active_message_floor"] = input_floor
                return result
            if action == "delivered":
                return tabu.mark_card_delivered(state, user_id=user_id, card_id=card_id, now=clock)
            if action == "pass":
                return tabu.pass_card(state, user_id=user_id, card_id=card_id, now=clock)
            if action == "appeal":
                return tabu.appeal(state, user_id=user_id, card_id=card_id, now=clock)
            if action == "decide":
                return tabu.decide_appeal(state, user_id=user_id, card_id=card_id,
                                         decision=decision, is_admin=moderator, now=clock)
            if action == "text":
                if state.get("phase") != "active":
                    return state
                if action_id.startswith(("cmd:", "msg:", "edit:")):
                    try:
                        message_id = int(action_id.split(":", 2)[1])
                    except ValueError:
                        raise ValueError("tabu_invalid_action") from None
                    floor = state.get("active_message_floor")
                    if floor is None or message_id <= int(floor):
                        raise ValueError("tabu_stale_card")
                if submitted_at is not None and int(submitted_at) < int(state.get("active_since", state.get("last_activity_at", 0))):
                    raise ValueError("tabu_stale_card")
                function = tabu.submit_clue if int(state["narrator_id"]) == int(user_id) else tabu.submit_guess
                return function(state, user_id=user_id, card_id=card_id, text=text, now=clock)
            raise ValueError("tabu_unknown_action")

        return await self._apply(chat_id, thread_id, action_id, transition, round_id=round_id, card_id=card_id)

    async def mint_invite(self, *, chat_id, thread_id=None, user_id, round_id, request_id=None):
        token = secrets.token_urlsafe(12)
        digest = hashlib.sha256(token.encode()).hexdigest()
        issued_at = time.time()

        def invite(state):
            if state["phase"] != "lobby" or not any(int(p["id"]) == int(user_id) for p in state["players"]):
                raise ValueError("tabu_not_in_lobby")
            previous = state.get("dm_invites", {}).get(str(user_id), {})
            if request_id is not None and previous.get("request_id") == str(request_id):
                raise ValueError("tabu_pending_invite")
            if issued_at - float(previous.get("issued_at", 0)) < 30:
                raise ValueError("tabu_recent_invite")
            result = dict(state)
            result["dm_invites"] = dict(state.get("dm_invites", {}))
            result["dm_invites"][str(user_id)] = {
                "hash": digest, "expires_at": issued_at + 600,
                "issued_at": issued_at, "request_id": str(request_id) if request_id is not None else None,
            }
            result["version"] = int(state["version"]) + 1
            return result

        action_id = f"invite-request:{request_id}" if request_id is not None else f"invite:{digest}"
        status, _ = await self._apply(chat_id, thread_id, action_id, invite, round_id=round_id)
        if status == "duplicate":
            raise ValueError("tabu_pending_invite")
        return f"tb_{_base36(abs(int(chat_id)))}_{_base36(int(thread_id or 0))}_{_base36(int(user_id))}_{token}"

    async def accept_invite(self, payload: str, *, user_id: int):
        parts = str(payload).split("_", 4)
        try:
            prefix, chat, topic, owner, token = parts
            if prefix != "tb" or int(owner, 36) != int(user_id):
                raise ValueError()
            chat_id, thread_id = -int(chat, 36), int(topic, 36) or None
        except (ValueError, TypeError):
            raise ValueError("tabu_invalid_invite") from None
        digest = hashlib.sha256(token.encode()).hexdigest()

        def accept(state):
            invitation = state.get("dm_invites", {}).get(str(user_id), {})
            if (float(invitation.get("expires_at", 0)) < time.time()
                    or not hmac.compare_digest(str(invitation.get("hash", "")), digest)):
                raise ValueError("tabu_invalid_invite")
            result = tabu.set_ready(state, user_id=user_id, ready=True)
            result["dm_invites"] = dict(result.get("dm_invites", {}))
            result["dm_invites"].pop(str(user_id), None)
            return result

        _, state = await self._apply(chat_id, thread_id, f"ready:{digest}", accept)
        return chat_id, thread_id, state

    async def advance(self, *, chat_id, thread_id=None, round_id, now=None):
        clock = time.time() if now is None else float(now)
        def transition(state):
            result = state
            if ((state["phase"] == "active" and clock >= float(state["deadline"]))
                    or (state["phase"] == "lobby" and clock >= float(state["last_activity_at"]) + tabu.LOBBY_IDLE_SECONDS)):
                result = tabu.expire(state, now=clock)
            elif state["phase"] == "card_delivery" and clock >= float(state["last_activity_at"]) + tabu.CARD_READY_SECONDS:
                result = tabu.expire(state, now=clock)
            if ((result["phase"] == "appeal" and clock >= float(result["appeal_until"]))
                    or (result["phase"] == "adjudication" and
                        (result.get("judge_decision") is not None or clock >= float(result["judge_deadline"])))):
                result = tabu.advance(result, now=clock)
            return result
        return await self._apply(chat_id, thread_id, f"clock:{round_id}:{clock}",
                                 transition, round_id=round_id)

    async def claim_delivery(self, *, chat_id, thread_id=None, kind, now=None):
        if kind not in {"panel", "card", "start"}:
            raise ValueError("tabu_invalid_delivery")
        clock = time.time() if now is None else float(now)
        async with self._lock(chat_id, thread_id):
            for _ in range(4):
                state = await self.current(chat_id, thread_id)
                if kind == "panel":
                    eligible = state and state.get("panel_dirty")
                else:
                    eligible = (state and state["phase"] == "card_delivery"
                                and clock < float(state["last_activity_at"]) + tabu.CARD_READY_SECONDS
                                and bool(state.get("delivered")) == (kind == "start"))
                retry_key = "ack_retry_at" if kind == "start" else f"{kind}_retry_at"
                if not eligible or float(state.get(retry_key, 0)) > clock:
                    return None
                claimed = dict(state, version=int(state["version"]) + 1)
                claimed[retry_key] = clock + 30
                if await self.repository.compare_and_save_game_state(
                    int(chat_id), self.key(thread_id), claimed, expected_version=int(state["version"]),
                ):
                    return claimed
            raise RuntimeError("game_state_conflict")

    async def acknowledge_panel(self, *, chat_id, thread_id=None, round_id, expected_version, message_id):
        async with self._lock(chat_id, thread_id):
            for _ in range(4):
                state = await self.current(chat_id, thread_id)
                if not state or state["round_id"] != round_id:
                    return False
                updated = dict(state, version=int(state["version"]) + 1)
                updated["panel_message_id"] = int(message_id)
                if int(state["version"]) == int(expected_version):
                    updated["panel_dirty"] = False
                if await self.repository.compare_and_save_game_state(
                    int(chat_id), self.key(thread_id), updated, expected_version=int(state["version"]),
                ):
                    return True
            raise RuntimeError("game_state_conflict")

    async def settle_scores(self, *, chat_id, thread_id=None):
        async with self._lock(chat_id, thread_id):
            for _ in range(4):
                state = await self.current(chat_id, thread_id)
                if not state or state["phase"] != "finished" or not state.get("score_pending"):
                    return
                if self.leaderboard is None:
                    raise RuntimeError("game_score_store_unavailable")
                for player in state["players"]:
                    if not player.get("active", True):
                        continue
                    points = max(0, min(100, int(state["team_scores"][player["team"]])))
                    event_id = f"tabu:{chat_id}:{state['round_id']}:{player['id']}"
                    await self.leaderboard.record_score(user_id=int(player["id"]), chat_id=int(chat_id),
                                                       game_type="tabu", score=points, user_name=player["name"], event_id=event_id)
                    if self.economy and points:
                        await self.economy.credit(int(player["id"]), int(chat_id), max(1, points // 2),
                                                  reason="tabu_reward", event_id=f"coin:{event_id}")
                settled = dict(state, score_pending=False, panel_dirty=True,
                               panel_retry_at=0, version=int(state["version"]) + 1)
                if await self.repository.compare_and_save_game_state(
                    int(chat_id), self.key(thread_id), settled, expected_version=int(state["version"]),
                ):
                    return
            raise RuntimeError("game_state_conflict")
