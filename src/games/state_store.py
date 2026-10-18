"""Persistence helpers for interactive game state."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from .catalog import GameSession

GAME_KEYS = {
    "hangman": "hangman",
    "word_game": "word_game",
    "word_guess": "word_guess",
    "connect4": "connect4",
    "tictactoe": "tictactoe",
    "gomoku": "gomoku",
    "wordle_tr": "wordle_tr",
    "batak_players": "batak",
    "bottle_players": "bottle",
    "quiz_answer": "quiz",
}


class GameStateStore(Protocol):
    """Versioned session store contract used by game workers and callbacks."""

    async def get(self, session_id: str) -> GameSession | None: ...
    async def create(self, session: GameSession) -> bool: ...
    async def save(self, session: GameSession, *, expected_version: int) -> bool: ...
    async def delete(self, session_id: str) -> bool: ...

    async def get_for_chat(self, chat_id: int, game_key: str) -> GameSession | None: ...

    async def snapshot(self) -> list[dict[str, Any]]: ...

    async def restore(self, rows: Sequence[Mapping[str, Any]]) -> int: ...


class InMemoryGameStateStore:
    """Bounded, deterministic store for tests and local replay runs."""

    def __init__(self, *, max_sessions: int = 10000) -> None:
        self.max_sessions = max(1, int(max_sessions))
        self._sessions: dict[str, GameSession] = {}
        self._state_keys: dict[str, str] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _copy(session: GameSession) -> GameSession:
        """Round-trip through the persistence contract to isolate callers."""

        return GameSession.from_dict(session.to_dict())

    async def get(self, session_id: str) -> GameSession | None:
        async with self._lock:
            session = self._sessions.get(str(session_id))
            return self._copy(session) if session is not None else None

    async def create(self, session: GameSession) -> bool:
        normalized = self._copy(session)
        async with self._lock:
            if (
                normalized.session_id in self._sessions
                or normalized.storage_key in self._state_keys
                or len(self._sessions) >= self.max_sessions
            ):
                return False
            self._sessions[normalized.session_id] = normalized
            self._state_keys[normalized.storage_key] = normalized.session_id
            return True

    async def save(self, session: GameSession, *, expected_version: int) -> bool:
        normalized = self._copy(session)
        async with self._lock:
            current = self._sessions.get(normalized.session_id)
            if current is None or current.version != int(expected_version):
                return False
            if normalized.storage_key != current.storage_key:
                owner = self._state_keys.get(normalized.storage_key)
                if owner not in (None, normalized.session_id):
                    return False
                self._state_keys.pop(current.storage_key, None)
                self._state_keys[normalized.storage_key] = normalized.session_id
            self._sessions[normalized.session_id] = normalized
            return True

    async def delete(self, session_id: str) -> bool:
        async with self._lock:
            session = self._sessions.pop(str(session_id), None)
            if session is None:
                return False
            self._state_keys.pop(session.storage_key, None)
            return True

    async def get_for_chat(self, chat_id: int, game_key: str) -> GameSession | None:
        key = f"game:{int(chat_id)}:{str(game_key).strip().casefold()}"
        async with self._lock:
            session_id = self._state_keys.get(key)
            session = self._sessions.get(session_id) if session_id else None
            return self._copy(session) if session is not None else None

    async def snapshot(self) -> list[dict[str, Any]]:
        """Return JSON-compatible records for restart/recovery workers."""

        async with self._lock:
            return [session.to_dict() for session in self._sessions.values()]

    async def restore(self, rows: Sequence[Mapping[str, Any]]) -> int:
        """Atomically recover validated sessions from a durable snapshot.

        Parsing happens before acquiring the lock, so one malformed record
        cannot leave a partially restored in-memory store.
        """

        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
            raise ValueError("game_state_snapshot_invalid")
        parsed = [self._copy(GameSession.from_dict(row)) for row in rows]
        if len(parsed) > self.max_sessions:
            raise ValueError("game_state_snapshot_capacity_exceeded")
        ids = [item.session_id for item in parsed]
        keys = [item.storage_key for item in parsed]
        if len(set(ids)) != len(ids) or len(set(keys)) != len(keys):
            raise ValueError("game_state_snapshot_duplicate")
        async with self._lock:
            self._sessions = {item.session_id: item for item in parsed}
            self._state_keys = {item.storage_key: item.session_id for item in parsed}
        return len(parsed)

    async def list_for_chat(self, chat_id: int, *, status: str | None = None) -> list[GameSession]:
        async with self._lock:
            return [
                self._copy(session) for session in self._sessions.values()
                if session.chat_id == int(chat_id) and (status is None or session.status == status)
            ]


async def hydrate_games(database, chat_id: int, chat_data) -> None:
    if getattr(database, "reachable", None) is False:
        return
    for key, game_type in GAME_KEYS.items():
        try:
            state = await database.get_game_state(chat_id, game_type)
        except Exception:
            continue
        if state is not None and key not in chat_data:
            if game_type == "quiz":
                chat_data.setdefault("quiz_answer", state.get("answer", ""))
                chat_data.setdefault("quiz_question", state.get("question", ""))
            elif game_type == "batak":
                chat_data["batak_players"] = list(state.get("players", []))
                chat_data["batak_names"] = list(state.get("names", []))
            elif game_type == "bottle":
                chat_data["bottle_players"] = list(state.get("players", []))
            else:
                chat_data[key] = state


async def persist_game(database, chat_id: int, game_type: str, state) -> None:
    try:
        payload: Any = state.to_dict() if isinstance(state, GameSession) else state
        await database.save_game_state(chat_id, game_type, payload)
    except Exception:
        return


async def delete_game(database, chat_id: int, game_type: str) -> None:
    try:
        await database.delete_game_state(chat_id, game_type)
    except Exception:
        return
