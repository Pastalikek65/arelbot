"""Pure rules for the private-role Vampir Köylü game."""

from __future__ import annotations

from copy import deepcopy
import secrets
import time

MIN_PLAYERS, MAX_PLAYERS = 7, 20
LOBBY_SECONDS = 1200
DELIVERY_SECONDS = 600
NIGHT_SECONDS = 90
DISCUSSION_SECONDS = 90
VOTE_SECONDS = 60
RUNOFF_SECONDS = 30
PANEL_WAIT_SECONDS = 300
MAX_DAYS = 12
MAX_EMPTY_DAYS = 3
TERMINAL = {"finished", "cancelled"}


def _time(now):
    return time.time() if now is None else float(now)


def _copy(state):
    result = deepcopy(state)
    result["version"] = int(state["version"]) + 1
    return result


def _player(state, user_id, *, alive=False):
    person = next((p for p in state["players"] if p["id"] == int(user_id)), None)
    if person is None or not person["active"] or (alive and not person["alive"]):
        raise ValueError("vampire_not_player")
    return person


def living(state):
    return [p for p in state["players"] if p["active"] and p["alive"]]


def role_counts(count):
    if not MIN_PLAYERS <= count <= MAX_PLAYERS:
        raise ValueError("vampire_player_count")
    vampires = 1 if count <= 8 else 2 if count <= 13 else 3 if count <= 17 else 4
    return {"vampire": vampires, "seer": 1, "healer": 1, "villager": count - vampires - 2}


def new_lobby(*, chat_id, thread_id, owner_id, owner_name, round_id,
              now=None, settings=None):
    if (settings or {}).get('ruleset') == 'greywolf':
        from src.games.werewolf import engine
        return engine.new_lobby(chat_id=chat_id, thread_id=thread_id, owner_id=owner_id, owner_name=owner_name, round_id=round_id, now=now, settings={k:v for k,v in settings.items() if k != 'ruleset'})
    moment = _time(now)
    options = {"repeat_protection": False, "self_heal": True, "reveal_on_death": False}
    options.update(settings or {})
    return {"round_id": str(round_id), "chat_id": int(chat_id),
            "thread_id": int(thread_id) if thread_id is not None else None,
            "owner_id": int(owner_id), "players": [{"id": int(owner_id),
            "name": str(owner_name)[:60], "ready": False, "active": True,
            "alive": True, "role": None, "role_ack": False}],
            "phase": "lobby", "version": 0, "created_at": moment,
            "last_activity_at": moment, "deadline": None, "day": 0,
            "phase_seq": 0, "phase_ready": False, "panel_ready": False,
            "settings": options,
            "panel_wait_until": None,
            "actions": {}, "votes": {}, "runoff_candidates": [],
            "seer_findings": {},
            "previous_protected": None, "last_deaths": [], "last_event": None,
            "empty_days": 0, "winner": None, "cancel_reason": None,
            "score_pending": False}


def join(state, *, user_id, user_name, now=None):
    if state.get('ruleset') == 'greywolf':
        from src.games.werewolf import engine
        return engine.join(state, user_id=user_id, user_name=user_name, now=now)
    if state["phase"] != "lobby":
        raise ValueError("vampire_lobby_closed")
    if _time(now) >= state["last_activity_at"] + LOBBY_SECONDS:
        raise ValueError("vampire_stale_phase")
    if any(p["id"] == int(user_id) for p in state["players"]):
        raise ValueError("vampire_already_joined")
    if len(state["players"]) >= MAX_PLAYERS:
        raise ValueError("vampire_player_count")
    result = _copy(state)
    result["players"].append({"id": int(user_id), "name": str(user_name)[:60],
                              "ready": False, "active": True, "alive": True,
                              "role": None, "role_ack": False})
    result["last_activity_at"] = _time(now)
    return result


def ready(state, *, user_id, now=None):
    if state.get('ruleset') == 'greywolf':
        from src.games.werewolf import engine
        return engine.ready(state, user_id=user_id, now=now)
    if state["phase"] != "lobby":
        raise ValueError("vampire_lobby_closed")
    if _time(now) >= state["last_activity_at"] + LOBBY_SECONDS:
        raise ValueError("vampire_stale_phase")
    _player(state, user_id)
    result = _copy(state)
    _player(result, user_id)["ready"] = True
    result["last_activity_at"] = _time(now)
    return result


def configure(state, *, user_id, option, enabled, now=None):
    if state.get('ruleset') == 'greywolf':
        from src.games.werewolf import engine
        return engine.configure(state, user_id=user_id, option=option, enabled=enabled, now=now)
    if state["phase"] != "lobby" or int(user_id) != state["owner_id"]:
        raise ValueError("vampire_not_owner")
    if _time(now) >= state["last_activity_at"] + LOBBY_SECONDS:
        raise ValueError("vampire_stale_phase")
    if option not in {"repeat_protection", "self_heal", "reveal_on_death"}:
        raise ValueError("vampire_invalid_setting")
    result = _copy(state)
    result["settings"][option] = bool(enabled)
    result["last_activity_at"] = _time(now)
    return result


def start(state, *, user_id, roles=None, now=None):
    if state.get('ruleset') == 'greywolf':
        from src.games.werewolf import engine
        return engine.start(state, user_id=user_id, roles=roles, now=now)
    if state["phase"] != "lobby" or int(user_id) != state["owner_id"]:
        raise ValueError("vampire_not_owner")
    if _time(now) >= state["last_activity_at"] + LOBBY_SECONDS:
        raise ValueError("vampire_stale_phase")
    counts = role_counts(len(state["players"]))
    if not all(p["ready"] for p in state["players"]):
        raise ValueError("vampire_not_ready")
    assigned = list(roles) if roles is not None else list(
        secrets.SystemRandom().sample(
            [role for role, amount in counts.items() for _ in range(amount)],
            len(state["players"])))
    if len(assigned) != len(state["players"]) or any(assigned.count(k) != v for k, v in counts.items()):
        raise ValueError("vampire_invalid_roles")
    result = _copy(state)
    for player, role in zip(result["players"], assigned):
        player["role"] = role
        player["role_ack"] = False
    result["phase"] = "role_delivery"
    result["phase_seq"] += 1
    result["last_activity_at"] = _time(now)
    return result


def role_ack(state, *, user_id, now=None):
    if state.get('ruleset') == 'greywolf':
        from src.games.werewolf import engine
        return engine.role_ack(state, user_id=user_id, now=now)
    if state["phase"] != "role_delivery":
        raise ValueError("vampire_stale_phase")
    if _time(now) >= state["last_activity_at"] + DELIVERY_SECONDS:
        raise ValueError("vampire_stale_phase")
    _player(state, user_id)
    result = _copy(state)
    _player(result, user_id)["role_ack"] = True
    if all(p["role_ack"] for p in result["players"] if p["active"]):
        _next_phase(result, "night", now=now)
        result["day"] = 1
    return result


def _next_phase(state, phase, *, now=None):
    state["phase"] = phase
    state["phase_seq"] += 1
    state["phase_ready"] = False
    state["panel_ready"] = False
    state["deadline"] = None
    state["panel_wait_until"] = _time(now) + PANEL_WAIT_SECONDS
    state["actions"] = {}
    state["votes"] = {}


def activate(state, *, now=None):
    if state.get('ruleset') == 'greywolf':
        from src.games.werewolf import engine
        return engine.activate(state, now=now)
    if state["phase"] not in {"night", "discussion", "vote", "runoff"}:
        return state
    if state["phase_ready"]:
        return state
    result = _copy(state)
    result["phase_ready"] = True
    result["panel_ready"] = True
    result["panel_wait_until"] = None
    seconds = {"night": NIGHT_SECONDS, "discussion": DISCUSSION_SECONDS,
               "vote": VOTE_SECONDS, "runoff": RUNOFF_SECONDS}[state["phase"]]
    result["deadline"] = _time(now) + seconds
    return result


def _winner(state):
    alive = living(state)
    vampires = sum(p["role"] == "vampire" for p in alive)
    if vampires == 0:
        return "villagers"
    if vampires >= len(alive) - vampires:
        return "vampires"
    return None


def _check_win(state):
    winner = _winner(state)
    if winner:
        state["winner"] = winner
        state["phase"] = "finished"
        state["phase_seq"] += 1
        state["deadline"] = None
        state["panel_wait_until"] = None
        state["score_pending"] = True
    return winner


def night_action(state, *, user_id, target_id=None, now=None):
    if state.get('ruleset') == 'greywolf':
        from src.games.werewolf import engine
        return engine.night_action(state, user_id=user_id, target_id=target_id, now=now)
    moment = _time(now)
    if state["phase"] != "night" or not state["phase_ready"] or moment >= state["deadline"]:
        raise ValueError("vampire_stale_phase")
    player = _player(state, user_id, alive=True)
    if player["role"] == "villager":
        raise ValueError("vampire_no_action")
    if str(user_id) in state["actions"]:
        raise ValueError("vampire_already_acted")
    if target_id is not None:
        target = _player(state, target_id, alive=True)
        if player["role"] == "vampire" and target["role"] == "vampire":
            raise ValueError("vampire_invalid_target")
        if player["role"] == "healer":
            if target["id"] == player["id"] and not state["settings"]["self_heal"]:
                raise ValueError("vampire_invalid_target")
            if target["id"] == state["previous_protected"] and not state["settings"]["repeat_protection"]:
                raise ValueError("vampire_invalid_target")
    result = _copy(state)
    result["actions"][str(user_id)] = int(target_id) if target_id is not None else None
    if player["role"] == "seer" and target_id is not None:
        result["seer_findings"][f"{state['day']}:{user_id}"] = {
            "user_id": int(user_id), "target_id": int(target_id),
            "is_vampire": target["role"] == "vampire", "delivered": False,
            "retry_at": 0}
    return result


def seer_result(state, *, user_id):
    player = _player(state, user_id)
    if player["role"] != "seer":
        raise ValueError("vampire_no_action")
    finding = state.get("seer_findings", {}).get(f"{state['day']}:{user_id}")
    return ({"target_id": finding["target_id"], "is_vampire": finding["is_vampire"]}
            if finding else None)


def _night_result(result, *, now):
    alive = living(result)
    healer = next((p for p in alive if p["role"] == "healer"), None)
    protected = result["actions"].get(str(healer["id"])) if healer else None
    result["previous_protected"] = protected
    targets = [result["actions"].get(str(p["id"])) for p in alive if p["role"] == "vampire"]
    targets = [t for t in targets if t is not None]
    # Strict majority of living vampires. A tie or missing ballots means no kill.
    victim = None
    if targets:
        counts = {t: targets.count(t) for t in set(targets)}
        best = max(counts, key=counts.get)
        if counts[best] > sum(p["role"] == "vampire" for p in alive) / 2:
            victim = best
    result["last_deaths"] = []
    if victim is not None and victim != protected and any(p["id"] == victim for p in alive):
        target = _player(result, victim, alive=True)
        target["alive"] = False
        result["last_deaths"] = [victim]
        result["last_event"] = "death"
        result["empty_days"] = 0
    else:
        result["last_event"] = "saved_or_no_kill"
    if not _check_win(result):
        _next_phase(result, "discussion", now=now)


def vote(state, *, user_id, target_id=None, now=None):
    if state.get('ruleset') == 'greywolf':
        from src.games.werewolf import engine
        return engine.vote(state, user_id=user_id, target_id=target_id, now=now)
    moment = _time(now)
    if state["phase"] not in {"vote", "runoff"} or not state["phase_ready"] or moment >= state["deadline"]:
        raise ValueError("vampire_stale_phase")
    _player(state, user_id, alive=True)
    if str(user_id) in state["votes"]:
        raise ValueError("vampire_already_voted")
    if target_id is not None:
        _player(state, target_id, alive=True)
        if int(target_id) == int(user_id) or (state["phase"] == "runoff" and int(target_id) not in state["runoff_candidates"]):
            raise ValueError("vampire_invalid_target")
    result = _copy(state)
    result["votes"][str(user_id)] = int(target_id) if target_id is not None else None
    return result


def _vote_result(result, *, now):
    alive = living(result)
    alive_ids = {p["id"] for p in alive}
    ballots = [v for voter, v in result["votes"].items()
               if int(voter) in alive_ids and v in alive_ids]
    counts = {v: ballots.count(v) for v in set(ballots)}
    result["last_deaths"] = []
    if counts:
        highest = max(counts.values())
        candidates = sorted(k for k, v in counts.items() if v == highest)
    else:
        highest, candidates = 0, []
    # Elimination needs an absolute majority of all living players.
    if len(candidates) == 1 and highest > len(alive) / 2:
        _player(result, candidates[0], alive=True)["alive"] = False
        result["last_deaths"] = candidates
        result["last_event"] = "elimination"
        result["empty_days"] = 0
        if _check_win(result):
            return
    elif result["phase"] == "vote" and len(candidates) > 1:
        result["runoff_candidates"] = candidates
        _next_phase(result, "runoff", now=now)
        return
    else:
        result["last_event"] = "no_elimination"
        result["empty_days"] += 1
    if result["empty_days"] >= MAX_EMPTY_DAYS or result["day"] >= MAX_DAYS:
        result["phase"] = "cancelled"
        result["cancel_reason"] = "inactivity_limit"
        result["phase_seq"] += 1
        result["deadline"] = None
        result["panel_wait_until"] = None
        result["score_pending"] = False
    else:
        result["day"] += 1
        _next_phase(result, "night", now=now)


def advance(state, *, now=None):
    if state.get('ruleset') == 'greywolf':
        from src.games.werewolf import engine
        return engine.advance(state, now=now)
    moment = _time(now)
    phase = state["phase"]
    if phase == "lobby" and moment >= state["last_activity_at"] + LOBBY_SECONDS:
        return cancel(state, reason="lobby_timeout")
    if phase == "role_delivery" and moment >= state["last_activity_at"] + DELIVERY_SECONDS:
        return cancel(state, reason="role_delivery_timeout")
    if (phase in {"night", "discussion", "vote", "runoff"} and not state["phase_ready"]
            and state.get("panel_wait_until") is not None
            and moment >= state["panel_wait_until"]):
        return cancel(state, reason="panel_delivery_timeout")
    if phase not in {"night", "discussion", "vote", "runoff"} or not state["phase_ready"] or moment < state["deadline"]:
        return state
    result = _copy(state)
    if phase == "night":
        _night_result(result, now=moment)
    elif phase == "discussion":
        _next_phase(result, "vote", now=moment)
    else:
        _vote_result(result, now=moment)
    return result


def leave(state, *, user_id, now=None):
    if state.get('ruleset') == 'greywolf':
        from src.games.werewolf import engine
        return engine.leave(state, user_id=user_id, now=now)
    if state["phase"] in TERMINAL:
        raise ValueError("vampire_closed")
    if state["phase_ready"] and state["deadline"] is not None and _time(now) >= state["deadline"]:
        raise ValueError("vampire_stale_phase")
    _player(state, user_id)
    result = _copy(state)
    person = _player(result, user_id)
    if state["phase"] == "lobby":
        result["players"] = [p for p in result["players"] if p["id"] != int(user_id)]
        if not result["players"]:
            return cancel(result, reason="empty_lobby")
    else:
        person["active"] = False
        person["alive"] = False
        if state["phase"] == "role_delivery":
            return cancel(result, reason="player_left")
        _check_win(result)
    if result["phase"] not in TERMINAL and result["owner_id"] == int(user_id):
        result["owner_id"] = next(p["id"] for p in result["players"] if p["active"])
    result["last_activity_at"] = _time(now)
    return result


def cancel(state, *, reason="moderator", user_id=None, moderator=False):
    if state.get('ruleset') == 'greywolf':
        from src.games.werewolf import engine
        return engine.cancel(state, reason=reason, user_id=user_id, moderator=moderator)
    if user_id is not None and not moderator and int(user_id) != state["owner_id"]:
        raise ValueError("vampire_not_owner")
    if state["phase"] in TERMINAL:
        raise ValueError("vampire_closed")
    result = _copy(state)
    result["phase"] = "cancelled"
    result["phase_seq"] += 1
    result["deadline"] = None
    result["panel_wait_until"] = None
    result["cancel_reason"] = reason
    result["score_pending"] = False
    return result


def public_state(state):
    if state.get('ruleset') == 'greywolf':
        from src.games.werewolf import engine
        return engine.public_state(state)
    result = {key: deepcopy(state.get(key)) for key in (
        "round_id", "chat_id", "thread_id", "owner_id", "phase", "phase_seq",
        "version", "day", "deadline", "phase_ready", "panel_ready", "last_deaths", "last_event",
        "panel_wait_until",
        "winner", "cancel_reason", "score_pending", "settings", "runoff_candidates")}
    result["players"] = [{k: deepcopy(p[k]) for k in ("id", "name", "ready", "active", "alive", "role_ack")}
                         for p in state["players"]]
    for view, player in zip(result["players"], state["players"]):
        if (state["phase"] == "finished" or
                state["settings"].get("reveal_on_death") and not player["alive"]):
            view["role"] = player["role"]
    result["ballots_cast"] = len(state.get("votes", {}))
    return result


def private_recipients(state):
    if state.get('ruleset') == 'greywolf':
        from src.games.werewolf.choices import private_recipients as recipients
        return recipients(state)
    if state['phase'] == 'role_delivery':
        return [p for p in state['players'] if p['active']]
    if state['phase'] == 'night':
        return [p for p in living(state) if p['role'] != 'villager']
    if state['phase'] in {'vote','runoff'}:
        return living(state)
    return []
