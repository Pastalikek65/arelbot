"""Pure, versioned rules for a two-team Turkish Tabu match.

The caller owns persistence, Telegram permissions, DM delivery and an action
ledger. Every accepted mutation returns a new state with version increased by
one, ready for compare-and-swap storage.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import re
import time
import uuid

from .tabu_cards import TABU_CARDS
from .turkish_words import canonical_word


TEAMS = ("red", "blue")
CARD_SECONDS = 60
CARD_READY_SECONDS = 120
APPEAL_SECONDS = 15
JUDGE_SECONDS = 45
LOBBY_IDLE_SECONDS = 20 * 60
MAX_PASSES = 2
MAX_OVERTIME_PAIRS = 2
_WORDS = re.compile(r"[^\W\d_]+", re.UNICODE)
_SUFFIXES = frozenset((
    "lar", "ler", "ı", "i", "u", "ü", "a", "e", "da", "de", "ta", "te",
    "dan", "den", "tan", "ten", "ın", "in", "un", "ün", "nın", "nin",
    "nun", "nün", "ya", "ye", "yı", "yi", "yu", "yü", "sı", "si",
    "su", "sü", "ları", "leri", "ların", "lerin", "larda", "lerde",
    "lardan", "lerden", "larını", "lerini", "larımız", "lerimiz",
    "ım", "im", "um", "üm", "ımız", "imiz", "umuz", "ümüz",
    "ına", "ine", "una", "üne", "m", "n", "mız", "miz", "muz", "müz",
))
_VOICE = {"p": ("b",), "ç": ("c",), "t": ("d",), "k": ("ğ", "g")}
_VOWELS = frozenset("aeıioöuü")
_GLUED_TAILS = frozenset(("gibi", "ile", "olan", "kadar", "şeklinde", "tarzı"))


def _now(now: float | None) -> float:
    return time.time() if now is None else float(now)


def _copy(state: dict, now: float | None = None) -> dict:
    result = deepcopy(state)
    result["version"] = int(state["version"]) + 1
    if now is not None:
        result["last_activity_at"] = float(now)
    return result


def _player(state: dict, user_id: int) -> dict:
    player = next((p for p in state["players"] if int(p["id"]) == int(user_id)), None)
    if player is None or not player.get("active", True):
        raise ValueError("tabu_not_player")
    return player


def _team_players(state: dict, team: str) -> list[dict]:
    return [p for p in state["players"] if p["team"] == team and p.get("active", True)]


def _card(state: dict, card_id: str) -> None:
    if str(card_id) != str(state.get("card_id")):
        raise ValueError("tabu_stale_card")


def _active(state: dict, card_id: str, now: float) -> None:
    _card(state, card_id)
    if state["phase"] != "active":
        raise ValueError("tabu_not_active")
    if now >= float(state["deadline"]):
        raise ValueError("tabu_expired")


def _tokens(value: object) -> list[str]:
    return [canonical_word(part) for part in _WORDS.findall(str(value or ""))]


def _deck_order(seed: int) -> list[int]:
    """Stable keyed permutation; a revealed card does not expose the next."""
    key = str(seed).encode("ascii")
    return sorted(
        range(len(TABU_CARDS)),
        key=lambda index: hashlib.sha256(
            key + b":" + TABU_CARDS[index]["id"].encode("ascii")
        ).digest(),
    )


def _inflected(token: str, root: str) -> bool:
    if token == root or len(root) < 3:
        return token == root
    stems = {root}
    if root[-1] in _VOICE:
        stems.update(root[:-1] + letter for letter in _VOICE[root[-1]])
    for stem in stems:
        if token.startswith(stem):
            ending = token[len(stem):]
            if ending in _SUFFIXES and (stem == root or ending[0] in _VOWELS):
                return True
            if stem == root and ending in _GLUED_TAILS:
                return True
    return False


def expression_match(text: object, expression: str) -> str | None:
    """Return exact/inflected/spelled, never guess a semantic synonym.

    Suffix policy is deliberately finite: direct common nominal case, plural
    and possessive endings only. No stemming, substring or phonetic matching.
    A spelling is each letter as its own token (spaces, dots or hyphens).
    """
    words = _tokens(text)
    target = _tokens(expression)
    if not target:
        return None
    length = len(target)
    for pos in range(len(words) - length + 1):
        candidate = words[pos:pos + length]
        if candidate == target:
            return "exact"
        if candidate[:-1] == target[:-1] and _inflected(candidate[-1], target[-1]):
            return "inflected"
    letters = list("".join(target))
    if len(letters) >= 3:
        for pos in range(len(words) - len(letters) + 1):
            if words[pos:pos + len(letters)] == letters:
                return "spelled"
    return None


def new_lobby(*, chat_id: int, thread_id: int | None, owner_id: int,
              owner_name: str, round_id: str | None = None,
              deck_seed: int = 0, now: float | None = None) -> dict:
    if not owner_id:
        raise ValueError("tabu_invalid_owner")
    if int(deck_seed) < 0:
        raise ValueError("tabu_invalid_deck_seed")
    moment = _now(now)
    return {
        "round_id": str(round_id or uuid.uuid4().hex[:16]), "chat_id": int(chat_id),
        "thread_id": int(thread_id) if thread_id is not None else None,
        "owner_id": int(owner_id), "players": [{"id": int(owner_id),
        "name": str(owner_name)[:60], "team": "red", "ready": False, "active": True}],
        "phase": "lobby", "version": 0, "created_at": moment,
        "last_activity_at": moment, "deadline": None, "team_scores": {"red": 0, "blue": 0},
        "cards_played": {"red": 0, "blue": 0}, "passes_left": {"red": 2, "blue": 2},
        "base_quota": 0, "quota": 0, "overtime_pairs": 0,
        # Decimal text preserves all 128 random bits through MongoDB BSON,
        # whose integer values are limited to signed 64 bits.
        "deck_seed": str(int(deck_seed)), "deck_order": _deck_order(int(deck_seed)),
        "card_seq": 0,
        "card_id": None, "card": None, "narrator_id": None,
        "delivered": False, "active_since": None,
        "pending_outcome": None, "pending_reason": None, "pending_evidence": None,
        "appeal_until": None, "appealed_by": None, "judge_deadline": None,
        "judge_decision": None, "winner_team": None, "score_pending": False,
    }


def join(state: dict, *, user_id: int, user_name: str, team: str,
         now: float | None = None) -> dict:
    if state["phase"] != "lobby":
        raise ValueError("tabu_lobby_closed")
    if team not in TEAMS:
        raise ValueError("tabu_invalid_team")
    if any(int(p["id"]) == int(user_id) for p in state["players"]):
        raise ValueError("tabu_already_joined")
    if len(_team_players(state, team)) >= 10:
        raise ValueError("tabu_team_full")
    result = _copy(state, _now(now))
    result["players"].append({"id": int(user_id), "name": str(user_name)[:60],
                              "team": team, "ready": False, "active": True})
    return result


def set_ready(state: dict, *, user_id: int, ready: bool = True,
              now: float | None = None) -> dict:
    if state["phase"] != "lobby":
        raise ValueError("tabu_lobby_closed")
    _player(state, user_id)
    result = _copy(state, _now(now))
    _player(result, user_id)["ready"] = bool(ready)
    return result


def change_team(state: dict, *, user_id: int, team: str,
                now: float | None = None) -> dict:
    if state["phase"] != "lobby":
        raise ValueError("tabu_lobby_closed")
    if team not in TEAMS:
        raise ValueError("tabu_invalid_team")
    player = _player(state, user_id)
    if player["team"] != team and len(_team_players(state, team)) >= 10:
        raise ValueError("tabu_team_full")
    result = _copy(state, _now(now))
    _player(result, user_id)["team"] = team
    _player(result, user_id)["ready"] = False
    return result


def _open_card(result: dict) -> dict:
    # A replacement for a departing narrator does not spend the team's turn.
    team = "red" if result["cards_played"]["red"] <= result["cards_played"]["blue"] else "blue"
    roster = _team_players(result, team)
    if len(roster) < 2:
        result["phase"] = "cancelled"
        result["score_pending"] = False
        return result
    narrator = roster[result["cards_played"][team] % len(roster)]
    if result["card_seq"] >= len(result["deck_order"]):
        raise ValueError("tabu_deck_exhausted")
    bank_index = result["deck_order"][result["card_seq"]]
    card = TABU_CARDS[bank_index]
    result["phase"] = "card_delivery"
    identity = f'{result["deck_seed"]}:{result["round_id"]}:{result["card_seq"] + 1}'
    result["card_id"] = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    result["card"] = deepcopy(card)
    result["narrator_id"] = narrator["id"]
    result["active_team"] = team
    result["delivered"] = False
    result["deadline"] = None
    result["active_since"] = None
    result["pending_outcome"] = None
    result["pending_reason"] = None
    result["pending_evidence"] = None
    result["appeal_until"] = None
    result["appealed_by"] = None
    result["judge_deadline"] = None
    result["judge_decision"] = None
    result["card_seq"] += 1
    return result


def start(state: dict, *, user_id: int, now: float | None = None) -> dict:
    if state["phase"] != "lobby":
        raise ValueError("tabu_lobby_closed")
    if int(state["owner_id"]) != int(user_id):
        raise ValueError("tabu_not_owner")
    sizes = [len(_team_players(state, team)) for team in TEAMS]
    if min(sizes) < 2 or max(sizes) > 10 or abs(sizes[0] - sizes[1]) > 1:
        raise ValueError("tabu_unbalanced_teams")
    if not all(p["ready"] for p in state["players"] if p.get("active", True)):
        raise ValueError("tabu_not_ready")
    result = _copy(state, _now(now))
    result["base_quota"] = max(sizes) * 2
    result["quota"] = result["base_quota"]
    return _open_card(result)


def private_card(state: dict, *, user_id: int, card_id: str) -> dict:
    _card(state, card_id)
    if int(user_id) != int(state.get("narrator_id") or 0):
        raise ValueError("tabu_not_narrator")
    if state["phase"] not in {"card_delivery", "active"}:
        raise ValueError("tabu_card_closed")
    return deepcopy(state["card"])


_PUBLIC_FIELDS = frozenset((
    "round_id", "chat_id", "thread_id", "owner_id", "players", "phase",
    "version", "created_at", "last_activity_at", "deadline", "team_scores",
    "cards_played", "passes_left", "base_quota", "quota", "overtime_pairs",
    "card_seq", "card_id", "narrator_id", "active_team", "delivered",
    "active_since",
    "pending_outcome", "appeal_until", "appealed_by", "judge_deadline",
    "judge_decision", "winner_team", "score_pending",
))


def public_state(state: dict) -> dict:
    """Allowlisted group-safe view; callers cannot accidentally render card data."""
    view = {key: deepcopy(value) for key, value in state.items() if key in _PUBLIC_FIELDS}
    if state.get("phase") in {"appeal", "adjudication", "finished", "cancelled"}:
        reason = str(state.get("pending_reason") or "")
        # Evidence can contain the entire private target or forbidden phrase.
        # A group panel only needs the objective outcome, never the clue.
        view["pending_reason"] = reason.split(":", 1)[0]
    return view


def mark_card_delivered(state: dict, *, user_id: int, card_id: str,
                        now: float | None = None) -> dict:
    _card(state, card_id)
    if state["phase"] != "card_delivery":
        raise ValueError("tabu_not_delivery")
    if int(user_id) != int(state["narrator_id"]):
        raise ValueError("tabu_not_narrator")
    moment = _now(now)
    if moment >= state["last_activity_at"] + CARD_READY_SECONDS:
        raise ValueError("tabu_card_ready_expired")
    if state["delivered"]:
        raise ValueError("tabu_already_delivered")
    result = _copy(state, moment)
    result["delivered"] = True
    return result


def ack_card(state: dict, *, user_id: int, card_id: str,
             now: float | None = None) -> dict:
    _card(state, card_id)
    if state["phase"] != "card_delivery":
        raise ValueError("tabu_not_delivery")
    if int(user_id) != int(state["narrator_id"]):
        raise ValueError("tabu_not_narrator")
    if not state["delivered"]:
        raise ValueError("tabu_not_delivered")
    moment = _now(now)
    if moment >= state["last_activity_at"] + CARD_READY_SECONDS:
        raise ValueError("tabu_card_ready_expired")
    result = _copy(state, moment)
    result["phase"] = "active"
    result["active_since"] = moment
    result["deadline"] = moment + CARD_SECONDS
    return result


def _close(state: dict, outcome: str, reason: str, now: float,
           evidence: str | None = None) -> dict:
    result = _copy(state, now)
    result["phase"] = "appeal"
    result["deadline"] = None
    result["pending_outcome"] = outcome
    result["pending_reason"] = reason
    # Group messages remain in Telegram for a neutral judge to inspect; do not
    # retain player prose or a private card phrase in durable game state.
    result["pending_evidence"] = None
    result["appeal_until"] = now + APPEAL_SECONDS
    return result


def submit_clue(state: dict, *, user_id: int, card_id: str, text: str,
                now: float | None = None) -> dict:
    moment = _now(now)
    _active(state, card_id, moment)
    if int(user_id) != int(state["narrator_id"]):
        raise ValueError("tabu_not_narrator")
    card = state["card"]
    for expression in (card["target"], *card.get("aliases", ()), *card["forbidden"]):
        kind = expression_match(text, expression)
        if kind:
            return _close(state, "forbidden", f"{kind}:{expression}", moment, text)
    return _copy(state, moment)


def submit_guess(state: dict, *, user_id: int, card_id: str, text: str,
                 now: float | None = None) -> dict:
    moment = _now(now)
    _active(state, card_id, moment)
    player = _player(state, user_id)
    if player["team"] != state["active_team"] or int(user_id) == int(state["narrator_id"]):
        raise ValueError("tabu_not_guesser")
    # A guess must be the target phrase, not a sentence containing it.
    if any(_tokens(text) == _tokens(answer) for answer in
           (state["card"]["target"], *state["card"].get("aliases", ()))):
        return _close(state, "correct", "exact_target", moment, text)
    return _copy(state, moment)


def pass_card(state: dict, *, user_id: int, card_id: str,
              now: float | None = None) -> dict:
    moment = _now(now)
    _active(state, card_id, moment)
    if int(user_id) != int(state["narrator_id"]):
        raise ValueError("tabu_not_narrator")
    if state["passes_left"][state["active_team"]] <= 0:
        raise ValueError("tabu_no_passes")
    result = _close(state, "pass", "narrator_pass", moment)
    result["passes_left"][state["active_team"]] -= 1
    return result


def expire(state: dict, *, now: float | None = None) -> dict:
    moment = _now(now)
    if state["phase"] == "lobby" and moment >= state["last_activity_at"] + LOBBY_IDLE_SECONDS:
        result = _copy(state, moment)
        result["phase"] = "cancelled"
        return result
    if state["phase"] == "card_delivery" and moment >= state["last_activity_at"] + CARD_READY_SECONDS:
        result = _copy(state, moment)
        result["phase"] = "cancelled"
        result["pending_reason"] = "card_ready_timeout"
        result["score_pending"] = False
        result["card"] = None
        result["deadline"] = None
        return result
    if state["phase"] == "active" and moment >= state["deadline"]:
        # Use the persisted deadline, never the restart time, as appeal origin.
        return _close(state, "timeout", "card_deadline", float(state["deadline"]))
    raise ValueError("tabu_not_expired")


def appeal(state: dict, *, user_id: int, card_id: str,
           now: float | None = None) -> dict:
    moment = _now(now)
    _card(state, card_id)
    if state["phase"] != "appeal" or moment >= state["appeal_until"]:
        raise ValueError("tabu_appeal_closed")
    if int(user_id) == int(state["narrator_id"]):
        raise ValueError("tabu_narrator_cannot_appeal")
    _player(state, user_id)
    if state["appealed_by"] is not None:
        raise ValueError("tabu_already_appealed")
    result = _copy(state, moment)
    result["phase"] = "adjudication"
    result["appealed_by"] = int(user_id)
    result["judge_deadline"] = moment + JUDGE_SECONDS
    return result


def decide_appeal(state: dict, *, user_id: int, card_id: str,
                  decision: str, is_admin: bool, now: float | None = None) -> dict:
    moment = _now(now)
    _card(state, card_id)
    if state["phase"] != "adjudication" or moment >= state["judge_deadline"]:
        raise ValueError("tabu_judge_closed")
    if not is_admin or any(int(p["id"]) == int(user_id) for p in state["players"]):
        raise ValueError("tabu_not_neutral_judge")
    if decision not in {"uphold", "neutral"}:
        raise ValueError("tabu_invalid_decision")
    if state["judge_decision"] is not None:
        raise ValueError("tabu_already_decided")
    result = _copy(state, moment)
    result["judge_decision"] = decision
    return result


def advance(state: dict, *, now: float | None = None) -> dict:
    moment = _now(now)
    if state["phase"] == "appeal":
        if moment < state["appeal_until"]:
            raise ValueError("tabu_appeal_open")
    elif state["phase"] == "adjudication":
        if state["judge_decision"] is None and moment < state["judge_deadline"]:
            raise ValueError("tabu_judge_open")
    else:
        raise ValueError("tabu_card_not_closed")
    result = _copy(state, moment)
    team = result["active_team"]
    outcome = result["pending_outcome"]
    if result["judge_decision"] == "neutral":
        outcome = "neutral"
    if outcome == "correct":
        result["team_scores"][team] += 1
    elif outcome == "forbidden":
        result["team_scores"][team] -= 1
    if result["pending_reason"] != "narrator_left":
        result["cards_played"][team] += 1
    if all(result["cards_played"][t] >= result["quota"] for t in TEAMS):
        red, blue = result["team_scores"]["red"], result["team_scores"]["blue"]
        if red == blue and result["overtime_pairs"] < MAX_OVERTIME_PAIRS:
            result["overtime_pairs"] += 1
            result["quota"] += 1
        else:
            result["phase"] = "finished"
            result["winner_team"] = "red" if red > blue else "blue" if blue > red else None
            result["score_pending"] = True
            result["card"] = None
            result["deadline"] = None
            return result
    return _open_card(result)


def leave(state: dict, *, user_id: int, now: float | None = None) -> dict:
    player = _player(state, user_id)
    moment = _now(now)
    result = _copy(state, moment)
    if state["phase"] == "lobby":
        result["players"] = [p for p in result["players"] if p["id"] != int(user_id)]
        if not result["players"]:
            result["phase"] = "cancelled"
    elif state["phase"] in {"card_delivery", "active", "appeal", "adjudication"}:
        _player(result, user_id)["active"] = False
        if len(_team_players(result, player["team"])) < 2:
            result["phase"] = "cancelled"
            result["score_pending"] = False
            result["deadline"] = None
        elif state["phase"] == "card_delivery" and int(user_id) == int(state["narrator_id"]):
            # Preserve the team's card opportunity; hand the same card to the
            # next active teammate, with no clock or new card draw.
            roster = _team_players(result, player["team"])
            result["narrator_id"] = roster[result["cards_played"][player["team"]] % len(roster)]["id"]
            result["delivered"] = False
        elif state["phase"] == "active" and int(user_id) == int(state["narrator_id"]):
            result = _close(result, "neutral", "narrator_left", moment)
            result["version"] = state["version"] + 1
    else:
        raise ValueError("tabu_not_running")
    if result["phase"] != "cancelled" and int(state["owner_id"]) == int(user_id):
        result["owner_id"] = int(result["players"][0]["id"] if state["phase"] == "lobby"
                                 else next(p["id"] for p in result["players"] if p.get("active", True)))
    return result


def cancel(state: dict, *, user_id: int, moderator: bool = False,
           now: float | None = None) -> dict:
    if not moderator and int(user_id) != int(state["owner_id"]):
        raise ValueError("tabu_not_owner")
    if state["phase"] in {"finished", "cancelled"}:
        raise ValueError("tabu_already_closed")
    result = _copy(state, _now(now))
    result["phase"] = "cancelled"
    result["score_pending"] = False
    result["deadline"] = None
    result["card"] = None
    return result
