"""Idempotent action claims for interactive Telegram games.

Telegram may deliver a callback more than once (for example after a slow
network response or a client retry).  Game state mutations and rewards must
therefore have a durable, chat-scoped action key.  The ledger uses MongoDB in
production and a bounded process-local set for tests/degraded development;
production startup still requires MongoDB for game persistence.
"""

from __future__ import annotations

import asyncio
from typing import Any


class GameActionLedger:
    """Claim/release callback actions exactly once per action id."""

    MAX_ACTION_ID = 180

    def __init__(self, database: Any | None = None) -> None:
        self.database = database
        self._claimed: set[str] = set()
        self._lock = asyncio.Lock()

    @staticmethod
    def _key(chat_id: int, game_type: str, action_id: str) -> str:
        action = str(action_id).strip()
        if not action or len(action) > GameActionLedger.MAX_ACTION_ID:
            raise ValueError("invalid_game_action_id")
        game = str(game_type).strip().casefold()
        if not game or len(game) > 64 or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for char in game):
            raise ValueError("invalid_game_type")
        return f"{int(chat_id)}:{game}:{action}"

    async def claim(
        self,
        chat_id: int,
        game_type: str,
        action_id: str,
        *,
        user_id: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> bool:
        """Atomically claim an action, returning ``False`` for a retry."""
        key = self._key(chat_id, game_type, action_id)
        claim_method = getattr(self.database, "claim_game_action", None)
        database_available = getattr(self.database, "available", True)
        supports = getattr(self.database, "supports", None)
        durable = supports("claim_game_action") if callable(supports) else claim_method is not None
        if claim_method is not None and durable and database_available is not False:
            return bool(
                await claim_method(
                    action_key=key,
                    chat_id=int(chat_id),
                    game_type=str(game_type),
                    action_id=str(action_id),
                    user_id=int(user_id) if user_id is not None else None,
                    payload=dict(payload or {}),
                )
            )
        async with self._lock:
            if key in self._claimed:
                return False
            self._claimed.add(key)
            return True

    async def release(self, chat_id: int, game_type: str, action_id: str) -> None:
        """Release a claim when the mutation failed before being committed."""
        key = self._key(chat_id, game_type, action_id)
        release_method = getattr(self.database, "release_game_action", None)
        database_available = getattr(self.database, "available", True)
        supports = getattr(self.database, "supports", None)
        durable = supports("release_game_action") if callable(supports) else release_method is not None
        if release_method is not None and durable and database_available is not False:
            await release_method(key)
            return
        async with self._lock:
            self._claimed.discard(key)

    async def close(self) -> None:
        async with self._lock:
            self._claimed.clear()
