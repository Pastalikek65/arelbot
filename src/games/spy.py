"""Pure rules for the Turkish Spyfall-style group game."""
from __future__ import annotations

import secrets
import time
import uuid
from copy import deepcopy

LOBBY_SECONDS = 1200
DELIVERY_SECONDS = 300
TURN_SECONDS = 90
DISCUSSION_SECONDS = 90
VOTE_SECONDS = 60
REVOTE_SECONDS = 30
MIN_PLAYERS, MAX_PLAYERS = 4, 16

# Original Turkish settings, each with a concrete role context for questions.
LOCATIONS = (
    ("İlçe kütüphanesi", "kütüphaneci"), ("Fırın", "fırıncı"),
    ("Tren garı", "gişe görevlisi"), ("Veteriner kliniği", "veteriner"),
    ("Çiçekçi", "çiçekçi"), ("Mahalle pazarı", "tezgâhtar"),
    ("Diş kliniği", "diş hekimi"), ("Müze", "müze rehberi"),
    ("Sahil güvenlik istasyonu", "sahil güvenlik görevlisi"),
    ("Seramik atölyesi", "seramik ustası"), ("Bisiklet tamircisi", "tamirci"),
    ("Radyo stüdyosu", "ses teknisyeni"), ("Arı çiftliği", "arıcı"),
    ("Botanik bahçesi", "bahçıvan"), ("Buz pateni pisti", "pist görevlisi"),
    ("Planetaryum", "gökbilim anlatıcısı"), ("Tiyatro kulisi", "sahne amiri"),
    ("Liman", "liman işçisi"), ("Dağ evi", "dağ rehberi"),
    ("Postane", "posta görevlisi"), ("Akaryakıt istasyonu", "pompacı"),
    ("Terzi dükkânı", "terzi"), ("Yüzme havuzu", "can kurtaran"),
    ("Otel resepsiyonu", "resepsiyonist"), ("Üniversite laboratuvarı", "laborant"),
    ("Film seti", "ışık teknisyeni"), ("Otobüs terminali", "peron görevlisi"),
    ("Kamp alanı", "kamp sorumlusu"), ("Balıkçı barınağı", "balıkçı"),
    ("Oyuncak atölyesi", "oyuncak ustası"), ("Gözlemevi", "gözlemci"),
    ("Kayak merkezi", "kayak eğitmeni"), ("Matbaa", "baskı ustası"),
    ("Çay bahçesi", "çaycı"), ("Antikacı", "antika uzmanı"),
    ("Fotoğraf stüdyosu", "fotoğrafçı"),
)

# Distinct truthful perspectives make ordinary questions less repetitive.
LOCATION_ROLES = {
    "İlçe kütüphanesi": ("kütüphaneci", "okur", "arşiv görevlisi", "çocuk etkinliği sorumlusu"),
    "Fırın": ("fırıncı", "tezgâhtar", "çırak", "ekmek alan müşteri"),
    "Tren garı": ("gişe görevlisi", "yolcu", "peron görevlisi", "makinist"),
    "Veteriner kliniği": ("veteriner", "hayvan sahibi", "veteriner teknisyeni", "resepsiyon görevlisi"),
    "Çiçekçi": ("çiçekçi", "müşteri", "çiçek tasarımcısı", "kurye"),
    "Mahalle pazarı": ("tezgâhtar", "müşteri", "pazar denetçisi", "yük taşıyıcı"),
    "Diş kliniği": ("diş hekimi", "hasta", "diş asistanı", "resepsiyon görevlisi"),
    "Müze": ("müze rehberi", "ziyaretçi", "eser koruma uzmanı", "gişe görevlisi"),
    "Sahil güvenlik istasyonu": ("sahil güvenlik görevlisi", "tekne kaptanı", "telsiz operatörü", "kurtarma dalgıcı"),
    "Seramik atölyesi": ("seramik ustası", "kursiyer", "fırın sorumlusu", "sır hazırlayıcısı"),
    "Bisiklet tamircisi": ("tamirci", "bisiklet sahibi", "yedek parça satıcısı", "çırak"),
    "Radyo stüdyosu": ("ses teknisyeni", "sunucu", "konuk", "yayın yönetmeni"),
    "Arı çiftliği": ("arıcı", "çiftlik ziyaretçisi", "bal paketleme görevlisi", "kovan bakımcısı"),
    "Botanik bahçesi": ("bahçıvan", "ziyaretçi", "bitki bilimci", "sera görevlisi"),
    "Buz pateni pisti": ("pist görevlisi", "patenci", "paten eğitmeni", "buz bakım görevlisi"),
    "Planetaryum": ("gökbilim anlatıcısı", "ziyaretçi", "projeksiyon teknisyeni", "öğretmen"),
    "Tiyatro kulisi": ("sahne amiri", "oyuncu", "kostüm sorumlusu", "ışık teknisyeni"),
    "Liman": ("liman işçisi", "gemi kaptanı", "gümrük görevlisi", "yük planlayıcısı"),
    "Dağ evi": ("dağ rehberi", "konuk", "ev işletmecisi", "arama kurtarma gönüllüsü"),
    "Postane": ("posta görevlisi", "müşteri", "dağıtım görevlisi", "tasnif memuru"),
    "Akaryakıt istasyonu": ("pompacı", "sürücü", "kasiyer", "istasyon müdürü"),
    "Terzi dükkânı": ("terzi", "müşteri", "kalıpçı", "dikiş çırağı"),
    "Yüzme havuzu": ("can kurtaran", "yüzücü", "yüzme eğitmeni", "havuz bakım görevlisi"),
    "Otel resepsiyonu": ("resepsiyonist", "konuk", "kat görevlisi", "bagaj görevlisi"),
    "Üniversite laboratuvarı": ("laborant", "araştırmacı", "öğrenci", "güvenlik sorumlusu"),
    "Film seti": ("ışık teknisyeni", "oyuncu", "yönetmen", "set amiri"),
    "Otobüs terminali": ("peron görevlisi", "yolcu", "otobüs şoförü", "bilet görevlisi"),
    "Kamp alanı": ("kamp sorumlusu", "kampçı", "doğa rehberi", "malzeme görevlisi"),
    "Balıkçı barınağı": ("balıkçı", "tekne sahibi", "ağ tamircisi", "barınak görevlisi"),
    "Oyuncak atölyesi": ("oyuncak ustası", "tasarımcı", "boyama görevlisi", "kalite kontrolcü"),
    "Gözlemevi": ("gözlemci", "astronom", "teleskop teknisyeni", "gece ziyaretçisi"),
    "Kayak merkezi": ("kayak eğitmeni", "kayakçı", "telesiyej görevlisi", "pist bakım görevlisi"),
    "Matbaa": ("baskı ustası", "grafik tasarımcı", "kâğıt kesim görevlisi", "sipariş sahibi"),
    "Çay bahçesi": ("çaycı", "müşteri", "servis görevlisi", "bahçe işletmecisi"),
    "Antikacı": ("antika uzmanı", "koleksiyoncu", "restorasyon ustası", "müşteri"),
    "Fotoğraf stüdyosu": ("fotoğrafçı", "müşteri", "ışık asistanı", "rötuş uzmanı"),
}


def _time(now):
    return time.time() if now is None else float(now)


def _next(state, now):
    result = deepcopy(state)
    result["version"] += 1
    result["last_activity_at"] = _time(now)
    return result


def _player(state, user_id):
    player = next((p for p in state["players"] if p["id"] == int(user_id) and p["active"]), None)
    if player is None:
        raise ValueError("spy_not_player")
    return player


def _phase(state, phase, now):
    if state["phase"] != phase:
        raise ValueError("spy_wrong_phase")
    if phase in {"question", "discussion", "vote"} and state.get("deadline") is None:
        raise ValueError("spy_waiting_panel")
    if state.get("deadline") is not None and _time(now) >= state["deadline"]:
        raise ValueError("spy_expired")


def new_lobby(*, chat_id, thread_id, owner_id, owner_name, round_id=None, now=None):
    moment = _time(now)
    return {"round_id": round_id or uuid.uuid4().hex[:12], "chat_id": int(chat_id),
            "thread_id": thread_id, "owner_id": int(owner_id), "phase": "lobby",
            "players": [{"id": int(owner_id), "name": str(owner_name)[:60], "active": True,
                         "delivered": False, "confirmed": False}], "version": 0,
            "created_at": moment, "last_activity_at": moment, "deadline": moment + LOBBY_SECONDS,
            "spy_id": None, "location": None, "role": None, "assigned_roles": {},
            "question_order": [],
            "turn_index": 0, "target_id": None, "question_pending": False,
            "question_count": 0, "vote_round": 0, "votes": {}, "candidates": [],
            "guess_used": False, "winner": None, "reason": None, "score_pending": False}


def join(state, *, user_id, user_name, now=None):
    _phase(state, "lobby", now)
    if any(p["id"] == int(user_id) for p in state["players"]):
        raise ValueError("spy_already_joined")
    if len(state["players"]) >= MAX_PLAYERS:
        raise ValueError("spy_full")
    result = _next(state, now)
    result["players"].append({"id": int(user_id), "name": str(user_name)[:60],
                              "active": True, "delivered": False, "confirmed": False})
    return result


def start(state, *, user_id, spy_id=None, location_index=None, first_index=None,
          rng=None, now=None):
    _phase(state, "lobby", now)
    if int(user_id) != state["owner_id"]:
        raise ValueError("spy_not_owner")
    roster = [p["id"] for p in state["players"] if p["active"]]
    if not MIN_PLAYERS <= len(roster) <= MAX_PLAYERS:
        raise ValueError("spy_player_count")
    randomizer = rng if rng is not None else secrets.SystemRandom()
    if spy_id is None:
        spy_id = randomizer.choice(roster)
    if int(spy_id) not in roster:
        raise ValueError("spy_invalid_spy")
    index = randomizer.randrange(len(LOCATIONS)) if location_index is None else int(location_index)
    if not 0 <= index < len(LOCATIONS):
        raise ValueError("spy_invalid_location")
    first = randomizer.randrange(len(roster)) if first_index is None else int(first_index)
    if not 0 <= first < len(roster):
        raise ValueError("spy_invalid_first")
    result = _next(state, now)
    roles = list(LOCATION_ROLES[LOCATIONS[index][0]])
    randomizer.shuffle(roles)
    innocents = [pid for pid in roster if pid != int(spy_id)]
    assigned_roles = {str(pid): roles[position % len(roles)]
                      for position, pid in enumerate(innocents)}
    result.update(phase="delivery", spy_id=int(spy_id), location=LOCATIONS[index][0],
                  role=LOCATIONS[index][1], assigned_roles=assigned_roles,
                  question_order=roster[first:] + roster[:first],
                  deadline=_time(now) + DELIVERY_SECONDS)
    return result


def private_role(state, *, user_id):
    player = _player(state, user_id)
    if state["phase"] != "delivery" or player["confirmed"]:
        raise ValueError("spy_role_closed")
    return {"spy": int(user_id) == state["spy_id"],
            "location": None if int(user_id) == state["spy_id"] else state["location"],
            "role": None if int(user_id) == state["spy_id"] else
                    state.get("assigned_roles", {}).get(str(user_id), state["role"])}


def delivered(state, *, user_id, now=None):
    _phase(state, "delivery", now)
    if _player(state, user_id)["delivered"]:
        raise ValueError("spy_already_delivered")
    result = _next(state, now)
    _player(result, user_id)["delivered"] = True
    return result


def confirm(state, *, user_id, now=None):
    _phase(state, "delivery", now)
    player = _player(state, user_id)
    if not player["delivered"]:
        raise ValueError("spy_not_delivered")
    if player["confirmed"]:
        raise ValueError("spy_already_confirmed")
    result = _next(state, now)
    _player(result, user_id)["confirmed"] = True
    if all(p["confirmed"] for p in result["players"] if p["active"]):
        result.update(phase="question", deadline=None, question_started_at=None)
    return result


def question_target(state, *, user_id, target_id, now=None):
    _phase(state, "question", now)
    if int(user_id) != state["question_order"][state["turn_index"]]:
        raise ValueError("spy_not_asker")
    if state["target_id"] is not None or int(target_id) == int(user_id):
        raise ValueError("spy_invalid_target")
    _player(state, target_id)
    result = _next(state, now)
    result["target_id"] = int(target_id)
    result["target_chosen_at"] = _time(now)
    return result


def group_text(state, *, user_id, text, now=None):
    _phase(state, "question", now)
    content = str(text).strip()
    if not 3 <= len(content) <= 500:
        raise ValueError("spy_invalid_text")
    asker = state["question_order"][state["turn_index"]]
    if state["target_id"] is None:
        raise ValueError("spy_target_required")
    expected = state["target_id"] if state["question_pending"] else asker
    if int(user_id) != expected:
        raise ValueError("spy_not_turn")
    result = _next(state, now)
    if not state["question_pending"]:
        result["question_pending"] = True
    else:
        result["question_pending"] = False
        result["target_id"] = None
        result["turn_index"] += 1
        result["question_count"] += 1
        if result["turn_index"] >= len(result["question_order"]):
            result.update(phase="discussion", deadline=None)
        else:
            result["deadline"] = _time(now) + TURN_SECONDS
            result["question_started_at"] = _time(now)
    return result


def begin_vote(state, *, now=None):
    if state["phase"] != "discussion" or state["deadline"] is None or _time(now) < state["deadline"]:
        raise ValueError("spy_not_expired")
    result = _next(state, now)
    result.update(phase="vote", vote_round=1, votes={},
                  candidates=[p["id"] for p in state["players"] if p["active"]],
                  deadline=None)
    return result


def vote(state, *, user_id, target_id, now=None):
    _phase(state, "vote", now)
    _player(state, user_id)
    if str(user_id) in state["votes"]:
        raise ValueError("spy_already_voted")
    if int(target_id) == int(user_id) or int(target_id) not in state["candidates"]:
        raise ValueError("spy_invalid_vote")
    _player(state, target_id)
    result = _next(state, now)
    result["votes"][str(user_id)] = int(target_id)
    return result


def _finish(state, winner, reason, now):
    result = _next(state, now)
    result.update(phase="finished", winner=winner, reason=reason, deadline=None,
                  score_pending=True)
    return result


def close_vote(state, *, now=None):
    if state["phase"] != "vote":
        raise ValueError("spy_wrong_phase")
    if state["deadline"] is None:
        raise ValueError("spy_waiting_panel")
    active = [p["id"] for p in state["players"] if p["active"]]
    if len(state["votes"]) < len(active) and _time(now) < state["deadline"]:
        raise ValueError("spy_vote_open")
    counts = {candidate: list(state["votes"].values()).count(candidate)
              for candidate in state["candidates"]}
    top = max(counts.values(), default=0)
    leaders = [candidate for candidate, count in counts.items() if count == top]
    if len(leaders) != 1:
        if state["vote_round"] == 1:
            result = _next(state, now)
            result.update(vote_round=2, votes={}, candidates=leaders,
                          deadline=None)
            return result
        return _finish(state, "spy", "vote_tie", now)
    # A strict majority of active voters is necessary to convict a suspect.
    if top <= len(active) // 2:
        if state["vote_round"] == 1:
            result = _next(state, now)
            result.update(vote_round=2, votes={}, candidates=leaders,
                          deadline=None)
            return result
        return _finish(state, "spy", "no_majority", now)
    return _finish(state, "group" if leaders[0] == state["spy_id"] else "spy",
                   "spy_found" if leaders[0] == state["spy_id"] else "innocent_accused", now)


def guess(state, *, user_id, location, now=None):
    if state["phase"] not in {"question", "discussion", "vote"}:
        raise ValueError("spy_wrong_phase")
    _phase(state, state["phase"], now)
    if int(user_id) != state["spy_id"] or state["guess_used"]:
        raise ValueError("spy_guess_forbidden")
    # This answer is never returned to the group renderer until the game ends.
    right = str(location).casefold().strip() == state["location"].casefold()
    result = _finish(state, "spy" if right else "group", "spy_guess", now)
    result["guess_used"] = True
    return result


def leave(state, *, user_id, now=None):
    _player(state, user_id)
    if state["phase"] in {"finished", "cancelled"}:
        raise ValueError("spy_wrong_phase")
    result = _next(state, now)
    _player(result, user_id)["active"] = False
    if state["phase"] == "lobby":
        if user_id == state["owner_id"]:
            result.update(phase="cancelled", reason="owner_left", deadline=None)
        return result
    if user_id == state["spy_id"]:
        result.update(phase="finished", winner="group", reason="spy_left",
                      deadline=None, score_pending=True)
        return result
    if sum(p["active"] for p in result["players"]) < MIN_PLAYERS:
        result.update(phase="cancelled", reason="too_few_players", deadline=None)
        return result
    if state["phase"] == "delivery":
        return result
    if state["phase"] == "question":
        removed_index = result["question_order"].index(user_id)
        result["question_order"] = [pid for pid in result["question_order"] if pid != user_id]
        if removed_index < result["turn_index"]:
            result["turn_index"] -= 1
        if removed_index == state["turn_index"] or result["target_id"] == user_id:
            result["target_id"] = None
            result["question_pending"] = False
            result["deadline"] = None
            result["question_started_at"] = None
            result["input_floor"] = None
            result.pop("target_chosen_at", None)
        if result["turn_index"] >= len(result["question_order"]):
            result.update(phase="discussion", deadline=None)
    if state["phase"] == "vote":
        result["votes"].pop(str(user_id), None)
        result["candidates"] = [pid for pid in result["candidates"] if pid != user_id]
        result["votes"] = {voter: target for voter, target in result["votes"].items()
                           if target != user_id}
    return result


def cancel(state, *, user_id, moderator=False, now=None):
    if state["phase"] in {"finished", "cancelled"}:
        raise ValueError("spy_wrong_phase")
    if int(user_id) != state["owner_id"] and not moderator:
        raise ValueError("spy_not_owner")
    result = _next(state, now)
    result.update(phase="cancelled", reason="manual_cancel", deadline=None)
    return result


def expire(state, *, now=None):
    if state["deadline"] is None or _time(now) < state["deadline"]:
        raise ValueError("spy_not_expired")
    if state["phase"] in {"lobby", "delivery"}:
        result = _next(state, now)
        result.update(phase="cancelled", reason="readiness_timeout", deadline=None)
        return result
    if state["phase"] == "question":
        result = _next(state, now)
        result.update(phase="discussion", deadline=None)
        return result
    if state["phase"] == "discussion":
        return begin_vote(state, now=now)
    if state["phase"] == "vote":
        return close_vote(state, now=now)
    raise ValueError("spy_wrong_phase")


def public_state(state):
    fields = ("round_id", "chat_id", "thread_id", "owner_id", "phase", "players",
              "version", "created_at", "deadline", "question_order", "turn_index",
              "target_id", "question_pending", "question_count", "vote_round",
              "candidates", "winner", "reason", "score_pending", "panel_message_id")
    view = {key: deepcopy(state[key]) for key in fields if key in state}
    view["players"] = [
        {**{key: deepcopy(player[key]) for key in ("id", "name", "active", "delivered", "confirmed") if key in player},
         "ready": player["id"] in state.get("dm_ready", [])}
        for player in state["players"]
    ]
    view["vote_count"] = len(state.get("votes", {}))
    if state["phase"] == "finished":
        view["location"] = state["location"]
        view["spy_id"] = state["spy_id"]
    return view
