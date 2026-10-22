"""Pure rules for a solo, persistent 4×4 2048 match."""

from __future__ import annotations

import secrets
import time
from copy import deepcopy

GAME_KEY = "2048"
GAME_KEYS = (GAME_KEY,)
TERMINAL = {"finished", "cancelled"}
PANEL_WAIT_SECONDS = 300
TURN_SECONDS = 600
MATCH_SECONDS = 3600
MAX_MOVES = 2000
DIRECTIONS = ("up", "down", "left", "right")


def _now(now):
    return time.time() if now is None else float(now)


def _copy(state, now):
    result = deepcopy(state)
    result["version"] += 1
    result["last_activity_at"] = _now(now)
    return result


def _validate_board(board):
    if (not isinstance(board, list) or len(board) != 4 or
            any(not isinstance(row, list) or len(row) != 4 for row in board)):
        raise ValueError("t2_invalid_board")
    if any(type(value) is not int or value == 1 or value < 0 or
           (value and value & (value - 1)) for row in board for value in row):
        raise ValueError("t2_invalid_board")


def _merge_line(line):
    compact = [value for value in line if value]
    merged, gained = [], 0
    index = 0
    while index < len(compact):
        if index + 1 < len(compact) and compact[index] == compact[index + 1]:
            value = compact[index] * 2
            merged.append(value)
            gained += value
            index += 2
        else:
            merged.append(compact[index])
            index += 1
    return merged + [0] * (4 - len(merged)), gained


def slide(board, direction):
    """Return (new board, merge score, changed); each tile merges at most once."""
    _validate_board(board)
    if direction not in DIRECTIONS:
        raise ValueError("t2_invalid_direction")
    result = [row[:] for row in board]
    gained = 0
    for index in range(4):
        vertical = direction in {"up", "down"}
        reverse = direction in {"right", "down"}
        line = ([board[row][index] for row in range(4)] if vertical else board[index][:])
        merged, points = _merge_line(line[::-1] if reverse else line)
        if reverse:
            merged.reverse()
        gained += points
        if vertical:
            for row, value in enumerate(merged):
                result[row][index] = value
        else:
            result[index] = merged
    return result, gained, result != board


def legal_moves(board):
    _validate_board(board)
    return any(slide(board, direction)[2] for direction in DIRECTIONS)


def _spawn(board, rng):
    empties = [(row, col) for row in range(4) for col in range(4) if board[row][col] == 0]
    if not empties:
        raise ValueError("t2_board_full")
    row, col = empties[rng.randrange(len(empties))]
    board[row][col] = 2 if rng.random() < 0.9 else 4
    return row, col, board[row][col]


def new(*, chat_id, thread_id, owner_id, owner_name, round_id, now=None, rng=None):
    moment = _now(now)
    rng = rng if rng is not None else secrets.SystemRandom()
    board = [[0] * 4 for _ in range(4)]
    _spawn(board, rng)
    _spawn(board, rng)
    return {"game_key": GAME_KEY, "chat_id": int(chat_id), "thread_id": thread_id,
            "owner_id": int(owner_id), "round_id": str(round_id), "version": 0,
            "phase_seq": 0, "phase": "playing",
            "players": [{"id": int(owner_id), "name": str(owner_name)[:32], "active": True}],
            "created_at": moment, "last_activity_at": moment,
            "session_expires_at": moment + MATCH_SECONDS, "lobby_expires_at": None,
            "deadline": None, "panel_wait_until": moment + PANEL_WAIT_SECONDS,
            "panel_dirty": True, "delivery_retry_at": 0, "panel_message_id": None,
            "score_pending": False, "winner_ids": [], "processed_actions": [],
            "board": board, "score": 0, "moves": 0, "best_tile": max(map(max, board)),
            "won": False, "reason": None}


def _finish(state, reason):
    state.update(phase="finished", phase_seq=state["phase_seq"] + 1,
                 deadline=None, panel_wait_until=None, lobby_expires_at=None,
                 score_pending=state["score"] > 0,
                 winner_ids=[state["owner_id"]] if state["won"] else [], reason=reason)


def move(state, *, user_id, direction, now=None, rng=None):
    moment = _now(now)
    if int(user_id) != state["owner_id"]:
        raise ValueError("t2_not_owner")
    if (state["phase"] != "playing" or state["deadline"] is None or
            moment >= state["deadline"] or moment >= state["session_expires_at"]):
        raise ValueError("t2_wrong_phase")
    board, gained, changed = slide(state["board"], direction)
    if not changed:
        raise ValueError("t2_no_move")
    result = _copy(state, moment)
    _spawn(board, rng if rng is not None else secrets.SystemRandom())
    result.update(board=board, score=state["score"] + gained,
                  moves=state["moves"] + 1, best_tile=max(map(max, board)),
                  won=state["won"] or any(value >= 2048 for row in board for value in row),
                  phase_seq=state["phase_seq"] + 1, deadline=None,
                  panel_wait_until=moment + PANEL_WAIT_SECONDS)
    if not legal_moves(board) or result["moves"] >= MAX_MOVES:
        _finish(result, "board_full" if not legal_moves(board) else "move_limit")
    return result


def cashout(state, *, user_id, now=None):
    moment = _now(now)
    if int(user_id) != state["owner_id"]:
        raise ValueError("t2_not_owner")
    if (state["phase"] != "playing" or not state["won"] or
            state["deadline"] is None or moment >= state["deadline"]):
        raise ValueError("t2_wrong_phase")
    result = _copy(state, moment)
    _finish(result, "claimed_2048")
    return result


def cancel(state, *, user_id, moderator=False, now=None, reason="manual"):
    if state["phase"] in TERMINAL:
        raise ValueError("t2_wrong_phase")
    if int(user_id) != state["owner_id"] and not moderator:
        raise ValueError("t2_not_owner")
    result = _copy(state, now)
    result.update(phase="cancelled", phase_seq=state["phase_seq"] + 1,
                  deadline=None, panel_wait_until=None, lobby_expires_at=None,
                  score_pending=False, winner_ids=[], reason=reason)
    return result


def advance(state, *, now=None):
    moment = _now(now)
    if state["phase"] in TERMINAL:
        return state
    if state["deadline"] is None:
        if moment < state["panel_wait_until"]:
            return state
        return cancel(state, user_id=state["owner_id"], now=moment,
                      reason="panel_timeout")
    if moment < state["deadline"]:
        return state
    return cancel(state, user_id=state["owner_id"], now=moment,
                  reason="idle_timeout")


def public_state(state):
    view = deepcopy(state)
    view.pop("processed_actions", None)
    return view
