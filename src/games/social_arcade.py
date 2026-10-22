"""Pure, finite rules for five distinct social arcade games."""
from __future__ import annotations

import random
import secrets
import time
import unicodedata
from copy import deepcopy

GAME_KEYS = ("amiral_batti", "iki_dogru_bir_yalan", "kimim_ben",
             "takim_bilgi_ligi", "zehirli_kadeh")
LIMITS = {"amiral_batti": (2, 2), "iki_dogru_bir_yalan": (3, 12),
          "kimim_ben": (3, 12), "takim_bilgi_ligi": (4, 20),
          "zehirli_kadeh": (3, 10)}
TERMINAL = {"finished", "cancelled"}
LOBBY_SECONDS = 1200
PANEL_WAIT_SECONDS = 300
RESULT_SECONDS = 5
SHIPS = (3, 2, 2)
IDENTITIES = ("astronot", "itfaiyeci", "arkeolog", "veteriner", "deniz feneri bekçisi",
              "fırıncı", "bahçıvan", "mimar", "tren makinisti", "gökbilimci", "kütüphaneci",
              "balıkçı", "heykeltıraş", "pilot", "müzisyen", "çiftçi")
# Adjacent questions form a matched topic/difficulty pair, one per team.
# Four locally checked subjects and five pairs per subject keep a five-round
# match varied without relying on changing facts or external APIs.
TRIVIA = (
    # Türkçe dil ve harf
    ("'Kalem' sözcüğünde kaç harf vardır?", ("4", "5", "6", "7"), 1),
    ("'Masa' sözcüğünde kaç harf vardır?", ("3", "4", "5", "6"), 1),
    ("'Kitap' sözcüğünde kaç sesli harf vardır?", ("1", "2", "3", "4"), 1),
    ("'Araba' sözcüğünde kaç sesli harf vardır?", ("2", "3", "4", "5"), 1),
    ("'Şemsiye' sözcüğünün ilk harfi hangisidir?", ("S", "Ş", "Ç", "C"), 1),
    ("'Çanta' sözcüğünün ilk harfi hangisidir?", ("C", "Ş", "Ç", "S"), 2),
    ("'Kelebek' sözcüğü kaç hecelidir?", ("2", "3", "4", "5"), 1),
    ("'Kaplumbağa' sözcüğü kaç hecelidir?", ("2", "3", "4", "5"), 2),
    ("Türk alfabesinde A'dan sonra hangi harf gelir?", ("B", "C", "Ç", "D"), 0),
    ("Türk alfabesinde C'den sonra hangi harf gelir?", ("B", "C", "Ç", "D"), 2),
    # Geometri
    ("Bir karenin kaç kenarı vardır?", ("3", "4", "5", "6"), 1),
    ("Bir üçgenin kaç kenarı vardır?", ("3", "4", "5", "6"), 0),
    ("Bir altıgenin kaç kenarı vardır?", ("5", "6", "7", "8"), 1),
    ("Bir sekizgenin kaç kenarı vardır?", ("6", "7", "8", "9"), 2),
    ("Bir karenin iç açılarının toplamı kaç derecedir?", ("180", "270", "360", "450"), 2),
    ("Bir üçgenin iç açılarının toplamı kaç derecedir?", ("90", "120", "180", "360"), 2),
    ("Bir dikdörtgenin kaç köşesi vardır?", ("3", "4", "5", "6"), 1),
    ("Bir beşgenin kaç köşesi vardır?", ("3", "4", "5", "6"), 2),
    ("Bir küpün kaç yüzü vardır?", ("4", "5", "6", "8"), 2),
    ("Kare tabanlı bir piramidin kaç yüzü vardır?", ("4", "5", "6", "8"), 1),
    # Birim ve sayı
    ("3 saat kaç dakikadır?", ("120", "150", "180", "240"), 2),
    ("4 saat kaç dakikadır?", ("180", "210", "240", "270"), 2),
    ("2 metre kaç santimetredir?", ("20", "100", "200", "2000"), 2),
    ("3 metre kaç santimetredir?", ("30", "150", "300", "3000"), 2),
    ("90'ın yarısı kaçtır?", ("35", "40", "45", "50"), 2),
    ("70'in yarısı kaçtır?", ("25", "30", "35", "40"), 2),
    ("12'den 5 çıkarılırsa kaç kalır?", ("5", "6", "7", "8"), 2),
    ("15'ten 6 çıkarılırsa kaç kalır?", ("7", "8", "9", "10"), 2),
    ("4 hafta kaç gündür?", ("21", "24", "28", "35"), 2),
    ("3 hafta kaç gündür?", ("14", "18", "21", "28"), 2),
    # Doğa ve basit bilim
    ("İnsanın bir elinde kaç parmak vardır?", ("4", "5", "6", "8"), 1),
    ("Bir örümceğin kaç bacağı vardır?", ("4", "6", "8", "10"), 2),
    ("Yunus hangi hayvan sınıfındadır?", ("Memeli", "Kuş", "Balık", "Böcek"), 0),
    ("Serçe hangi hayvan sınıfındadır?", ("Memeli", "Kuş", "Balık", "Böcek"), 1),
    ("Bitkide fotosentezin en yoğun olduğu yapı hangisidir?", ("Yaprak", "Kök", "Çiçek", "Gövde"), 0),
    ("Bitkide topraktan suyu alan yapı hangisidir?", ("Yaprak", "Kök", "Çiçek", "Gövde"), 1),
    ("Deniz seviyesinde su kaç °C'de donar?", ("0", "25", "50", "100"), 0),
    ("Deniz seviyesinde su kaç °C'de kaynar?", ("0", "25", "50", "100"), 3),
    ("Dünya'nın doğal uydusu hangisidir?", ("Ay", "Güneş", "Mars", "Venüs"), 0),
    ("Dünya'nın çevresinde dolandığı yıldız hangisidir?", ("Ay", "Güneş", "Mars", "Venüs"), 1),
)


def _now(now):
    return time.time() if now is None else float(now)


def _copy(state):
    result = deepcopy(state)
    result["version"] += 1
    return result


def _person(state, user_id):
    person = next((p for p in state["players"] if p["id"] == int(user_id)), None)
    if person is None:
        raise ValueError("sa_not_player")
    return person


def _phase(state, name, now, duration, recipients=()):
    state.update(phase=name, phase_seq=state["phase_seq"] + 1, deadline=None,
                 duration=duration, phase_ready=False, group_ready=False,
                 panel_wait_until=_now(now) + PANEL_WAIT_SECONDS,
                 panel_dirty=True, delivery_retry_at=0,
                 action_panel_pending=False,
                 private_recipients=list(dict.fromkeys(int(p) for p in recipients)),
                 private_delivery={}, private_retry={}, private_retry_at=0,
                 private_dirty=bool(recipients))


def _finish(state, *, now, winners=()):
    _phase(state, "finished", now, None)
    earned = any(int(points) > 0 for points in state["scores"].values())
    state.update(deadline=None, panel_wait_until=None, phase_ready=False,
                 private_dirty=False, winner_ids=list(winners) if earned else [],
                 score_pending=earned)


def _cancel(state, *, now, reason):
    _phase(state, "cancelled", now, None)
    state.update(deadline=None, panel_wait_until=None, private_dirty=False,
                 score_pending=False, reason=reason)


def new(*, game_key, chat_id, thread_id, owner_id, owner_name, round_id, now=None):
    if game_key not in GAME_KEYS:
        raise ValueError("sa_invalid_game")
    moment = _now(now)
    return {"game_key": game_key, "chat_id": int(chat_id), "thread_id": thread_id,
            "owner_id": int(owner_id), "round_id": str(round_id), "version": 0,
            "phase_seq": 0, "phase": "lobby", "created_at": moment,
            "players": [{"id": int(owner_id), "name": str(owner_name)[:32], "ready": False,
                         "active": True}],
            "scores": {str(owner_id): 0}, "deadline": None,
            "lobby_expires_at": moment + LOBBY_SECONDS, "panel_wait_until": None,
            "panel_dirty": True, "delivery_retry_at": 0, "panel_message_id": None,
            "private_dirty": False, "private_retry_at": None, "private_retry": {},
            "private_delivery": {}, "private_recipients": [], "private_message_ids": {},
            "private_panels": {}, "action_panel_pending": False,
            "phase_ready": False, "group_ready": False, "duration": None,
            "score_pending": False, "winner_ids": [], "processed_actions": [],
            "dm_invites": {}, "reason": None}


def join(state, *, user_id, user_name, now=None):
    if state["phase"] != "lobby" or _now(now) >= state["lobby_expires_at"]:
        raise ValueError("sa_lobby_closed")
    if any(p["id"] == int(user_id) for p in state["players"]):
        raise ValueError("sa_already_joined")
    if len(state["players"]) >= LIMITS[state["game_key"]][1]:
        raise ValueError("sa_full")
    result = _copy(state)
    result["players"].append({"id": int(user_id), "name": str(user_name)[:32],
                              "ready": False, "active": True})
    result["scores"][str(user_id)] = 0
    return result


def ready(state, *, user_id):
    if state["phase"] != "lobby":
        raise ValueError("sa_lobby_closed")
    if _person(state, user_id)["ready"]:
        raise ValueError("sa_already_ready")
    result = _copy(state)
    _person(result, user_id)["ready"] = True
    return result


def _fleet(seed):
    rng = random.Random(seed)
    for _ in range(10000):
        occupied = set()
        ships = []
        for length in SHIPS:
            candidates = []
            for row in range(6):
                for col in range(6):
                    for dr, dc in ((0, 1), (1, 0)):
                        cells = tuple((row + dr * k) * 6 + col + dc * k for k in range(length))
                        if (all(row + dr * k < 6 and col + dc * k < 6 for k in range(length))
                                and not occupied.intersection(cells)):
                            candidates.append(cells)
            if not candidates:
                break
            selected = rng.choice(candidates)
            occupied.update(selected)
            ships.append(list(selected))
        if len(ships) == len(SHIPS):
            return ships
    raise RuntimeError("sa_fleet_generation_failed")


def start(state, *, user_id, now=None, seed=None):
    moment = _now(now)
    if state["phase"] != "lobby" or moment >= state["lobby_expires_at"]:
        raise ValueError("sa_lobby_closed")
    if int(user_id) != state["owner_id"]:
        raise ValueError("sa_not_owner")
    lower, upper = LIMITS[state["game_key"]]
    if not lower <= len(state["players"]) <= upper:
        raise ValueError("sa_player_count")
    if not all(p["ready"] for p in state["players"]):
        raise ValueError("sa_dm_not_ready")
    result = _copy(state)
    result["lobby_expires_at"] = None
    ids = [p["id"] for p in result["players"]]
    key = state["game_key"]
    rng = random.Random(seed if seed is not None else secrets.randbits(64))
    if key == "amiral_batti":
        result.update(ships={str(uid): _fleet(rng.getrandbits(64)) for uid in ids},
                      shots={str(uid): [] for uid in ids}, misses={str(uid): 0 for uid in ids},
                      turn_index=0, consecutive_timeouts={str(uid): 0 for uid in ids},
                      last_event=None)
        _phase(result, "shot", moment, 60, ids)
    elif key == "iki_dogru_bir_yalan":
        result.update(author_order=ids * 2, author_index=0, claims=[], lie_index=None,
                      votes={}, last_event=None)
        _phase(result, "author", moment, 120, [ids[0]])
    elif key == "kimim_ben":
        cards = rng.sample(IDENTITIES, len(ids))
        result.update(identities={str(uid): card for uid, card in zip(ids, cards)},
                      subject_index=0, question_count=0, question_text=None,
                      answers={}, last_event=None)
        _phase(result, "question", moment, 60, ids)
    elif key == "takim_bilgi_ligi":
        teams = {str(uid): index % 2 for index, uid in enumerate(ids)}
        # Each round chooses a matched pair: both teams face the same topic and
        # arithmetic depth, while separate questions prevent answer copying.
        selected_pairs = rng.sample(range(len(TRIVIA) // 2), 5)
        questions = []
        for pair in selected_pairs:
            for offset in (0, 1):
                prompt, options, answer = TRIVIA[2 * pair + offset]
                scrambled = list(options)
                rng.shuffle(scrambled)
                questions.append((prompt, tuple(scrambled), scrambled.index(options[answer])))
        result.update(teams=teams, team_scores=[0, 0], turn_no=0,
                      wager=1, passes=[1, 1], questions=questions, last_event=None)
        _phase(result, "trivia", moment, 60)
    else:
        poisoner = rng.choice(ids)
        result.update(poisoner_id=poisoner, round_no=1, health={str(uid): 2 for uid in ids},
                      swaps={str(uid): 1 for uid in ids}, refuses={str(uid): 1 for uid in ids},
                      poison_cup=None, served_cup=None, response=None, last_event=None)
        _phase(result, "poison", moment, 60, [poisoner])
    return result


def activate(state, *, now=None):
    moment = _now(now)
    if state["phase"] in TERMINAL or state["phase_ready"]:
        return state
    if not state["group_ready"] or any(str(uid) not in state["private_delivery"]
                                       for uid in state["private_recipients"]):
        return state
    if moment >= state["panel_wait_until"]:
        result = _copy(state)
        _cancel(result, now=moment, reason="delivery_timeout")
        return result
    result = _copy(state)
    result.update(phase_ready=True, deadline=moment + state["duration"],
                  panel_wait_until=None, panel_dirty=True, delivery_retry_at=0)
    return result


def _next_author(state, now):
    state["author_index"] += 1
    if state["author_index"] >= len(state["author_order"]):
        best = max(state["scores"].values())
        winners = [int(uid) for uid, points in state["scores"].items() if points == best]
        _finish(state, now=now, winners=winners)
        return
    state.update(claims=[], lie_index=None, votes={}, last_event=None)
    _phase(state, "author", now, 120, [state["author_order"][state["author_index"]]])


def _next_subject(state, now):
    state["subject_index"] += 1
    if state["subject_index"] >= len(state["players"]):
        best = max(state["scores"].values())
        winners = [int(uid) for uid, score in state["scores"].items() if score == best]
        _finish(state, now=now, winners=winners)
        return
    state.update(question_count=0, question_text=None, answers={}, last_event=None)
    _phase(state, "question", now, 60, [p["id"] for p in state["players"]])


def _next_poison_round(state, now):
    alive = [p["id"] for p in state["players"] if state["health"][str(p["id"])] > 0]
    if state["round_no"] >= 5 or len(alive) <= 1:
        best = max(state["health"].values())
        winners = [int(uid) for uid, health in state["health"].items() if health == best]
        _finish(state, now=now, winners=winners)
        return
    state["round_no"] += 1
    if state["health"].get(str(state["poisoner_id"]), 0) <= 0:
        state["poisoner_id"] = secrets.choice(alive)
    state.update(poison_cup=None, served_cup=None, response=None, last_event=None)
    _phase(state, "poison", now, 60, [state["poisoner_id"]])


def _normalized(text):
    return "".join(ch for ch in unicodedata.normalize("NFKC", str(text).strip().casefold())
                   if ch.isalnum() or ch.isspace()).strip()


def act(state, *, user_id, action, value=None, text=None, now=None, moderator=False):
    moment = _now(now)
    if action == "join":
        return join(state, user_id=user_id, user_name=text or "Oyuncu", now=moment)
    if action == "start":
        return start(state, user_id=user_id, now=moment)
    if action == "stop":
        if int(user_id) != state["owner_id"] and not moderator:
            raise ValueError("sa_not_owner")
        if state["phase"] in TERMINAL:
            raise ValueError("sa_closed")
        result = _copy(state)
        _cancel(result, now=moment, reason="manual")
        return result
    if action == "leave":
        person = _person(state, user_id)
        if not person["active"]:
            raise ValueError("sa_not_player")
        if state["phase"] in TERMINAL:
            raise ValueError("sa_closed")
        result = _copy(state)
        if state["phase"] == "lobby" and int(user_id) != state["owner_id"]:
            result["players"] = [p for p in result["players"] if p["id"] != int(user_id)]
            result["scores"].pop(str(user_id), None)
            result["dm_invites"].pop(str(user_id), None)
            return result
        if (state["game_key"] == "zehirli_kadeh" and
                state.get("health", {}).get(str(user_id), 1) <= 0):
            _person(result, user_id)["active"] = False
            return result
        if state["game_key"] == "amiral_batti" and state["phase"] != "lobby":
            winner = next(p["id"] for p in state["players"] if p["id"] != int(user_id))
            result["scores"][str(winner)] = 10
            result["last_event"] = {"forfeit": int(user_id)}
            _finish(result, now=moment, winners=[winner])
            return result
        # Multi-party secret ballots and team assignments cannot be silently
        # reweighted after a participant has seen role/question material.
        _cancel(result, now=moment, reason="player_left")
        return result
    if state["phase"] in TERMINAL or not state["phase_ready"] or moment >= state["deadline"]:
        raise ValueError("sa_wrong_phase")
    key, phase = state["game_key"], state["phase"]
    if key == "amiral_batti" and phase == "shot" and action == "shot":
        actor = state["players"][state["turn_index"]]["id"]
        if int(user_id) != actor:
            raise ValueError("sa_not_turn")
        cell = int(value)
        if not 0 <= cell < 36 or cell in state["shots"][str(actor)]:
            raise ValueError("sa_invalid_target")
        result = _copy(state)
        target = next(p["id"] for p in state["players"] if p["id"] != actor)
        result["shots"][str(actor)].append(cell)
        hit = any(cell in ship for ship in state["ships"][str(target)])
        result["last_event"] = {"cell": cell, "hit": hit, "actor": actor}
        result["consecutive_timeouts"][str(actor)] = 0
        if not hit:
            result["misses"][str(actor)] += 1
            result["turn_index"] = 1 - state["turn_index"]
        target_cells = {c for ship in state["ships"][str(target)] for c in ship}
        if target_cells.issubset(set(result["shots"][str(actor)])):
            result["scores"][str(actor)] = 10
            _finish(result, now=moment, winners=[actor])
        else:
            _phase(result, "shot", moment, 60, [p["id"] for p in state["players"]])
        return result
    if key == "iki_dogru_bir_yalan":
        author = state["author_order"][state["author_index"]]
        if phase == "author" and action == "claim" and int(user_id) == author:
            claim = " ".join(str(text or "").split())
            units = len(claim.encode("utf-16-le")) // 2
            if not 5 <= units <= 100 or len(state["claims"]) >= 3:
                raise ValueError("sa_invalid_claim")
            canonical = _normalized(claim)
            if any(_normalized(existing) == canonical for existing in state["claims"]):
                raise ValueError("sa_duplicate_claim")
            result = _copy(state)
            result["claims"].append(claim)
            return result
        if phase == "author" and action == "lie" and int(user_id) == author:
            if len(state["claims"]) != 3 or int(value) not in (0, 1, 2):
                raise ValueError("sa_invalid_lie")
            result = _copy(state)
            result["lie_index"] = int(value)
            _phase(result, "vote", moment, 60,
                   [p["id"] for p in state["players"] if p["id"] != author])
            return result
        if phase == "vote" and action == "vote":
            if int(user_id) == author or str(user_id) in state["votes"] or int(value) not in (0, 1, 2):
                raise ValueError("sa_invalid_vote")
            _person(state, user_id)
            result = _copy(state)
            result["votes"][str(user_id)] = int(value)
            if int(value) == state["lie_index"]:
                result["scores"][str(user_id)] += 1
            else:
                result["scores"][str(author)] += 1
            if len(result["votes"]) == len(result["players"]) - 1:
                result["last_event"] = {"lie_index": state["lie_index"],
                                        "votes": deepcopy(result["votes"])}
                _phase(result, "result", moment, RESULT_SECONDS)
            return result
    if key == "kimim_ben":
        subject = state["players"][state["subject_index"]]["id"]
        if phase == "question" and int(user_id) == subject and action == "question":
            question = " ".join(str(text or "").split())
            if not question.endswith("?") or not 5 <= len(question) <= 120 or state["question_count"] >= 8:
                raise ValueError("sa_invalid_question")
            result = _copy(state)
            result["question_text"] = question
            result["answers"] = {}
            result["question_count"] += 1
            _phase(result, "answer", moment, 60,
                   [p["id"] for p in state["players"] if p["id"] != subject])
            return result
        if phase == "question" and int(user_id) == subject and action == "guess":
            guess = _normalized(text)
            if not guess or len(guess) > 80:
                raise ValueError("sa_invalid_guess")
            result = _copy(state)
            won = guess == _normalized(state["identities"][str(subject)])
            result["question_count"] += 1
            if won:
                result["scores"][str(subject)] += max(1, 9 - state["question_count"])
            result["last_event"] = {"guess": guess, "correct": won,
                                    **({"identity": state["identities"][str(subject)]}
                                       if won or result["question_count"] >= 8 else {})}
            if won or result["question_count"] >= 8:
                _phase(result, "result", moment, RESULT_SECONDS)
            else:
                _phase(result, "question", moment, 60, [p["id"] for p in state["players"]])
            return result
        if phase == "answer" and action in {"yes", "no"}:
            if int(user_id) == subject or str(user_id) in state["answers"]:
                raise ValueError("sa_invalid_vote")
            _person(state, user_id)
            result = _copy(state)
            result["answers"][str(user_id)] = action == "yes"
            if len(result["answers"]) == len(result["players"]) - 1:
                yes = sum(result["answers"].values())
                result["last_event"] = {"answer": "Evet" if yes > (len(result["answers"]) / 2)
                                        else "Hayır" if yes < (len(result["answers"]) / 2) else "Berabere"}
                _phase(result, "result", moment, RESULT_SECONDS)
            return result
    if key == "takim_bilgi_ligi" and phase == "trivia":
        turn = state["turn_no"]
        team = turn % 2
        members = [p["id"] for p in state["players"] if state["teams"][str(p["id"])] == team]
        captain = members[(turn // 2) % len(members)]
        if int(user_id) != captain:
            raise ValueError("sa_not_captain")
        result = _copy(state)
        if action == "wager":
            if int(value) not in (1, 2):
                raise ValueError("sa_invalid_wager")
            result["wager"] = int(value)
            return result
        if action == "pass":
            if result["passes"][team] <= 0:
                raise ValueError("sa_no_pass")
            result["passes"][team] -= 1
            result["last_event"] = {"team": team, "passed": True,
                                    "answer": state["questions"][turn][2]}
        elif action == "answer":
            index = int(value)
            if not 0 <= index < 4:
                raise ValueError("sa_invalid_answer")
            correct = index == state["questions"][turn][2]
            result["team_scores"][team] += result["wager"] if correct else -result["wager"]
            result["last_event"] = {"team": team, "correct": correct,
                                    "answer": state["questions"][turn][2], "chosen": index}
        else:
            raise ValueError("sa_invalid_action")
        if action == "answer":
            delta = result["wager"] if result["last_event"].get("correct") else -result["wager"]
            for member in members:
                result["scores"][str(member)] += delta
        _phase(result, "result", moment, RESULT_SECONDS)
        return result
    if key == "zehirli_kadeh":
        poisoner = state["poisoner_id"]
        alive = [p["id"] for p in state["players"] if state["health"][str(p["id"])] > 0]
        server_candidates = [uid for uid in alive if uid != poisoner]
        server = server_candidates[(state["round_no"] - 1) % len(server_candidates)] if server_candidates else poisoner
        target_candidates = [uid for uid in alive if uid != server]
        target = target_candidates[(state["round_no"] - 1) % len(target_candidates)] if target_candidates else server
        if phase == "poison" and action == "poison" and int(user_id) == poisoner:
            if int(value) not in (0, 1):
                raise ValueError("sa_invalid_cup")
            result = _copy(state)
            result["poison_cup"] = int(value)
            _phase(result, "serve", moment, 60, [server])
            return result
        if phase == "serve" and action == "serve" and int(user_id) == server:
            if int(value) not in (0, 1):
                raise ValueError("sa_invalid_cup")
            result = _copy(state)
            result["served_cup"] = int(value)
            _phase(result, "react", moment, 60, [target])
            return result
        if phase == "react" and action in {"drink", "swap", "refuse"} and int(user_id) == target:
            result = _copy(state)
            cup = state["served_cup"]
            if action == "swap":
                if result["swaps"][str(target)] <= 0:
                    raise ValueError("sa_no_swap")
                result["swaps"][str(target)] -= 1
                cup = 1 - cup
            if action == "refuse":
                if result["refuses"][str(target)] <= 0:
                    raise ValueError("sa_no_refuse")
                result["refuses"][str(target)] -= 1
            poisoned = action != "refuse" and cup == state["poison_cup"]
            if poisoned:
                result["health"][str(target)] = max(0, result["health"][str(target)] - 1)
                result["scores"][str(poisoner)] += 1
            elif action != "refuse":
                result["scores"][str(target)] += 1
            result["last_event"] = {"target": target, "action": action,
                                    "poison_cup": state["poison_cup"], "served_cup": state["served_cup"],
                                    "poisoned": poisoned}
            _phase(result, "result", moment, RESULT_SECONDS)
            return result
    raise ValueError("sa_invalid_action")


def advance(state, *, now=None):
    moment = _now(now)
    if state["phase"] in TERMINAL:
        return state
    if state["phase"] == "lobby":
        if moment < state["lobby_expires_at"]:
            return state
        result = _copy(state)
        _cancel(result, now=moment, reason="lobby_timeout")
        return result
    if not state["phase_ready"]:
        if moment < state["panel_wait_until"]:
            return state
        result = _copy(state)
        _cancel(result, now=moment, reason="delivery_timeout")
        return result
    if moment < state["deadline"]:
        return state
    result = _copy(state)
    key, phase = state["game_key"], state["phase"]
    if phase == "result":
        if key == "iki_dogru_bir_yalan":
            _next_author(result, moment)
        elif key == "kimim_ben":
            subject = state["players"][state["subject_index"]]["id"]
            if state["last_event"].get("answer") and state["question_count"] < 8:
                result.update(question_text=None, answers={})
                _phase(result, "question", moment, 60, [p["id"] for p in state["players"]])
            else:
                result["last_event"] = dict(state["last_event"], identity=state["identities"][str(subject)])
                _next_subject(result, moment)
        elif key == "takim_bilgi_ligi":
            result["turn_no"] += 1
            result["wager"] = 1
            if result["turn_no"] >= 10:
                best = max(result["team_scores"])
                winners = [p["id"] for p in state["players"]
                           if result["team_scores"][state["teams"][str(p["id"])]] == best]
                _finish(result, now=moment, winners=winners)
            else:
                _phase(result, "trivia", moment, 60)
        elif key == "zehirli_kadeh":
            _next_poison_round(result, moment)
        return result
    if key == "amiral_batti" and phase == "shot":
        actor = state["players"][state["turn_index"]]["id"]
        result["consecutive_timeouts"][str(actor)] += 1
        if result["consecutive_timeouts"][str(actor)] >= 3:
            winner = next(p["id"] for p in state["players"] if p["id"] != actor)
            result["scores"][str(winner)] = 10
            result["last_event"] = {"forfeit": actor}
            _finish(result, now=moment, winners=[winner])
        else:
            result["turn_index"] = 1 - state["turn_index"]
            result["last_event"] = {"timeout": actor}
            _phase(result, "shot", moment, 60, [p["id"] for p in state["players"]])
    elif key == "iki_dogru_bir_yalan":
        if phase == "author":
            result["last_event"] = {"skipped": state["author_order"][state["author_index"]]}
            _phase(result, "result", moment, RESULT_SECONDS)
        elif phase == "vote":
            author = state["author_order"][state["author_index"]]
            for p in state["players"]:
                if p["id"] != author and str(p["id"]) not in result["votes"]:
                    result["scores"][str(author)] += 1
            result["last_event"] = {"lie_index": state["lie_index"],
                                    "votes": deepcopy(result["votes"])}
            _phase(result, "result", moment, RESULT_SECONDS)
    elif key == "kimim_ben":
        subject = state["players"][state["subject_index"]]["id"]
        if phase == "question":
            result["last_event"] = {"timeout": subject,
                                    "identity": state["identities"][str(subject)]}
            _phase(result, "result", moment, RESULT_SECONDS)
        elif phase == "answer":
            yes = sum(result["answers"].values())
            result["last_event"] = {"answer": "Evet" if yes > (len(result["answers"]) / 2)
                                    else "Hayır" if yes < (len(result["answers"]) / 2) else "Berabere"}
            _phase(result, "result", moment, RESULT_SECONDS)
    elif key == "takim_bilgi_ligi" and phase == "trivia":
        result["last_event"] = {"team": state["turn_no"] % 2, "timeout": True,
                                "answer": state["questions"][state["turn_no"]][2]}
        _phase(result, "result", moment, RESULT_SECONDS)
    elif key == "zehirli_kadeh":
        if phase == "poison":
            result["poison_cup"] = 0
            _phase(result, "serve", moment, 60,
                   [poison_server_and_target(result)[0]])
        elif phase == "serve":
            result["served_cup"] = 0
            _phase(result, "react", moment, 60,
                   [poison_server_and_target(result)[1]])
        elif phase == "react":
            target = poison_server_and_target(result)[1]
            if result["refuses"][str(target)] > 0:
                result["refuses"][str(target)] -= 1
            result["last_event"] = {"target": target, "action": "timeout_refuse",
                                    "poison_cup": state["poison_cup"],
                                    "served_cup": state["served_cup"], "poisoned": False}
            _phase(result, "result", moment, RESULT_SECONDS)
    return result


def poison_server_and_target(state):
    alive = [p["id"] for p in state["players"] if state["health"][str(p["id"])] > 0]
    server_candidates = [uid for uid in alive if uid != state["poisoner_id"]]
    server = server_candidates[(state["round_no"] - 1) % len(server_candidates)] if server_candidates else state["poisoner_id"]
    target_candidates = [uid for uid in alive if uid != server]
    target = target_candidates[(state["round_no"] - 1) % len(target_candidates)] if target_candidates else server
    return server, target


def public_view(state):
    view = deepcopy(state)
    for key in ("ships", "identities", "poisoner_id", "poison_cup", "dm_invites",
                "processed_actions", "private_message_ids", "private_retry",
                "private_delivery", "private_recipients", "private_panels", "questions"):
        view.pop(key, None)
    if state["game_key"] == "iki_dogru_bir_yalan" and state["phase"] == "author":
        view.pop("claims", None)
        view.pop("lie_index", None)
    if state["game_key"] == "iki_dogru_bir_yalan" and state["phase"] == "vote":
        view.pop("lie_index", None)
        view.pop("votes", None)
    if (state["game_key"] == "kimim_ben" and state["phase"] != "finished"
            and isinstance(view.get("last_event"), dict)):
        view["last_event"].pop("identity", None)
    if state["game_key"] == "zehirli_kadeh" and state["phase"] not in {"result", "finished"}:
        view.pop("served_cup", None)
        view.pop("last_event", None)
    return view
