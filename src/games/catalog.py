"""Shared local game catalog metadata.

The catalog is deliberately dependency-free so Telegram menus, the tool
runtime, and replay tests all use the same canonical category identifiers.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
import uuid
import unicodedata
from typing import Any, Mapping


GAME_SCHEMA_VERSION = 1
_VALID_SESSION_STATUSES = frozenset({"active", "finished", "cancelled", "expired"})
_GAME_KEY_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_-")


WORD_GAME_CATEGORY_LABELS = {
    "hayvanlar": "Hayvanlar",
    "yemekler": "Yemekler",
    "şehirler": "Şehirler",
    "doğa": "Doğa",
    "bilim": "Bilim",
    "spor": "Spor",
    "sanat": "Sanat",
    "tarih": "Tarih",
    "teknoloji": "Teknoloji",
    "meslekler": "Meslekler",
    "uzay": "Uzay",
    "genel": "Genel",
}


@dataclass(frozen=True, slots=True)
class GameDefinition:
    """Typed, transport-neutral contract for one playable game."""

    key: str
    label: str
    category: str = "general"
    min_players: int = 1
    max_players: int = 100
    turn_timeout_seconds: int = 240
    supports_difficulty: bool = True
    supports_hints: bool = True
    score_cap: int = 100
    schema_version: int = GAME_SCHEMA_VERSION
    state_key: str | None = None
    board_size: tuple[int, int] | None = None
    connect_length: int | None = None

    def __post_init__(self) -> None:
        key = str(self.key).strip().casefold()
        if not key or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for char in key):
            raise ValueError("invalid_game_definition_key")
        object.__setattr__(self, "key", key)
        if int(self.min_players) < 1 or int(self.max_players) < int(self.min_players):
            raise ValueError("invalid_game_player_bounds")
        if int(self.turn_timeout_seconds) <= 0 or int(self.turn_timeout_seconds) > 86400:
            raise ValueError("invalid_game_turn_timeout")
        if int(self.score_cap) < 1 or int(self.score_cap) > 10000:
            raise ValueError("invalid_game_score_cap")
        schema_version = int(self.schema_version)
        if schema_version < 1 or schema_version > 100:
            raise ValueError("invalid_game_schema_version")
        object.__setattr__(self, "schema_version", schema_version)
        state_key = str(self.state_key or key).strip().casefold()
        if not state_key or any(char not in _GAME_KEY_CHARS for char in state_key):
            raise ValueError("invalid_game_state_key")
        object.__setattr__(self, "state_key", state_key)
        if self.board_size is not None:
            try:
                rows, columns = (int(item) for item in self.board_size)
            except (TypeError, ValueError):
                raise ValueError("invalid_game_board_size") from None
            if rows < 1 or columns < 1 or rows > 64 or columns > 64:
                raise ValueError("invalid_game_board_size")
            object.__setattr__(self, "board_size", (rows, columns))
        if self.connect_length is not None:
            connect_length = int(self.connect_length)
            if connect_length < 2 or connect_length > 64:
                raise ValueError("invalid_game_connect_length")
            if self.board_size is not None and connect_length > max(self.board_size):
                raise ValueError("invalid_game_connect_length")
            object.__setattr__(self, "connect_length", connect_length)


@dataclass(frozen=True, slots=True)
class GameSession:
    """Versioned game session state shared by handlers and replay workers."""

    session_id: str
    game_key: str
    chat_id: int
    owner_id: int
    players: tuple[int, ...]
    status: str
    difficulty: str
    turn_index: int
    turn_user_id: int | None
    combo: int
    scores: Mapping[int, int]
    state: Mapping[str, Any]
    processed_actions: tuple[str, ...]
    started_at: datetime
    updated_at: datetime
    expires_at: datetime
    version: int = 0
    schema_version: int = GAME_SCHEMA_VERSION
    state_key: str = ""
    board_size: tuple[int, int] | None = None
    connect_length: int | None = None

    @classmethod
    def create(
        cls,
        definition: GameDefinition,
        *,
        chat_id: int,
        owner_id: int,
        players: list[int] | tuple[int, ...] | None = None,
        difficulty: str = "normal",
        state: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> "GameSession":
        moment = now or datetime.now(timezone.utc)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        if int(chat_id) == 0 or int(owner_id) <= 0:
            raise ValueError("invalid_game_scope")
        raw_players = [int(owner_id), *(int(item) for item in (players or ()))]
        unique_players = tuple(dict.fromkeys(item for item in raw_players if item > 0))
        if len(unique_players) < definition.min_players or len(unique_players) > definition.max_players:
            raise ValueError("invalid_game_players")
        return cls(
            session_id=uuid.uuid4().hex,
            game_key=definition.key,
            chat_id=int(chat_id),
            owner_id=int(owner_id),
            players=unique_players,
            status="active",
            difficulty=str(difficulty or "normal").strip().casefold() or "normal",
            turn_index=0,
            turn_user_id=unique_players[0],
            combo=0,
            scores={},
            state=dict(state or {}),
            processed_actions=(),
            started_at=moment,
            updated_at=moment,
            expires_at=moment + timedelta(seconds=int(definition.turn_timeout_seconds)),
            version=0,
            schema_version=definition.schema_version,
            state_key=str(definition.state_key),
            board_size=definition.board_size,
            connect_length=definition.connect_length,
        )

    @property
    def storage_key(self) -> str:
        """Stable chat/game key used by state stores and recovery jobs."""

        return f"game:{int(self.chat_id)}:{self.state_key or self.game_key}"

    def with_updates(self, **changes: Any) -> "GameSession":
        changes.setdefault("updated_at", datetime.now(timezone.utc))
        return replace(self, **changes)

    def is_expired(self, now: datetime | None = None) -> bool:
        moment = now or datetime.now(timezone.utc)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return self.status != "active" or moment >= self.expires_at

    def advance_turn(self, user_id: int | None = None, *, timeout_seconds: int | None = None) -> "GameSession":
        if not self.players:
            return self.with_updates(turn_user_id=None, status="finished")
        if user_id is None:
            next_index = (self.turn_index + 1) % len(self.players)
        else:
            try:
                next_index = self.players.index(int(user_id))
            except ValueError:
                raise ValueError("unknown_game_player") from None
        seconds = max(1, int(timeout_seconds or max(1, int((self.expires_at - self.updated_at).total_seconds()))))
        now = datetime.now(timezone.utc)
        return self.with_updates(
            turn_index=next_index,
            turn_user_id=self.players[next_index],
            expires_at=now + timedelta(seconds=seconds),
            combo=0,
            version=self.version + 1,
        )

    def record_action(self, action_id: str) -> "GameSession":
        action = str(action_id or "").strip()
        if not action or len(action) > 180:
            raise ValueError("invalid_game_action_id")
        if action in self.processed_actions:
            return self
        return self.with_updates(
            processed_actions=(*self.processed_actions[-199:], action),
            version=self.version + 1,
        )

    def to_dict(self) -> dict[str, Any]:
        definition = GAME_DEFINITIONS.get(self.game_key)
        board_size = self.board_size if self.board_size is not None else (definition.board_size if definition else None)
        connect_length = self.connect_length if self.connect_length is not None else (definition.connect_length if definition else None)
        return {
            "session_id": self.session_id,
            "game_key": self.game_key,
            "chat_id": self.chat_id,
            "owner_id": self.owner_id,
            "players": list(self.players),
            "status": self.status,
            "difficulty": self.difficulty,
            "turn_index": self.turn_index,
            "turn_user_id": self.turn_user_id,
            "combo": self.combo,
            "scores": {str(k): int(v) for k, v in self.scores.items()},
            "state": deepcopy(dict(self.state)),
            "processed_actions": list(self.processed_actions),
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "expires_at": self.expires_at,
            "version": self.version,
            "schema_version": self.schema_version,
            "state_key": self.state_key or self.game_key,
            "board_size": list(board_size) if board_size is not None else None,
            "connect_length": connect_length,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "GameSession":
        if not isinstance(value, Mapping):
            raise ValueError("game_state_not_mapping")

        def _dt(item: Any) -> datetime:
            if item is None:
                raise ValueError("game_state_datetime_missing")
            if isinstance(item, datetime):
                return item if item.tzinfo else item.replace(tzinfo=timezone.utc)
            try:
                parsed = datetime.fromisoformat(str(item))
            except (TypeError, ValueError):
                raise ValueError("game_state_datetime_invalid") from None
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

        def _positive_int(item: Any, reason: str) -> int:
            if isinstance(item, bool):
                raise ValueError(reason)
            try:
                parsed = int(item)
            except (TypeError, ValueError):
                raise ValueError(reason) from None
            if parsed <= 0:
                raise ValueError(reason)
            return parsed

        session_id = str(value.get("session_id") or "").strip()
        if not session_id or len(session_id) > 128 or any(char.isspace() for char in session_id):
            raise ValueError("game_state_session_id_invalid")
        game_key = str(value.get("game_key") or "").strip().casefold()
        definition = get_game_definition(game_key)
        try:
            chat_id = int(value.get("chat_id"))
        except (TypeError, ValueError):
            raise ValueError("game_state_chat_id_invalid") from None
        if chat_id == 0:
            raise ValueError("game_state_chat_id_invalid")
        owner_id = _positive_int(value.get("owner_id"), "game_state_owner_id_invalid")
        raw_players = value.get("players")
        if not isinstance(raw_players, (list, tuple)) or not raw_players:
            raise ValueError("game_state_players_invalid")
        players = tuple(_positive_int(item, "game_state_player_invalid") for item in raw_players)
        if len(set(players)) != len(players) or len(players) > definition.max_players or len(players) < definition.min_players:
            raise ValueError("game_state_players_invalid")
        if owner_id not in players:
            raise ValueError("game_state_owner_missing")
        status = str(value.get("status") or "").strip().casefold()
        if status not in _VALID_SESSION_STATUSES:
            raise ValueError("game_state_status_invalid")
        try:
            turn_index = int(value.get("turn_index"))
            combo = int(value.get("combo") or 0)
            version = int(value.get("version") or 0)
        except (TypeError, ValueError):
            raise ValueError("game_state_numeric_invalid") from None
        if turn_index < 0 or turn_index >= len(players) or combo < 0 or combo > 10000 or version < 0:
            raise ValueError("game_state_numeric_invalid")
        turn_user_id = value.get("turn_user_id")
        if turn_user_id is not None:
            turn_user_id = _positive_int(turn_user_id, "game_state_turn_user_invalid")
            if turn_user_id not in players:
                raise ValueError("game_state_turn_user_invalid")
        difficulty = str(value.get("difficulty") or "normal").strip().casefold()
        if not difficulty or len(difficulty) > 32:
            raise ValueError("game_state_difficulty_invalid")
        raw_scores = value.get("scores") or {}
        raw_state = value.get("state") or {}
        raw_actions = value.get("processed_actions") or ()
        if not isinstance(raw_scores, Mapping) or not isinstance(raw_state, Mapping) or not isinstance(raw_actions, (list, tuple)):
            raise ValueError("game_state_payload_invalid")
        try:
            scores = {_positive_int(key, "game_state_score_key_invalid"): int(score) for key, score in raw_scores.items()}
        except (TypeError, ValueError):
            raise ValueError("game_state_scores_invalid") from None
        if any(score < 0 or score > 10**9 for score in scores.values()):
            raise ValueError("game_state_scores_invalid")
        processed_actions = tuple(str(item).strip() for item in raw_actions)
        if any(not action or len(action) > 180 or any(char.isspace() for char in action) for action in processed_actions):
            raise ValueError("game_state_actions_invalid")
        if len(set(processed_actions)) != len(processed_actions) or len(processed_actions) > 200:
            raise ValueError("game_state_actions_invalid")
        started_at = _dt(value.get("started_at"))
        updated_at = _dt(value.get("updated_at"))
        expires_at = _dt(value.get("expires_at"))
        # An active session may be recovered after its deadline elapsed; the
        # timeout is domain state, not a malformed timestamp. Only chronology
        # before the session start is invalid.
        if updated_at < started_at or expires_at < started_at:
            raise ValueError("game_state_datetime_order_invalid")
        try:
            schema_version = int(value.get("schema_version", definition.schema_version))
        except (TypeError, ValueError):
            raise ValueError("game_state_schema_version_invalid") from None
        if schema_version < 1 or schema_version > definition.schema_version:
            raise ValueError("unsupported_game_schema")
        state_key = str(value.get("state_key") or definition.state_key).strip().casefold()
        if state_key != definition.state_key:
            raise ValueError("game_state_key_mismatch")
        raw_board_size = value.get("board_size", definition.board_size)
        board_size: tuple[int, int] | None
        if raw_board_size is None:
            board_size = None
        else:
            if not isinstance(raw_board_size, (list, tuple)) or len(raw_board_size) != 2:
                raise ValueError("game_state_board_size_invalid")
            try:
                board_size = (int(raw_board_size[0]), int(raw_board_size[1]))
            except (TypeError, ValueError):
                raise ValueError("game_state_board_size_invalid") from None
            legacy_tictactoe_board = (
                game_key == "tictactoe" and board_size == (3, 3)
            )
            selectable_tictactoe_board = (
                game_key == "tictactoe"
                and board_size[0] == board_size[1]
                and board_size[0] in (6, 7, 8)
            )
            selectable_gomoku_board = (
                game_key == "gomoku"
                and board_size[0] == board_size[1]
                and 6 <= board_size[0] <= 15
            )
            if board_size != definition.board_size and not (
                legacy_tictactoe_board
                or selectable_tictactoe_board
                or selectable_gomoku_board
            ):
                raise ValueError("game_state_board_size_mismatch")
        connect_length = value.get("connect_length", definition.connect_length)
        if connect_length is not None:
            try:
                connect_length = int(connect_length)
            except (TypeError, ValueError):
                raise ValueError("game_state_connect_length_invalid") from None
            if connect_length != definition.connect_length and not (
                game_key == "tictactoe"
                and board_size == (3, 3)
                and connect_length == 3
            ):
                raise ValueError("game_state_connect_length_mismatch")
        return cls(
            session_id=session_id,
            game_key=game_key,
            chat_id=chat_id,
            owner_id=owner_id,
            players=players,
            status=status,
            difficulty=difficulty,
            turn_index=turn_index,
            turn_user_id=turn_user_id,
            combo=combo,
            scores=scores,
            state=dict(raw_state),
            processed_actions=processed_actions,
            started_at=started_at,
            updated_at=updated_at,
            expires_at=expires_at,
            version=version,
            schema_version=schema_version,
            state_key=state_key,
            board_size=board_size,
            connect_length=connect_length,
        )


def _fold(value: object) -> str:
    text = str(value or "").strip().casefold()
    return "".join(
        char for char in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(char)
    )


_CATEGORY_ALIASES = {
    _fold(canonical): canonical for canonical in WORD_GAME_CATEGORY_LABELS
}
_CATEGORY_ALIASES.update(
    {
        "sehir": "şehirler",
        "sehirler": "şehirler",
        "sehir hayati": "şehirler",
        "şehir": "şehirler",
        "şehirler": "şehirler",
        "dogal": "doğa",
        "doga": "doğa",
        "doğa": "doğa",
        "uzay bilimleri": "uzay",
        "meslek": "meslekler",
        "meslek hayati": "meslekler",
    }
)


def normalize_word_game_category(value: object) -> str | None:
    """Return a canonical category for Turkish and ASCII aliases."""

    return _CATEGORY_ALIASES.get(_fold(value))


GAME_DEFINITIONS: dict[str, GameDefinition] = {
    "word_game": GameDefinition("word_game", "Kelime Oyunu", category="word", max_players=30, turn_timeout_seconds=240),
    "word_guess": GameDefinition("word_guess", "Anlatmaca", category="word", max_players=30, turn_timeout_seconds=240),
    "hangman": GameDefinition("hangman", "Adam Asmaca", category="word", max_players=30, turn_timeout_seconds=240),
    "connect4": GameDefinition("connect4", "Connect Four", category="board", min_players=2, max_players=2, turn_timeout_seconds=180, schema_version=1, state_key="connect4", board_size=(6, 7), connect_length=4),
    # Gomoku's immutable move log lives in ``src.games.gomoku``; this
    # definition supplies the durable GameSession envelope used by recovery.
    # The engine supports 6..15 boards; the lobby default is 10x10.
    "gomoku": GameDefinition("gomoku", "Gomoku / Beşli", category="board", min_players=2, max_players=2, turn_timeout_seconds=300, schema_version=1, state_key="gomoku", board_size=(10, 10), connect_length=5),
    "chess": GameDefinition("chess", "Satranç", category="board", min_players=2, max_players=2, turn_timeout_seconds=1800, schema_version=1, state_key="chess", board_size=(8, 8)),
    "akinator": GameDefinition("akinator", "Akinatör", category="quiz", min_players=1, max_players=1, turn_timeout_seconds=900, schema_version=1, state_key="akinator"),
    "guess_age": GameDefinition("guess_age", "Yaş Tahmini", category="quiz", min_players=1, max_players=100, turn_timeout_seconds=600, schema_version=1, state_key="guess_age"),
    "uno": GameDefinition("uno", "UNO", category="card", min_players=2, max_players=8, turn_timeout_seconds=60, schema_version=1, state_key="uno"),
    "kelime_zinciri": GameDefinition("kelime_zinciri", "Kelime Zinciri", category="word", min_players=2, max_players=20, turn_timeout_seconds=30, schema_version=2, state_key="kelime_zinciri"),
    "tabu": GameDefinition("tabu", "Takım Tabu", category="party", min_players=4, max_players=20, turn_timeout_seconds=60, supports_difficulty=False, supports_hints=False, state_key="tabu"),
    "casus": GameDefinition("casus", "Casus", category="party", min_players=4, max_players=16, turn_timeout_seconds=90, supports_difficulty=False, supports_hints=False, state_key="casus"),
    "vampir_koylu": GameDefinition("vampir_koylu", "Vampir Köylü", category="party", min_players=7, max_players=20, turn_timeout_seconds=60, supports_difficulty=False, supports_hints=False, state_key="vampir_koylu"),
    # New rounds use a selectable 6x6, 7x7 or 8x8 board and four-in-a-row.
    # The UI domain still reads legacy 3x3 snapshots for restart compatibility.
    "tictactoe": GameDefinition("tictactoe", "XOX", category="board", min_players=2, max_players=2, turn_timeout_seconds=180, schema_version=1, state_key="tictactoe", board_size=(6, 6), connect_length=4),
    "quiz": GameDefinition("quiz", "Bilgi Yarışması", category="quiz", max_players=100, turn_timeout_seconds=180),
    # Wordle is a user-scoped single-player session. Puzzle payload (answer
    # hash, guesses and keyboard state) stays in generic session state so a
    # restart preserves the board without persisting the plaintext answer.
    "wordle_tr": GameDefinition("wordle_tr", "Türkçe Wordle", category="word", min_players=1, max_players=1, turn_timeout_seconds=900, schema_version=1, state_key="wordle_tr"),
    "batak": GameDefinition("batak", "Batak", category="card", min_players=2, max_players=4, turn_timeout_seconds=45),
    "blackjack": GameDefinition("blackjack", "Blackjack", category="card", max_players=1, turn_timeout_seconds=90),
    "bottle": GameDefinition("bottle", "Şişe Çevirme", category="social", max_players=100, turn_timeout_seconds=120),
}


from src.games.puzzle_catalog import PUZZLES

for _puzzle in PUZZLES:
    GAME_DEFINITIONS[_puzzle.key] = GameDefinition(
        _puzzle.key, _puzzle.title, category={"words": "word", "boards": "board", "guess": "quiz", "social": "party", "cards": "card"}[_puzzle.category],
        min_players=_puzzle.min_players, max_players=_puzzle.max_players,
        state_key=_puzzle.key, supports_difficulty=_puzzle.key == "sudoku",
        supports_hints=_puzzle.key in {"wordle_tr", "anagram", "sudoku"},
    )


def get_game_definition(game_key: object) -> GameDefinition:
    key = str(game_key or "").strip().casefold()
    try:
        return GAME_DEFINITIONS[key]
    except KeyError:
        raise ValueError("unknown_game_type") from None


def register_game_definition(definition: GameDefinition) -> GameDefinition:
    """Register a definition for a plugin/replay without replacing silently."""
    if definition.key in GAME_DEFINITIONS:
        raise ValueError("game_definition_exists")
    GAME_DEFINITIONS[definition.key] = definition
    return definition
