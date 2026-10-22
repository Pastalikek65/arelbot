"""Pure, versioned rules for two small Telegram grid puzzles."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import secrets
import time

MEMORY_KEY = "hafiza_eslestirme"
MINES_KEY = "mayin_tarlasi"
GAME_KEYS = (MEMORY_KEY, MINES_KEY)
TERMINAL = {"finished", "cancelled"}
PANEL_WAIT_SECONDS = 300
LOBBY_SECONDS = 1200
PREVIEW_SECONDS = 8
TURN_SECONDS = 30
MISMATCH_SECONDS = 3
MINES_SECONDS = 600
MAX_TURNS = 64
CARD_SYMBOLS = "ABCDEFGH"


def _now(now):
    return time.time() if now is None else float(now)


def _copy(state):
    result = deepcopy(state)
    result["version"] = int(state["version"]) + 1
    return result


def _person(state, user_id):
    player = next((p for p in state["players"] if p["id"] == int(user_id)), None)
    if not player:
        raise ValueError("grid_not_player")
    return player


def _base(*, game_key, chat_id, thread_id, owner_id, owner_name, round_id, now):
    moment = _now(now)
    return {"game_key": game_key, "chat_id": int(chat_id),
            "thread_id": int(thread_id) if thread_id is not None else None,
            "round_id": str(round_id), "owner_id": int(owner_id),
            "players": [{"id": int(owner_id), "name": str(owner_name)[:60], "score": 0}],
            "phase": "lobby" if game_key == MEMORY_KEY else "playing",
            "version": 0, "phase_seq": 0, "turn_seq": 0,
            "created_at": moment, "last_activity_at": moment,
            "lobby_expires_at": moment + LOBBY_SECONDS if game_key == MEMORY_KEY else None,
            "deadline": None, "panel_wait_until": (None if game_key == MEMORY_KEY else moment + PANEL_WAIT_SECONDS),
            "phase_ready": False, "panel_dirty": True, "delivery_retry_at": 0,
            "panel_message_id": None, "score_pending": False, "winner_ids": [],
            "outcome": None, "cancel_reason": None}


def new_memory(*, chat_id, thread_id, owner_id, owner_name, round_id, now=None, cards=None):
    state = _base(game_key=MEMORY_KEY, chat_id=chat_id, thread_id=thread_id,
                  owner_id=owner_id, owner_name=owner_name, round_id=round_id, now=now)
    if cards is None:
        cards = secrets.SystemRandom().sample(list(CARD_SYMBOLS * 2), 16)
    if len(cards) != 16 or sorted(cards) != sorted(CARD_SYMBOLS * 2):
        raise ValueError("grid_invalid_cards")
    state.update(cards=list(cards), matched=[], first_pick=None, second_pick=None,
                 current_actor=int(owner_id), turn_count=0, last_event=None)
    return state


def new_mines(*, chat_id, thread_id, owner_id, owner_name, round_id, now=None, seed=None):
    state = _base(game_key=MINES_KEY, chat_id=chat_id, thread_id=thread_id,
                  owner_id=owner_id, owner_name=owner_name, round_id=round_id, now=now)
    state.update(seed=str(seed if seed is not None else secrets.randbits(128)),
                 mines=None, opened=[], flags=[], flag_mode=False,
                 first_open=None)
    return state


def memory_join(state, *, user_id, user_name, now=None):
    if state["game_key"] != MEMORY_KEY or state["phase"] != "lobby":
        raise ValueError("grid_lobby_closed")
    if _now(now) >= state["lobby_expires_at"]:
        raise ValueError("grid_expired")
    if any(p["id"] == int(user_id) for p in state["players"]):
        raise ValueError("grid_already_joined")
    if len(state["players"]) >= 4:
        raise ValueError("grid_full")
    result = _copy(state)
    result["players"].append({"id": int(user_id), "name": str(user_name)[:60], "score": 0})
    return result


def _next_phase(state, phase, *, now):
    state["phase"] = phase
    state["phase_seq"] += 1
    state["phase_ready"] = False
    state["deadline"] = None
    state["panel_wait_until"] = _now(now) + PANEL_WAIT_SECONDS


def memory_start(state, *, user_id, now=None):
    if state["game_key"] != MEMORY_KEY or state["phase"] != "lobby":
        raise ValueError("grid_lobby_closed")
    if int(user_id) != state["owner_id"]:
        raise ValueError("grid_not_owner")
    if _now(now) >= state["lobby_expires_at"]:
        raise ValueError("grid_expired")
    result = _copy(state)
    _next_phase(result, "preview", now=now)
    result["lobby_expires_at"] = None
    return result


def activate(state, *, now=None):
    if state["phase"] not in {"preview", "turn", "mismatch", "playing"} or state["phase_ready"]:
        return state
    moment = _now(now)
    if moment >= state["panel_wait_until"]:
        raise ValueError("grid_expired")
    result = _copy(state)
    result["phase_ready"] = True
    result["panel_wait_until"] = None
    duration = {"preview": PREVIEW_SECONDS, "turn": TURN_SECONDS,
                "mismatch": MISMATCH_SECONDS, "playing": MINES_SECONDS}[state["phase"]]
    result["deadline"] = moment + duration
    return result


def _next_actor(state):
    roster = [p["id"] for p in state["players"]]
    offset = roster.index(state["current_actor"])
    state["current_actor"] = roster[(offset + 1) % len(roster)]
    state["turn_seq"] += 1


def _close_memory_turn(state, *, now, keep_actor):
    state["first_pick"] = None
    state["second_pick"] = None
    state["turn_count"] += 1
    if state["turn_count"] >= MAX_TURNS:
        state["phase"] = "cancelled"
        state["phase_seq"] += 1
        state["deadline"] = None
        state["panel_wait_until"] = None
        state["cancel_reason"] = "turn_limit"
        return
    if not keep_actor:
        _next_actor(state)
    else:
        state["turn_seq"] += 1
    _next_phase(state, "turn", now=now)


def memory_pick(state, *, user_id, index, now=None):
    moment = _now(now)
    if state["game_key"] != MEMORY_KEY or state["phase"] != "turn" or not state["phase_ready"] or moment >= state["deadline"]:
        raise ValueError("grid_stale_turn")
    if int(user_id) != state["current_actor"]:
        raise ValueError("grid_not_actor")
    if not isinstance(index, int) or isinstance(index, bool) or index < 0 or index >= 16:
        raise ValueError("grid_invalid_cell")
    if index in state["matched"] or index == state["first_pick"]:
        raise ValueError("grid_already_open")
    result = _copy(state)
    if result["first_pick"] is None:
        result["first_pick"] = index
        return result
    result["second_pick"] = index
    first = result["first_pick"]
    if result["cards"][first] == result["cards"][index]:
        result["matched"] = sorted([*result["matched"], first, index])
        _person(result, user_id)["score"] += 1
        result["last_event"] = "match"
        if len(result["matched"]) == 16:
            high = max(p["score"] for p in result["players"])
            result["winner_ids"] = [p["id"] for p in result["players"] if p["score"] == high]
            result["phase"] = "finished"
            result["phase_seq"] += 1
            result["deadline"] = None
            result["panel_wait_until"] = None
            result["score_pending"] = True
            result["outcome"] = "completed"
        else:
            _close_memory_turn(result, now=moment, keep_actor=True)
    else:
        result["last_event"] = "mismatch"
        _next_phase(result, "mismatch", now=moment)
    return result


def _neighbors(index):
    row, col = divmod(index, 6)
    return [r * 6 + c for r in range(max(0, row - 1), min(6, row + 2))
            for c in range(max(0, col - 1), min(6, col + 2)) if r * 6 + c != index]


def _mine_layout(seed, first):
    prohibited = {first, *_neighbors(first)}
    candidates = [i for i in range(36) if i not in prohibited]
    return sorted(sorted(candidates, key=lambda i: hashlib.sha256(
        f"{seed}:{i}".encode("ascii")).digest())[:6])


def mine_count(state, index):
    return sum(i in state["mines"] for i in _neighbors(index))


def _mine_action(state, user_id, index, now):
    moment = _now(now)
    if state["game_key"] != MINES_KEY or state["phase"] != "playing" or not state["phase_ready"] or moment >= state["deadline"]:
        raise ValueError("grid_stale_turn")
    if int(user_id) != state["owner_id"]:
        raise ValueError("grid_not_actor")
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < 36:
        raise ValueError("grid_invalid_cell")
    return moment


def mine_open(state, *, user_id, index, now=None):
    _mine_action(state, user_id, index, now)
    if index in state["flags"] or index in state["opened"]:
        raise ValueError("grid_already_open")
    result = _copy(state)
    if result["mines"] is None:
        result["first_open"] = index
        result["mines"] = _mine_layout(result["seed"], index)
    if index in result["mines"]:
        result["phase"] = "finished"
        result["phase_seq"] += 1
        result["outcome"] = "mine"
        result["deadline"] = None
        result["panel_wait_until"] = None
        result["score_pending"] = False
        result["opened"] = sorted([*result["opened"], index])
        return result
    opened = set(result["opened"])
    queue = [index]
    while queue:
        current = queue.pop(0)
        if current in opened or current in result["flags"] or current in result["mines"]:
            continue
        opened.add(current)
        if mine_count(result, current) == 0:
            queue.extend(_neighbors(current))
    result["opened"] = sorted(opened)
    if len(opened) == 30:
        result["phase"] = "finished"
        result["phase_seq"] += 1
        result["outcome"] = "cleared"
        result["winner_ids"] = [state["owner_id"]]
        result["score_pending"] = True
        result["deadline"] = None
        result["panel_wait_until"] = None
    return result


def mine_flag(state, *, user_id, index, now=None):
    _mine_action(state, user_id, index, now)
    if index in state["opened"]:
        raise ValueError("grid_already_open")
    result = _copy(state)
    flags = set(result["flags"])
    if index in flags:
        flags.remove(index)
    else:
        if len(flags) >= 6:
            raise ValueError("grid_flag_limit")
        flags.add(index)
    result["flags"] = sorted(flags)
    return result


def mine_mode(state, *, user_id, enabled, now=None):
    _mine_action(state, user_id, 0, now)
    if not isinstance(enabled, bool) or state["flag_mode"] == enabled:
        raise ValueError("grid_stale_turn")
    result = _copy(state)
    result["flag_mode"] = enabled
    return result


def cancel(state, *, user_id=None, moderator=False, reason="owner"):
    if state["phase"] in TERMINAL:
        raise ValueError("grid_closed")
    if user_id is not None and int(user_id) != state["owner_id"] and not moderator:
        raise ValueError("grid_not_owner")
    result = _copy(state)
    result.update(phase="cancelled", phase_seq=state["phase_seq"] + 1,
                  deadline=None, panel_wait_until=None, lobby_expires_at=None,
                  score_pending=False, cancel_reason=reason)
    return result


def advance(state, *, now=None):
    moment = _now(now)
    if state["phase"] == "lobby" and moment >= state["lobby_expires_at"]:
        return cancel(state, reason="lobby_timeout")
    if state["phase"] in TERMINAL:
        return state
    if not state["phase_ready"]:
        if state["panel_wait_until"] is not None and moment >= state["panel_wait_until"]:
            return cancel(state, reason="panel_timeout")
        return state
    if moment < state["deadline"]:
        return state
    result = _copy(state)
    phase = result["phase"]
    if phase == "preview":
        _next_phase(result, "turn", now=moment)
    elif phase == "turn":
        result["last_event"] = "timeout"
        _close_memory_turn(result, now=moment, keep_actor=False)
    elif phase == "mismatch":
        _close_memory_turn(result, now=moment, keep_actor=False)
    elif phase == "playing":
        result = cancel(state, reason="game_timeout")
    return result


def public_view(state):
    fields = ("game_key", "chat_id", "thread_id", "round_id", "owner_id",
              "phase", "phase_seq", "turn_seq", "version", "deadline",
              "panel_wait_until", "phase_ready", "panel_message_id", "score_pending",
              "winner_ids", "outcome", "cancel_reason", "current_actor",
              "turn_count", "last_event", "matched", "opened", "flags", "flag_mode")
    view = {key: deepcopy(state.get(key)) for key in fields}
    view["players"] = deepcopy(state["players"])
    if state["game_key"] == MEMORY_KEY:
        visible = [None] * 16
        indexes = (range(16) if state["phase"] == "preview" else
                   set(state["matched"]) | {i for i in (state["first_pick"], state["second_pick"]) if i is not None})
        for i in indexes:
            visible[i] = state["cards"][i]
        view["visible"] = visible
    else:
        view["visible"] = {str(i): mine_count(state, i) for i in state["opened"]
                           if state["mines"] is not None and i not in state["mines"]}
    return view
