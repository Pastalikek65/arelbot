"""Deterministic rules for Sudoku, number league and reflex rounds."""

from __future__ import annotations

from copy import deepcopy
import random
import secrets
import time

GAME_KEYS = ("sudoku", "sayisal_tahmin", "hizli_buton", "sifre_tahmin", "fark_bulmaca")
TERMINAL = {"finished", "cancelled"}
LOBBY_SECONDS = 1200
PANEL_WAIT_SECONDS = 300
TURN_SECONDS = 30
REFLEX_SECONDS = 15
SUDOKU_SECONDS = 1800
ROUNDS = 5
RESULT_SECONDS = 5
SYMBOLS = ("◆", "●", "▲", "■")
SPOT_SYMBOLS = ("◆", "●", "▲", "■")
SPOT_PAIR = {"◆": "◇", "●": "○", "▲": "△", "■": "□"}
ARCADE_ROUNDS = 3

# Curated givens; the solver validates one completion for every transformed board.
BASE = {
    4: ("1004", "0410", "0043", "4001"),
    9: ("530070000", "600195000", "098000060", "800060003", "400803001",
        "700020006", "060000280", "000419005", "000080079"),
}


def _time(now):
    return time.time() if now is None else float(now)


def _copy(state, now=None):
    result = deepcopy(state)
    result["version"] = int(state["version"]) + 1
    if now is not None:
        result["last_activity_at"] = _time(now)
    return result


def _valid_move(board, row, col, digit):
    size = len(board)
    box = int(size ** .5)
    return (all(board[row][c] != digit for c in range(size) if c != col)
            and all(board[r][col] != digit for r in range(size) if r != row)
            and all(board[r][c] != digit for r in range(row // box * box, row // box * box + box)
                    for c in range(col // box * box, col // box * box + box)
                    if (r, c) != (row, col)))


def solve(board, *, limit=2):
    """Return at most `limit` solutions; used to validate authored givens."""
    size = len(board)
    if size not in (4, 9) or any(len(row) != size for row in board):
        raise ValueError("lp_invalid_board")
    result = []
    work = [list(map(int, row)) for row in board]

    def search():
        if len(result) >= limit:
            return
        best = None
        choices = None
        for r in range(size):
            for c in range(size):
                value = work[r][c]
                if value and (value > size or not _valid_move(work, r, c, value)):
                    return
                if not value:
                    candidates = [d for d in range(1, size + 1) if _valid_move(work, r, c, d)]
                    if not candidates:
                        return
                    if choices is None or len(candidates) < len(choices):
                        best, choices = (r, c), candidates
        if best is None:
            result.append([row[:] for row in work])
            return
        r, c = best
        for digit in choices:
            work[r][c] = digit
            search()
            work[r][c] = 0
            if len(result) >= limit:
                return

    search()
    return result


def puzzle(size, seed):
    if size not in BASE:
        raise ValueError("lp_invalid_size")
    # Sudoku symmetries preserve every constraint and the number of solutions.
    digits = list(range(1, size + 1))
    import random
    rng = random.Random(int(seed))
    rng.shuffle(digits)
    source = [[int(ch) for ch in row] for row in BASE[size]]
    box = int(size ** .5)
    def axis():
        groups = list(range(box))
        rng.shuffle(groups)
        order = []
        for group in groups:
            slots = list(range(box))
            rng.shuffle(slots)
            order.extend(group * box + slot for slot in slots)
        return order
    rows, cols = axis(), axis()
    board = [[digits[source[rows[r]][cols[c]] - 1] if source[rows[r]][cols[c]] else 0
              for c in range(size)] for r in range(size)]
    if rng.randrange(2):
        board = [list(row) for row in zip(*board)]
    solutions = solve(board)
    if len(solutions) != 1:
        raise RuntimeError("lp_puzzle_not_unique")
    return board, solutions[0]


def new(*, game_key, chat_id, thread_id, owner_id, owner_name, round_id,
        now=None, size=4, seed=None):
    if game_key not in GAME_KEYS:
        raise ValueError("lp_invalid_game")
    moment = _time(now)
    state = {"game_key": game_key, "chat_id": int(chat_id), "thread_id": thread_id,
             "owner_id": int(owner_id), "players": [{"id": int(owner_id), "name": str(owner_name)[:40]}],
             "phase": "active" if game_key == "sudoku" else "lobby",
             "round_id": str(round_id), "version": 0, "created_at": moment,
             "last_activity_at": moment, "deadline": None, "panel_wait_until": moment + PANEL_WAIT_SECONDS,
             "lobby_expires_at": moment + LOBBY_SECONDS if game_key != "sudoku" else None,
             "panel_dirty": True, "delivery_retry_at": 0, "panel_message_id": None,
             "score_pending": False, "winner_id": None, "scores": {str(owner_id): 0}}
    if game_key == "sudoku":
        board, solution = puzzle(size, secrets.randbits(32) if seed is None else seed)
        state.update(size=size, givens=board, board=deepcopy(board), solution=solution,
                     selected=None, focus_row=0, focus_band=0, undo=[], hints_left=3, hint_count=0, mistakes=0,
                     moves=0, panel_ready=False)
    elif game_key == "sayisal_tahmin":
        state.update(round_no=0, turn_index=0, guesses_this_round=[], guess_budget=1,
                     guesses_counts={},
                     low=1, high=1000, secret=None, last_guess=None, last_distance=None,
                     feedback=None, panel_ready=False)
    elif game_key == "hizli_buton":
        state.update(round_no=0, attempts={}, target=None, decoys=[],
                     go_at=None, panel_ready=False, result=None)
    elif game_key == "sifre_tahmin":
        state.update(round_no=0, turn_index=0, code_length=4, alphabet="123456",
                     secret=None, draft="", history=[], attempts_left=8,
                     hint_revealed=False, revealed_digit=None, round_winner=None)
    else:
        state.update(round_no=0, left_grid=[], right_grid=[], diff_positions=[],
                     found=[], tried={}, misses={}, hint_used=False, hint_row=None)
    return state


def join(state, *, user_id, user_name, now=None):
    moment = _time(now)
    if state["phase"] != "lobby" or moment >= state["lobby_expires_at"]:
        raise ValueError("lp_lobby_closed")
    if any(p["id"] == int(user_id) for p in state["players"]):
        raise ValueError("lp_already_joined")
    limit = 12 if state["game_key"] in {"sifre_tahmin", "fark_bulmaca"} else 30
    if len(state["players"]) >= limit:
        raise ValueError("lp_full")
    result = _copy(state, moment)
    result["players"].append({"id": int(user_id), "name": str(user_name)[:40]})
    result["scores"][str(user_id)] = 0
    return result


def _next_number_round(result, now, *, secret=None):
    if result["round_no"] >= ROUNDS:
        result.update(phase="finished", deadline=None, panel_wait_until=None,
                      score_pending=True)
        best = max(result["scores"].values())
        winners = [int(uid) for uid, score in result["scores"].items() if score == best]
        result["winner_id"] = winners[0] if best > 0 and len(winners) == 1 else None
        return
    result["round_no"] += 1
    result.update(phase="turn_pending", secret=int(secret) if secret is not None else secrets.randbelow(1000) + 1,
                  low=1, high=1000, last_guess=None, last_distance=None, feedback=None,
                  guesses_this_round=[], guesses_counts={str(p["id"]): 0 for p in result["players"]},
                  guess_budget=10, turn_index=(result["round_no"] - 1) % len(result["players"]),
                  deadline=None, panel_wait_until=_time(now) + PANEL_WAIT_SECONDS, panel_ready=False)


def _next_reflex_round(result, now, *, delay=None, symbol=None):
    if result["round_no"] >= ROUNDS:
        result.update(phase="finished", deadline=None, panel_wait_until=None,
                      score_pending=True)
        best = max(result["scores"].values())
        winners = [int(uid) for uid, score in result["scores"].items() if score == best]
        result["winner_id"] = winners[0] if best > 0 and len(winners) == 1 else None
        return
    result["round_no"] += 1
    result.update(phase="waiting", target=symbol or secrets.choice(SYMBOLS),
                  decoys=list(SYMBOLS), attempts={}, result=None,
                  go_at=None, delay_seconds=delay if delay is not None else secrets.randbelow(5) + 3,
                  deadline=None, panel_wait_until=_time(now) + PANEL_WAIT_SECONDS, panel_ready=False)


def start(state, *, user_id, now=None, secret=None, delay=None, symbol=None, seed=None):
    moment = _time(now)
    if state["phase"] != "lobby" or moment >= state["lobby_expires_at"]:
        raise ValueError("lp_lobby_closed")
    if int(user_id) != state["owner_id"]:
        raise ValueError("lp_not_owner")
    minimum = 2 if state["game_key"] == "hizli_buton" else 1
    if len(state["players"]) < minimum:
        raise ValueError("lp_player_count")
    result = _copy(state, moment)
    result["lobby_expires_at"] = None
    if state["game_key"] == "sayisal_tahmin":
        _next_number_round(result, moment, secret=secret)
    elif state["game_key"] == "hizli_buton":
        _next_reflex_round(result, moment, delay=delay, symbol=symbol)
    elif state["game_key"] == "sifre_tahmin":
        _next_code_round(result, moment, secret=secret)
    else:
        _next_spot_round(result, moment, seed=seed)
    return result


def _arcade_finish(state):
    best = max(state["scores"].values(), default=0)
    state.update(phase="finished", deadline=None, panel_wait_until=None,
                 winner_id=next((int(uid) for uid, score in state["scores"].items() if score == best), None) if best > 0 else None,
                 score_pending=best > 0)


def _next_code_round(state, now, *, secret=None):
    if state["round_no"] >= ARCADE_ROUNDS:
        _arcade_finish(state)
        return
    round_no = state["round_no"] + 1
    length = 5 if round_no == 3 else 4
    alphabet = "123456" if round_no == 1 else "12345678"
    if secret is None:
        secret = "".join(secrets.SystemRandom().sample(alphabet, length))
    if len(secret) != length or len(set(secret)) != length or any(digit not in alphabet for digit in secret):
        raise ValueError("lp_invalid_secret")
    state.update(round_no=round_no, phase="turn_pending", secret=secret,
                 code_length=length, alphabet=alphabet, draft="", history=[],
                 attempts_left=8, turn_index=(round_no - 1) % len(state["players"]),
                 round_winner=None, hint_revealed=False, revealed_digit=None, deadline=None,
                 panel_wait_until=now + PANEL_WAIT_SECONDS)


def _code_turn(state, user_id, now):
    if state["game_key"] != "sifre_tahmin" or state["phase"] != "turn" or state.get("deadline") is None:
        raise ValueError("lp_wrong_phase")
    if now >= state["deadline"]:
        raise ValueError("lp_expired")
    if state["players"][state["turn_index"]]["id"] != int(user_id):
        raise ValueError("lp_not_turn")


def code_digit(state, *, user_id, digit, now=None):
    moment = _time(now)
    _code_turn(state, user_id, moment)
    digit = str(digit)
    if digit not in state["alphabet"] or len(digit) != 1 or len(state["draft"]) >= state["code_length"]:
        raise ValueError("lp_invalid_digit")
    if digit in state["draft"]:
        raise ValueError("lp_duplicate_digit")
    result = _copy(state, moment)
    result["draft"] += digit
    return result


def code_clear(state, *, user_id, now=None):
    moment = _time(now)
    _code_turn(state, user_id, moment)
    if not state["draft"]:
        raise ValueError("lp_empty_draft")
    result = _copy(state, moment)
    result["draft"] = result["draft"][:-1]
    return result


def code_submit(state, *, user_id, now=None):
    moment = _time(now)
    _code_turn(state, user_id, moment)
    if len(state["draft"]) != state["code_length"]:
        raise ValueError("lp_incomplete_code")
    result = _copy(state, moment)
    exact = sum(a == b for a, b in zip(result["draft"], result["secret"]))
    misplaced = sum(digit in result["secret"] for digit in result["draft"]) - exact
    result["history"].append({"user_id": int(user_id), "guess": result["draft"],
                              "exact": exact, "misplaced": misplaced})
    result["attempts_left"] -= 1
    result["draft"] = ""
    if exact == result["code_length"]:
        result["scores"][str(user_id)] += max(2, 10 - len(result["history"]))
        result["round_winner"] = int(user_id)
    if exact == result["code_length"] or result["attempts_left"] == 0:
        result.update(phase="round_result", deadline=None,
                      panel_wait_until=moment + PANEL_WAIT_SECONDS)
    else:
        result.update(phase="turn_pending", deadline=None,
                      panel_wait_until=moment + PANEL_WAIT_SECONDS,
                      turn_index=(result["turn_index"] + 1) % len(result["players"]))
    return result


def code_hint(state, *, user_id, now=None):
    moment = _time(now)
    _code_turn(state, user_id, moment)
    if state["hint_revealed"] or len(state["history"]) < 3:
        raise ValueError("lp_hint_unavailable")
    result = _copy(state, moment)
    result["hint_revealed"] = True
    result["revealed_digit"] = result["secret"][0]
    result["scores"][str(user_id)] -= 1
    return result


def _next_spot_round(state, now, *, seed=None):
    if state["round_no"] >= ARCADE_ROUNDS:
        _arcade_finish(state)
        return
    round_no = state["round_no"] + 1
    rng = random.Random(seed if seed is not None else secrets.randbits(64))
    left = [rng.choice(SPOT_SYMBOLS) for _ in range(25)]
    right = left[:]
    positions = sorted(rng.sample(range(25), round_no + 1))
    for index in positions:
        right[index] = SPOT_PAIR[left[index]]
    state.update(round_no=round_no, phase="spot_pending", left_grid=left,
                 right_grid=right, diff_positions=positions, found=[], tried={},
                 misses={}, hint_used=False, hint_row=None,
                 deadline=None, panel_wait_until=now + PANEL_WAIT_SECONDS)


def spot_pick(state, *, user_id, index, now=None):
    moment = _time(now)
    if state["game_key"] != "fark_bulmaca" or state["phase"] != "spot" or state.get("deadline") is None:
        raise ValueError("lp_wrong_phase")
    if moment >= state["deadline"]:
        raise ValueError("lp_expired")
    if not any(p["id"] == int(user_id) for p in state["players"]):
        raise ValueError("lp_not_player")
    index = int(index)
    if not 0 <= index < 25:
        raise ValueError("lp_invalid_cell")
    if index in state["found"]:
        raise ValueError("lp_already_found")
    if index in state["tried"].get(str(user_id), []):
        raise ValueError("lp_already_tried")
    if state["misses"].get(str(user_id), 0) >= 3:
        raise ValueError("lp_no_attempts")
    result = _copy(state, moment)
    result["tried"].setdefault(str(user_id), []).append(index)
    if index in result["diff_positions"]:
        result["found"].append(index)
        result["scores"][str(user_id)] += 2
        if len(result["found"]) == len(result["diff_positions"]):
            result.update(phase="round_result", deadline=None,
                          panel_wait_until=moment + PANEL_WAIT_SECONDS)
    else:
        result["misses"][str(user_id)] = result["misses"].get(str(user_id), 0) + 1
        result["scores"][str(user_id)] -= 1
    return result


def spot_hint(state, *, user_id, now=None):
    moment = _time(now)
    if state["game_key"] != "fark_bulmaca" or state["phase"] != "spot" or state["hint_used"]:
        raise ValueError("lp_hint_unavailable")
    if state.get("deadline") is None or moment >= state["deadline"]:
        raise ValueError("lp_expired")
    if not any(p["id"] == int(user_id) for p in state["players"]):
        raise ValueError("lp_not_player")
    remaining = [index for index in state["diff_positions"] if index not in state["found"]]
    if not remaining:
        raise ValueError("lp_hint_unavailable")
    result = _copy(state, moment)
    result["hint_used"] = True
    result["hint_row"] = remaining[0] // 5 + 1
    result["scores"][str(user_id)] -= 1
    return result


def select(state, *, user_id, row, col, now=None):
    if state["game_key"] != "sudoku" or state["phase"] != "active":
        raise ValueError("lp_wrong_phase")
    if int(user_id) != state["owner_id"]:
        raise ValueError("lp_not_owner")
    if not (0 <= row < state["size"] and 0 <= col < state["size"]):
        raise ValueError("lp_invalid_cell")
    result = _copy(state, now)
    result["selected"] = [row, col]
    result["focus_row"] = row
    result["focus_band"] = row // int(state["size"] ** .5)
    return result


def focus_row(state, *, user_id, row, now=None):
    if state["game_key"] != "sudoku" or state["phase"] != "active":
        raise ValueError("lp_wrong_phase")
    if int(user_id) != state["owner_id"]:
        raise ValueError("lp_not_owner")
    if not 0 <= row < state["size"]:
        raise ValueError("lp_invalid_cell")
    result = _copy(state, now)
    result["focus_row"] = row
    result["focus_band"] = row // int(state["size"] ** .5)
    result["selected"] = None
    return result


def focus_band(state, *, user_id, band, now=None):
    if state["game_key"] != "sudoku" or state["phase"] != "active":
        raise ValueError("lp_wrong_phase")
    if int(user_id) != state["owner_id"]:
        raise ValueError("lp_not_owner")
    box = int(state["size"] ** .5)
    if not 0 <= band < box:
        raise ValueError("lp_invalid_cell")
    result = _copy(state, now)
    result["focus_band"] = band
    result["focus_row"] = band * box
    result["selected"] = None
    return result


def digit(state, *, user_id, value, expected_cell=None, now=None):
    if state["game_key"] != "sudoku" or state["phase"] != "active" or not state.get("panel_ready"):
        raise ValueError("lp_wrong_phase")
    if int(user_id) != state["owner_id"]:
        raise ValueError("lp_not_owner")
    selected = state.get("selected")
    if expected_cell is not None and selected != list(expected_cell):
        raise ValueError("lp_stale_cell")
    if selected is None or not 0 <= int(value) <= state["size"]:
        raise ValueError("lp_invalid_digit")
    row, col = selected
    if state["givens"][row][col]:
        raise ValueError("lp_fixed_cell")
    if int(value) and not _valid_move(state["board"], row, col, int(value)):
        raise ValueError("lp_conflict")
    if state["board"][row][col] == int(value):
        raise ValueError("lp_same_digit")
    result = _copy(state, now)
    result["undo"].append([row, col, result["board"][row][col]])
    result["undo"] = result["undo"][-81:]
    result["board"][row][col] = int(value)
    result["moves"] += 1
    if result["board"] == result["solution"]:
        result.update(phase="finished", score_pending=True, winner_id=int(user_id),
                      deadline=None, panel_wait_until=None)
    return result


def undo(state, *, user_id, now=None):
    if state["game_key"] != "sudoku" or state["phase"] != "active" or not state["undo"]:
        raise ValueError("lp_no_undo")
    if int(user_id) != state["owner_id"]:
        raise ValueError("lp_not_owner")
    result = _copy(state, now)
    row, col, previous = result["undo"].pop()
    result["board"][row][col] = previous
    return result


def hint(state, *, user_id, now=None):
    if state["game_key"] != "sudoku" or state["phase"] != "active" or state["hints_left"] <= 0:
        raise ValueError("lp_no_hints")
    if int(user_id) != state["owner_id"]:
        raise ValueError("lp_not_owner")
    empty = [(r, c) for r in range(state["size"]) for c in range(state["size"])
             if state["board"][r][c] != state["solution"][r][c]]
    if not empty:
        raise ValueError("lp_wrong_phase")
    row, col = empty[0]
    result = _copy(state, now)
    result["board"][row][col] = result["solution"][row][col]
    result["hints_left"] -= 1
    result["hint_count"] += 1
    result["undo"] = []
    if result["board"] == result["solution"]:
        result.update(phase="finished", score_pending=True, winner_id=int(user_id),
                      deadline=None, panel_wait_until=None)
    return result


def number_guess(state, *, user_id, value, now=None):
    moment = _time(now)
    if (state["game_key"] != "sayisal_tahmin" or state["phase"] != "turn" or
            state.get("deadline") is None or moment >= state["deadline"]):
        raise ValueError("lp_wrong_phase")
    player = state["players"][state["turn_index"]]
    if player["id"] != int(user_id):
        raise ValueError("lp_not_turn")
    if not isinstance(value, int) or isinstance(value, bool) or not state["low"] <= value <= state["high"]:
        raise ValueError("lp_guess_out_of_range")
    result = _copy(state, moment)
    result["guesses_this_round"].append(int(user_id))
    counts = result.setdefault("guesses_counts", {str(p["id"]): 0 for p in result["players"]})
    counts[str(user_id)] += 1
    distance = abs(value - result["secret"])
    relative = "yaklaştın" if result["last_distance"] is not None and distance < result["last_distance"] else \
               "uzaklaştın" if result["last_distance"] is not None and distance > result["last_distance"] else ""
    result["last_guess"], result["last_distance"] = value, distance
    if value == result["secret"]:
        result["scores"][str(user_id)] += 1
        result["feedback"] = f"{value} doğru. {player['name']} bu turu kazandı."
        result["phase"] = "round_result"
        result["deadline"] = None
        result["panel_wait_until"] = moment + PANEL_WAIT_SECONDS
    else:
        result["feedback"] = ("Daha büyük" if value < result["secret"] else "Daha küçük") + \
                             (f" · {relative}" if relative else "")
        if value < result["secret"]:
            result["low"] = value + 1
        else:
            result["high"] = value - 1
        if all(counts[str(p["id"])] >= result["guess_budget"] for p in result["players"]):
            result["phase"] = "round_result"
            result["deadline"] = None
            result["panel_wait_until"] = moment + PANEL_WAIT_SECONDS
            result["feedback"] += f" · Tur bitti; sayı {result['secret']} idi."
        else:
            result["turn_index"] = _next_number_player(result)
            result["phase"] = "turn_pending"
            result["deadline"] = None
            result["panel_wait_until"] = moment + PANEL_WAIT_SECONDS
    return result


def _next_number_player(state):
    for offset in range(1, len(state["players"]) + 1):
        index = (state["turn_index"] + offset) % len(state["players"])
        person = state["players"][index]
        if state["guesses_counts"][str(person["id"])] < state["guess_budget"]:
            return index
    raise ValueError("lp_budget_exhausted")


def reflex_press(state, *, user_id, symbol, now=None):
    moment = _time(now)
    if state["game_key"] != "hizli_buton" or state["phase"] not in {"waiting", "go"}:
        raise ValueError("lp_wrong_phase")
    if not any(p["id"] == int(user_id) for p in state["players"]):
        raise ValueError("lp_not_player")
    if str(user_id) in state["attempts"]:
        raise ValueError("lp_already_pressed")
    if state["phase"] == "go" and (state.get("deadline") is None or moment >= state["deadline"]):
        raise ValueError("lp_expired")
    if state["phase"] == "go" and symbol not in SYMBOLS:
        raise ValueError("lp_invalid_symbol")
    result = _copy(state, moment)
    if state["phase"] == "waiting":
        result["attempts"][str(user_id)] = "false_start"
        result["scores"][str(user_id)] -= 1
        result["result"] = "Erken basış: −1 puan. Bu turda yeniden basılamaz."
    elif symbol == state["target"]:
        result["attempts"][str(user_id)] = "correct"
        result["scores"][str(user_id)] += 2
        result["winner_id"] = int(user_id)
        result["result"] = f"{next(p['name'] for p in result['players'] if p['id'] == int(user_id))} doğru simgeye ilk ulaştı."
        result["phase"] = "round_result"
        result["deadline"] = None
        result["panel_wait_until"] = moment + PANEL_WAIT_SECONDS
    else:
        result["attempts"][str(user_id)] = "wrong"
        result["scores"][str(user_id)] -= 1
        result["result"] = "Yanlış simge: −1 puan."
    return result


def advance(state, *, now=None):
    moment = _time(now)
    if state["phase"] in TERMINAL:
        return state
    if state["phase"] == "lobby" and moment >= state["lobby_expires_at"]:
        result = _copy(state, moment)
        result.update(phase="cancelled", panel_wait_until=None, deadline=None)
        return result
    if state["game_key"] == "sudoku" and state["phase"] == "active" and \
            state.get("deadline") is not None and moment >= state["deadline"]:
        result = _copy(state, moment)
        result.update(phase="cancelled", deadline=None, panel_wait_until=None,
                      score_pending=False)
        return result
    if state.get("panel_wait_until") is not None and moment >= state["panel_wait_until"]:
        if state["phase"] in {"active", "turn_pending", "turn", "spot_pending", "spot", "go_pending", "go", "round_result", "waiting"}:
            result = _copy(state, moment)
            result.update(phase="cancelled", deadline=None, panel_wait_until=None, score_pending=False)
            return result
    if state["phase"] == "round_result" and state.get("deadline") is not None and moment >= state["deadline"]:
        result = _copy(state, moment)
        if state["game_key"] == "sayisal_tahmin":
            _next_number_round(result, moment)
        elif state["game_key"] == "hizli_buton":
            _next_reflex_round(result, moment)
        elif state["game_key"] == "sifre_tahmin":
            _next_code_round(result, moment)
        else:
            _next_spot_round(result, moment)
        return result
    if state["game_key"] == "sifre_tahmin" and state["phase"] == "turn" and state.get("deadline") is not None and moment >= state["deadline"]:
        result = _copy(state, moment)
        result["history"].append({"user_id": state["players"][state["turn_index"]]["id"],
                                  "guess": "—", "exact": 0, "misplaced": 0})
        result["draft"] = ""
        result["attempts_left"] -= 1
        result.update(phase="round_result" if result["attempts_left"] == 0 else "turn_pending",
                      deadline=None, panel_wait_until=moment + PANEL_WAIT_SECONDS,
                      turn_index=(result["turn_index"] + 1) % len(result["players"]))
        return result
    if state["game_key"] == "fark_bulmaca" and state["phase"] == "spot" and state.get("deadline") is not None and moment >= state["deadline"]:
        result = _copy(state, moment)
        result.update(phase="round_result", deadline=None,
                      panel_wait_until=moment + PANEL_WAIT_SECONDS)
        return result
    if (state["game_key"] == "sayisal_tahmin" and state["phase"] == "turn" and
            state.get("deadline") is not None and moment >= state["deadline"]):
        result = _copy(state, moment)
        result["feedback"] = "Süre doldu; tahmin hakkı geçti."
        result["guesses_this_round"].append(state["players"][state["turn_index"]]["id"])
        user_id = state["players"][state["turn_index"]]["id"]
        result.setdefault("guesses_counts", {str(p["id"]): 0 for p in result["players"]})[str(user_id)] += 1
        if all(result["guesses_counts"][str(p["id"])] >= result["guess_budget"]
               for p in result["players"]):
            result.update(phase="round_result", deadline=None,
                          panel_wait_until=moment + PANEL_WAIT_SECONDS)
            result["feedback"] += f" Tur bitti; sayı {result['secret']} idi."
        else:
            result.update(phase="turn_pending", deadline=None,
                          panel_wait_until=moment + PANEL_WAIT_SECONDS,
                          turn_index=_next_number_player(result))
        return result
    if state["game_key"] == "hizli_buton":
        if state["phase"] == "waiting" and state.get("go_at") is not None and moment >= state["go_at"]:
            result = _copy(state, moment)
            result.update(phase="go_pending", deadline=None,
                          panel_wait_until=moment + PANEL_WAIT_SECONDS)
            return result
        if state["phase"] == "go" and state.get("deadline") is not None and moment >= state["deadline"]:
            result = _copy(state, moment)
            result.update(phase="round_result", deadline=None,
                          panel_wait_until=moment + PANEL_WAIT_SECONDS,
                          result="Süre doldu; bu turda kazanan yok.")
            return result
    return state


def cancel(state, *, user_id, moderator=False, now=None):
    if state["phase"] in TERMINAL:
        raise ValueError("lp_wrong_phase")
    if int(user_id) != state["owner_id"] and not moderator:
        raise ValueError("lp_not_owner")
    result = _copy(state, now)
    result.update(phase="cancelled", deadline=None, panel_wait_until=None, score_pending=False)
    return result


def public_state(state):
    view = deepcopy(state)
    view.pop("solution", None)
    if view["phase"] not in {"round_result", "finished"}:
        view.pop("secret", None)
    view.pop("diff_positions", None)
    if view["game_key"] == "hizli_buton" and view["phase"] in {"lobby", "waiting", "go_pending"}:
        view.pop("target", None)
    view.pop("processed_actions", None)
    return view
