"""Deterministic Gomoku/Connect-N game core.

The Telegram surface can render this state with any button layout.  This
module intentionally has no Telegram, database or network dependency: a game
is a small immutable value object, and every move returns a new state.  That
makes replay, compare-and-swap persistence and recovery tests deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from typing import Any, Iterable, Mapping, Sequence


MIN_BOARD_SIZE = 6
MAX_BOARD_SIZE = 15
DEFAULT_BOARD_SIZE = 10
DEFAULT_WIN_LENGTH = 5
SCHEMA_VERSION = 1


class GomokuStatus(str, Enum):
    """Stable result codes returned by :func:`apply_move`."""

    ACTIVE = "active"
    MOVED = "moved"
    WON = "won"
    DRAW = "draw"
    NOT_A_PLAYER = "not_a_player"
    NOT_YOUR_TURN = "not_your_turn"
    INVALID_COORDINATE = "invalid_coordinate"
    OCCUPIED = "occupied"
    FINISHED = "finished"
    DUPLICATE_ACTION = "duplicate_action"
    INVALID_STATE = "invalid_state"


@dataclass(frozen=True, slots=True)
class GomokuConfig:
    """Bounded board configuration shared by the game and replay helpers."""

    board_size: int = DEFAULT_BOARD_SIZE
    win_length: int = DEFAULT_WIN_LENGTH

    def __post_init__(self) -> None:
        if isinstance(self.board_size, bool) or not isinstance(self.board_size, int):
            raise ValueError("invalid_gomoku_board_size")
        if not MIN_BOARD_SIZE <= self.board_size <= MAX_BOARD_SIZE:
            raise ValueError("invalid_gomoku_board_size")
        if isinstance(self.win_length, bool) or not isinstance(self.win_length, int):
            raise ValueError("invalid_gomoku_win_length")
        if self.win_length < 3 or self.win_length > self.board_size:
            raise ValueError("invalid_gomoku_win_length")

    @property
    def cells(self) -> int:
        return self.board_size * self.board_size


@dataclass(frozen=True, slots=True)
class GomokuMove:
    """One accepted board mutation, suitable for a durable replay log."""

    turn: int
    player_id: int
    row: int
    column: int
    action_id: str

    def __post_init__(self) -> None:
        if isinstance(self.turn, bool) or not isinstance(self.turn, int) or self.turn < 1:
            raise ValueError("invalid_gomoku_move_turn")
        if isinstance(self.player_id, bool) or not isinstance(self.player_id, int) or self.player_id <= 0:
            raise ValueError("invalid_gomoku_move_player")
        if isinstance(self.row, bool) or isinstance(self.column, bool) or not isinstance(self.row, int) or not isinstance(self.column, int):
            raise ValueError("invalid_gomoku_move_coordinate")
        if self.row < 0 or self.column < 0:
            raise ValueError("invalid_gomoku_move_coordinate")
        action = str(self.action_id or "").strip()
        if not action or len(action) > 180:
            raise ValueError("invalid_gomoku_move_action")
        object.__setattr__(self, "action_id", action)

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn": int(self.turn),
            "player_id": int(self.player_id),
            "row": int(self.row),
            "column": int(self.column),
            "action_id": self.action_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "GomokuMove":
        if not isinstance(value, Mapping):
            raise ValueError("invalid_gomoku_move")
        try:
            return cls(
                turn=int(value["turn"]),
                player_id=int(value["player_id"]),
                row=int(value["row"]),
                column=int(value["column"]),
                action_id=str(value["action_id"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, ValueError) and str(exc).startswith("invalid_gomoku_"):
                raise
            raise ValueError("invalid_gomoku_move") from None


@dataclass(frozen=True, slots=True)
class GomokuState:
    """Immutable, validated game state.

    ``board`` stores marker values 0, 1 and 2.  Marker 1 belongs to
    ``players[0]`` and marker 2 to ``players[1]``; Telegram IDs never appear
    in the board and therefore cannot accidentally become a UI token.
    """

    game_id: str
    chat_id: int
    players: tuple[int, int]
    config: GomokuConfig
    board: tuple[int, ...]
    turn_index: int
    status: GomokuStatus
    winner_id: int | None
    moves: tuple[GomokuMove, ...]
    version: int = 0
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        game_id = str(self.game_id or "").strip()
        if len(game_id) > 180:
            raise ValueError("invalid_gomoku_game_id")
        object.__setattr__(self, "game_id", game_id)
        if isinstance(self.chat_id, bool) or int(self.chat_id) == 0:
            raise ValueError("invalid_gomoku_scope")
        if len(self.players) != 2 or any(isinstance(item, bool) or int(item) <= 0 for item in self.players):
            raise ValueError("invalid_gomoku_players")
        if self.players[0] == self.players[1]:
            raise ValueError("invalid_gomoku_players")
        if len(self.board) != self.config.cells or any(value not in (0, 1, 2) for value in self.board):
            raise ValueError("invalid_gomoku_board")
        if self.turn_index not in (0, 1):
            raise ValueError("invalid_gomoku_turn")
        if not isinstance(self.status, GomokuStatus):
            try:
                object.__setattr__(self, "status", GomokuStatus(str(self.status)))
            except ValueError:
                raise ValueError("invalid_gomoku_status") from None
        if self.winner_id is not None and self.winner_id not in self.players:
            raise ValueError("invalid_gomoku_winner")
        if int(self.version) < 0 or int(self.version) != len(self.moves):
            raise ValueError("invalid_gomoku_version")
        if int(self.schema_version) != SCHEMA_VERSION:
            raise ValueError("invalid_gomoku_schema_version")

    @property
    def turn_player_id(self) -> int:
        return self.players[self.turn_index]

    @property
    def move_count(self) -> int:
        return len(self.moves)

    def index_for(self, row: int, column: int) -> int:
        _validate_coordinate(self.config, row, column)
        return int(row) * self.config.board_size + int(column)

    def coordinates_for(self, index: int) -> tuple[int, int]:
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < self.config.cells:
            raise ValueError("invalid_gomoku_cell")
        return divmod(index, self.config.board_size)

    def available_moves(self) -> tuple[tuple[int, int], ...]:
        return tuple(self.coordinates_for(index) for index, value in enumerate(self.board) if value == 0)

    def winning_line_for(self, row: int, column: int, *, marker: int | None = None) -> tuple[tuple[int, int], ...]:
        _validate_coordinate(self.config, row, column)
        cell_marker = int(marker if marker is not None else self.board[self.index_for(row, column)])
        if cell_marker not in (1, 2):
            return ()
        best: tuple[tuple[int, int], ...] = ()
        for delta_row, delta_column in ((0, 1), (1, 0), (1, 1), (1, -1)):
            negative = _walk(self, row, column, -delta_row, -delta_column, cell_marker)
            positive = _walk(self, row, column, delta_row, delta_column, cell_marker)
            line = tuple(reversed(negative)) + ((int(row), int(column)),) + positive
            if len(line) > len(best):
                best = line
        return best if len(best) >= self.config.win_length else ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "game_id": self.game_id,
            "chat_id": int(self.chat_id),
            "players": [int(item) for item in self.players],
            "config": {
                "board_size": self.config.board_size,
                "win_length": self.config.win_length,
            },
            "board": list(self.board),
            "turn_index": int(self.turn_index),
            "status": self.status.value,
            "winner_id": self.winner_id,
            "moves": [move.to_dict() for move in self.moves],
            "version": int(self.version),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "GomokuState":
        if not isinstance(value, Mapping):
            raise ValueError("invalid_gomoku_state")
        try:
            config_data = value["config"]
            config = GomokuConfig(board_size=int(config_data["board_size"]), win_length=int(config_data["win_length"]))
            moves = tuple(GomokuMove.from_dict(item) for item in value.get("moves", ()))
            if int(value.get("schema_version", 0)) != SCHEMA_VERSION:
                raise ValueError("invalid_gomoku_schema_version")
            replayed = replay_moves(
                chat_id=int(value["chat_id"]),
                players=tuple(int(item) for item in value["players"]),
                board_size=config.board_size,
                win_length=config.win_length,
                game_id=str(value.get("game_id", "")),
                moves=moves,
            )
            expected = dict(value)
            if tuple(int(item) for item in expected.get("board", ())) != replayed.board:
                raise ValueError("invalid_gomoku_board")
            if int(expected.get("turn_index", -1)) != replayed.turn_index:
                raise ValueError("invalid_gomoku_turn")
            if str(expected.get("status", "")) != replayed.status.value:
                raise ValueError("invalid_gomoku_status")
            if expected.get("winner_id") != replayed.winner_id:
                raise ValueError("invalid_gomoku_winner")
            if int(expected.get("version", -1)) != replayed.version:
                raise ValueError("invalid_gomoku_version")
            return replayed
        except ValueError:
            raise
        except (KeyError, TypeError, IndexError):
            raise ValueError("invalid_gomoku_state") from None


@dataclass(frozen=True, slots=True)
class GomokuMoveResult:
    """Pure result envelope for an attempted move."""

    status: GomokuStatus
    state: GomokuState
    move: GomokuMove | None = None
    winning_line: tuple[tuple[int, int], ...] = ()
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.status in {GomokuStatus.MOVED, GomokuStatus.WON, GomokuStatus.DRAW}


def create_game(
    *,
    chat_id: int,
    players: Sequence[int],
    board_size: int = DEFAULT_BOARD_SIZE,
    win_length: int = DEFAULT_WIN_LENGTH,
    game_id: str = "",
) -> GomokuState:
    """Create a two-player game with a deterministic empty board."""

    config = GomokuConfig(board_size=board_size, win_length=win_length)
    if isinstance(chat_id, bool) or int(chat_id) == 0:
        raise ValueError("invalid_gomoku_scope")
    if len(players) != 2 or any(isinstance(item, bool) or int(item) <= 0 for item in players):
        raise ValueError("invalid_gomoku_players")
    normalized_players = (int(players[0]), int(players[1]))
    if normalized_players[0] == normalized_players[1]:
        raise ValueError("invalid_gomoku_players")
    return GomokuState(
        game_id=str(game_id or "").strip(),
        chat_id=int(chat_id),
        players=normalized_players,
        config=config,
        board=(0,) * config.cells,
        turn_index=0,
        status=GomokuStatus.ACTIVE,
        winner_id=None,
        moves=(),
        version=0,
    )


def apply_move(
    state: GomokuState,
    *,
    player_id: int,
    row: int,
    column: int,
    action_id: str | None = None,
) -> GomokuMoveResult:
    """Validate and apply one move without mutating the input state."""

    if not isinstance(state, GomokuState):
        raise TypeError("state must be GomokuState")
    if isinstance(player_id, bool) or int(player_id) not in state.players:
        return GomokuMoveResult(GomokuStatus.NOT_A_PLAYER, state, reason="player is not in this game")
    action = str(action_id or f"move-{state.move_count + 1}").strip()
    if not action or len(action) > 180:
        return GomokuMoveResult(GomokuStatus.INVALID_STATE, state, reason="invalid action id")
    if any(move.action_id == action for move in state.moves):
        return GomokuMoveResult(GomokuStatus.DUPLICATE_ACTION, state, reason="action already applied")
    if state.status in {GomokuStatus.WON, GomokuStatus.DRAW, GomokuStatus.FINISHED}:
        return GomokuMoveResult(GomokuStatus.FINISHED, state, reason="game is finished")
    if int(player_id) != state.turn_player_id:
        return GomokuMoveResult(GomokuStatus.NOT_YOUR_TURN, state, reason="another player owns the turn")
    try:
        index = state.index_for(row, column)
    except (TypeError, ValueError):
        return GomokuMoveResult(GomokuStatus.INVALID_COORDINATE, state, reason="coordinate is outside board")
    if state.board[index] != 0:
        return GomokuMoveResult(GomokuStatus.OCCUPIED, state, reason="cell is occupied")

    marker = 1 if int(player_id) == state.players[0] else 2
    board = list(state.board)
    board[index] = marker
    move = GomokuMove(state.move_count + 1, int(player_id), int(row), int(column), action)
    moves = (*state.moves, move)
    candidate = GomokuState(
        game_id=state.game_id,
        chat_id=state.chat_id,
        players=state.players,
        config=state.config,
        board=tuple(board),
        turn_index=(state.turn_index + 1) % 2,
        status=GomokuStatus.MOVED,
        winner_id=None,
        moves=moves,
        version=state.version + 1,
    )
    line = candidate.winning_line_for(row, column, marker=marker)
    if line:
        candidate = GomokuState(
            game_id=candidate.game_id,
            chat_id=candidate.chat_id,
            players=candidate.players,
            config=candidate.config,
            board=candidate.board,
            turn_index=candidate.turn_index,
            status=GomokuStatus.WON,
            winner_id=int(player_id),
            moves=candidate.moves,
            version=candidate.version,
        )
        return GomokuMoveResult(GomokuStatus.WON, candidate, move, line)
    if not any(value == 0 for value in board):
        candidate = GomokuState(
            game_id=candidate.game_id,
            chat_id=candidate.chat_id,
            players=candidate.players,
            config=candidate.config,
            board=candidate.board,
            turn_index=candidate.turn_index,
            status=GomokuStatus.DRAW,
            winner_id=None,
            moves=candidate.moves,
            version=candidate.version,
        )
        return GomokuMoveResult(GomokuStatus.DRAW, candidate, move)
    return GomokuMoveResult(GomokuStatus.MOVED, candidate, move)


def replay_moves(
    *,
    chat_id: int,
    players: Sequence[int],
    board_size: int = DEFAULT_BOARD_SIZE,
    win_length: int = DEFAULT_WIN_LENGTH,
    game_id: str = "",
    moves: Iterable[GomokuMove | Mapping[str, Any]],
) -> GomokuState:
    """Rebuild a state from accepted moves and reject any tampering."""

    state = create_game(
        chat_id=chat_id,
        players=players,
        board_size=board_size,
        win_length=win_length,
        game_id=game_id,
    )
    for expected_turn, raw_move in enumerate(moves, start=1):
        move = raw_move if isinstance(raw_move, GomokuMove) else GomokuMove.from_dict(raw_move)
        if move.turn != expected_turn:
            raise ValueError("invalid_gomoku_replay")
        result = apply_move(
            state,
            player_id=move.player_id,
            row=move.row,
            column=move.column,
            action_id=move.action_id,
        )
        if not result.ok:
            raise ValueError("invalid_gomoku_replay")
        state = result.state
    return state


def _validate_coordinate(config: GomokuConfig, row: int, column: int) -> None:
    if isinstance(row, bool) or isinstance(column, bool) or not isinstance(row, int) or not isinstance(column, int):
        raise ValueError("invalid_gomoku_coordinate")
    if not 0 <= row < config.board_size or not 0 <= column < config.board_size:
        raise ValueError("invalid_gomoku_coordinate")


def _walk(
    state: GomokuState,
    row: int,
    column: int,
    delta_row: int,
    delta_column: int,
    marker: int,
) -> tuple[tuple[int, int], ...]:
    cells: list[tuple[int, int]] = []
    current_row = int(row) + delta_row
    current_column = int(column) + delta_column
    size = state.config.board_size
    while 0 <= current_row < size and 0 <= current_column < size:
        if state.board[current_row * size + current_column] != marker:
            break
        cells.append((current_row, current_column))
        current_row += delta_row
        current_column += delta_column
    return tuple(cells)


__all__ = [
    "DEFAULT_BOARD_SIZE",
    "DEFAULT_WIN_LENGTH",
    "GomokuConfig",
    "GomokuMove",
    "GomokuMoveResult",
    "GomokuState",
    "GomokuStatus",
    "MAX_BOARD_SIZE",
    "MIN_BOARD_SIZE",
    "SCHEMA_VERSION",
    "apply_move",
    "create_game",
    "replay_moves",
]
