"""Durable CAS sessions, delivery receipts and idempotent effects for Word Arcade."""
from __future__ import annotations

import asyncio
import hashlib
import secrets
import time
from copy import deepcopy

from src.games import word_arcade as rules
from src.observability.metrics import MetricsRegistry

_CODES = {"harf_maratonu": "h", "sozcuk_avi": "s", "yasak_harf": "y",
          "ortak_hikaye": "o", "bomba": "b"}
_KEYS = {value: key for key, value in _CODES.items()}


def _b36(value):
    value = int(value)
    if value == 0:
        return "0"
    result = ""
    while value:
        value, rem = divmod(value, 36)
        result = "0123456789abcdefghijklmnopqrstuvwxyz"[rem] + result
    return result


def _stage(state):
    return (state["phase"], state["phase_seq"], state.get("round_no"),
            state.get("deadline"), state.get("panel_wait_until"),
            state.get("lobby_expires_at"))


class WordArcadeService:
    GAME_KEYS = rules.GAME_KEYS

    def __init__(self, repository, leaderboard=None, economy=None, metrics=None):
        self.repository, self.leaderboard, self.economy = repository, leaderboard, economy
        self.metrics = metrics if metrics is not None else MetricsRegistry()
        self._locks = {}

    @staticmethod
    def key(game_key, thread_id=None):
        if game_key not in rules.GAME_KEYS:
            raise ValueError("wa_invalid_game")
        return game_key if thread_id is None else f"{game_key}_t{int(thread_id)}"

    @staticmethod
    def public_state(state):
        return rules.public_state(state)

    def _lock(self, game_key, chat_id, thread_id):
        return self._locks.setdefault((game_key, int(chat_id), thread_id), asyncio.Lock())

    async def current(self, *, game_key, chat_id, thread_id=None):
        return await self.repository.get_game_state(int(chat_id), self.key(game_key, thread_id))

    async def _apply(self, *, game_key, chat_id, thread_id, action_id, transition,
                     round_id=None, phase_seq=None):
        if not action_id or len(str(action_id)) > 180:
            raise ValueError("wa_invalid_action")
        async with self._lock(game_key, chat_id, thread_id):
            for _ in range(8):
                state = await self.current(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
                if state is None:
                    raise ValueError("wa_not_found")
                if round_id is not None and state["round_id"] != round_id:
                    raise ValueError("wa_stale_round")
                if phase_seq is not None and state["phase_seq"] != int(phase_seq):
                    raise ValueError("wa_stale_phase")
                if action_id in state.get("processed_actions", []):
                    self.metrics.inc("word_arcade_duplicate_action_total")
                    return "duplicate", state
                result = transition(state)
                if result is state:
                    return "ignored", state
                if result["version"] != state["version"] + 1:
                    raise RuntimeError("wa_invalid_transition")
                before, after = rules.public_state(state), rules.public_state(result)
                before.pop("version", None)
                after.pop("version", None)
                changed = before != after
                result["panel_dirty"] = bool(state.get("panel_dirty") or changed)
                if changed:
                    result["delivery_retry_at"] = 0
                if (result["phase"] in rules.ACTIVE_PHASES | {"secret_delivery"}
                        and result.get("deadline") is None
                        and (state["phase"] != result["phase"] or
                             state["phase_seq"] != result["phase_seq"] or
                             state.get("deadline") is not None)):
                    result["panel_wait_until"] = float(result["last_activity_at"]) + rules.PANEL_WAIT_SECONDS
                result["processed_actions"] = [*state.get("processed_actions", []), action_id][-2048:]
                if await self.repository.compare_and_save_game_state(
                    int(chat_id), self.key(game_key, thread_id), result,
                    expected_version=state["version"]):
                    self.metrics.inc("word_arcade_action_total")
                    return "applied", result
            raise RuntimeError("game_state_conflict")

    async def open(self, *, game_key, chat_id, thread_id=None, user_id, user_name):
        self.key(game_key, thread_id)
        for _ in range(12):
            pending = False
            async with self._lock(game_key, chat_id, thread_id):
                prior = await self.current(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
                if prior and prior["phase"] not in rules.TERMINAL:
                    return "existing", prior
                if prior and prior.get("score_pending"):
                    pending = True
                else:
                    fresh = rules.new_lobby(game_key=game_key, chat_id=chat_id,
                        thread_id=thread_id, owner_id=user_id, owner_name=user_name)
                    fresh.update(leaderboard_required=self.leaderboard is not None,
                                 economy_required=self.economy is not None)
                    if prior is None:
                        saved = await self.repository.create_game_state_if_absent(
                            int(chat_id), self.key(game_key, thread_id), fresh)
                    else:
                        fresh["version"] = prior["version"] + 1
                        saved = await self.repository.compare_and_save_game_state(
                            int(chat_id), self.key(game_key, thread_id), fresh,
                            expected_version=prior["version"])
                    if saved:
                        self.metrics.inc("word_arcade_lobby_created_total")
                        return "created", fresh
            if pending:
                await self.settle_scores(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
        raise RuntimeError("game_state_conflict")

    async def act(self, *, game_key, chat_id, thread_id=None, user_id, action_id, action,
                  round_id=None, phase_seq=None, user_name="Oyuncu", text="",
                  moderator=False, message_id=None, reply_to_message_id=None,
                  submitted_at=None, private=False, card_order=None, bigram=None,
                  fuse_seconds=None, now=None):
        clock = time.time() if now is None else float(now)

        def transition(state):
            if submitted_at is not None and float(submitted_at) < state["created_at"]:
                raise ValueError("wa_stale_message")
            if action == "join":
                return rules.join(state, user_id=user_id, user_name=user_name, now=clock)
            if action == "start":
                return rules.start(state, user_id=user_id, card_order=card_order,
                    bigram=bigram, fuse_seconds=fuse_seconds, now=clock)
            if action == "leave":
                return rules.leave(state, user_id=user_id, now=clock)
            if action == "stop":
                return rules.cancel(state, user_id=user_id, moderator=moderator, now=clock)
            if action == "secret_ack":
                if int(message_id or 0) != int(state.get("private_message_id") or 0):
                    raise ValueError("wa_stale_private")
                return rules.secret_ack(state, user_id=user_id, now=clock)
            if action == "edit_clue":
                return rules.edit_clue(state, user_id=user_id, text=text,
                                       message_id=message_id, now=clock)
            if action == "text":
                if private:
                    if game_key != "harf_maratonu" or state["phase"] != "collect":
                        raise ValueError("wa_wrong_phase")
                    expected = int(state.get("dm_messages", {}).get(str(user_id)) or 0)
                    if not expected or int(reply_to_message_id or 0) != expected:
                        raise ValueError("wa_stale_private")
                    floor = int(state.get("private_input_floor", {}).get(str(user_id), expected))
                    if message_id is None or int(message_id) <= floor:
                        raise ValueError("wa_stale_message")
                    if (submitted_at is not None and int(float(submitted_at)) <
                            int(float(state.get("input_opened_at") or state["created_at"]))):
                        raise ValueError("wa_stale_message")
                else:
                    if game_key == "harf_maratonu":
                        raise ValueError("wa_private_only")
                    if message_id is None or int(message_id) <= int(state.get("input_floor") or 0):
                        raise ValueError("wa_stale_message")
                    if (submitted_at is not None and int(float(submitted_at)) <
                            int(float(state.get("input_opened_at") or state["created_at"]))):
                        raise ValueError("wa_stale_message")
                result = rules.submit(state, user_id=user_id, text=text, now=clock)
                if result is state:
                    result = deepcopy(state)
                    result["version"] += 1
                    result["last_activity_at"] = clock
                if (not private and game_key == "yasak_harf" and
                        state["phase"] == "clue" and int(user_id) == state["narrator_id"]
                        and result["phase"] == "clue" and result["clue_count"] > state["clue_count"]):
                    result.setdefault("clue_message_ids", []).append(int(message_id))
                if private:
                    result.setdefault("private_input_floor", {})[str(user_id)] = int(message_id)
                else:
                    result["input_floor"] = int(message_id)
                return result
            raise ValueError("wa_invalid_action")

        return await self._apply(game_key=game_key, chat_id=chat_id, thread_id=thread_id,
                                 action_id=action_id, transition=transition,
                                 round_id=round_id, phase_seq=phase_seq)

    async def advance(self, *, game_key, chat_id, thread_id=None, round_id, now=None):
        clock = time.time() if now is None else float(now)
        observed = await self.current(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
        if observed is None:
            raise ValueError("wa_not_found")
        stage = _stage(observed)

        def transition(state):
            if _stage(state) != stage:
                return state
            if (state["phase"] in rules.ACTIVE_PHASES | {"secret_delivery"}
                    and state.get("deadline") is None
                    and state.get("panel_wait_until") is not None
                    and clock >= state["panel_wait_until"]):
                result = deepcopy(state)
                result["version"] += 1
                result.update(phase="cancelled", phase_seq=state["phase_seq"] + 1,
                              deadline=None, panel_wait_until=None,
                              private_dirty=False, private_retry_at=None,
                              score_pending=False, reason="delivery_timeout")
                return result
            if state["phase"] == "lobby" and clock >= state["lobby_expires_at"]:
                return rules.expire(state, now=clock)
            if (state["phase"] in rules.ACTIVE_PHASES and state.get("deadline") is not None
                    and clock >= state["deadline"]):
                return rules.expire(state, now=clock)
            return state

        action_id = f"clock:{round_id}:{stage[0]}:{stage[1]}:{secrets.token_hex(6)}"
        return await self._apply(game_key=game_key, chat_id=chat_id, thread_id=thread_id,
                                 action_id=action_id, transition=transition, round_id=round_id)

    async def claim_panel(self, *, game_key, chat_id, thread_id=None, now=None, force=False):
        clock = time.time() if now is None else float(now)
        async with self._lock(game_key, chat_id, thread_id):
            for _ in range(8):
                state = await self.current(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
                if (not state or (not state.get("panel_dirty") and not force) or
                        float(state.get("delivery_retry_at") or 0) > clock):
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
        clock = time.time() if now is None else float(now)
        async with self._lock(game_key, chat_id, thread_id):
            for _ in range(8):
                state = await self.current(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
                if not state or state["round_id"] != round_id:
                    return False
                result = dict(state, version=state["version"] + 1)
                if state["version"] != int(expected_version):
                    if not state.get("panel_message_id"):
                        result["panel_message_id"] = int(message_id)
                    result.update(panel_dirty=True, delivery_retry_at=0)
                else:
                    result.update(panel_message_id=int(message_id), panel_dirty=False,
                                  delivery_retry_at=0)
                    if state["phase"] in rules.ACTIVE_PHASES and state.get("deadline") is None:
                        if state.get("panel_wait_until") is not None and clock >= state["panel_wait_until"]:
                            result.update(phase="cancelled", phase_seq=state["phase_seq"] + 1,
                                          panel_wait_until=None, private_dirty=False,
                                          private_retry_at=None, score_pending=False,
                                          panel_dirty=True, reason="delivery_timeout")
                        elif (state["phase"] == "collect" and
                              any(p["active"] and str(p["id"]) not in state.get("dm_messages", {})
                                  for p in state["players"])):
                            # The group card is visible, but a sealed answer is not actionable
                            # until every active participant can use the private reply route.
                            pass
                        else:
                            duration = {"collect": 60, "hunt": 120, "clue": 60,
                                        "story_turn": 45, "bomb_turn": 10, "reveal": 5}[state["phase"]]
                            if state["phase"] == "bomb_turn":
                                fuse_end = (float(state["fuse_deadline"])
                                            if state.get("fuse_deadline") is not None else
                                            clock + float(state["fuse_remaining"]))
                                result["fuse_deadline"] = fuse_end
                                result["deadline"] = min(clock + duration, fuse_end)
                            else:
                                result["deadline"] = clock + duration
                            result.update(panel_wait_until=None, panel_dirty=True,
                                          delivery_retry_at=0)
                            if state["phase"] != "reveal":
                                result["input_opened_at"] = clock
                                if state["phase"] != "collect":
                                    result["input_floor"] = int(message_id)
                if await self.repository.compare_and_save_game_state(
                    int(chat_id), self.key(game_key, thread_id), result,
                    expected_version=state["version"]):
                    return state["version"] == int(expected_version)
            raise RuntimeError("game_state_conflict")

    async def mint_invite(self, *, game_key, chat_id, thread_id=None, user_id,
                          round_id, now=None):
        clock = time.time() if now is None else float(now)
        nonce = secrets.token_hex(5)
        token = (f"wa_{_b36(abs(chat_id))}_{_b36(thread_id or 0)}_"
                 f"{_CODES[game_key]}_{nonce}")
        digest = hashlib.sha256(token.encode()).hexdigest()

        def transition(state):
            if game_key == "yasak_harf":
                if state["phase"] != "secret_delivery" or state["narrator_id"] != int(user_id):
                    raise ValueError("wa_invite_forbidden")
            elif game_key == "harf_maratonu":
                if state["phase"] != "collect":
                    raise ValueError("wa_invite_forbidden")
                rules._member(state, user_id)
            else:
                raise ValueError("wa_invite_forbidden")
            result = deepcopy(state)
            result["version"] += 1
            result["invitations"][str(user_id)] = {"hash": digest,
                "expires_at": clock + 600, "phase_seq": state["phase_seq"],
                "round_id": state["round_id"]}
            return result

        await self._apply(game_key=game_key, chat_id=chat_id, thread_id=thread_id,
                          action_id=f"invite:{user_id}:{nonce}", transition=transition,
                          round_id=round_id)
        return token

    async def accept_invite(self, payload, *, user_id, now=None):
        clock = time.time() if now is None else float(now)
        try:
            prefix, chat_code, topic_code, code, nonce = str(payload).split("_", 4)
            if prefix != "wa" or code not in _KEYS or len(nonce) != 10:
                raise ValueError
            chat_id, thread_id = -int(chat_code, 36), int(topic_code, 36) or None
            game_key = _KEYS[code]
        except (ValueError, TypeError) as exc:
            raise ValueError("wa_invalid_invite") from exc
        state = await self.current(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
        if not state:
            raise ValueError("wa_invite_forbidden")
        entry = state.get("invitations", {}).get(str(user_id))
        digest = hashlib.sha256(str(payload).encode()).hexdigest()
        if (not entry or not secrets.compare_digest(entry["hash"], digest) or
                clock >= entry["expires_at"] or entry["phase_seq"] != state["phase_seq"] or
                entry["round_id"] != state["round_id"]):
            raise ValueError("wa_invite_forbidden")

        def transition(current):
            invite = current.get("invitations", {}).get(str(user_id))
            if not invite or not secrets.compare_digest(invite["hash"], digest):
                raise ValueError("wa_invite_forbidden")
            if clock >= invite["expires_at"] or invite["phase_seq"] != current["phase_seq"]:
                raise ValueError("wa_invite_forbidden")
            result = deepcopy(current)
            result["version"] += 1
            del result["invitations"][str(user_id)]
            return result

        await self._apply(game_key=game_key, chat_id=chat_id, thread_id=thread_id,
            action_id=f"accept:{digest[:20]}", transition=transition,
            round_id=state["round_id"], phase_seq=state["phase_seq"])
        return chat_id, thread_id, game_key

    async def register_private(self, *, game_key, chat_id, thread_id=None,
                               user_id, round_id, message_id):
        def transition(state):
            if game_key == "harf_maratonu" and state["phase"] == "collect":
                rules._member(state, user_id)
            elif game_key == "yasak_harf" and state["phase"] == "secret_delivery" and state["narrator_id"] == int(user_id):
                pass
            else:
                raise ValueError("wa_stale_private")
            result = deepcopy(state)
            result["version"] += 1
            result["dm_messages"][str(user_id)] = int(message_id)
            result.setdefault("private_input_floor", {})[str(user_id)] = int(message_id)
            if game_key == "yasak_harf":
                result["private_dirty"] = True
                result["private_retry_at"] = 0
            return result

        return await self._apply(game_key=game_key, chat_id=chat_id, thread_id=thread_id,
            action_id=f"private_open:{round_id}:{user_id}:{message_id}",
            transition=transition, round_id=round_id)

    async def claim_private(self, *, game_key, chat_id, thread_id=None, now=None):
        clock = time.time() if now is None else float(now)
        if game_key != "yasak_harf":
            return None
        async with self._lock(game_key, chat_id, thread_id):
            for _ in range(8):
                state = await self.current(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
                if (not state or state["phase"] != "secret_delivery" or
                        not state.get("private_dirty") or
                        not state.get("dm_messages", {}).get(str(state["narrator_id"])) or
                        float(state.get("private_retry_at") or 0) > clock):
                    return None
                result = dict(state, private_retry_at=clock + 30, version=state["version"] + 1)
                if await self.repository.compare_and_save_game_state(
                    int(chat_id), self.key(game_key, thread_id), result,
                    expected_version=state["version"]):
                    return result
            raise RuntimeError("game_state_conflict")

    async def acknowledge_private(self, *, game_key, chat_id, thread_id=None,
                                  round_id, expected_version, message_id):
        async with self._lock(game_key, chat_id, thread_id):
            for _ in range(8):
                state = await self.current(game_key=game_key, chat_id=chat_id, thread_id=thread_id)
                if not state or state["round_id"] != round_id or state["phase"] != "secret_delivery":
                    return False
                result = dict(state, version=state["version"] + 1)
                if state["version"] == int(expected_version):
                    result.update(private_message_id=int(message_id), private_dirty=False,
                                  private_retry_at=None)
                else:
                    if not state.get("private_message_id"):
                        result["private_message_id"] = int(message_id)
                    result.update(private_dirty=True, private_retry_at=0)
                if await self.repository.compare_and_save_game_state(
                    int(chat_id), self.key(game_key, thread_id), result,
                    expected_version=state["version"]):
                    return state["version"] == int(expected_version)
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
                    points = int(state["scores"].get(str(player["id"]), 0))
                    if not player["active"] or points <= 0:
                        continue
                    event = f"{game_key}:{chat_id}:{state['round_id']}:{player['id']}"
                    if state.get("leaderboard_required") is not False and self.leaderboard:
                        await self.leaderboard.record_score(user_id=player["id"], chat_id=int(chat_id),
                            game_type=game_key, score=points, user_name=player["name"], event_id=event)
                    if state.get("economy_required") is not False and self.economy is not None:
                        await self.economy.credit(player["id"], int(chat_id), max(1, points // 10),
                            reason=f"{game_key}_reward", event_id=f"coin:{event}")
                result = dict(state, score_pending=False, panel_dirty=True,
                              delivery_retry_at=0, version=state["version"] + 1)
                if await self.repository.compare_and_save_game_state(
                    int(chat_id), self.key(game_key, thread_id), result,
                    expected_version=state["version"]):
                    self.metrics.inc("word_arcade_score_settlement_total")
                    return
            raise RuntimeError("game_state_conflict")
