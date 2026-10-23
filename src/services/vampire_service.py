"""CAS-backed Vampir Köylü sessions and private delivery claims."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import time

from src.games import vampire
from src.observability.metrics import MetricsRegistry


def base36(value):
    value = int(value)
    if value < 0:
        raise ValueError('vampire_invalid_action')
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    result = ""
    while value:
        value, digit = divmod(value, 36)
        result = digits[digit] + result
    return result or "0"


def _controls_pending(state):
    if state.get('ruleset') == 'greywolf':
        recipients = (vampire.private_recipients(state) if state['phase'] == 'role_delivery'
                      or state.get('panel_ready') else [])
        return any(str(p['id']) not in state.get('private_delivery', {}) for p in recipients)
    phase = state["phase"]
    if phase == "role_delivery":
        recipients = [p for p in state["players"] if p["active"]]
    elif phase == "night" and state.get("panel_ready"):
        recipients = [p for p in state["players"] if p["active"] and p["alive"] and p["role"] != "villager"]
    elif phase in {"vote", "runoff"} and state.get("panel_ready"):
        recipients = [p for p in state["players"] if p["active"] and p["alive"]]
    else:
        recipients = []
    return any(str(p["id"]) not in state.get("private_delivery", {}) for p in recipients)


def _private_pending(state):
    return (_controls_pending(state) or
            any(not item["delivered"] for item in state.get("seer_findings", {}).values()))


class VampireService:
    GAME_TYPE = "vampir_koylu"

    def __init__(self, repository, leaderboard=None, economy=None, metrics=None, *, ruleset='legacy'):
        if ruleset not in {'legacy', 'greywolf'}:
            raise ValueError('werewolf_invalid_ruleset')
        self.ruleset = ruleset
        self.repository = repository
        self.leaderboard = leaderboard
        self.economy = economy
        self.metrics = metrics if metrics is not None else MetricsRegistry()
        self._locks = {}

    @staticmethod
    def key(thread_id):
        return "vampir_koylu" if thread_id is None else f"vampir_koylu_t{int(thread_id)}"

    def _lock(self, chat_id, thread_id):
        return self._locks.setdefault((int(chat_id), thread_id), asyncio.Lock())

    async def current(self, chat_id, thread_id=None):
        return await self.repository.get_game_state(int(chat_id), self.key(thread_id))

    async def _apply(self, chat_id, thread_id, action_id, transform, *, round_id=None, phase_seq=None):
        if not action_id or len(str(action_id)) > 180:
            raise ValueError("vampire_invalid_action")
        async with self._lock(chat_id, thread_id):
            for _ in range(8):
                state = await self.current(chat_id, thread_id)
                if not state:
                    raise ValueError("vampire_not_found")
                if round_id is not None and state["round_id"] != round_id:
                    raise ValueError("vampire_stale_round")
                if phase_seq is not None and int(state["phase_seq"]) != int(phase_seq):
                    raise ValueError("vampire_stale_phase")
                if action_id in state.get("processed_actions", []):
                    self.metrics.inc("game_duplicate_action_total")
                    return "duplicate", state
                result = transform(state)
                if result is state or int(result["version"]) == int(state["version"]):
                    return "ignored", state
                before, after = vampire.public_state(state), vampire.public_state(result)
                for view in (before, after):
                    view.pop("version", None)
                changed = before != after
                result["panel_dirty"] = bool(state.get("panel_dirty") or changed)
                if changed:
                    result["panel_retry_at"] = 0
                if state["phase_seq"] != result["phase_seq"]:
                    result["private_delivery"] = {}
                    result["private_retry"] = {}
                    result["private_retry_at"] = 0
                result["private_dirty"] = _private_pending(result)
                if result["private_dirty"] and not state.get("private_dirty"):
                    result["private_retry_at"] = 0
                result["version"] = int(state["version"]) + 1
                result["processed_actions"] = [*state.get("processed_actions", []), str(action_id)][-1024:]
                if await self.repository.compare_and_save_game_state(
                    int(chat_id), self.key(thread_id), result, expected_version=int(state["version"])):
                    self.metrics.inc("game_action_total")
                    return "applied", result
            raise RuntimeError("game_state_conflict")

    async def open(self, *, chat_id, thread_id=None, user_id, user_name, mode=None):
        if mode not in {None, 'classic', 'normal', 'chaos'}:
            raise ValueError('werewolf_invalid_ruleset')
        async with self._lock(chat_id, thread_id):
            for _ in range(8):
                old = await self.current(chat_id, thread_id)
                if old and old["phase"] not in vampire.TERMINAL:
                    return "existing", old
                if old and old.get("score_pending"):
                    raise ValueError("vampire_rewards_pending")
                previous_mode = (old or {}).get('ruleset') if old else None
                advanced = (mode in {'normal', 'chaos'} or
                            mode is None and (previous_mode == 'greywolf' or
                                              old is None and self.ruleset == 'greywolf'))
                options = None
                if advanced:
                    previous_settings = (old or {}).get('settings') or {}
                    kept = ('repeat_protection', 'self_heal', 'reveal_on_death', 'full_thief',
                            'burning_overkill', 'disabled_roles', 'max_days')
                    options = {'ruleset': 'greywolf', **{key: previous_settings[key] for key in kept
                                                         if previous_mode == 'greywolf' and key in previous_settings},
                               'chaos': mode == 'chaos' if mode is not None else
                                        bool(previous_settings.get('chaos', False))}
                fresh = vampire.new_lobby(chat_id=chat_id, thread_id=thread_id,
                                          owner_id=user_id, owner_name=user_name,
                                          round_id=secrets.token_hex(6), settings=options)
                fresh["panel_dirty"] = True
                if old is None:
                    saved = await self.repository.create_game_state_if_absent(
                        int(chat_id), self.key(thread_id), fresh)
                else:
                    fresh["version"] = int(old["version"]) + 1
                    saved = await self.repository.compare_and_save_game_state(
                        int(chat_id), self.key(thread_id), fresh, expected_version=int(old["version"]))
                if saved:
                    self.metrics.inc("game_lobby_created_total")
                    return "created", fresh
            raise RuntimeError("game_state_conflict")

    async def act(self, *, chat_id, thread_id=None, user_id, action_id, action,
                  user_name="Oyuncu", round_id=None, phase_seq=None, target_id=None,
                  moderator=False, now=None, submitted_at=None, option=None, enabled=None):
        clock = time.time() if now is None else float(now)

        def transition(state):
            if submitted_at is not None and float(submitted_at) < float(state["created_at"]):
                raise ValueError("vampire_stale_round")
            if state.get('ruleset') == 'greywolf':
                from src.games.werewolf import engine
                if action == 'day':
                    return engine.day_action(state, user_id=user_id, target_id=target_id, now=clock)
                if action == 'ignite':
                    return engine.ignite(state, user_id=user_id, now=clock)
                if action == 'hunter':
                    return engine.hunter_action(state, user_id=user_id, target_id=target_id, now=clock)
                if action == 'disable_role':
                    return engine.disable_role(state, user_id=user_id, role_key=option, now=clock)
            if action == "join":
                return vampire.join(state, user_id=user_id, user_name=user_name, now=clock)
            if action == "start":
                return vampire.start(state, user_id=user_id, now=clock)
            if action == "configure":
                return vampire.configure(state, user_id=user_id, option=option,
                                         enabled=enabled, now=clock)
            if action == "ack":
                if not state.get("private_delivery", {}).get(str(user_id)):
                    raise ValueError("vampire_role_not_delivered")
                return vampire.role_ack(state, user_id=user_id, now=clock)
            if action == "night":
                return vampire.night_action(state, user_id=user_id, target_id=target_id, now=clock)
            if action == "vote":
                return vampire.vote(state, user_id=user_id, target_id=target_id, now=clock)
            if action == "leave":
                return vampire.leave(state, user_id=user_id, now=clock)
            if action == "stop":
                return vampire.cancel(state, user_id=user_id, moderator=moderator)
            raise ValueError("vampire_invalid_action")

        return await self._apply(chat_id, thread_id, action_id, transition,
                                 round_id=round_id, phase_seq=phase_seq)

    async def mint_invite(self, *, chat_id, thread_id=None, user_id, round_id, request_id=None):
        token = secrets.token_urlsafe(12)
        digest = hashlib.sha256(token.encode()).hexdigest()
        moment = time.time()

        def transform(state):
            if state["phase"] != "lobby":
                raise ValueError("vampire_lobby_closed")
            vampire._player(state, user_id)
            previous = state.get("dm_invites", {}).get(str(user_id), {})
            if request_id is not None and previous.get("request_id") == str(request_id):
                raise ValueError("vampire_invite_pending")
            if moment - float(previous.get("issued_at", 0)) < 30:
                raise ValueError("vampire_invite_recent")
            result = vampire._copy(state)
            result.setdefault("dm_invites", {})[str(user_id)] = {
                "hash": digest, "expires_at": moment + 600,
                "issued_at": moment, "request_id": str(request_id) if request_id else None}
            return result

        await self._apply(chat_id, thread_id, f"invite:{digest}", transform, round_id=round_id)
        return f"vp_{base36(abs(chat_id))}_{base36(thread_id or 0)}_{base36(user_id)}_{token}"

    async def accept_invite(self, payload, *, user_id):
        try:
            prefix, chat, topic, owner, token = str(payload).split("_", 4)
            if prefix != "vp" or int(owner, 36) != int(user_id):
                raise ValueError()
            chat_id, thread_id = -int(chat, 36), int(topic, 36) or None
        except (ValueError, TypeError):
            raise ValueError("vampire_invalid_invite") from None
        digest = hashlib.sha256(token.encode()).hexdigest()

        def transform(state):
            invitation = state.get("dm_invites", {}).get(str(user_id), {})
            if (state["phase"] != "lobby" or time.time() >= float(invitation.get("expires_at", 0))
                    or not hmac.compare_digest(str(invitation.get("hash", "")), digest)):
                raise ValueError("vampire_invalid_invite")
            result = vampire.ready(state, user_id=user_id)
            result.setdefault("dm_invites", {}).pop(str(user_id), None)
            return result

        status, state = await self._apply(chat_id, thread_id, f"ready:{digest}", transform)
        if status == "duplicate":
            raise ValueError("vampire_invalid_invite")
        return chat_id, thread_id, state

    async def advance(self, *, chat_id, thread_id=None, round_id, now=None):
        clock = time.time() if now is None else float(now)
        for _ in range(4):
            current = await self.current(chat_id, thread_id)
            if not current:
                raise ValueError("vampire_not_found")
            if current["round_id"] != round_id:
                raise ValueError("vampire_stale_round")
            phase_seq = int(current["phase_seq"])
            try:
                return await self._apply(chat_id, thread_id,
                    f"clock:{round_id}:{phase_seq}:{clock}",
                    lambda state: vampire.advance(state, now=clock),
                    round_id=round_id, phase_seq=phase_seq)
            except ValueError as exc:
                if str(exc) != "vampire_stale_phase":
                    raise
        raise RuntimeError("game_state_conflict")

    async def claim_delivery(self, *, chat_id, thread_id=None, kind, user_id=None, now=None):
        if kind not in {"panel", "private", "finding"}:
            raise ValueError("vampire_invalid_delivery")
        clock = time.time() if now is None else float(now)
        async with self._lock(chat_id, thread_id):
            for _ in range(8):
                state = await self.current(chat_id, thread_id)
                if not state:
                    return None
                if kind == "panel":
                    eligible = state.get("panel_dirty") and float(state.get("panel_retry_at", 0)) <= clock
                    retry = "panel_retry_at"
                elif kind == "private":
                    person = next((p for p in state["players"] if p["id"] == int(user_id or 0)), None)
                    eligible = (person is not None and person["active"] and
                                (state["phase"] == "role_delivery" or
                                 (state["phase"] == "night" and state.get("panel_ready") and person["alive"] and person["role"] != "villager") or
                                 (state["phase"] in {"vote", "runoff"} and state.get("panel_ready") and person["alive"])))
                    if state.get('ruleset') == 'greywolf':
                        eligible = (state['phase'] == 'role_delivery' or state.get('panel_ready')) and any(
                            p['id'] == int(user_id or 0) for p in vampire.private_recipients(state))
                    eligible = eligible and not state.get("private_delivery", {}).get(str(user_id))
                    eligible = eligible and float(state.get("private_retry", {}).get(str(user_id), 0)) <= clock
                    retry = None
                else:
                    finding = state.get("seer_findings", {}).get(str(user_id))
                    eligible = finding is not None and not finding["delivered"] and float(finding.get("retry_at", 0)) <= clock
                    retry = None
                if not eligible:
                    return None
                updated = dict(state, version=int(state["version"]) + 1)
                if retry:
                    updated[retry] = clock + 30
                elif kind == "private":
                    updated["private_retry"] = dict(state.get("private_retry", {}))
                    updated["private_retry"][str(user_id)] = clock + 30
                    updated["private_retry_at"] = clock + 30
                else:
                    updated["seer_findings"] = dict(state["seer_findings"])
                    updated["seer_findings"][str(user_id)] = dict(finding, retry_at=clock + 30)
                    updated["private_retry_at"] = clock + 30
                if await self.repository.compare_and_save_game_state(
                    int(chat_id), self.key(thread_id), updated, expected_version=int(state["version"])):
                    return updated
            raise RuntimeError("game_state_conflict")

    async def request_panel_refresh(self, *, chat_id, thread_id=None, round_id, delay_seconds=0):
        """Restore the live panel after a temporary read-only group guide."""
        async with self._lock(chat_id, thread_id):
            for _ in range(8):
                state = await self.current(chat_id, thread_id)
                if not state or state['round_id'] != round_id:
                    return False
                updated = dict(state, version=int(state['version']) + 1,
                               panel_dirty=True, panel_retry_at=time.time() + max(0, delay_seconds))
                if await self.repository.compare_and_save_game_state(
                    int(chat_id), self.key(thread_id), updated, expected_version=int(state['version'])):
                    return True
            raise RuntimeError('game_state_conflict')

    async def acknowledge_panel(self, *, chat_id, thread_id=None, round_id, expected_version, message_id, now=None):
        clock = time.time() if now is None else float(now)
        async with self._lock(chat_id, thread_id):
            for _ in range(8):
                state = await self.current(chat_id, thread_id)
                if not state or state["round_id"] != round_id:
                    return False
                updated = dict(state, version=int(state["version"]) + 1,
                               panel_message_id=int(message_id))
                if state["version"] == expected_version:
                    updated["panel_dirty"] = False
                    if (state["phase"] in {"night", "discussion", "vote", "runoff", "hunter"}
                            and not state["phase_ready"]
                            and clock < float(state.get("panel_wait_until") or 0)):
                        updated["panel_ready"] = True
                        can_activate = (not _private_pending(updated) if state.get('ruleset') == 'greywolf'
                                        else state['phase'] == 'discussion')
                        if can_activate:
                            activated = vampire.activate(state, now=clock)
                            updated.update({"phase_ready": activated["phase_ready"],
                                            "deadline": activated["deadline"],
                                            "panel_wait_until": activated["panel_wait_until"],
                                            "panel_dirty": True, "panel_retry_at": 0})
                        else:
                            updated["private_dirty"] = True
                            updated["private_retry_at"] = 0
                if await self.repository.compare_and_save_game_state(
                    int(chat_id), self.key(thread_id), updated, expected_version=int(state["version"])):
                    return True
            raise RuntimeError("game_state_conflict")

    async def acknowledge_private(self, *, chat_id, thread_id=None, round_id,
                                  phase_seq, user_id, message_id=None, now=None):
        clock = time.time() if now is None else float(now)
        def transform(state):
            allowed_phases = {'role_delivery', 'night', 'vote', 'runoff'}
            if state.get('ruleset') == 'greywolf':
                allowed_phases |= {'discussion', 'hunter'}
            if state["phase"] not in allowed_phases:
                raise ValueError("vampire_stale_phase")
            if (state["phase"] != "role_delivery" and
                    (not state.get("panel_ready") or clock >= float(state.get("panel_wait_until") or 0))
                    and not state["phase_ready"]):
                raise ValueError("vampire_stale_phase")
            vampire._player(state, user_id)
            result = vampire._copy(state)
            result.setdefault("private_delivery", {})[str(user_id)] = int(message_id) if message_id else True
            pending = _private_pending(result) if state.get('ruleset') == 'greywolf' else _controls_pending(result)
            if (state["phase"] != 'role_delivery' and
                    state.get("panel_ready") and not pending and not state["phase_ready"]):
                activated = vampire.activate(result, now=clock)
                result.update({"phase_ready": activated["phase_ready"],
                               "deadline": activated["deadline"],
                               "panel_wait_until": activated["panel_wait_until"]})
            result["private_dirty"] = _private_pending(result)
            result["private_retry_at"] = 0 if result["private_dirty"] else None
            return result
        return await self._apply(chat_id, thread_id, f"private:{phase_seq}:{user_id}",
                                 transform, round_id=round_id, phase_seq=phase_seq)

    async def acknowledge_finding(self, *, chat_id, thread_id=None, round_id, finding_id, user_id, now=None):
        clock = time.time() if now is None else float(now)
        def transform(state):
            item = state.get("seer_findings", {}).get(finding_id)
            if not item or item["user_id"] != int(user_id):
                raise ValueError("vampire_stale_phase")
            result = vampire._copy(state)
            result["seer_findings"][finding_id]["delivered"] = True
            if (state.get('ruleset') == 'greywolf' and state.get('panel_ready') and
                    state['phase'] != 'role_delivery' and not state['phase_ready'] and
                    clock < float(state.get('panel_wait_until') or 0) and not _private_pending(result)):
                activated = vampire.activate(result, now=clock)
                result.update({key: activated[key] for key in ('phase_ready', 'deadline', 'panel_wait_until')})
            result["private_dirty"] = _private_pending(result)
            result["private_retry_at"] = 0 if result["private_dirty"] else None
            return result
        return await self._apply(chat_id, thread_id, f"finding-delivered:{finding_id}",
                                 transform, round_id=round_id)

    async def settle_scores(self, *, chat_id, thread_id=None):
        async with self._lock(chat_id, thread_id):
            for _ in range(8):
                state = await self.current(chat_id, thread_id)
                if not state or state["phase"] != "finished" or not state.get("score_pending"):
                    return
                if self.leaderboard is None:
                    raise RuntimeError("game_score_store_unavailable")
                for person in state["players"]:
                    if not person["active"]:
                        continue
                    won = (person['id'] in state.get('winner_ids', []) if state.get('ruleset') == 'greywolf'
                           else (state["winner"] == "vampires") == (person["role"] == "vampire"))
                    score = 10 if won else 0
                    event = f"vampir_koylu:{chat_id}:{state['round_id']}:{person['id']}"
                    await self.leaderboard.record_score(user_id=person["id"], chat_id=int(chat_id),
                                                        game_type=self.GAME_TYPE, score=score,
                                                        user_name=person["name"], event_id=event)
                    if self.economy and score:
                        await self.economy.credit(person["id"], int(chat_id), 5,
                                                  reason="vampir_koylu_reward", event_id=f"coin:{event}")
                updated = dict(state, version=int(state["version"]) + 1,
                               score_pending=False, panel_dirty=True, panel_retry_at=0)
                if await self.repository.compare_and_save_game_state(
                    int(chat_id), self.key(thread_id), updated, expected_version=int(state["version"])):
                    return
            raise RuntimeError("game_state_conflict")
