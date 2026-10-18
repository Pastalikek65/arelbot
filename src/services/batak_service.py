"""Persistent group Batak orchestration and settlement."""

from __future__ import annotations

import logging
from typing import Any

from src.games.action_ledger import GameActionLedger
from src.games.batak_round import BatakRound

logger = logging.getLogger(__name__)


class BatakService:
    GAME_TYPE = "batak"

    def __init__(self, database: Any, *, leaderboard_service=None, economy_service=None, action_ledger=None):
        self.database = database
        self.leaderboard = leaderboard_service
        self.economy = economy_service
        self.actions = action_ledger or GameActionLedger(database)

    async def _load(self, chat_id: int) -> dict[str, Any] | None:
        getter = getattr(self.database, "get_game_state", None)
        state = await getter(int(chat_id), self.GAME_TYPE) if getter else None
        return state if isinstance(state, dict) else None

    async def _save(self, chat_id: int, state: dict[str, Any]) -> None:
        saver = getattr(self.database, "save_game_state", None)
        if saver:
            await saver(int(chat_id), self.GAME_TYPE, state)

    async def _delete(self, chat_id: int) -> None:
        deleter = getattr(self.database, "delete_game_state", None)
        if deleter:
            await deleter(int(chat_id), self.GAME_TYPE)

    async def cancel(self, *, chat_id: int, user_id: int, moderator: bool = False) -> dict[str, Any]:
        """Cancel a persistent lobby/round by its owner or group moderator."""
        state = await self._load(chat_id)
        if not state:
            raise ValueError("game_expired")
        if not moderator and int(state.get("owner_id", 0)) != int(user_id):
            raise ValueError("not_owner")
        await self._delete(chat_id)
        return {"status": "cancelled", "terminal": True}

    async def create_lobby(self, *, chat_id: int, owner_id: int, owner_name: str) -> dict[str, Any]:
        existing = await self._load(chat_id)
        if existing and existing.get("phase") in {"lobby", "playing"}:
            raise ValueError("active_game")
        state = BatakRound.lobby(owner_id, owner_name)
        await self._save(chat_id, state)
        return {"state": state, "text": BatakRound.render(state, owner_id), "status": "created"}

    async def join(self, *, chat_id: int, user_id: int, user_name: str, action_id: str) -> dict[str, Any]:
        claimed = await self.actions.claim(chat_id, self.GAME_TYPE, action_id, user_id=user_id, payload={"action": "join"})
        if not claimed:
            raise ValueError("duplicate_action")
        try:
            state = await self._load(chat_id)
            if not state:
                raise ValueError("game_expired")
            result = BatakRound.join(state, user_id, user_name)
            await self._save(chat_id, state)
            return {"state": state, "status": result, "text": BatakRound.render(state, user_id)}
        except Exception:
            await self.actions.release(chat_id, self.GAME_TYPE, action_id)
            raise

    async def start(self, *, chat_id: int, user_id: int, action_id: str) -> dict[str, Any]:
        claimed = await self.actions.claim(chat_id, self.GAME_TYPE, action_id, user_id=user_id, payload={"action": "start"})
        if not claimed:
            raise ValueError("duplicate_action")
        try:
            state = await self._load(chat_id)
            if not state:
                raise ValueError("game_expired")
            if int(state.get("owner_id", 0)) != int(user_id):
                raise ValueError("not_owner")
            BatakRound.start(state)
            await self._save(chat_id, state)
            return {"state": state, "status": "started", "text": BatakRound.render(state, user_id)}
        except Exception:
            await self.actions.release(chat_id, self.GAME_TYPE, action_id)
            raise

    async def play(self, *, chat_id: int, user_id: int, card_index: int, action_id: str) -> dict[str, Any]:
        claimed = await self.actions.claim(chat_id, self.GAME_TYPE, action_id, user_id=user_id, payload={"action": "play", "card_index": card_index})
        if not claimed:
            raise ValueError("duplicate_action")
        try:
            state = await self._load(chat_id)
            if not state:
                raise ValueError("game_expired")
            outcome = BatakRound.play(state, user_id, card_index)
            if outcome.get("phase") == "completed":
                await self._settle(chat_id, state)
                text = BatakRound.render(state, user_id)
                await self._delete(chat_id)
                return {"state": state, "status": "completed", "terminal": True, "text": text}
            await self._save(chat_id, state)
            return {"state": state, "status": "playing", "terminal": False, "text": BatakRound.render(state, user_id)}
        except Exception:
            await self.actions.release(chat_id, self.GAME_TYPE, action_id)
            raise

    async def _settle(self, chat_id: int, state: dict[str, Any]) -> None:
        scores = state.get("scores", {})
        game_id = str(state.get("game_id", state.get("completed_tricks", 0)))[:80]
        for player in state.get("players", []):
            user_id = int(player["id"])
            score = max(-100, min(100, int(scores.get(str(user_id), 0))))
            event_id = f"batak:{chat_id}:{game_id}:{user_id}"
            if self.leaderboard:
                try:
                    await self.leaderboard.record_score(
                        user_id=user_id, chat_id=int(chat_id), game_type="batak",
                        score=score, user_name=player.get("name"), event_id=event_id,
                    )
                except Exception:
                    logger.warning("Batak leaderboard settlement failed", exc_info=True)
            if self.economy and score > 0:
                try:
                    await self.economy.credit(
                        user_id, int(chat_id), min(score, 100), reason="batak_reward", event_id=f"coin:{event_id}"
                    )
                except Exception:
                    logger.warning("Batak economy settlement failed", exc_info=True)
