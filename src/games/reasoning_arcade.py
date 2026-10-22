"""Deterministic decisions for four original Turkish reasoning games."""

from __future__ import annotations

import random
import secrets
import time
from copy import deepcopy

RANK = "kor_siralama"
CALENDAR = "kronoloji"
TRUE_FALSE = "dogru_yanlis"
DETECTIVE = "dedektif"
GAME_KEYS = (RANK, CALENDAR, TRUE_FALSE, DETECTIVE)
TERMINAL = {"finished", "cancelled"}
LOBBY_SECONDS = 1200
PANEL_WAIT_SECONDS = 300
RANK_SECONDS = 90
CALENDAR_SECONDS = 120
TRUE_FALSE_SECONDS = 45
REVEAL_SECONDS = 6
DETECTIVE_SECONDS = 180

SUSPECTS = ("Mira", "Tuna", "Lale")
PLACES = ("sera", "iskele", "kütüphane")
OBJECTS = ("anahtar", "fener", "defter")
EVENT_SETS = (
    ("Fener yakıldı", "Köprü boyandı", "Kutu mühürlendi", "Bahçe açıldı", "Harita çizildi"),
    ("Saat kuruldu", "Kapı ölçüldü", "Mektup yazıldı", "Kule temizlendi", "Rota belirlendi"),
    ("Çadır kuruldu", "Bayrak dikildi", "Sandık taşındı", "Yol işaretlendi", "Şenlik başladı"),
    ("Defter bulundu", "Anahtar dövüldü", "Çeşme onarıldı", "Pazar açıldı", "Kilise boyandı"),
    ("Tohum ekildi", "Çit yenilendi", "Afiş asıldı", "Masa kuruldu", "Seri başladı"),
)


def _now(now):
    return time.time() if now is None else float(now)


def _copy(state, now=None):
    result = deepcopy(state)
    result["version"] += 1
    if now is not None:
        result["last_activity_at"] = _now(now)
    return result


def _phase(state, phase, now):
    state.update(phase=phase, phase_seq=state["phase_seq"] + 1,
                 deadline=None, panel_wait_until=_now(now) + PANEL_WAIT_SECONDS)


def _finish(state, *, winners=()):
    winner_ids = list(winners)
    state.update(phase="finished", phase_seq=state["phase_seq"] + 1,
                 deadline=None, panel_wait_until=None, lobby_expires_at=None,
                 winner_ids=winner_ids, score_pending=bool(winner_ids))


def rank_score(values, assigned):
    if len(values) != 5 or len(set(values)) != 5 or sorted(assigned) != [1, 2, 3, 4, 5]:
        raise ValueError("ra_invalid_rank")
    ordered = sorted(range(5), key=lambda i: values[i])
    actual = [0] * 5
    for rank, index in enumerate(ordered, 1):
        actual[index] = rank
    displacement = sum(abs(assigned[index] - actual[index]) for index in range(5))
    return round(100 * (12 - displacement) / 12)


def calendar_puzzle(index, *, seed=None):
    if not 0 <= index < 5:
        raise ValueError("ra_invalid_round")
    rng = random.Random(1429 + index if seed is None else f"{int(seed)}:calendar:{index}")
    names = (list(EVENT_SETS[index]) if seed is None else
             rng.sample([event for group in EVENT_SETS for event in group], 5))
    solution = list(range(5))
    rng.shuffle(solution)
    days = (0, 2, 5, 9, 14)
    clues = [{"before": solution[i], "after": solution[i + 1],
              "days": days[i + 1] - days[i]} for i in range(4)]
    rng.shuffle(clues)
    return {"events": names, "clues": clues, "solution": solution}


def calendar_clues_hold(puzzle, order):
    if sorted(order) != list(range(5)):
        return False
    positions = {event: place for place, event in enumerate(order)}
    return all(positions[clue["before"]] < positions[clue["after"]]
               for clue in puzzle["clues"])


def _prime(number):
    return number >= 2 and all(number % divisor for divisor in range(2, int(number ** .5) + 1))


def claim(index, *, seed=None):
    if not 0 <= index < 10:
        raise ValueError("ra_invalid_round")
    if seed is not None:
        rng = random.Random(f"{int(seed)}:claim:{index}")
        truthful = bool(rng.getrandbits(1))
        mode = index % 4
        if mode == 0:
            a, b = rng.randint(3, 49), rng.randint(3, 49)
            stated = a + b if truthful else a + b + rng.choice((-3, -2, -1, 1, 2, 3))
            return {"kind": "sum", "a": a, "b": b, "stated": stated,
                    "text": f"{a} + {b} = {stated}", "answer": a + b == stated,
                    "explanation": f"{a} + {b} = {a + b}."}
        if mode == 1:
            primes = (11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47)
            composites = (12, 14, 15, 21, 25, 27, 33, 35, 39, 45, 49)
            number = rng.choice(primes if truthful else composites)
            divisor = next((d for d in range(2, number) if number % d == 0), None)
            return {"kind": "prime", "number": number,
                    "text": f"{number} asal sayıdır.", "answer": _prime(number),
                    "explanation": (f"{number} asaldır." if divisor is None else
                                    f"{number} asal değildir; {divisor} × {number // divisor} = {number}.")}
        if mode == 2:
            number = rng.randrange(11, 90, 2) if truthful else rng.randrange(10, 90, 2)
            return {"kind": "odd", "number": number,
                    "text": f"{number} tek sayıdır.", "answer": number % 2 == 1,
                    "explanation": f"{number} {'tektir' if number % 2 else 'çifttir'}."}
        word = rng.choice(("papatya", "merhaba", "istanbul", "çiçek", "gökyüzü", "ışık"))
        stated = len(word) if truthful else len(word) + rng.choice((-2, -1, 1, 2))
        return {"kind": "letters", "word": word, "stated": stated,
                "text": f"'{word}' sözcüğü {stated} Türkçe harften oluşur.",
                "answer": len(word) == stated,
                "explanation": f"'{word}' sözcüğünde {len(word)} harf vardır."}
    mode = index % 4
    if mode == 0:
        a, b, stated = 7 + index, 9, 16 + index + (1 if index == 4 else 0)
        return {"kind": "sum", "a": a, "b": b, "stated": stated,
                "text": f"{a} + {b} = {stated}",
                "answer": a + b == stated,
                "explanation": f"{a} + {b} = {a + b}."}
    if mode == 1:
        number = {1: 13, 5: 15, 9: 17}[index]
        divisor = next((d for d in range(2, number) if number % d == 0), None)
        return {"kind": "prime", "number": number,
                "text": f"{number} asal sayıdır.", "answer": _prime(number),
                "explanation": (f"{number} asaldır." if _prime(number) else
                                f"{number} asal değildir; {divisor} × {number // divisor} = {number}.")}
    if mode == 2:
        number = 15 if index == 2 else 20
        return {"kind": "odd", "number": number,
                "text": f"{number} tek sayıdır.", "answer": number % 2 == 1,
                "explanation": f"{number} {'tektir' if number % 2 else 'çifttir'}."}
    word = ("papatya", "merhaba", "istanbul")[index % 3]
    stated = len(word) + (1 if index == 7 else 0)
    return {"kind": "letters", "word": word, "stated": stated,
            "text": f"'{word}' sözcüğü {stated} Türkçe harften oluşur.",
            "answer": len(word) == stated,
            "explanation": f"'{word}' sözcüğünde {len(word)} harf vardır."}


def evaluate_claim(item):
    kind = item["kind"]
    if kind == "sum":
        return item["a"] + item["b"] == item["stated"]
    if kind == "prime":
        return _prime(item["number"])
    if kind == "odd":
        return item["number"] % 2 == 1
    if kind == "letters":
        return len(item["word"]) == item["stated"]
    raise ValueError("ra_invalid_claim")


def detective_case(index, *, seed=None):
    if not 0 <= index < 3:
        raise ValueError("ra_invalid_round")
    rng = random.Random(812 + index if seed is None else f"{int(seed)}:detective:{index}")
    place_by_person = list(range(3))
    object_by_person = list(range(3))
    rng.shuffle(place_by_person)
    rng.shuffle(object_by_person)
    culprit = index if seed is None else rng.randrange(3)
    others = [person for person in range(3) if person != culprit]
    # Two fixed relations in each 3×3 bijection determine the third. The two
    # culprit exclusions complete a unique 3! × 3! × 3 candidate assignment.
    clues = [
        {"kind": "person_place", "person": 0, "place": place_by_person[0]},
        {"kind": "person_object", "person": 0, "object": object_by_person[0]},
        {"kind": "culprit_not", "person": others[0]},
        {"kind": "person_place", "person": 1, "place": place_by_person[1]},
        {"kind": "person_object", "person": 1, "object": object_by_person[1]},
        {"kind": "culprit_not", "person": others[1]},
    ]
    solution = [culprit, place_by_person[culprit], object_by_person[culprit]]
    return {"case_no": index + 1, "solution": solution, "clues": clues,
            "culprit": culprit, "place_by_person": place_by_person,
            "object_by_person": object_by_person,
            "suspects": list(SUSPECTS), "places": list(PLACES), "objects": list(OBJECTS)}


def detective_assignment_clues_hold(case, culprit, place_by_person,
                                    object_by_person, *, count=None):
    if (culprit not in range(3) or sorted(place_by_person) != [0, 1, 2]
            or sorted(object_by_person) != [0, 1, 2]):
        return False
    clues = case["clues"] if count is None else case["clues"][:count]
    for clue in clues:
        if clue["kind"] == "person_place" and place_by_person[clue["person"]] != clue["place"]:
            return False
        if clue["kind"] == "person_object" and object_by_person[clue["person"]] != clue["object"]:
            return False
        if clue["kind"] == "culprit_not" and culprit == clue["person"]:
            return False
    return True


def detective_clues_hold(case, person, place, item, *, count=None):
    """Whether any complete 3×3 assignment supports an accused triple."""
    from itertools import permutations

    return any(
        locations[person] == place and objects[person] == item
        and detective_assignment_clues_hold(case, person, locations, objects,
                                            count=count)
        for locations in permutations(range(3))
        for objects in permutations(range(3))) if all(
            value in range(3) for value in (person, place, item)) else False


def new(*, game_key, chat_id, thread_id, owner_id, owner_name, round_id,
        now=None, values=None, seed=None):
    if game_key not in GAME_KEYS:
        raise ValueError("ra_invalid_game")
    moment = _now(now)
    owner_id = int(owner_id)
    state = {"game_key": game_key, "chat_id": int(chat_id), "thread_id": thread_id,
             "owner_id": owner_id, "round_id": str(round_id), "version": 0,
             "puzzle_seed": secrets.randbits(63) if seed is None else int(seed),
             "phase_seq": 0, "phase": "playing" if game_key == RANK else "lobby",
             "players": [{"id": owner_id, "name": str(owner_name)[:32], "score": 0,
                          "lives": 3, "accusations": 0, "active": True}],
             "created_at": moment, "last_activity_at": moment,
             "lobby_expires_at": None if game_key == RANK else moment + LOBBY_SECONDS,
             "deadline": None, "panel_wait_until": moment + PANEL_WAIT_SECONDS if game_key == RANK else None,
             "panel_dirty": True, "delivery_retry_at": 0, "panel_message_id": None,
             "score_pending": False, "winner_ids": [], "reason": None,
             "processed_actions": []}
    if game_key == RANK:
        values = list(values) if values is not None else secrets.SystemRandom().sample(range(10, 100), 5)
        if len(values) != 5 or len(set(values)) != 5 or any(not isinstance(v, int) for v in values):
            raise ValueError("ra_invalid_values")
        state.update(values=values, assigned=[None] * 5, step=0)
    elif game_key == CALENDAR:
        state.update(round_no=0, puzzle=None, selections={}, confirmed=[])
    elif game_key == TRUE_FALSE:
        state.update(round_no=0, proposition=None, votes={}, revealed=False,
                     round_baseline={})
    else:
        state.update(round_no=0, case=None, clues_shown=3, solved=[])
    return state


def join(state, *, user_id, user_name, now=None):
    moment = _now(now)
    if state["phase"] != "lobby" or moment >= state["lobby_expires_at"]:
        raise ValueError("ra_lobby_closed")
    if any(p["id"] == int(user_id) and p.get("active", True) for p in state["players"]):
        raise ValueError("ra_already_joined")
    maximum = 30 if state["game_key"] == TRUE_FALSE else 12
    if sum(bool(p.get("active", True)) for p in state["players"]) >= maximum:
        raise ValueError("ra_full")
    result = _copy(state, moment)
    former = next((p for p in result["players"] if p["id"] == int(user_id)), None)
    if former is not None:
        former.update(active=True, name=str(user_name)[:32])
        return result
    result["players"].append({"id": int(user_id), "name": str(user_name)[:32],
                              "score": 0, "lives": 3, "accusations": 0,
                              "active": True})
    return result


def _participants(state):
    return [player for player in state["players"] if player.get("active", True)]


def leave(state, *, user_id, now=None):
    """Remove a participant without granting their points to the remaining players."""
    if state["phase"] in TERMINAL:
        raise ValueError("ra_wrong_phase")
    _player(state, user_id)
    moment = _now(now)
    result = _copy(state, moment)
    _player(result, user_id)["active"] = False
    remaining = _participants(result)
    if not remaining or state["game_key"] == RANK:
        result.update(phase="cancelled", phase_seq=result["phase_seq"] + 1,
                      deadline=None, panel_wait_until=None, lobby_expires_at=None,
                      score_pending=False, winner_ids=[], reason="all_players_left")
        return result
    if int(user_id) == result["owner_id"]:
        result["owner_id"] = remaining[0]["id"]
    if state["phase"] == "lobby":
        return result
    # A departure can complete the current round. Count only eligible players.
    if state["phase"] == "playing" and state["game_key"] == CALENDAR:
        if all(p["id"] in result["confirmed"] for p in remaining):
            _next_round(result, moment)
    elif state["phase"] == "playing" and state["game_key"] == TRUE_FALSE:
        if all(p["lives"] <= 0 or str(p["id"]) in result["votes"] for p in remaining):
            _phase(result, "reveal", moment)
            result["revealed"] = True
    elif (state["phase"] == "playing" and state["game_key"] == DETECTIVE
          and all(p["accusations"] >= 3 or p["id"] in result["solved"] for p in remaining)):
        _next_round(result, moment)
    return result


def _next_round(state, now):
    total = {CALENDAR: 5, TRUE_FALSE: 10, DETECTIVE: 3}[state["game_key"]]
    if state["round_no"] >= total:
        active = _participants(state)
        high = max(p["score"] for p in active)
        _finish(state, winners=(p["id"] for p in active
                                if high > 0 and p["score"] == high))
        return
    state["round_no"] += 1
    _phase(state, "playing", now)
    if state["game_key"] == CALENDAR:
        state.update(puzzle=calendar_puzzle(state["round_no"] - 1,
                                            seed=state["puzzle_seed"]),
                     selections={}, confirmed=[])
    elif state["game_key"] == TRUE_FALSE:
        state.update(proposition=claim(state["round_no"] - 1,
                                       seed=state["puzzle_seed"]),
                     votes={}, revealed=False,
                     round_baseline={str(p["id"]): [p["score"], p["lives"]]
                                     for p in state["players"]})
    else:
        state.update(case=detective_case(state["round_no"] - 1,
                                         seed=state["puzzle_seed"]),
                     clues_shown=3, solved=[])
        for player in state["players"]:
            player["accusations"] = 0


def start(state, *, user_id, now=None):
    moment = _now(now)
    if state["phase"] != "lobby" or moment >= state["lobby_expires_at"]:
        raise ValueError("ra_lobby_closed")
    if int(user_id) != state["owner_id"]:
        raise ValueError("ra_not_owner")
    result = _copy(state, moment)
    result["lobby_expires_at"] = None
    _next_round(result, moment)
    return result


def _active(state, now):
    moment = _now(now)
    if state["phase"] != "playing" or state["deadline"] is None or moment >= state["deadline"]:
        raise ValueError("ra_wrong_phase")
    return moment


def _player(state, user_id):
    player = next((p for p in state["players"] if p["id"] == int(user_id)), None)
    if player is None or not player.get("active", True):
        raise ValueError("ra_not_player")
    return player


def assign_rank(state, *, user_id, rank, now=None):
    moment = _active(state, now)
    if state["game_key"] != RANK or int(user_id) != state["owner_id"]:
        raise ValueError("ra_not_owner")
    if rank not in range(1, 6) or rank in state["assigned"]:
        raise ValueError("ra_rank_taken")
    result = _copy(state, moment)
    result["assigned"][result["step"]] = rank
    result["step"] += 1
    if result["step"] == 5:
        points = rank_score(result["values"], result["assigned"])
        result["players"][0]["score"] = points
        _finish(result, winners=[user_id] if points > 0 else [])
    else:
        _phase(result, "playing", moment)
    return result


def calendar_choose(state, *, user_id, index, now=None):
    moment = _active(state, now)
    if state["game_key"] != CALENDAR:
        raise ValueError("ra_wrong_game")
    _player(state, user_id)
    if int(user_id) in state["confirmed"] or index not in range(5):
        raise ValueError("ra_invalid_choice")
    prior = state["selections"].get(str(user_id), [])
    if index in prior or len(prior) == 5:
        raise ValueError("ra_invalid_choice")
    result = _copy(state, moment)
    result["selections"].setdefault(str(user_id), []).append(index)
    return result


def calendar_clear(state, *, user_id, now=None):
    moment = _active(state, now)
    if state["game_key"] != CALENDAR:
        raise ValueError("ra_wrong_game")
    _player(state, user_id)
    if int(user_id) in state["confirmed"]:
        raise ValueError("ra_already_answered")
    result = _copy(state, moment)
    result["selections"][str(user_id)] = []
    return result


def calendar_confirm(state, *, user_id, now=None):
    moment = _active(state, now)
    if state["game_key"] != CALENDAR:
        raise ValueError("ra_wrong_game")
    _player(state, user_id)
    order = state["selections"].get(str(user_id), [])
    if int(user_id) in state["confirmed"] or len(order) != 5:
        raise ValueError("ra_incomplete_order")
    result = _copy(state, moment)
    result["confirmed"].append(int(user_id))
    if order == result["puzzle"]["solution"]:
        _player(result, user_id)["score"] += 5
    if all(p["id"] in result["confirmed"] for p in _participants(result)):
        _next_round(result, moment)
    return result


def vote(state, *, user_id, answer, risk, now=None):
    moment = _active(state, now)
    if state["game_key"] != TRUE_FALSE or not isinstance(answer, bool) or not isinstance(risk, bool):
        raise ValueError("ra_invalid_vote")
    player = _player(state, user_id)
    if player["lives"] <= 0 or str(user_id) in state["votes"]:
        raise ValueError("ra_already_answered")
    result = _copy(state, moment)
    result["votes"][str(user_id)] = {"answer": answer, "risk": risk}
    actor = _player(result, user_id)
    if answer == result["proposition"]["answer"]:
        actor["score"] += 2 if risk else 1
    else:
        actor["lives"] = max(0, actor["lives"] - (2 if risk else 1))
    if all(p["lives"] <= 0 or str(p["id"]) in result["votes"] for p in _participants(result)):
        _phase(result, "reveal", moment)
        result["revealed"] = True
    return result


def detective_hint(state, *, user_id, now=None):
    moment = _active(state, now)
    if state["game_key"] != DETECTIVE or state["clues_shown"] >= 6:
        raise ValueError("ra_no_hint")
    _player(state, user_id)
    result = _copy(state, moment)
    result["clues_shown"] += 1
    _player(result, user_id)["score"] -= 1
    return result


def detective_accuse(state, *, user_id, person, place, item, now=None):
    moment = _active(state, now)
    if state["game_key"] != DETECTIVE:
        raise ValueError("ra_wrong_game")
    actor = _player(state, user_id)
    if actor["accusations"] >= 3 or int(user_id) in state["solved"]:
        raise ValueError("ra_no_accusations")
    if [person, place, item] not in [[a, b, c] for a in range(3) for b in range(3) for c in range(3)]:
        raise ValueError("ra_invalid_accusation")
    result = _copy(state, moment)
    actor = _player(result, user_id)
    actor["accusations"] += 1
    if [person, place, item] == result["case"]["solution"]:
        actor["score"] += max(1, 4 - actor["accusations"])
        result["solved"].append(int(user_id))
    if all(p["accusations"] >= 3 or p["id"] in result["solved"] for p in _participants(result)):
        _next_round(result, moment)
    return result


def cancel(state, *, user_id, moderator=False, now=None, reason="owner"):
    if state["phase"] in TERMINAL:
        raise ValueError("ra_wrong_phase")
    if int(user_id) != state["owner_id"] and not moderator:
        raise ValueError("ra_not_owner")
    result = _copy(state, now)
    result.update(phase="cancelled", phase_seq=state["phase_seq"] + 1,
                  deadline=None, panel_wait_until=None, lobby_expires_at=None,
                  score_pending=False, reason=reason)
    return result


def advance(state, *, now=None):
    moment = _now(now)
    if state["phase"] in TERMINAL:
        return state
    if state["phase"] == "lobby":
        return cancel(state, user_id=state["owner_id"], now=moment,
                      reason="lobby_timeout") if moment >= state["lobby_expires_at"] else state
    if state["deadline"] is None:
        return (cancel(state, user_id=state["owner_id"], now=moment,
                       reason="panel_timeout") if moment >= state["panel_wait_until"] else state)
    if moment < state["deadline"]:
        return state
    result = _copy(state, moment)
    if state["game_key"] == RANK:
        return cancel(state, user_id=state["owner_id"], now=moment,
                      reason="rank_timeout")
    if state["game_key"] == CALENDAR:
        _next_round(result, moment)
    elif state["game_key"] == TRUE_FALSE:
        if state["phase"] == "playing":
            _phase(result, "reveal", moment)
            result["revealed"] = True
        else:
            _next_round(result, moment)
    else:
        _next_round(result, moment)
    return result


def public_state(state):
    view = deepcopy(state)
    view.pop("processed_actions", None)
    view.pop("puzzle_seed", None)
    if state["game_key"] == RANK and state["phase"] != "finished":
        view["values"] = state["values"][:state["step"] + 1]
    if state["game_key"] == CALENDAR and state["puzzle"] is not None and state["phase"] != "finished":
        view["puzzle"].pop("solution", None)
        view.pop("selections", None)
    if state["game_key"] == TRUE_FALSE and state["proposition"] is not None and not state["revealed"]:
        view["proposition"].pop("answer", None)
        view["proposition"].pop("explanation", None)
        view["votes"] = {key: {"risk": entry["risk"]} for key, entry in view["votes"].items()}
        for player in view["players"]:
            score, lives = state["round_baseline"][str(player["id"])]
            player["score"], player["lives"] = score, lives
    if state["game_key"] == DETECTIVE and state["case"] is not None and state["phase"] != "finished":
        view["case"].pop("solution", None)
        view["case"].pop("culprit", None)
        view["case"].pop("place_by_person", None)
        view["case"].pop("object_by_person", None)
        view["case"]["clues"] = state["case"]["clues"][:state["clues_shown"]]
    return view
