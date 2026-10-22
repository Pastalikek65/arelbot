"""CAS-backed sessions, private delivery and idempotent social arcade rewards."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import time

from src.games import social_arcade as rules
from src.observability.metrics import MetricsRegistry


def b36(value):
    value = int(value)
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    answer = ""
    while value:
        value, digit = divmod(value, 36)
        answer = chars[digit] + answer
    return answer or "0"


GROUP_ACTION_PHASES = {("amiral_batti", "shot"), ("takim_bilgi_ligi", "trivia")}


def _all_private(state):
    return all(str(uid) in state.get("private_delivery", {})
               for uid in state.get("private_recipients", []))


def _stage(state):
    return (state["phase"], state["phase_seq"], state.get("deadline"),
            state.get("panel_wait_until"), state.get("lobby_expires_at"),
            state.get("turn_index"), state.get("author_index"), state.get("subject_index"),
            state.get("turn_no"), state.get("round_no"))


class SocialArcadeService:
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
            raise ValueError("sa_invalid_game")
        return game_key if thread_id is None else f"{game_key}_t{int(thread_id)}"

    def _lock(self, game_key, chat_id, thread_id):
        return self._locks.setdefault((game_key, int(chat_id), thread_id), asyncio.Lock())

    async def current(self, *, game_key, chat_id, thread_id=None):
        return await self.repository.get_game_state(int(chat_id), self.key(game_key, thread_id))

    async def _apply(self, *, game_key, chat_id, thread_id=None, round_id, action_id,
                     transform, phase_seq=None, expected_version=None):
        if not action_id or len(str(action_id)) > 180:
            raise ValueError("sa_invalid_action")
        async with self._lock(game_key, chat_id, thread_id):
            for _ in range(8):
                state = await self.current(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
                if not state:
                    raise ValueError("sa_not_found")
                if state["round_id"] != round_id:
                    raise ValueError("sa_stale_round")
                if phase_seq is not None and state["phase_seq"] != int(phase_seq):
                    raise ValueError("sa_stale_phase")
                if expected_version is not None and state["version"] != expected_version:
                    return "ignored", state
                if action_id in state.get("processed_actions", []):
                    self.metrics.inc("social_arcade_duplicate_action_total")
                    return "duplicate", state
                result = transform(state)
                if result is state:
                    return "ignored", state
                if result["version"] != state["version"] + 1:
                    raise RuntimeError("sa_invalid_transition")
                if (game_key == "iki_dogru_bir_yalan" and state["phase"] == "author"
                        and result["phase"] == "author" and result.get("claims") != state.get("claims")):
                    author = state["author_order"][state["author_index"]]
                    result["private_delivery"].pop(str(author), None)
                    result["private_retry"][str(author)] = 0
                before, after = rules.public_view(state), rules.public_view(result)
                before.pop("version", None)
                after.pop("version", None)
                changed = before != after
                result["panel_dirty"] = bool(state.get("panel_dirty") or changed)
                if changed:
                    result["delivery_retry_at"] = 0
                pending = [float(result.get("private_retry", {}).get(str(uid), 0))
                           for uid in result.get("private_recipients", [])
                           if str(uid) not in result.get("private_delivery", {})]
                result["private_dirty"] = bool(pending)
                result["private_retry_at"] = min(pending) if pending else None
                result["processed_actions"] = [*state.get("processed_actions", []), action_id][-2048:]
                if await self.repository.compare_and_save_game_state(
                    int(chat_id), self.key(game_key, thread_id), result,
                    expected_version=state["version"]):
                    self.metrics.inc("social_arcade_action_total")
                    return "applied", result
        raise RuntimeError("game_state_conflict")

    async def open(self, *, game_key, chat_id, thread_id=None, user_id, user_name, now=None):
        self.key(game_key, thread_id)
        for _ in range(12):
            current = await self.current(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
            if current and current["phase"] not in rules.TERMINAL:
                return "existing", current
            if current and current.get("score_pending"):
                await self.settle_scores(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
                continue
            fresh = rules.new(game_key=game_key, chat_id=chat_id, thread_id=thread_id,
                              owner_id=user_id, owner_name=user_name,
                              round_id=secrets.token_hex(6), now=now)
            fresh.update(leaderboard_required=self.leaderboard is not None,
                         economy_required=self.economy is not None)
            if current is None:
                saved = await self.repository.create_game_state_if_absent(
                    int(chat_id), self.key(game_key, thread_id), fresh)
            else:
                fresh["version"] = current["version"] + 1
                saved = await self.repository.compare_and_save_game_state(
                    int(chat_id), self.key(game_key, thread_id), fresh,
                    expected_version=current["version"])
            if saved:
                self.metrics.inc("social_arcade_lobby_total")
                return "created", fresh
        raise RuntimeError("game_state_conflict")

    async def act(self, *, game_key, chat_id, thread_id=None, user_id, action_id,
                  action, round_id, phase_seq=None, user_name="Oyuncu", value=None,
                  text=None, moderator=False, private_message_id=None,
                  submitted_at=None, input_message_id=None, now=None):
        moment = time.time() if now is None else float(now)

        def transition(state):
            # Telegram message dates have whole-second precision; compare to the
            # same precision and use message order for private input in that second.
            if submitted_at is not None and float(submitted_at) < int(state["created_at"]):
                raise ValueError("sa_stale_input")
            if action in {"claim", "lie", "vote", "question", "guess", "yes", "no",
                          "poison", "serve", "drink", "swap", "refuse"}:
                receipt = state.get("private_panels", {}).get(str(user_id), {})
                if (not receipt or receipt.get("phase_seq") != state["phase_seq"] or
                        int(receipt.get("message_id") or 0) != int(private_message_id or 0)):
                    raise ValueError("sa_stale_private_panel")
                if submitted_at is not None and float(submitted_at) < int(receipt.get("opened_at") or 0):
                    raise ValueError("sa_stale_input")
                if input_message_id is not None and int(input_message_id) <= int(receipt["message_id"]):
                    raise ValueError("sa_stale_input")
            return rules.act(state, user_id=user_id, action=action, value=value,
                             text=text if text is not None else user_name,
                             moderator=moderator, now=moment)

        try:
            return await self._apply(game_key=game_key, chat_id=chat_id,
                thread_id=thread_id, round_id=round_id, action_id=action_id,
                phase_seq=phase_seq, transform=transition)
        except ValueError:
            self.metrics.inc("social_arcade_rejected_action_total")
            raise

    async def mint_invite(self, *, game_key, chat_id, thread_id=None,
                          user_id, round_id, request_id=None, now=None):
        token = secrets.token_urlsafe(12)
        digest = hashlib.sha256(token.encode()).hexdigest()
        moment = time.time() if now is None else float(now)

        def transform(state):
            if state["phase"] != "lobby":
                raise ValueError("sa_lobby_closed")
            person = rules._person(state, user_id)
            if person["ready"]:
                raise ValueError("sa_already_ready")
            previous = state.get("dm_invites", {}).get(str(user_id), {})
            if request_id is not None and previous.get("request_id") == str(request_id):
                raise ValueError("sa_invite_pending")
            if moment - float(previous.get("issued_at", 0)) < 30:
                raise ValueError("sa_invite_recent")
            result = rules._copy(state)
            result["dm_invites"][str(user_id)] = {
                "hash": digest, "issued_at": moment, "expires_at": moment + 600,
                "request_id": str(request_id) if request_id else None}
            return result

        await self._apply(game_key=game_key, chat_id=chat_id, thread_id=thread_id,
                          round_id=round_id, action_id=f"invite:{digest}", transform=transform)
        payload = (f"sa_{self._code(game_key)}_{b36(abs(chat_id))}_{b36(thread_id or 0)}_"
                   f"{b36(user_id)}_{token}")
        if len(payload.encode()) > 64:
            raise ValueError("sa_invite_too_long")
        return payload

    @staticmethod
    def _code(game_key):
        return {key: str(index) for index, key in enumerate(rules.GAME_KEYS)}[game_key]

    async def accept_invite(self, payload, *, user_id, now=None):
        try:
            prefix, code, chat, topic, owner, token = str(payload).split("_", 5)
            if prefix != "sa" or int(owner, 36) != int(user_id) or not token:
                raise ValueError()
            game_key = rules.GAME_KEYS[int(code)]
            chat_id, thread_id = -int(chat, 36), int(topic, 36) or None
        except (ValueError, TypeError, IndexError):
            raise ValueError("sa_invalid_invite") from None
        digest = hashlib.sha256(token.encode()).hexdigest()
        moment = time.time() if now is None else float(now)
        state = await self.current(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
        if not state:
            raise ValueError("sa_invalid_invite")

        def transform(row):
            invite = row.get("dm_invites", {}).get(str(user_id), {})
            if (row["phase"] != "lobby" or moment >= float(invite.get("expires_at", 0)) or
                    not hmac.compare_digest(str(invite.get("hash", "")), digest)):
                raise ValueError("sa_invalid_invite")
            result = rules.ready(row, user_id=user_id)
            result["dm_invites"].pop(str(user_id), None)
            return result

        status, saved = await self._apply(game_key=game_key, chat_id=chat_id,
            thread_id=thread_id, round_id=state["round_id"], action_id=f"ready:{digest}",
            transform=transform)
        if status != "applied":
            raise ValueError("sa_invalid_invite")
        return game_key, chat_id, thread_id, saved

    async def advance(self, *, game_key, chat_id, thread_id=None, round_id, now=None,
                      expected_version=None):
        observed = await self.current(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
        if not observed:
            raise ValueError("sa_not_found")
        stage = _stage(observed)
        stage_id = ":".join(str(part) for part in stage)
        if len(stage_id) > 120:
            raise RuntimeError("sa_invalid_stage")
        clock = time.time() if now is None else float(now)

        def transform(state):
            if _stage(state) != stage:
                return state
            return rules.advance(state, now=clock)

        return await self._apply(game_key=game_key, chat_id=chat_id,
            thread_id=thread_id, round_id=round_id,
            action_id=f"clock:{round_id}:{stage_id}", transform=transform,
            expected_version=expected_version)

    async def claim_delivery(self, *, game_key, chat_id, thread_id=None,
                             kind, user_id=None, now=None, force=False):
        if kind not in {"panel", "private"}:
            raise ValueError("sa_invalid_delivery")
        moment = time.time() if now is None else float(now)
        async with self._lock(game_key, chat_id, thread_id):
            for _ in range(8):
                state = await self.current(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
                if not state:
                    return None
                if kind == "panel":
                    eligible = (state.get("panel_dirty") or force) and float(state.get("delivery_retry_at") or 0) <= moment
                else:
                    eligible = (int(user_id or 0) in state.get("private_recipients", []) and
                                str(user_id) not in state.get("private_delivery", {}) and
                                float(state.get("private_retry", {}).get(str(user_id), 0)) <= moment)
                if not eligible:
                    return None
                result = dict(state, version=state["version"] + 1)
                if kind == "panel":
                    result["delivery_retry_at"] = moment + 30
                else:
                    result["private_retry"] = dict(state.get("private_retry", {}))
                    result["private_retry"][str(user_id)] = moment + 30
                    pending = [float(result["private_retry"].get(str(uid), 0))
                               for uid in state["private_recipients"]
                               if str(uid) not in state["private_delivery"]]
                    result["private_retry_at"] = min(pending) if pending else None
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
                if state["version"] != expected_version:
                    repair = dict(state, version=state["version"] + 1,
                                  panel_dirty=True, delivery_retry_at=0,
                                  panel_message_id=state.get("panel_message_id") or int(message_id))
                    if await self.repository.compare_and_save_game_state(
                        int(chat_id), self.key(game_key, thread_id), repair,
                        expected_version=state["version"]):
                        return False
                    continue
                result = dict(state, version=state["version"] + 1,
                              panel_message_id=int(message_id), panel_dirty=False,
                              delivery_retry_at=0, group_ready=True)
                if state["phase"] not in rules.TERMINAL and state["phase"] != "lobby":
                    if moment >= float(state.get("panel_wait_until") or 0) and not state["phase_ready"]:
                        result = rules._copy(state)
                        rules._cancel(result, now=moment, reason="delivery_timeout")
                        result["panel_message_id"] = int(message_id)
                    elif (state["game_key"], state["phase"]) in GROUP_ACTION_PHASES:
                        if state.get("action_panel_pending") and _all_private(state):
                            activated = rules.activate(dict(result), now=moment)
                            result.update(phase_ready=activated["phase_ready"],
                                          deadline=activated["deadline"],
                                          panel_wait_until=activated["panel_wait_until"],
                                          panel_dirty=False)
                        elif _all_private(state):
                            result["action_panel_pending"] = True
                            result["panel_dirty"] = True
                    elif _all_private(state) and not state["phase_ready"]:
                        activated = rules.activate(dict(result), now=moment)
                        result.update(phase_ready=activated["phase_ready"],
                                      deadline=activated["deadline"],
                                      panel_wait_until=activated["panel_wait_until"])
                if await self.repository.compare_and_save_game_state(
                    int(chat_id), self.key(game_key, thread_id), result,
                    expected_version=state["version"]):
                    return True
        raise RuntimeError("game_state_conflict")

    async def acknowledge_private(self, *, game_key, chat_id, thread_id=None,
                                  round_id, phase_seq, user_id, message_id,
                                  expected_version=None, now=None):
        moment = time.time() if now is None else float(now)
        # A private send can finish after a newer revision was authored. Its
        # delivery receipt must name the exact leased state that was rendered.
        if expected_version is None:
            return "ignored", await self.current(game_key=game_key, chat_id=chat_id,
                                                  thread_id=thread_id)

        def transform(state):
            if state["phase"] in rules.TERMINAL or int(user_id) not in state.get("private_recipients", []):
                raise ValueError("sa_stale_private_panel")
            if str(user_id) in state.get("private_delivery", {}):
                return state
            if not state["phase_ready"] and moment >= float(state.get("panel_wait_until") or 0):
                raise ValueError("sa_delivery_timeout")
            result = rules._copy(state)
            result["private_delivery"][str(user_id)] = int(message_id)
            result["private_panels"][str(user_id)] = {"message_id": int(message_id),
                                                      "phase_seq": state["phase_seq"],
                                                      "opened_at": moment}
            if _all_private(result) and state["group_ready"]:
                if (state["game_key"], state["phase"]) in GROUP_ACTION_PHASES:
                    result["action_panel_pending"] = True
                    result["panel_dirty"] = True
                    result["delivery_retry_at"] = 0
                else:
                    activated = rules.activate(result, now=moment)
                    result.update(phase_ready=activated["phase_ready"],
                                  deadline=activated["deadline"],
                                  panel_wait_until=activated["panel_wait_until"],
                                  panel_dirty=True, delivery_retry_at=0)
            return result

        return await self._apply(game_key=game_key, chat_id=chat_id,
            thread_id=thread_id, round_id=round_id, phase_seq=phase_seq,
            action_id=f"private:{round_id}:{phase_seq}:{user_id}:{message_id}",
            expected_version=expected_version, transform=transform)

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
                    points = max(0, min(100, int(state["scores"].get(str(player["id"]), 0)) * 10))
                    event = f"{game_key}:{chat_id}:{state['round_id']}:{player['id']}"
                    if state.get("leaderboard_required") is not False and self.leaderboard:
                        await self.leaderboard.record_score(user_id=player["id"], chat_id=int(chat_id),
                            game_type=game_key, score=points, user_name=player["name"], event_id=event)
                    if state.get("economy_required") is not False and self.economy and points and player["id"] in state.get("winner_ids", []):
                        await self.economy.credit(player["id"], int(chat_id), max(1, points // 20),
                            reason=f"{game_key}_reward", event_id=f"coin:{event}")
                result = dict(state, version=state["version"] + 1,
                              score_pending=False, panel_dirty=True, delivery_retry_at=0)
                if await self.repository.compare_and_save_game_state(
                    int(chat_id), self.key(game_key, thread_id), result,
                    expected_version=state["version"]):
                    self.metrics.inc("social_arcade_settled_total")
                    return
        raise RuntimeError("game_state_conflict")
