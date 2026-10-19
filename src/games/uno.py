"""Deterministic, transport-neutral UNO rules for Arelbot.

The upstream project is used only as a behaviour reference.  This module is
an independent implementation so Telegram and a future Mini App can share
the same state transitions and replay tests.
"""

from __future__ import annotations

from copy import deepcopy
import random
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping


COLORS = ("red", "yellow", "green", "blue")
COLOR_LABELS = {"red": "Kırmızı", "yellow": "Sarı", "green": "Yeşil", "blue": "Mavi"}
SPECIAL_LABELS = {"skip": "Pas", "reverse": "Yön değiştir", "draw2": "+2", "wild": "Renk seç", "draw4": "+4"}
VALID_TYPES = frozenset({"0", *[str(n) for n in range(1, 10)], "skip", "reverse", "draw2", "wild", "draw4"})
MIN_PLAYERS = 2
MAX_PLAYERS = 8
HAND_SIZE = 7
DEFAULT_TURN_SECONDS = 60


class UnoError(ValueError):
    """Stable public error code for handler and Mini App adapters."""

    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _card(card_id: str, color: str, kind: str) -> dict[str, str]:
    return {"id": str(card_id), "color": str(color), "kind": str(kind)}


def build_deck(rng: random.Random | None = None) -> list[dict[str, str]]:
    """Create the classic 108-card deck used by the reference game."""

    deck: list[dict[str, str]] = []
    for color in COLORS:
        deck.append(_card(f"{color[0]}0-0", color, "0"))
        for number in range(1, 10):
            for copy in range(2):
                deck.append(_card(f"{color[0]}{number}-{copy}", color, str(number)))
        for kind in ("skip", "reverse", "draw2"):
            for copy in range(2):
                deck.append(_card(f"{color[0]}{kind[:2]}-{copy}", color, kind))
    for kind in ("wild", "draw4"):
        for copy in range(4):
            deck.append(_card(f"w{kind[:2]}-{copy}", "black", kind))
    (rng or random.Random()).shuffle(deck)
    return deck


def _player(user_id: int, name: str) -> dict[str, Any]:
    return {
        "user_id": int(user_id),
        "name": str(name or "oyuncu")[:80],
        "hand": [],
        "ready": False,
        "uno_called": False,
        "drawn_card_id": None,
        "pending_draw": 0,
        "score": 0,
    }


def new_uno_state(*, chat_id: int, owner_id: int, owner_name: str, thread_id: int | None = None, turn_seconds: int = DEFAULT_TURN_SECONDS) -> dict[str, Any]:
    if int(chat_id) == 0 or int(owner_id) <= 0:
        raise UnoError("UNO_INVALID_SCOPE")
    return {
        "schema_version": 1,
        "game_key": "uno",
        "status": "lobby",
        "chat_id": int(chat_id),
        "thread_id": int(thread_id) if thread_id is not None else None,
        "owner_id": int(owner_id),
        "round_id": uuid.uuid4().hex[:16],
        "players": [_player(owner_id, owner_name)],
        "min_players": MIN_PLAYERS,
        "max_players": MAX_PLAYERS,
        "turn_seconds": max(15, min(300, int(turn_seconds))),
        "turn_expires_at": None,
        "deck": [],
        "discard": [],
        "current_player_id": None,
        "direction": 1,
        "current_color": None,
        "drawn_card_id": None,
        "awaiting_color": False,
        "last_action_id": None,
        "processed_actions": [],
        "winner_id": None,
        "prompt_message_id": None,
        "revision": 0,
        "round_number": 0,
        "match_target": 500,
        "match_complete": False,
        "last_activity_at": _utc_timestamp(),
        "uno_vulnerable_id": None,
    }


def validate_state(state: Mapping[str, Any]) -> bool:
    if not isinstance(state, Mapping) or state.get("game_key") != "uno":
        return False
    if str(state.get("status")) not in {"lobby", "playing", "finished", "cancelled", "expired"}:
        return False
    try:
        players = state.get("players") or []
        if not MIN_PLAYERS <= len(players) <= MAX_PLAYERS:
            # A lobby may contain only its owner until another player joins.
            if str(state.get("status")) != "lobby" or len(players) != 1:
                return False
        ids = [int(p.get("user_id")) for p in players]
        if len(ids) != len(set(ids)) or int(state.get("owner_id")) not in ids:
            return False
        if any(not isinstance(p.get("hand", []), list) for p in players):
            return False
    except (TypeError, ValueError, AttributeError):
        return False
    return True


def _clone(state: Mapping[str, Any]) -> dict[str, Any]:
    return deepcopy(dict(state))


def _utc_timestamp() -> float:
    return datetime.now(timezone.utc).timestamp()


def turn_expired(state: Mapping[str, Any], *, now: float | None = None) -> bool:
    """Return whether the active turn deadline has elapsed.

    Legacy snapshots do not have a deadline; they remain playable and receive
    one on the next accepted state transition.  This keeps old rounds
    restart-compatible while making new rounds bounded.
    """

    if str(state.get("status") or "") != "playing":
        return False
    raw = state.get("turn_expires_at")
    if raw in (None, ""):
        return False
    try:
        return float(now if now is not None else _utc_timestamp()) >= float(raw)
    except (TypeError, ValueError, OverflowError):
        return True


def _turn_seconds(state: Mapping[str, Any]) -> int:
    try:
        return max(15, min(300, int(state.get("turn_seconds", DEFAULT_TURN_SECONDS) or DEFAULT_TURN_SECONDS)))
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_TURN_SECONDS


def _refresh_turn_deadline(state: dict[str, Any]) -> None:
    state["turn_expires_at"] = _utc_timestamp() + _turn_seconds(state)


def timeout_turn(state: Mapping[str, Any]) -> dict[str, Any]:
    """Move a stalled game forward without ending everybody's match."""
    result = _clone(state)
    if not validate_state(result) or not turn_expired(result):
        raise UnoError("UNO_TURN_NOT_EXPIRED")
    player_id = int(result["current_player_id"])
    player = _find_player(result, player_id)
    result.pop("last_penalty", None)
    if result.get("awaiting_color"):
        counts = {color: sum(card.get("color") == color for card in player.get("hand", [])) for color in COLORS}
        result["current_color"] = max(COLORS, key=lambda color: counts[color])
        result["awaiting_color"] = False
        pending = int(result.get("pending_draw", 0) or 0)
        if pending:
            target_id = _next_player_id(result)
            _find_player(result, target_id)["hand"].extend(_draw(result, pending))
            result["last_penalty"] = {"user_id": target_id, "cards": pending}
            result["current_player_id"] = _next_player_id(result, steps=2)
        else:
            result["current_player_id"] = _next_player_id(result)
        result["pending_draw"] = 0
    else:
        if not player.get("drawn_card_id"):
            player["hand"].extend(_draw(result, 1))
        player["drawn_card_id"] = None
        result["drawn_card_id"] = None
        result["current_player_id"] = _next_player_id(result)
    result["last_timeout_user_id"] = player_id
    result["uno_vulnerable_id"] = None
    _refresh_turn_deadline(result)
    return result


def _ensure_turn_active(state: dict[str, Any]) -> None:
    if turn_expired(state):
        raise UnoError("UNO_TURN_EXPIRED")


def _find_player(state: dict[str, Any], user_id: int) -> dict[str, Any]:
    for player in state.get("players", []):
        if int(player.get("user_id", 0)) == int(user_id):
            return player
    raise UnoError("UNO_PLAYER_NOT_FOUND")


def _player_index(state: dict[str, Any], user_id: int) -> int:
    for index, player in enumerate(state.get("players", [])):
        if int(player.get("user_id", 0)) == int(user_id):
            return index
    raise UnoError("UNO_PLAYER_NOT_FOUND")


def _next_player_id(state: dict[str, Any], *, steps: int = 1) -> int:
    players = state["players"]
    index = _player_index(state, int(state["current_player_id"]))
    return int(players[(index + int(state.get("direction", 1)) * steps) % len(players)]["user_id"])


def _draw(state: dict[str, Any], amount: int = 1) -> list[dict[str, str]]:
    drawn: list[dict[str, str]] = []
    for _ in range(max(0, int(amount))):
        if not state.get("deck"):
            discard = state.get("discard", [])
            if len(discard) <= 1:
                break
            top = discard[-1]
            state["deck"] = list(reversed(discard[:-1]))
            random.SystemRandom().shuffle(state["deck"])
            state["discard"] = [top]
        if state.get("deck"):
            drawn.append(state["deck"].pop())
    return drawn


def _current_card(state: Mapping[str, Any]) -> dict[str, Any] | None:
    discard = state.get("discard") or []
    return discard[-1] if discard else None


def can_play(state: Mapping[str, Any], card: Mapping[str, Any], *, player_id: int | None = None) -> bool:
    current = _current_card(state)
    if not current:
        return False
    color = str(state.get("current_color") or current.get("color"))
    card_color = str(card.get("color"))
    kind = str(card.get("kind"))
    if kind not in VALID_TYPES or card_color not in {*COLORS, "black"}:
        return False
    if card_color == "black":
        if kind == "draw4" and player_id is not None:
            player = _find_player(_clone(state), int(player_id))
            if any(str(item.get("color")) == color for item in player.get("hand", [])):
                return False
        return True
    return card_color == color or kind == str(current.get("kind"))


def start_round(state: Mapping[str, Any], *, rng: random.Random | None = None) -> dict[str, Any]:
    result = _clone(state)
    if not validate_state(result):
        raise UnoError("UNO_INVALID_STATE")
    if result.get("status") not in {"lobby", "finished", "cancelled"}:
        raise UnoError("UNO_ALREADY_STARTED")
    if len(result.get("players", [])) < MIN_PLAYERS:
        raise UnoError("UNO_NEED_TWO_PLAYERS")
    randomizer = rng or random.Random()
    deck = build_deck(randomizer)
    for player in result["players"]:
        player["hand"] = [deck.pop() for _ in range(HAND_SIZE)]
        player["ready"] = False
        player["uno_called"] = False
        player["drawn_card_id"] = None
        player["pending_draw"] = 0
        player["score"] = int(player.get("score", 0))
    rejected: list[dict[str, str]] = []
    while deck:
        top = deck.pop()
        if top["color"] != "black" and top["kind"] not in {"skip", "reverse", "draw2"}:
            break
        rejected.append(top)
    else:
        raise UnoError("UNO_DECK_INVALID")
    # A setup card that cannot start a classic round is returned to the
    # draw pile instead of silently shrinking the 108-card deck.
    if rejected:
        deck.extend(rejected)
        randomizer.shuffle(deck)
    round_number = int(result.get("round_number", 0)) + 1
    result.update({
        "status": "playing", "deck": deck, "discard": [top],
        "current_player_id": int(result["players"][(round_number - 1) % len(result["players"])]["user_id"]),
        "direction": 1, "current_color": top["color"], "drawn_card_id": None,
        "awaiting_color": False, "winner_id": None, "processed_actions": [],
        "turn_expires_at": _utc_timestamp() + max(15, min(300, int(result.get("turn_seconds", DEFAULT_TURN_SECONDS) or DEFAULT_TURN_SECONDS))),
        "round_id": uuid.uuid4().hex[:16],
        "round_number": round_number, "match_complete": False,
        "last_activity_at": _utc_timestamp(), "uno_vulnerable_id": None,
        "last_penalty": None, "last_timeout_user_id": None,
    })
    return result


def join_player(state: Mapping[str, Any], *, user_id: int, name: str) -> dict[str, Any]:
    result = _clone(state)
    if result.get("status") != "lobby":
        raise UnoError("UNO_ALREADY_STARTED")
    if any(int(p.get("user_id", 0)) == int(user_id) for p in result["players"]):
        return result
    if len(result["players"]) >= MAX_PLAYERS:
        raise UnoError("UNO_LOBBY_FULL")
    result["players"].append(_player(user_id, name))
    return result


def _claim(state: dict[str, Any], action_id: str) -> None:
    action_id = str(action_id or "").strip()
    if not action_id or len(action_id) > 180:
        raise UnoError("UNO_INVALID_ACTION")
    if action_id in state.setdefault("processed_actions", []):
        raise UnoError("UNO_DUPLICATE_ACTION")
    state["processed_actions"] = [*state["processed_actions"][-199:], action_id]
    state["last_action_id"] = action_id


def play_card(state: Mapping[str, Any], *, user_id: int, card_id: str, action_id: str) -> dict[str, Any]:
    result = _clone(state)
    _claim(result, action_id)
    result.pop("last_penalty", None)
    result.pop("last_timeout_user_id", None)
    if result.get("status") != "playing":
        raise UnoError("UNO_NOT_PLAYING")
    _ensure_turn_active(result)
    if int(result.get("current_player_id")) != int(user_id):
        raise UnoError("UNO_NOT_YOUR_TURN")
    if result.get("awaiting_color"):
        raise UnoError("UNO_CHOOSE_COLOR_FIRST")
    player = _find_player(result, user_id)
    drawn_card_id = str(player.get("drawn_card_id") or "")
    if drawn_card_id and str(card_id) != drawn_card_id:
        raise UnoError("UNO_MUST_PLAY_DRAWN_CARD")
    try:
        index = next(i for i, item in enumerate(player["hand"]) if str(item.get("id")) == str(card_id))
    except StopIteration:
        raise UnoError("UNO_CARD_NOT_IN_HAND") from None
    card = player["hand"][index]
    if not can_play(result, card, player_id=user_id):
        raise UnoError("UNO_CARD_CANNOT_PLAY")
    player["hand"].pop(index)
    player["uno_called"] = bool(player.get("uno_called")) and len(player["hand"]) == 1
    player["drawn_card_id"] = None
    result["discard"].append(card)
    result["current_color"] = None if card["color"] == "black" else card["color"]
    result["drawn_card_id"] = None
    result["uno_vulnerable_id"] = int(user_id) if len(player["hand"]) == 1 and not player["uno_called"] else None
    if not player["hand"]:
        if card["kind"] in {"draw2", "draw4"}:
            penalty = 2 if card["kind"] == "draw2" else 4
            target_id = _next_player_id(result)
            _find_player(result, target_id)["hand"].extend(_draw(result, penalty))
            result["last_penalty"] = {"user_id": target_id, "cards": penalty}
        earned = sum(_card_points(p.get("hand", [])) for p in result["players"] if p is not player)
        player["score"] = int(player.get("score", 0)) + earned
        result["round_points"] = earned
        result["match_complete"] = player["score"] >= int(result.get("match_target", 500))
        result["status"] = "finished"
        result["winner_id"] = int(user_id)
        result["current_player_id"] = None
        result["turn_expires_at"] = None
        return result
    kind = card["kind"]
    if kind in {"wild", "draw4"}:
        result["awaiting_color"] = True
        result["pending_draw"] = 4 if kind == "draw4" else 0
        _refresh_turn_deadline(result)
        return result
    result["pending_draw"] = 0
    steps = 2 if kind in {"skip", "reverse"} and len(result["players"]) == 2 else 1
    if kind == "reverse":
        result["direction"] = -int(result.get("direction", 1))
    if kind == "skip":
        steps = 2
    if kind == "draw2":
        target_id = _next_player_id(result)
        _find_player(result, target_id)["hand"].extend(_draw(result, 2))
        result["last_penalty"] = {"user_id": target_id, "cards": 2}
        steps = 2
    result["current_player_id"] = _next_player_id(result, steps=steps)
    _refresh_turn_deadline(result)
    return result


def choose_color(state: Mapping[str, Any], *, user_id: int, color: str, action_id: str) -> dict[str, Any]:
    result = _clone(state)
    _claim(result, action_id)
    result.pop("last_penalty", None)
    result.pop("last_timeout_user_id", None)
    if result.get("status") != "playing" or not result.get("awaiting_color") or int(result.get("current_player_id")) != int(user_id):
        raise UnoError("UNO_COLOR_NOT_ALLOWED")
    _ensure_turn_active(result)
    color = str(color).casefold()
    if color not in COLORS:
        raise UnoError("UNO_INVALID_COLOR")
    result["current_color"] = color
    result["awaiting_color"] = False
    pending = int(result.get("pending_draw", 0) or 0)
    if pending:
        target_id = _next_player_id(result)
        target = _find_player(result, target_id)
        target["hand"].extend(_draw(result, pending))
        result["last_penalty"] = {"user_id": target_id, "cards": pending}
        result["current_player_id"] = _next_player_id(result, steps=2)
    else:
        result["current_player_id"] = _next_player_id(result)
    result["pending_draw"] = 0
    _refresh_turn_deadline(result)
    return result


def draw_card(state: Mapping[str, Any], *, user_id: int, action_id: str) -> dict[str, Any]:
    result = _clone(state)
    _claim(result, action_id)
    result.pop("last_penalty", None)
    result.pop("last_timeout_user_id", None)
    if result.get("status") != "playing" or int(result.get("current_player_id")) != int(user_id):
        raise UnoError("UNO_NOT_YOUR_TURN")
    _ensure_turn_active(result)
    if result.get("awaiting_color"):
        raise UnoError("UNO_CHOOSE_COLOR_FIRST")
    player = _find_player(result, user_id)
    if player.get("drawn_card_id"):
        raise UnoError("UNO_DRAW_ALREADY_TAKEN")
    amount = int(player.pop("pending_draw", 0) or 0) or 1
    drawn = _draw(result, amount)
    player["hand"].extend(drawn)
    player["drawn_card_id"] = drawn[-1].get("id") if drawn else None
    result["drawn_card_id"] = player.get("drawn_card_id")
    result["uno_vulnerable_id"] = None
    player["uno_called"] = False
    if not drawn or not can_play(result, drawn[-1], player_id=user_id):
        player["drawn_card_id"] = None
        result["drawn_card_id"] = None
        result["current_player_id"] = _next_player_id(result)
        _refresh_turn_deadline(result)
    return result


def skip_after_draw(state: Mapping[str, Any], *, user_id: int, action_id: str) -> dict[str, Any]:
    result = _clone(state)
    _claim(result, action_id)
    if result.get("status") != "playing" or int(result.get("current_player_id")) != int(user_id):
        raise UnoError("UNO_NOT_YOUR_TURN")
    _ensure_turn_active(result)
    player = _find_player(result, user_id)
    if not player.get("drawn_card_id"):
        raise UnoError("UNO_DRAW_FIRST")
    player["drawn_card_id"] = None
    result["drawn_card_id"] = None
    result["current_player_id"] = _next_player_id(result)
    _refresh_turn_deadline(result)
    return result


def call_uno(state: Mapping[str, Any], *, user_id: int, action_id: str) -> dict[str, Any]:
    result = _clone(state)
    _claim(result, action_id)
    if result.get("status") != "playing":
        raise UnoError("UNO_NOT_PLAYING")
    _ensure_turn_active(result)
    player = _find_player(result, user_id)
    if result.get("status") != "playing" or len(player.get("hand", [])) not in {1, 2}:
        raise UnoError("UNO_CALL_NOT_ALLOWED")
    player["uno_called"] = True
    if result.get("uno_vulnerable_id") == int(user_id):
        result["uno_vulnerable_id"] = None
    return result


def challenge_uno(state: Mapping[str, Any], *, user_id: int, target_id: int, action_id: str) -> dict[str, Any]:
    """Challenge a player who ended a turn with one card without calling UNO."""
    result = _clone(state)
    _claim(result, action_id)
    if result.get("status") != "playing":
        raise UnoError("UNO_NOT_PLAYING")
    _ensure_turn_active(result)
    _find_player(result, user_id)
    target = _find_player(result, target_id)
    if (int(target_id) == int(user_id) or len(target.get("hand", [])) != 1 or target.get("uno_called")
            or result.get("uno_vulnerable_id") != int(target_id)):
        raise UnoError("UNO_CHALLENGE_NOT_ALLOWED")
    target["hand"].extend(_draw(result, 2))
    target["uno_called"] = False
    result["last_challenge_by"] = int(user_id)
    result["uno_vulnerable_id"] = None
    return result


def _card_points(cards: list[Mapping[str, Any]]) -> int:
    total = 0
    for card in cards:
        kind = str(card.get("kind"))
        total += int(kind) if kind.isdigit() else (20 if kind in {"skip", "reverse", "draw2"} else 50)
    return total


def score_card(card: Mapping[str, Any]) -> int:
    return _card_points([card])


def player_view(state: Mapping[str, Any], user_id: int) -> dict[str, Any]:
    result = _clone(state)
    for player in result.get("players", []):
        if int(player.get("user_id", 0)) != int(user_id):
            player["hand"] = [{"id": str(card.get("id")), "color": "hidden", "kind": "hidden"} for card in player.get("hand", [])]
    return result


__all__ = [
    "COLORS", "COLOR_LABELS", "SPECIAL_LABELS", "DEFAULT_TURN_SECONDS", "UnoError",
    "build_deck", "can_play", "call_uno", "challenge_uno", "choose_color", "draw_card", "join_player",
    "new_uno_state", "play_card", "player_view", "score_card", "skip_after_draw",
    "start_round", "validate_state",
    "turn_expired",
    "timeout_turn",
]
