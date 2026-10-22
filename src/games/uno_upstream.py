"""Durable JSON adapter around the maildian UNO Game/Player/Deck mechanics.

The vendored classes decide card legality, draw stacking, turn effects and
bluff penalties. This boundary reconstructs them for each atomic transition;
no Telegram, Pony ORM, process-global game manager or pickle state is used.
"""

from __future__ import annotations

from copy import deepcopy
import logging
import random
import re
import secrets
from types import SimpleNamespace
from typing import Any, Mapping
import uuid

from src.games import uno as legacy
from src.vendor.uno_maildian import card as uc
from src.vendor.uno_maildian.deck import Deck
from src.vendor.uno_maildian.errors import DeckEmptyError
from src.vendor.uno_maildian.game import Game
from src.vendor.uno_maildian.player import Player

UnoError = legacy.UnoError
COLORS = legacy.COLORS
COLOR_LABELS = legacy.COLOR_LABELS
SPECIAL_LABELS = legacy.SPECIAL_LABELS
DEFAULT_TURN_SECONDS = legacy.DEFAULT_TURN_SECONDS

_COLOR_TO_CORE = {"red": "r", "blue": "b", "green": "g", "yellow": "y", "black": None}
_CORE_TO_COLOR = {value: key for key, value in _COLOR_TO_CORE.items() if value}
_KIND_TO_CORE = {"draw2": "draw", "wild": "colorchooser", "draw4": "draw_four"}
_CORE_TO_KIND = {value: key for key, value in _KIND_TO_CORE.items()}


def _core_card(data: Mapping[str, Any]) -> uc.Card:
    kind = str(data["kind"])
    card = (uc.Card(None, None, special=_KIND_TO_CORE[kind]) if kind in {"wild", "draw4"}
            else uc.Card(_COLOR_TO_CORE[str(data["color"])], _KIND_TO_CORE.get(kind, kind)))
    card._arel_id = str(data["id"])
    return card


def _json_card(card: uc.Card) -> dict[str, str]:
    kind = _CORE_TO_KIND[card.special] if card.special else _CORE_TO_KIND.get(card.value, card.value)
    return {"id": str(card._arel_id), "color": "black" if card.special else _CORE_TO_COLOR[card.color], "kind": kind}


def _engine(state: Mapping[str, Any]) -> tuple[Game, dict[int, Player]]:
    """Rebuild one source game from a snapshot without drawing new cards."""
    game = Game.__new__(Game)
    game.chat = SimpleNamespace(id=int(state["chat_id"]))
    game.logger = logging.getLogger(__name__)
    game.reversed = int(state.get("direction", 1)) < 0
    game.draw_counter = int(state.get("pending_draw", 0) or 0)
    game.choosing_color = bool(state.get("awaiting_color"))
    game.started = state.get("status") == "playing"
    game.owner = int(state["owner_id"])
    game.open = False
    game.translate = False
    game.players_won = 0
    game.deck = Deck.__new__(Deck)
    game.deck.cards = [_core_card(c) for c in state.get("deck", [])]
    game.deck.graveyard = [_core_card(c) for c in state.get("discard", [])[:-1]]
    game.deck.rng = None
    game.deck.shuffle_seed = str(state["private_shuffle_seed"])
    game.deck.shuffle_count = int(state["private_shuffle_count"])
    game.deck.logger = game.logger
    top = state["discard"][-1]
    game.last_card = _core_card(top)
    if game.last_card.special and state.get("current_color") in _COLOR_TO_CORE:
        game.last_card.color = _COLOR_TO_CORE[state["current_color"]]
    players: dict[int, Player] = {}
    ordered: list[Player] = []
    for data in state["players"]:
        player = Player.__new__(Player)
        player.game = game
        player.user = SimpleNamespace(id=int(data["user_id"]), first_name=data["name"])
        player.cards = [_core_card(c) for c in data["hand"]]
        player.logger = game.logger
        player.bluffing = bool(data.get("bluffing", False))
        player.drew = bool(data.get("draw_attempted") or data.get("drawn_card_id"))
        player._arel_drawn_id = data.get("drawn_card_id")
        player.anti_cheat = 0
        player.turn_started = None
        player.waiting_time = 90
        players[player.user.id] = player
        ordered.append(player)
    for index, player in enumerate(ordered):
        player._next = ordered[(index + 1) % len(ordered)]
        player._prev = ordered[(index - 1) % len(ordered)]
    game.current_player = players[int(state["current_player_id"])]
    return game, players


def _snapshot(result: dict[str, Any], game: Game, players: dict[int, Player]) -> dict[str, Any]:
    result["deck"] = [_json_card(c) for c in game.deck.cards]
    result["discard"] = [_json_card(c) for c in game.deck.graveyard] + [_json_card(game.last_card)]
    result["current_color"] = _CORE_TO_COLOR.get(game.last_card.color)
    result["direction"] = -1 if game.reversed else 1
    result["pending_draw"] = game.draw_counter
    result["private_shuffle_count"] = game.deck.shuffle_count
    result["awaiting_color"] = game.choosing_color
    result["current_player_id"] = game.current_player.user.id
    for data in result["players"]:
        player = players[int(data["user_id"])]
        data["hand"] = [_json_card(c) for c in player.cards]
        data["bluffing"] = player.bluffing
        data["draw_attempted"] = bool(player.drew)
        data["drawn_card_id"] = (player._arel_drawn_id if player.drew and
                                  any(c._arel_id == player._arel_drawn_id for c in player.cards) else None)
    current = next(p for p in result["players"] if p["user_id"] == result["current_player_id"])
    result["drawn_card_id"] = current["drawn_card_id"]
    legacy._refresh_turn_deadline(result)
    return result


def _ready(state: Mapping[str, Any], action_id: str, user_id: int, *, turn: bool = True) -> dict[str, Any]:
    if not validate_state(state):
        raise UnoError("UNO_INVALID_STATE")
    result = deepcopy(dict(state))
    legacy._claim(result, action_id)
    if result.get("status") != "playing":
        raise UnoError("UNO_NOT_PLAYING")
    legacy._ensure_turn_active(result)
    legacy._find_player(result, user_id)
    if turn and int(result.get("current_player_id") or 0) != int(user_id):
        raise UnoError("UNO_NOT_YOUR_TURN")
    result["last_penalty"] = None
    result["last_timeout_user_id"] = None
    return result


def _finish(result: dict[str, Any], user_id: int) -> dict[str, Any]:
    winner = legacy._find_player(result, user_id)
    earned = sum(legacy._card_points(p["hand"]) for p in result["players"] if p is not winner)
    winner["score"] = int(winner.get("score", 0)) + earned
    result.update(status="finished", winner_id=user_id, round_points=earned,
                  match_complete=winner["score"] >= int(result.get("match_target", 500)),
                  current_player_id=None, turn_expires_at=None, drawn_card_id=None)
    return result


def new_uno_state(**kwargs: Any) -> dict[str, Any]:
    result = legacy.new_uno_state(**kwargs)
    result["engine"] = "maildian"
    return result


def validate_state(state: Mapping[str, Any]) -> bool:
    if state.get("engine") != "maildian" or not legacy.validate_state(state):
        return False
    if state.get("status") not in {"playing", "finished"}:
        return True
    try:
        if not re.fullmatch(r"[0-9a-f]{64}", str(state["private_shuffle_seed"])):
            return False
        if not 0 <= int(state["private_shuffle_count"]) <= 1000000:
            return False
        players = state["players"]
        if state["status"] == "playing" and int(state["current_player_id"]) not in {int(p["user_id"]) for p in players}:
            return False
        deck, discard = state["deck"], state["discard"]
        if not isinstance(deck, list) or not isinstance(discard, list) or not discard:
            return False
        cards = deck + discard + [card for player in players for card in player["hand"]]
        if len(cards) != 108:
            return False
        ids = [str(card["id"]) for card in cards]
        if any(not identity or len(identity) > 64 for identity in ids) or len(set(ids)) != 108:
            return False
        for card in cards:
            color, kind = str(card["color"]), str(card["kind"])
            if kind not in legacy.VALID_TYPES:
                return False
            if color == "black":
                if kind not in {"wild", "draw4"}:
                    return False
            elif color not in COLORS or kind in {"wild", "draw4"}:
                return False
        active = state.get("current_color")
        if active not in COLORS and not (state.get("awaiting_color") and active is None):
            return False
        if int(state.get("pending_draw", 0) or 0) < 0:
            return False
        for player in players:
            drawn = player.get("drawn_card_id")
            if drawn is not None and drawn not in {card["id"] for card in player["hand"]}:
                return False
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
        return False
    return True


def join_player(state: Mapping[str, Any], *, user_id: int, name: str) -> dict[str, Any]:
    if not validate_state(state):
        raise UnoError("UNO_INVALID_STATE")
    result = legacy.join_player(state, user_id=user_id, name=name)
    if result.get("engine") != "maildian":
        raise UnoError("UNO_INVALID_STATE")
    return result


def start_round(state: Mapping[str, Any], *, rng: random.Random | None = None) -> dict[str, Any]:
    if not validate_state(state):
        raise UnoError("UNO_INVALID_STATE")
    if state.get("status") not in {"lobby", "finished", "cancelled"}:
        raise UnoError("UNO_ALREADY_STARTED")
    if len(state["players"]) < 2:
        raise UnoError("UNO_NEED_TWO_PLAYERS")
    result = deepcopy(dict(state))
    result["private_shuffle_seed"] = secrets.token_hex(32)
    result["private_shuffle_count"] = 0
    game = Game(SimpleNamespace(id=result["chat_id"]), rng=rng or random.Random())
    # Give each physical card a stable identity before Player deals from Deck.
    for index, card in enumerate([game.last_card, *game.deck.cards]):
        card._arel_id = f"u{index:03d}"
    players = []
    for data in result["players"]:
        player = Player(game, SimpleNamespace(id=data["user_id"], first_name=data["name"]))
        players.append(player)
    # The original bot applies the opening card's action, including skip and
    # reverse. Game.play_card also dismisses that same card; remove its duplicate.
    opening = game.last_card
    game.play_card(opening)
    game.deck.graveyard = [c for c in game.deck.graveyard if c is not opening]
    game.started = True
    result.update(status="playing", round_id=uuid.uuid4().hex[:16],
                  round_number=int(result.get("round_number", 0)) + 1,
                  winner_id=None, match_complete=False, processed_actions=[],
                  uno_vulnerable_id=None, last_penalty=None, last_timeout_user_id=None)
    for data, player in zip(result["players"], players):
        data.update(hand=[_json_card(c) for c in player.cards], ready=False,
                    uno_called=False, drawn_card_id=None, pending_draw=0,
                    score=int(data.get("score", 0)), bluffing=False, draw_attempted=False)
    result["deck"] = [_json_card(c) for c in game.deck.cards]
    result["discard"] = [_json_card(game.last_card)]
    result["current_player_id"] = game.current_player.user.id
    result["direction"] = -1 if game.reversed else 1
    result["current_color"] = _CORE_TO_COLOR[game.last_card.color]
    result["pending_draw"] = game.draw_counter
    result["awaiting_color"] = game.choosing_color
    result["drawn_card_id"] = None
    legacy._refresh_turn_deadline(result)
    return result


def can_play(state: Mapping[str, Any], card: Mapping[str, Any], *, player_id: int | None = None) -> bool:
    if not validate_state(state) or state.get("status") != "playing" or state.get("awaiting_color"):
        return False
    try:
        game, players = _engine(state)
        player = players[int(player_id or state["current_player_id"])]
        data = legacy._find_player(dict(state), player.user.id)
        if data.get("draw_attempted") and not data.get("drawn_card_id"):
            return False
        owned = next(c for c in player.cards if c._arel_id == str(card["id"]))
        return any(candidate is owned for candidate in player.playable_cards())
    except (KeyError, StopIteration, TypeError, ValueError):
        return False


def play_card(state: Mapping[str, Any], *, user_id: int, card_id: str, action_id: str) -> dict[str, Any]:
    result = _ready(state, action_id, user_id)
    if result.get("awaiting_color"):
        raise UnoError("UNO_CHOOSE_COLOR_FIRST")
    game, players = _engine(result)
    player = players[int(user_id)]
    card = next((c for c in player.cards if c._arel_id == str(card_id)), None)
    if card is None:
        raise UnoError("UNO_CARD_NOT_IN_HAND")
    drawn = legacy._find_player(result, user_id).get("drawn_card_id")
    if legacy._find_player(result, user_id).get("draw_attempted") and not drawn:
        raise UnoError("UNO_MUST_PLAY_DRAWN_CARD")
    if drawn and drawn != str(card_id):
        raise UnoError("UNO_MUST_PLAY_DRAWN_CARD")
    if not any(candidate is card for candidate in player.playable_cards()):
        raise UnoError("UNO_CARD_CANNOT_PLAY")
    player.play(card)
    player.drew = False
    player._arel_drawn_id = None
    result = _snapshot(result, game, players)
    data = legacy._find_player(result, user_id)
    data["uno_called"] = bool(data.get("uno_called")) and len(data["hand"]) == 1
    result["uno_vulnerable_id"] = user_id if len(data["hand"]) == 1 and not data["uno_called"] else None
    if not data["hand"]:
        return _finish(result, user_id)
    return result


def choose_color(state: Mapping[str, Any], *, user_id: int, color: str, action_id: str) -> dict[str, Any]:
    result = _ready(state, action_id, user_id)
    if not result.get("awaiting_color"):
        raise UnoError("UNO_COLOR_NOT_ALLOWED")
    if color not in COLORS:
        raise UnoError("UNO_INVALID_COLOR")
    game, players = _engine(result)
    game.choose_color(_COLOR_TO_CORE[color])
    return _snapshot(result, game, players)


def draw_card(state: Mapping[str, Any], *, user_id: int, action_id: str) -> dict[str, Any]:
    result = _ready(state, action_id, user_id)
    if result.get("awaiting_color"):
        raise UnoError("UNO_CHOOSE_COLOR_FIRST")
    data = legacy._find_player(result, user_id)
    if data.get("draw_attempted") or data.get("drawn_card_id"):
        raise UnoError("UNO_DRAW_ALREADY_TAKEN")
    game, players = _engine(result)
    player = players[int(user_id)]
    penalty = game.draw_counter
    before = len(player.cards)
    try:
        player.draw()
    except DeckEmptyError:
        # Upstream Player.draw keeps cards already drawn and sets drew in its
        # finally block; the player may still pass when no card was available.
        pass
    player._arel_drawn_id = player.cards[-1]._arel_id if len(player.cards) > before else None
    if penalty:
        player._arel_drawn_id = None
        result["last_penalty"] = {"user_id": user_id, "cards": len(player.cards) - before}
        game.turn()
    result["uno_vulnerable_id"] = None
    data["uno_called"] = False
    return _snapshot(result, game, players)


def skip_after_draw(state: Mapping[str, Any], *, user_id: int, action_id: str) -> dict[str, Any]:
    result = _ready(state, action_id, user_id)
    data = legacy._find_player(result, user_id)
    if not data.get("draw_attempted") and not data.get("drawn_card_id"):
        raise UnoError("UNO_DRAW_FIRST")
    game, players = _engine(result)
    game.turn()
    return _snapshot(result, game, players)


def call_bluff(state: Mapping[str, Any], *, user_id: int, action_id: str) -> dict[str, Any]:
    result = _ready(state, action_id, user_id)
    if not (result.get("pending_draw") == 4 and result["discard"][-1]["kind"] == "draw4"):
        raise UnoError("UNO_BLUFF_NOT_ALLOWED")
    game, players = _engine(result)
    challenger = players[int(user_id)]
    accused = challenger.prev
    if accused.bluffing:
        game.draw_counter = 4
        before = len(accused.cards)
        try:
            accused.draw()
        except DeckEmptyError:
            pass
        result["last_penalty"] = {"user_id": accused.user.id, "cards": len(accused.cards) - before}
        # The source handler calls game.turn() after this draw.
        game.turn()
    else:
        game.draw_counter += 2
        before = len(challenger.cards)
        try:
            challenger.draw()
        except DeckEmptyError:
            pass
        result["last_penalty"] = {"user_id": user_id, "cards": len(challenger.cards) - before}
        game.turn()
    result["uno_vulnerable_id"] = None
    return _snapshot(result, game, players)


def call_uno(state: Mapping[str, Any], *, user_id: int, action_id: str) -> dict[str, Any]:
    if not validate_state(state):
        raise UnoError("UNO_INVALID_STATE")
    return legacy.call_uno(state, user_id=user_id, action_id=action_id)


def challenge_uno(state: Mapping[str, Any], *, user_id: int, target_id: int, action_id: str) -> dict[str, Any]:
    if not validate_state(state):
        raise UnoError("UNO_INVALID_STATE")
    return legacy.challenge_uno(state, user_id=user_id, target_id=target_id, action_id=action_id)


def timeout_turn(state: Mapping[str, Any]) -> dict[str, Any]:
    if not validate_state(state):
        raise UnoError("UNO_INVALID_STATE")
    if not turn_expired(state):
        raise UnoError("UNO_TURN_NOT_EXPIRED")
    result = deepcopy(dict(state))
    user_id = int(result["current_player_id"])
    game, players = _engine(result)
    if game.choosing_color:
        owned = players[user_id].cards
        color = max(uc.COLORS, key=lambda value: sum(c.color == value for c in owned))
        game.choose_color(color)
    else:
        player = players[user_id]
        if not player.drew:
            try:
                player.draw()
            except DeckEmptyError:
                # The source bot leaves an exhausted deck in place. The
                # durable clock still advances the turn so it cannot stall.
                pass
        game.turn()
    result = _snapshot(result, game, players)
    result["last_timeout_user_id"] = user_id
    result["uno_vulnerable_id"] = None
    return result


turn_expired = legacy.turn_expired


def player_view(state: Mapping[str, Any], user_id: int) -> dict[str, Any]:
    result = legacy.player_view(state, user_id)
    result.pop("private_shuffle_seed", None)
    result.pop("private_shuffle_count", None)
    return result


score_card = legacy.score_card
