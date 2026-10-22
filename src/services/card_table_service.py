"""CAS-backed card matches, private hand receipts and idempotent settlement."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import time

from src.games import card_table as rules
from src.observability.metrics import MetricsRegistry


def b36(number):
    number = int(number)
    if number == 0:
        return "0"
    text = ""
    while number:
        number, digit = divmod(number, 36)
        text = "0123456789abcdefghijklmnopqrstuvwxyz"[digit] + text
    return text


def _all_hands(state):
    return all(str(uid) in state.get("private_delivery", {})
               for uid in state.get("private_recipients", []))


def _stage(state):
    return (state["phase"], state["phase_seq"], state.get("deadline"),
            state.get("panel_wait_until"), state.get("lobby_expires_at"),
            state.get("turn_index"), state.get("turn_no"))


class CardTableService:
    GAME_KEYS = rules.GAME_KEYS

    def __init__(self, repository, leaderboard=None, economy=None, metrics=None):
        self.repository, self.leaderboard, self.economy = repository, leaderboard, economy
        self.metrics = metrics if metrics is not None else MetricsRegistry()
        self._locks = {}

    @staticmethod
    def key(game_key, thread_id=None):
        if game_key not in rules.GAME_KEYS:
            raise ValueError("ct_invalid_game")
        return game_key if thread_id is None else f"{game_key}_t{int(thread_id)}"

    def _lock(self, game_key, chat_id, thread_id):
        return self._locks.setdefault((game_key, int(chat_id), thread_id), asyncio.Lock())

    async def current(self, *, game_key, chat_id, thread_id=None):
        return await self.repository.get_game_state(int(chat_id), self.key(game_key, thread_id))

    async def _apply(self, *, game_key, chat_id, thread_id=None, round_id, action_id,
                     transform, phase_seq=None, expected_version=None):
        if not action_id or len(str(action_id)) > 180:
            raise ValueError("ct_invalid_action")
        async with self._lock(game_key, chat_id, thread_id):
            for _ in range(8):
                state = await self.current(game_key=game_key, chat_id=chat_id,
                                           thread_id=thread_id)
                if not state:
                    raise ValueError("ct_not_found")
                if state["round_id"] != round_id:
                    raise ValueError("ct_stale_round")
                if phase_seq is not None and state["phase_seq"] != int(phase_seq):
                    raise ValueError("ct_stale_phase")
                if expected_version is not None and state["version"] != expected_version:
                    return "ignored", state
                if action_id in state.get("processed_actions", []):
                    self.metrics.inc("card_table_duplicate_action_total")
                    return "duplicate", state
                result = transform(state)
                if result is state:
                    return "ignored", state
                if result["version"] != state["version"] + 1:
                    raise RuntimeError("ct_invalid_transition")
                before, after = rules.public_state(state), rules.public_state(result)
                for public in (before, after):
                    for field in ("version", "panel_dirty", "delivery_retry_at", "private_dirty",
                                  "private_retry_at", "group_ready", "phase_ready"):
                        public.pop(field, None)
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
                    self.metrics.inc("card_table_action_total")
                    return "applied", result
        raise RuntimeError("game_state_conflict")

    async def open(self, *, game_key, chat_id, thread_id=None, user_id, user_name, now=None):
        self.key(game_key, thread_id)
        for _ in range(12):
            current = await self.current(game_key=game_key, chat_id=chat_id,
                                         thread_id=thread_id)
            if current and current["phase"] not in rules.TERMINAL:
                return "existing", current
            if current and current.get("score_pending"):
                await self.settle_scores(game_key=game_key, chat_id=chat_id,
                                         thread_id=thread_id)
                continue
            fresh = rules.new(game_key=game_key, chat_id=chat_id,
                thread_id=thread_id, owner_id=user_id, owner_name=user_name,
                now=now)
            if current is None:
                saved = await self.repository.create_game_state_if_absent(
                    int(chat_id), self.key(game_key, thread_id), fresh)
            else:
                fresh["version"] = current["version"] + 1
                saved = await self.repository.compare_and_save_game_state(
                    int(chat_id), self.key(game_key, thread_id), fresh,
                    expected_version=current["version"])
            if saved:
                self.metrics.inc("card_table_lobby_total")
                return "created", fresh
        raise RuntimeError("game_state_conflict")

    async def act(self, *, game_key, chat_id, thread_id=None, user_id, action_id,
                  action, round_id, phase_seq=None, user_name="Oyuncu", value=None,
                  moderator=False, private_message_id=None, submitted_at=None,
                  now=None, rng=None, expected_version=None):
        moment = time.time() if now is None else float(now)

        def transition(state):
            if submitted_at is not None and float(submitted_at) < int(state["created_at"]):
                raise ValueError("ct_stale_input")
            if action in {"play", "draw"}:
                receipt = state.get("private_panels", {}).get(str(user_id), {})
                if (receipt.get("phase_seq") != state["phase_seq"] or
                        int(receipt.get("message_id") or 0) != int(private_message_id or 0)):
                    raise ValueError("ct_stale_hand")
                if submitted_at is not None and float(submitted_at) < int(receipt.get("opened_at") or 0):
                    raise ValueError("ct_stale_input")
            return rules.act(state, user_id=user_id, action=action, value=value,
                             user_name=user_name, moderator=moderator, now=moment,
                             rng=rng)

        try:
            return await self._apply(game_key=game_key, chat_id=chat_id,
                thread_id=thread_id, round_id=round_id, action_id=action_id,
                phase_seq=phase_seq, expected_version=expected_version,
                transform=transition)
        except ValueError:
            self.metrics.inc("card_table_rejected_action_total")
            raise

    async def advance(self, *, game_key, chat_id, thread_id=None, round_id, now=None,
                      expected_version=None, rng=None):
        observed = await self.current(game_key=game_key, chat_id=chat_id,
                                      thread_id=thread_id)
        if not observed:
            raise ValueError("ct_not_found")
        stage = _stage(observed)
        moment = time.time() if now is None else float(now)

        def transition(state):
            return state if _stage(state) != stage else rules.advance(state, now=moment, rng=rng)

        token = ":".join(str(part) for part in stage)
        return await self._apply(game_key=game_key, chat_id=chat_id,
            thread_id=thread_id, round_id=round_id,
            action_id=f"clock:{round_id}:{token}", transform=transition,
            expected_version=expected_version)

    async def mint_invite(self, *, game_key, chat_id, thread_id=None,
                          user_id, round_id, now=None):
        moment = time.time() if now is None else float(now)
        token = secrets.token_urlsafe(12)
        digest = hashlib.sha256(token.encode()).hexdigest()

        def transform(state):
            if state["phase"] != "lobby":
                raise ValueError("ct_lobby_closed")
            person = rules._player(state, user_id)
            if person["ready"]:
                raise ValueError("ct_already_ready")
            prior = state["dm_invites"].get(str(user_id), {})
            if moment - float(prior.get("issued_at", 0)) < 20:
                raise ValueError("ct_invite_recent")
            result = rules._copy(state)
            result["dm_invites"][str(user_id)] = {
                "hash": digest, "issued_at": moment, "expires_at": moment + 600}
            return result

        await self._apply(game_key=game_key, chat_id=chat_id, thread_id=thread_id,
            round_id=round_id, action_id=f"invite:{digest}", transform=transform)
        code = "p" if game_key == "pisti" else "k"
        payload = f"ct_{code}_{b36(abs(chat_id))}_{b36(thread_id or 0)}_{b36(user_id)}_{token}"
        if len(payload.encode()) > 64:
            raise ValueError("ct_invite_too_long")
        return payload

    async def accept_invite(self, payload, *, user_id, now=None):
        try:
            prefix, code, chat, topic, actor, token = str(payload).split("_", 5)
            if prefix != "ct" or code not in {"p", "k"} or int(actor, 36) != int(user_id):
                raise ValueError()
            game_key = "pisti" if code == "p" else "papaz_kimde"
            chat_id, thread_id = -int(chat, 36), int(topic, 36) or None
        except (TypeError, ValueError):
            raise ValueError("ct_invalid_invite") from None
        digest = hashlib.sha256(token.encode()).hexdigest()
        moment = time.time() if now is None else float(now)
        state = await self.current(game_key=game_key, chat_id=chat_id,
                                   thread_id=thread_id)
        if not state:
            raise ValueError("ct_invalid_invite")

        def transform(row):
            invite = row.get("dm_invites", {}).get(str(user_id), {})
            if (row["phase"] != "lobby" or moment >= float(invite.get("expires_at", 0)) or
                    not hmac.compare_digest(str(invite.get("hash", "")), digest)):
                raise ValueError("ct_invalid_invite")
            result = rules.ready(row, user_id=user_id)
            result["dm_invites"].pop(str(user_id), None)
            return result

        status, saved = await self._apply(game_key=game_key, chat_id=chat_id,
            thread_id=thread_id, round_id=state["round_id"],
            action_id=f"ready:{digest}", transform=transform)
        if status != "applied":
            raise ValueError("ct_invalid_invite")
        return game_key, chat_id, thread_id, saved

    async def claim_delivery(self, *, game_key, chat_id, thread_id=None,
                             kind, user_id=None, now=None):
        moment = time.time() if now is None else float(now)
        if kind not in {"panel", "private"}:
            raise ValueError("ct_invalid_delivery")
        async with self._lock(game_key, chat_id, thread_id):
            for _ in range(8):
                state = await self.current(game_key=game_key, chat_id=chat_id,
                                           thread_id=thread_id)
                if not state:
                    return None
                if kind == "panel":
                    allowed = state.get("panel_dirty") and float(state.get("delivery_retry_at") or 0) <= moment
                else:
                    allowed = (int(user_id or 0) in state.get("private_recipients", []) and
                               str(user_id) not in state.get("private_delivery", {}) and
                               float(state.get("private_retry", {}).get(str(user_id), 0)) <= moment)
                if not allowed:
                    return None
                result = dict(state, version=state["version"] + 1)
                if kind == "panel":
                    result["delivery_retry_at"] = moment + 30
                else:
                    result["private_retry"] = dict(state["private_retry"])
                    result["private_retry"][str(user_id)] = moment + 30
                    waiting = [float(result["private_retry"].get(str(uid), 0))
                               for uid in state["private_recipients"]
                               if str(uid) not in state["private_delivery"]]
                    result["private_retry_at"] = min(waiting) if waiting else None
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
                state = await self.current(game_key=game_key, chat_id=chat_id,
                                           thread_id=thread_id)
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
                if state["phase"] == "playing" and not state["phase_ready"]:
                    if moment >= state["panel_wait_until"]:
                        result = rules._copy(state)
                        rules._cancel(result, reason="delivery_timeout")
                        result["panel_message_id"] = int(message_id)
                    elif _all_hands(state):
                        result.update(phase_ready=True, deadline=moment + rules.TURN_SECONDS,
                                      panel_wait_until=None, panel_dirty=True)
                if await self.repository.compare_and_save_game_state(
                    int(chat_id), self.key(game_key, thread_id), result,
                    expected_version=state["version"]):
                    return True
        raise RuntimeError("game_state_conflict")

    async def acknowledge_private(self, *, game_key, chat_id, thread_id=None,
                                  round_id, phase_seq, user_id, expected_version,
                                  message_id, now=None):
        moment = time.time() if now is None else float(now)

        def transform(state):
            if (state["phase"] != "playing" or int(user_id) not in state["private_recipients"] or
                    str(user_id) in state["private_delivery"]):
                return state
            if moment >= state["panel_wait_until"]:
                raise ValueError("ct_delivery_timeout")
            result = rules._copy(state)
            result["private_delivery"][str(user_id)] = int(message_id)
            result["private_panels"][str(user_id)] = {"message_id": int(message_id),
                "phase_seq": state["phase_seq"], "opened_at": moment}
            if _all_hands(result) and state["group_ready"]:
                result.update(phase_ready=True, deadline=moment + rules.TURN_SECONDS,
                              panel_wait_until=None, panel_dirty=True, delivery_retry_at=0)
            return result

        return await self._apply(game_key=game_key, chat_id=chat_id,
            thread_id=thread_id, round_id=round_id, phase_seq=phase_seq,
            expected_version=expected_version,
            action_id=f"private:{round_id}:{phase_seq}:{user_id}:{message_id}",
            transform=transform)

    async def settle_scores(self, *, game_key, chat_id, thread_id=None):
        async with self._lock(game_key, chat_id, thread_id):
            for _ in range(8):
                state = await self.current(game_key=game_key, chat_id=chat_id,
                                           thread_id=thread_id)
                if not state or state["phase"] != "finished" or not state["score_pending"]:
                    return
                if self.leaderboard is None:
                    raise RuntimeError("game_score_store_unavailable")
                for player in state["players"]:
                    uid = player["id"]
                    points = max(0, min(100, int(state["scores"].get(str(uid), 0))))
                    event = f"{game_key}:{chat_id}:{state['round_id']}:{uid}"
                    await self.leaderboard.record_score(user_id=uid, chat_id=int(chat_id),
                        game_type=game_key, score=points, user_name=player["name"], event_id=event)
                    if self.economy and points > 0 and uid in state["winner_ids"]:
                        await self.economy.credit(uid, int(chat_id), max(1, points // 20),
                            reason=f"{game_key}_reward", event_id=f"coin:{event}")
                result = dict(state, version=state["version"] + 1,
                              score_pending=False, panel_dirty=True, delivery_retry_at=0)
                if await self.repository.compare_and_save_game_state(
                    int(chat_id), self.key(game_key, thread_id), result,
                    expected_version=state["version"]):
                    self.metrics.inc("card_table_settled_total")
                    return
        raise RuntimeError("game_state_conflict")
