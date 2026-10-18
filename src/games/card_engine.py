"""Deterministic, transport-neutral card game primitives.

The Telegram games in :mod:`src.games` use several different state formats.
This module is a small common contract for games that need a standard deck,
replayable actions, and blackjack-style hand valuation.  It deliberately has
no Telegram, database, random global state, or network dependency.

All mutations return a new immutable value.  A caller can therefore persist a
state after every accepted action and reconstruct the exact same state after a
restart.  ``random.Random`` is seeded locally so two games never influence one
another through module-level random state.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
import json
import random
from types import MappingProxyType
from typing import Any


RANKS: tuple[str, ...] = ("2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A")
SUITS: tuple[str, ...] = ("C", "D", "H", "S")
SUIT_LABELS: Mapping[str, str] = MappingProxyType({"C": "clubs", "D": "diamonds", "H": "hearts", "S": "spades"})
CARD_SCHEMA_VERSION = 1
DECK_SCHEMA_VERSION = 1
ACTION_SCHEMA_VERSION = 1
GAME_SCHEMA_VERSION = 1
MAX_DECK_CARDS = 416
MAX_PROCESSED_ACTIONS = 2_000


class DeckExhausted(ValueError):
    """Raised when a draw asks for more cards than remain in a deck."""


def _validate_identifier(value: Any, *, reason: str, maximum: int = 180) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum or any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise ValueError(reason)
    return text


@dataclass(frozen=True, slots=True, order=True)
class Card:
    """A canonical playing card represented by rank and suit codes.

    Ranks use ``2`` through ``10``, ``J``, ``Q``, ``K``, ``A``.  Suits use
    ``C`` (clubs), ``D`` (diamonds), ``H`` (hearts) and ``S`` (spades).  The
    compact ``code`` is stable in snapshots and safe to use in callback data.
    """

    rank: str
    suit: str

    def __post_init__(self) -> None:
        rank = str(self.rank).strip().upper()
        suit = str(self.suit).strip().upper()
        if rank not in RANKS:
            raise ValueError("invalid_card_rank")
        if suit not in SUITS:
            raise ValueError("invalid_card_suit")
        object.__setattr__(self, "rank", rank)
        object.__setattr__(self, "suit", suit)

    @property
    def code(self) -> str:
        return f"{self.rank}{self.suit}"

    @property
    def value(self) -> int:
        if self.rank == "A":
            return 11
        if self.rank in {"J", "Q", "K"}:
            return 10
        return int(self.rank)

    def __str__(self) -> str:
        return self.code

    def to_dict(self) -> dict[str, str | int]:
        return {"schema_version": CARD_SCHEMA_VERSION, "rank": self.rank, "suit": self.suit}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Card":
        if not isinstance(value, Mapping):
            raise ValueError("invalid_card")
        try:
            return cls(value["rank"], value["suit"])
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, ValueError) and str(exc).startswith("invalid_card_"):
                raise
            raise ValueError("invalid_card") from None

    @classmethod
    def from_code(cls, code: str) -> "Card":
        compact = str(code or "").strip().upper()
        if len(compact) < 2:
            raise ValueError("invalid_card_code")
        rank, suit = compact[:-1], compact[-1]
        try:
            return cls(rank, suit)
        except ValueError as exc:
            raise ValueError("invalid_card_code") from exc


def coerce_card(value: Any) -> Card:
    """Convert safe legacy card forms to :class:`Card`.

    Existing game implementations historically used ``(rank, suit)`` tuples.
    Supporting that representation here makes migration incremental while the
    canonical state and serialization format remains typed.
    """

    if isinstance(value, Card):
        return value
    if isinstance(value, str):
        return Card.from_code(value)
    if isinstance(value, Mapping):
        return Card.from_dict(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) and len(value) == 2:
        return Card(value[0], value[1])
    raise ValueError("invalid_card")


@dataclass(frozen=True, slots=True)
class Deck:
    """Immutable deck with a deterministic shuffle and cursor."""

    cards: tuple[Card, ...]
    position: int = 0
    seed: int | None = None
    schema_version: int = DECK_SCHEMA_VERSION

    def __post_init__(self) -> None:
        cards = tuple(coerce_card(card) for card in self.cards)
        if not cards or len(cards) > MAX_DECK_CARDS:
            raise ValueError("invalid_deck_size")
        if len(set(card.code for card in cards)) != len(cards):
            raise ValueError("duplicate_card")
        position = self.position
        if isinstance(position, bool) or not isinstance(position, int) or position < 0 or position > len(cards):
            raise ValueError("invalid_deck_position")
        if self.schema_version != DECK_SCHEMA_VERSION:
            raise ValueError("invalid_deck_schema_version")
        if self.seed is not None and (isinstance(self.seed, bool) or not isinstance(self.seed, int)):
            raise ValueError("invalid_deck_seed")
        object.__setattr__(self, "cards", cards)

    @classmethod
    def standard(cls, *, seed: int | None = None) -> "Deck":
        cards = tuple(Card(rank, suit) for suit in SUITS for rank in RANKS)
        deck = cls(cards=cards, seed=seed)
        return deck.shuffle(seed=seed)

    @classmethod
    def from_cards(cls, cards: Iterable[Any], *, seed: int | None = None) -> "Deck":
        normalized = tuple(coerce_card(card) for card in cards)
        return cls(cards=normalized, seed=seed)

    @property
    def remaining(self) -> int:
        return len(self.cards) - self.position

    @property
    def exhausted(self) -> bool:
        return self.remaining == 0

    def shuffle(self, *, seed: int | None = None) -> "Deck":
        """Shuffle only undealt cards and keep already drawn cards fixed."""

        chosen_seed = self.seed if seed is None else seed
        if chosen_seed is not None and (isinstance(chosen_seed, bool) or not isinstance(chosen_seed, int)):
            raise ValueError("invalid_deck_seed")
        rng = random.Random(chosen_seed)
        dealt = self.cards[: self.position]
        remainder = list(self.cards[self.position :])
        rng.shuffle(remainder)
        return Deck(dealt + tuple(remainder), position=self.position, seed=chosen_seed)

    def draw(self, count: int = 1) -> tuple[tuple[Card, ...], "Deck"]:
        if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
            raise ValueError("invalid_draw_count")
        if count > self.remaining:
            raise DeckExhausted("deck_exhausted")
        start = self.position
        end = start + count
        return self.cards[start:end], Deck(self.cards, position=end, seed=self.seed)

    def draw_one(self) -> tuple[Card, "Deck"]:
        cards, deck = self.draw(1)
        return cards[0], deck

    def reset(self) -> "Deck":
        return Deck(self.cards, position=0, seed=self.seed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": DECK_SCHEMA_VERSION,
            "cards": [card.to_dict() for card in self.cards],
            "position": self.position,
            "seed": self.seed,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Deck":
        if not isinstance(value, Mapping):
            raise ValueError("invalid_deck")
        if int(value.get("schema_version", DECK_SCHEMA_VERSION)) != DECK_SCHEMA_VERSION:
            raise ValueError("invalid_deck_schema_version")
        try:
            return cls(
                cards=tuple(Card.from_dict(item) for item in value["cards"]),
                position=int(value.get("position", 0)),
                seed=value.get("seed"),
            )
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            if isinstance(exc, ValueError) and str(exc).startswith(("invalid_deck_", "duplicate_card")):
                raise
            raise ValueError("invalid_deck") from None


@dataclass(frozen=True, slots=True)
class BlackjackValue:
    """A blackjack hand's best score and ace state."""

    total: int
    soft: bool
    bust: bool
    blackjack: bool


def evaluate_hand(hand: Iterable[Any]) -> BlackjackValue:
    """Return best blackjack total using aces as 11 or 1 as needed."""

    cards = tuple(coerce_card(card) for card in hand)
    total = sum(card.value for card in cards)
    aces = sum(card.rank == "A" for card in cards)
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return BlackjackValue(total=total, soft=aces > 0, bust=total > 21, blackjack=len(cards) == 2 and total == 21)


hand_value = evaluate_hand


@dataclass(frozen=True, slots=True)
class CardAction:
    """Validated, replay-safe action envelope."""

    action_id: str
    kind: str
    player_id: int
    payload: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    schema_version: int = ACTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        action_id = _validate_identifier(self.action_id, reason="invalid_card_action_id")
        kind = str(self.kind or "").strip().casefold()
        if not kind or len(kind) > 64 or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for char in kind):
            raise ValueError("invalid_card_action_kind")
        if isinstance(self.player_id, bool) or not isinstance(self.player_id, int) or self.player_id <= 0:
            raise ValueError("invalid_card_action_player")
        if not isinstance(self.payload, Mapping) or len(self.payload) > 32:
            raise ValueError("invalid_card_action_payload")
        try:
            payload = deepcopy(dict(self.payload))
            encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("invalid_card_action_payload") from exc
        if len(encoded.encode("utf-8")) > 4096:
            raise ValueError("card_action_payload_too_large")
        if any(not isinstance(key, str) or not key.strip() or len(key) > 64 for key in payload):
            raise ValueError("invalid_card_action_payload")
        if self.schema_version != ACTION_SCHEMA_VERSION:
            raise ValueError("invalid_card_action_schema_version")
        object.__setattr__(self, "action_id", action_id)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "payload", MappingProxyType(payload))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ACTION_SCHEMA_VERSION,
            "action_id": self.action_id,
            "kind": self.kind,
            "player_id": self.player_id,
            "payload": deepcopy(dict(self.payload)),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CardAction":
        if not isinstance(value, Mapping):
            raise ValueError("invalid_card_action")
        try:
            return cls(
                action_id=value["action_id"],
                kind=value["kind"],
                player_id=int(value["player_id"]),
                payload=value.get("payload", {}),
                schema_version=int(value.get("schema_version", ACTION_SCHEMA_VERSION)),
            )
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            if isinstance(exc, ValueError) and str(exc).startswith(("invalid_card_action_", "card_action_")):
                raise
            raise ValueError("invalid_card_action") from None


@dataclass(frozen=True, slots=True)
class CardActionLedger:
    """Bounded immutable ledger used to make callback/replay actions idempotent."""

    actions: tuple[CardAction, ...] = ()

    def __post_init__(self) -> None:
        actions = tuple(action if isinstance(action, CardAction) else CardAction.from_dict(action) for action in self.actions)
        if len(actions) > MAX_PROCESSED_ACTIONS:
            raise ValueError("card_action_ledger_too_large")
        ids = [item.action_id for item in actions]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate_card_action")
        object.__setattr__(self, "actions", actions)

    @property
    def action_ids(self) -> frozenset[str]:
        return frozenset(action.action_id for action in self.actions)

    def contains(self, action_id: str) -> bool:
        return str(action_id).strip() in self.action_ids

    def accept(self, action: CardAction) -> tuple["CardActionLedger", bool]:
        if not isinstance(action, CardAction):
            raise ValueError("invalid_card_action")
        if action.action_id in self.action_ids:
            return self, False
        if len(self.actions) >= MAX_PROCESSED_ACTIONS:
            raise ValueError("card_action_ledger_full")
        return CardActionLedger((*self.actions, action)), True

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": ACTION_SCHEMA_VERSION, "actions": [action.to_dict() for action in self.actions]}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CardActionLedger":
        if not isinstance(value, Mapping):
            raise ValueError("invalid_card_action_ledger")
        if int(value.get("schema_version", ACTION_SCHEMA_VERSION)) != ACTION_SCHEMA_VERSION:
            raise ValueError("invalid_card_action_schema_version")
        try:
            return cls(tuple(CardAction.from_dict(item) for item in value.get("actions", ())))
        except (TypeError, ValueError, OverflowError) as exc:
            if isinstance(exc, ValueError) and str(exc).startswith(("invalid_card_action", "card_action_")):
                raise
            raise ValueError("invalid_card_action_ledger") from exc


class CardDrawStatus(str, Enum):
    ACCEPTED = "accepted"
    REPLAYED = "replayed"


@dataclass(frozen=True, slots=True)
class CardDrawResult:
    state: "CardGameState"
    cards: tuple[Card, ...]
    status: CardDrawStatus

    @property
    def accepted(self) -> bool:
        return self.status is CardDrawStatus.ACCEPTED


@dataclass(frozen=True, slots=True)
class CardGameState:
    """Generic two-or-more-player state for deterministic draw-based games."""

    game_id: str
    chat_id: int
    players: tuple[int, ...]
    deck: Deck
    hands: Mapping[int, tuple[Card, ...]]
    turn_index: int = 0
    status: str = "active"
    ledger: CardActionLedger = CardActionLedger()
    version: int = 0
    schema_version: int = GAME_SCHEMA_VERSION

    def __post_init__(self) -> None:
        game_id = _validate_identifier(self.game_id, reason="invalid_card_game_id")
        if isinstance(self.chat_id, bool) or not isinstance(self.chat_id, int) or self.chat_id == 0:
            raise ValueError("invalid_card_scope")
        players = tuple(int(player) for player in self.players)
        if len(players) < 1 or len(players) > 16 or any(player <= 0 for player in players) or len(set(players)) != len(players):
            raise ValueError("invalid_card_players")
        if isinstance(self.turn_index, bool) or not isinstance(self.turn_index, int) or not 0 <= self.turn_index < len(players):
            raise ValueError("invalid_card_turn")
        if self.status not in {"active", "finished", "cancelled"}:
            raise ValueError("invalid_card_status")
        if isinstance(self.ledger, Mapping):
            ledger = CardActionLedger.from_dict(self.ledger)
        elif isinstance(self.ledger, CardActionLedger):
            ledger = self.ledger
        else:
            raise ValueError("invalid_card_ledger")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 0:
            raise ValueError("invalid_card_version")
        if self.schema_version != GAME_SCHEMA_VERSION:
            raise ValueError("invalid_card_game_schema_version")
        normalized_hands: dict[int, tuple[Card, ...]] = {}
        for player in players:
            raw_hand = self.hands.get(player, ()) if isinstance(self.hands, Mapping) else ()
            normalized_hands[player] = tuple(coerce_card(card) for card in raw_hand)
        object.__setattr__(self, "game_id", game_id)
        object.__setattr__(self, "players", players)
        object.__setattr__(self, "hands", MappingProxyType(normalized_hands))
        object.__setattr__(self, "ledger", ledger)

    @classmethod
    def create(
        cls,
        *,
        game_id: str,
        chat_id: int,
        players: Sequence[int],
        deck: Deck | None = None,
    ) -> "CardGameState":
        unique = tuple(dict.fromkeys(int(player) for player in players))
        if not unique:
            raise ValueError("invalid_card_players")
        return cls(
            game_id=game_id,
            chat_id=int(chat_id),
            players=unique,
            deck=deck or Deck.standard(),
            hands={player: () for player in unique},
        )

    @property
    def current_player_id(self) -> int:
        return self.players[self.turn_index]

    def draw(self, *, player_id: int, action_id: str, count: int = 1) -> CardDrawResult:
        if player_id not in self.players:
            raise ValueError("unknown_game_player")
        action = CardAction(action_id=action_id, kind="draw", player_id=player_id, payload={"count": int(count)})
        if self.ledger.contains(action.action_id):
            previous = next(item for item in self.ledger.actions if item.action_id == action.action_id)
            if previous != action:
                raise ValueError("card_action_conflict")
            return CardDrawResult(self, (), CardDrawStatus.REPLAYED)
        if self.status != "active":
            raise ValueError("game_finished")
        if player_id != self.current_player_id:
            raise ValueError("not_your_turn")
        ledger, accepted = self.ledger.accept(action)
        if not accepted:  # Defensive: the ledger was checked above under immutability.
            return CardDrawResult(self, (), CardDrawStatus.REPLAYED)
        cards, deck = self.deck.draw(count)
        hands = dict(self.hands)
        hands[player_id] = (*hands.get(player_id, ()), *cards)
        next_index = (self.turn_index + 1) % len(self.players)
        state = CardGameState(
            game_id=self.game_id,
            chat_id=self.chat_id,
            players=self.players,
            deck=deck,
            hands=hands,
            turn_index=next_index,
            status=self.status,
            ledger=ledger,
            version=self.version + 1,
            schema_version=self.schema_version,
        )
        return CardDrawResult(state, cards, CardDrawStatus.ACCEPTED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": GAME_SCHEMA_VERSION,
            "game_id": self.game_id,
            "chat_id": self.chat_id,
            "players": list(self.players),
            "deck": self.deck.to_dict(),
            "hands": {str(player): [card.to_dict() for card in hand] for player, hand in self.hands.items()},
            "turn_index": self.turn_index,
            "status": self.status,
            "ledger": self.ledger.to_dict(),
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CardGameState":
        if not isinstance(value, Mapping):
            raise ValueError("invalid_card_game_state")
        try:
            raw_hands = value.get("hands", {})
            if not isinstance(raw_hands, Mapping):
                raise ValueError("invalid_card_hands")
            hands = {
                int(player): tuple(Card.from_dict(card) for card in cards)
                for player, cards in raw_hands.items()
            }
            return cls(
                game_id=value["game_id"],
                chat_id=int(value["chat_id"]),
                players=tuple(int(player) for player in value["players"]),
                deck=Deck.from_dict(value["deck"]),
                hands=hands,
                turn_index=int(value.get("turn_index", 0)),
                status=str(value.get("status", "active")),
                ledger=CardActionLedger.from_dict(value.get("ledger", {})),
                version=int(value.get("version", 0)),
                schema_version=int(value.get("schema_version", GAME_SCHEMA_VERSION)),
            )
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            if isinstance(exc, ValueError) and str(exc).startswith("invalid_card_"):
                raise
            raise ValueError("invalid_card_game_state") from None


__all__ = [
    "ACTION_SCHEMA_VERSION",
    "CARD_SCHEMA_VERSION",
    "Card",
    "CardAction",
    "CardActionLedger",
    "CardDrawResult",
    "CardDrawStatus",
    "CardGameState",
    "BlackjackValue",
    "Deck",
    "DeckExhausted",
    "DECK_SCHEMA_VERSION",
    "GAME_SCHEMA_VERSION",
    "RANKS",
    "SUITS",
    "coerce_card",
    "evaluate_hand",
    "hand_value",
]
