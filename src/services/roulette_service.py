"""Persistent, idempotent group roulette game service.

The old roulette command generated an isolated random sentence on every
request. This service owns the lobby, turn order and chamber state so a
callback retry or a process restart cannot silently create a second game.
"""

from __future__ import annotations

import copy
import html
import uuid
from datetime import datetime, timezone
from typing import Any

from src.games.action_ledger import GameActionLedger
from src.games.roulette import RouletteGame


class RouletteService:
    GAME_TYPE = "roulette"
    MAX_PLAYERS = 8

    def __init__(
        self,
        database: Any,
        *,
        action_ledger: GameActionLedger | None = None,
        leaderboard_service: Any | None = None,
        economy_service: Any | None = None,
    ) -> None:
        self.database = database
        self.actions = action_ledger or GameActionLedger(database)
        self.leaderboard = leaderboard_service
        self.economy = economy_service
        self._memory: dict[int, dict[str, Any]] = {}

    async def _load(self, chat_id: int) -> dict[str, Any] | None:
        getter = getattr(self.database, "get_game_state", None)
        if getter is not None and getattr(self.database, "available", True) is not False:
            try:
                state = await getter(int(chat_id), self.GAME_TYPE)
                if isinstance(state, dict):
                    return copy.deepcopy(state)
            except Exception:
                # Degraded local mode retains a bounded process-local state.
                pass
        state = self._memory.get(int(chat_id))
        return copy.deepcopy(state) if state else None

    async def _save(self, chat_id: int, state: dict[str, Any]) -> None:
        snapshot = copy.deepcopy(state)
        self._memory[int(chat_id)] = snapshot
        saver = getattr(self.database, "save_game_state", None)
        if saver is not None and getattr(self.database, "available", True) is not False:
            try:
                await saver(int(chat_id), self.GAME_TYPE, snapshot)
            except Exception:
                pass

    async def _delete(self, chat_id: int) -> None:
        self._memory.pop(int(chat_id), None)
        deleter = getattr(self.database, "delete_game_state", None)
        if deleter is not None and getattr(self.database, "available", True) is not False:
            try:
                await deleter(int(chat_id), self.GAME_TYPE)
            except Exception:
                pass

    @staticmethod
    def _action_id(action_id: str) -> str:
        value = str(action_id or "").strip()
        if not value or len(value) > 180:
            raise ValueError("invalid_game_action_id")
        return value

    async def _claim(self, chat_id: int, user_id: int, action_id: str, action: str) -> bool:
        return await self.actions.claim(
            int(chat_id),
            self.GAME_TYPE,
            self._action_id(action_id),
            user_id=int(user_id),
            payload={"action": action},
        )

    async def _release(self, chat_id: int, action_id: str) -> None:
        await self.actions.release(int(chat_id), self.GAME_TYPE, self._action_id(action_id))

    @staticmethod
    def _player_name(state: dict[str, Any], user_id: int) -> str:
        for player_id, name in zip(state.get("players", []), state.get("player_names", [])):
            if int(player_id) == int(user_id):
                return str(name)[:80]
        return "Oyuncu"

    @classmethod
    def _render(cls, state: dict[str, Any], *, result: str | None = None) -> str:
        names = []
        current = state.get("current_player_id")
        for player_id, name in zip(state.get("players", []), state.get("player_names", [])):
            if player_id in state.get("alive", []):
                marker = " â—€" if current is not None and int(player_id) == int(current) else ""
                names.append(f"â€¢ {html.escape(str(name)[:80])}{marker}")
        header = "ğŸ”« <b>KalÄ±cÄ± Rus Ruleti</b>"
        if state.get("phase") == "lobby":
            body = "Lobi aÃ§Ä±k. Oyuncular katÄ±lsÄ±n, sonra sahibi baÅŸlatsÄ±n."
        else:
            body = "SÄ±ra iÅŸaretli oyuncuda. Her callback tek kez uygulanÄ±r."
        text = f"{header}\n\n{body}\n\n<b>Oyuncular</b>\n" + ("\n".join(names) or "â€¢ HenÃ¼z oyuncu yok")
        if result:
            text += f"\n\n{result}"
        return text

    async def create_lobby(self, *, chat_id: int, owner_id: int, owner_name: str) -> dict[str, Any]:
        existing = await self._load(chat_id)
        if existing and existing.get("phase") in {"lobby", "playing"}:
            raise ValueError("active_game")
        game = RouletteGame()
        state = game.create_game(int(chat_id))
        state.update(
            {
                "phase": "lobby",
                "owner_id": int(owner_id),
                "players": [int(owner_id)],
                "player_names": [str(owner_name or "Oyuncu")[:80]],
                "alive": [int(owner_id)],
                "current_player_idx": 0,
                "current_player_id": None,
                "game_id": uuid.uuid4().hex,
                "created_at": datetime.now(timezone.utc),
            }
        )
        await self._save(chat_id, state)
        return {"status": "created", "state": state, "text": self._render(state)}

    async def join(self, *, chat_id: int, user_id: int, user_name: str, action_id: str) -> dict[str, Any]:
        if not await self._claim(chat_id, user_id, action_id, "join"):
            return {"status": "duplicate_action"}
        try:
            state = await self._load(chat_id)
            if not state or state.get("phase") != "lobby":
                raise ValueError("game_not_lobby")
            if int(user_id) in [int(item) for item in state.get("players", [])]:
                return {"status": "already_joined", "state": state, "text": self._render(state)}
            if len(state.get("players", [])) >= self.MAX_PLAYERS:
                raise ValueError("lobby_full")
            state.setdefault("players", []).append(int(user_id))
            state.setdefault("player_names", []).append(str(user_name or "Oyuncu")[:80])
            state.setdefault("alive", []).append(int(user_id))
            await self._save(chat_id, state)
            return {"status": "joined", "state": state, "text": self._render(state)}
        except Exception:
            await self._release(chat_id, action_id)
            raise

    async def start(self, *, chat_id: int, user_id: int, action_id: str) -> dict[str, Any]:
        if not await self._claim(chat_id, user_id, action_id, "start"):
            return {"status": "duplicate_action"}
        try:
            state = await self._load(chat_id)
            if not state or state.get("phase") != "lobby":
                raise ValueError("game_not_lobby")
            if int(state.get("owner_id", 0)) != int(user_id):
                raise ValueError("not_owner")
            if len(state.get("players", [])) < 2:
                raise ValueError("need_two_players")
            state["phase"] = "playing"
            state["current_player_idx"] = 0
            state["current_player_id"] = int(state["alive"][0])
            await self._save(chat_id, state)
            return {"status": "started", "state": state, "text": self._render(state)}
        except Exception:
            await self._release(chat_id, action_id)
            raise

    async def pull(self, *, chat_id: int, user_id: int, action_id: str) -> dict[str, Any]:
        if not await self._claim(chat_id, user_id, action_id, "pull"):
            return {"status": "duplicate_action"}
        try:
            state = await self._load(chat_id)
            if not state or state.get("phase") != "playing":
                raise ValueError("game_not_playing")
            if int(state.get("current_player_id", 0)) != int(user_id):
                raise ValueError("not_turn")
            player_name = self._player_name(state, user_id)
            result = RouletteGame().pull_trigger(state, player_name)
            alive = [int(item) for item in state.get("alive", [])]
            if result.get("hit"):
                alive = [item for item in alive if item != int(user_id)]
                state["alive"] = alive
                if len(alive) <= 1:
                    winner_id = alive[0] if alive else None
                    await self._delete(chat_id)
                    winner_name = self._player_name(state, winner_id) if winner_id else "kimse"
                    if winner_id is not None:
                        event_base = f"roulette:{int(chat_id)}:{state.get('game_id', 'unknown')}:{int(winner_id)}"
                        if self.leaderboard is not None:
                            try:
                                await self.leaderboard.record_score(
                                    user_id=int(winner_id),
                                    chat_id=int(chat_id),
                                    game_type=self.GAME_TYPE,
                                    score=25,
                                    user_name=winner_name,
                                    event_id=f"score:{event_base}",
                                )
                            except Exception:
                                pass
                        if self.economy is not None:
                            try:
                                await self.economy.credit(
                                    int(winner_id),
                                    int(chat_id),
                                    amount=25,
                                    reason="roulette_reward",
                                    event_id=f"coin:{event_base}",
                                )
                            except Exception:
                                pass
                    return {
                        "status": "finished",
                        "terminal": True,
                        "winner_id": winner_id,
                        "state": state,
                        "text": f"{result.get('message', '')}\n\nğŸ† Kazanan: <b>{html.escape(winner_name)}</b>",
                    }
                state["current_player_idx"] = state["current_player_idx"] % len(alive)
            else:
                if alive:
                    current_index = alive.index(int(user_id))
                    state["current_player_idx"] = (current_index + 1) % len(alive)
            if alive:
                state["current_player_id"] = alive[state["current_player_idx"]]
            await self._save(chat_id, state)
            return {
                "status": "playing",
                "terminal": False,
                "state": state,
                "text": self._render(state, result=str(result.get("message", ""))),
            }
        except Exception:
            await self._release(chat_id, action_id)
            raise

    async def cancel(self, *, chat_id: int, user_id: int, moderator: bool = False) -> dict[str, Any]:
        state = await self._load(chat_id)
        if not state:
            raise ValueError("game_expired")
        if not moderator and int(state.get("owner_id", 0)) != int(user_id):
            raise ValueError("not_owner")
        await self._delete(chat_id)
        return {"status": "cancelled", "terminal": True}
