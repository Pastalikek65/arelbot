"""Persistent, idempotent group roulette game service.

The old roulette command generated an isolated random sentence on every
request. This service owns the lobby, turn order and chamber state so a
callback retry or a process restart cannot silently create a second game.
"""

from __future__ import annotations

import copy
import asyncio
import functools
import html
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from src.games.action_ledger import GameActionLedger
from src.games.roulette import RouletteGame
from src.games.state_store import memory_fallback_allowed

logger = logging.getLogger(__name__)


def _serialized(method):
    """Serialize lobby and turn mutations for one chat."""

    @functools.wraps(method)
    async def wrapper(self, *args, **kwargs):
        chat_id = kwargs.get("chat_id")
        if chat_id is None and args:
            chat_id = args[0]
        if chat_id is None:
            raise ValueError("invalid_game_chat_id")
        async with self._lock_for(int(chat_id)):
            return await method(self, *args, **kwargs)

    return wrapper


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
        self._locks: dict[int, asyncio.Lock] = {}
        self._allow_memory_fallback = memory_fallback_allowed(database)

    def _lock_for(self, chat_id: int) -> asyncio.Lock:
        return self._locks.setdefault(int(chat_id), asyncio.Lock())

    async def _load(self, chat_id: int) -> dict[str, Any] | None:
        getter = getattr(self.database, "get_game_state", None)
        if getter is None:
            if not self._allow_memory_fallback:
                raise RuntimeError("game_persistence_unavailable")
        elif getattr(self.database, "available", True) is False:
            if not self._allow_memory_fallback:
                raise RuntimeError("game_persistence_unavailable")
        else:
            try:
                state = await getter(int(chat_id), self.GAME_TYPE)
                if isinstance(state, dict):
                    return copy.deepcopy(state)
            except Exception:
                if not self._allow_memory_fallback:
                    raise RuntimeError("game_persistence_unavailable") from None
        state = self._memory.get(int(chat_id))
        return copy.deepcopy(state) if state else None

    async def _save(self, chat_id: int, state: dict[str, Any]) -> None:
        snapshot = copy.deepcopy(state)
        saver = getattr(self.database, "save_game_state", None)
        if saver is None:
            if not self._allow_memory_fallback:
                raise RuntimeError("game_persistence_unavailable")
            self._memory[int(chat_id)] = snapshot
            return
        if getattr(self.database, "available", True) is False:
            if not self._allow_memory_fallback:
                raise RuntimeError("game_persistence_unavailable")
            self._memory[int(chat_id)] = snapshot
            return
        try:
            await saver(int(chat_id), self.GAME_TYPE, snapshot)
        except Exception:
            if not self._allow_memory_fallback:
                raise RuntimeError("game_persistence_unavailable") from None
            self._memory[int(chat_id)] = snapshot
        else:
            self._memory[int(chat_id)] = snapshot

    async def _delete(self, chat_id: int) -> None:
        deleter = getattr(self.database, "delete_game_state", None)
        if deleter is None:
            if not self._allow_memory_fallback:
                raise RuntimeError("game_persistence_unavailable")
            self._memory.pop(int(chat_id), None)
            return
        if getattr(self.database, "available", True) is False:
            if not self._allow_memory_fallback:
                raise RuntimeError("game_persistence_unavailable")
            self._memory.pop(int(chat_id), None)
            return
        try:
            await deleter(int(chat_id), self.GAME_TYPE)
        except Exception:
            if not self._allow_memory_fallback:
                raise RuntimeError("game_persistence_unavailable") from None
            self._memory.pop(int(chat_id), None)
        else:
            self._memory.pop(int(chat_id), None)

    @staticmethod
    def _action_id(action_id: str) -> str:
        value = str(action_id or "").strip()
        if not value or len(value) > 180:
            raise ValueError("invalid_game_action_id")
        return value

    @staticmethod
    def _check_callback_panel(state: dict[str, Any], game_id: str | None,
                              panel_message_id: int | None) -> None:
        """Bind callbacks to the current game and its canonical group panel."""
        canonical = state.get("panel_message_id")
        if canonical is None:
            # Direct legacy service callers have no Telegram panel. Once the
            # first panel is acknowledged, both bindings become mandatory.
            if game_id is not None and str(game_id) != str(state.get("game_id")):
                raise ValueError("stale_game")
            return
        if (str(game_id or "") != str(state.get("game_id")) or
                panel_message_id is None or int(panel_message_id) != int(canonical)):
            raise ValueError("stale_game")

    @_serialized
    async def acknowledge_panel(self, chat_id: int, game_id: str,
                                message_id: int) -> bool:
        state = await self._load(chat_id)
        if (not state or state.get("phase") not in {"lobby", "playing"}
                or str(state.get("game_id")) != str(game_id)):
            return False
        state["panel_message_id"] = int(message_id)
        await self._save(chat_id, state)
        return True

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
                marker = " ◀" if current is not None and int(player_id) == int(current) else ""
                names.append(f"• {html.escape(str(name)[:80])}{marker}")
        header = "🔫 <b>Kalıcı Rus Ruleti</b>"
        if state.get("phase") == "lobby":
            body = "Lobi açık. Oyuncular katılsın, sonra sahibi başlatsın."
        elif state.get("phase") == "expired":
            body = "Sıra süresi dolduğu için masa kapandı. Yeni bir tur açılabilir."
        elif state.get("phase") == "finished":
            body = "Maç tamamlandı."
        else:
            body = "Sıra işaretli oyuncuda. Her callback tek kez uygulanır."
        text = f"{header}\n\n{body}\n\n<b>Oyuncular</b>\n" + ("\n".join(names) or "• Henüz oyuncu yok")
        if result:
            text += f"\n\n{result}"
        return text

    @staticmethod
    def result_text(state: dict[str, Any]) -> str:
        """Return the immutable terminal result for a delivery retry."""
        return str(state.get("result_text") or "Rulet sonucu kaydedildi.")

    @classmethod
    def _expire_state(cls, state: dict[str, Any]) -> dict[str, Any]:
        state.update(phase="expired", turn_expires_at=None,
                     panel_dirty=True, panel_retry_at=0)
        state["result_text"] = cls._render(
            state, result="Sıra süresi doldu; masa kapandı."
        )
        return state

    @_serialized
    async def expire(self, *, chat_id: int, now: float | None = None) -> dict[str, Any] | None:
        """Close an abandoned turn even when nobody presses its old button."""
        moment = time.time() if now is None else float(now)
        state = await self._load(chat_id)
        if not state or state.get("phase") != "playing":
            return None
        deadline = state.get("turn_expires_at")
        if deadline is None:
            return None
        try:
            if moment < float(deadline):
                return None
        except (TypeError, ValueError, OverflowError):
            raise ValueError("invalid_state") from None
        self._expire_state(state)
        await self._save(chat_id, state)
        return {"status": "expired", "terminal": True, "state": state,
                "text": self.result_text(state)}

    async def _settle_finished(self, chat_id: int, state: dict[str, Any]) -> dict[str, Any]:
        if state.get("phase") != "finished" or not state.get("score_pending"):
            return state
        if ((state.get("leaderboard_required") and self.leaderboard is None) or
                (state.get("economy_required") and self.economy is None)):
            raise RuntimeError("game_reward_store_unavailable")
        winner_id = state.get("winner_id")
        if winner_id is not None:
            winner_id = int(winner_id)
            event_base = f"roulette:{int(chat_id)}:{state['game_id']}:{winner_id}"
            if self.leaderboard is not None:
                await self.leaderboard.record_score(
                    user_id=winner_id, chat_id=int(chat_id), game_type=self.GAME_TYPE,
                    score=25, user_name=self._player_name(state, winner_id),
                    event_id=f"score:{event_base}",
                )
            if self.economy is not None:
                await self.economy.credit(
                    winner_id, int(chat_id), amount=25,
                    reason="roulette_reward", event_id=f"coin:{event_base}",
                )
        settled = dict(state, score_pending=False)
        await self._save(chat_id, settled)
        return settled

    @_serialized
    async def settle_pending(self, *, chat_id: int) -> dict[str, Any] | None:
        """Retry the persisted result after a process restart or sink outage."""
        state = await self._load(chat_id)
        if state is None:
            return None
        return await self._settle_finished(chat_id, state)

    @_serialized
    async def claim_result_panel(self, *, chat_id: int, now: float | None = None) -> dict[str, Any] | None:
        moment = time.time() if now is None else float(now)
        state = await self._load(chat_id)
        if (not state or state.get("phase") not in {"finished", "expired"} or not state.get("panel_dirty")
                or float(state.get("panel_retry_at") or 0) > moment):
            return None
        state["panel_retry_at"] = moment + 30
        await self._save(chat_id, state)
        return state

    @_serialized
    async def acknowledge_result_panel(self, *, chat_id: int, game_id: str,
                                       message_id: int) -> bool:
        state = await self._load(chat_id)
        if not state or state.get("phase") not in {"finished", "expired"} or state.get("game_id") != game_id:
            return False
        state.update(panel_dirty=False, panel_retry_at=0, panel_message_id=int(message_id))
        await self._save(chat_id, state)
        return True

    @_serialized
    async def create_lobby(self, *, chat_id: int, owner_id: int, owner_name: str) -> dict[str, Any]:
        existing = await self._load(chat_id)
        if existing and existing.get("phase") in {"lobby", "playing"}:
            raise ValueError("active_game")
        if existing and existing.get("phase") == "expired" and existing.get("panel_dirty"):
            raise ValueError("result_pending")
        if existing and existing.get("phase") == "finished":
            if existing.get("score_pending"):
                try:
                    existing = await self._settle_finished(chat_id, existing)
                except Exception as exc:
                    logger.warning("Roulette reward deferred: %s", type(exc).__name__)
                    raise ValueError("reward_pending") from None
            if existing.get("panel_dirty"):
                raise ValueError("result_pending")
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
                "turn_timeout_seconds": 180,
                "turn_expires_at": None,
                "game_id": uuid.uuid4().hex,
                "created_at": datetime.now(timezone.utc),
                "score_pending": False,
                "leaderboard_required": False,
                "economy_required": False,
                "panel_dirty": False,
                "panel_retry_at": 0,
                "panel_message_id": None,
            }
        )
        await self._save(chat_id, state)
        return {"status": "created", "state": state, "text": self._render(state)}

    @_serialized
    async def join(self, *, chat_id: int, user_id: int, user_name: str, action_id: str,
                   game_id: str | None = None, panel_message_id: int | None = None) -> dict[str, Any]:
        if not await self._claim(chat_id, user_id, action_id, "join"):
            return {"status": "duplicate_action"}
        try:
            state = await self._load(chat_id)
            if not state or state.get("phase") != "lobby":
                raise ValueError("game_not_lobby")
            self._check_callback_panel(state, game_id, panel_message_id)
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

    @_serialized
    async def start(self, *, chat_id: int, user_id: int, action_id: str,
                    game_id: str | None = None, panel_message_id: int | None = None) -> dict[str, Any]:
        if not await self._claim(chat_id, user_id, action_id, "start"):
            return {"status": "duplicate_action"}
        try:
            state = await self._load(chat_id)
            if not state or state.get("phase") != "lobby":
                raise ValueError("game_not_lobby")
            self._check_callback_panel(state, game_id, panel_message_id)
            if int(state.get("owner_id", 0)) != int(user_id):
                raise ValueError("not_owner")
            if len(state.get("players", [])) < 2:
                raise ValueError("need_two_players")
            state["phase"] = "playing"
            state["current_player_idx"] = 0
            state["current_player_id"] = int(state["alive"][0])
            state["turn_expires_at"] = time.time() + int(state.get("turn_timeout_seconds", 180))
            await self._save(chat_id, state)
            return {"status": "started", "state": state, "text": self._render(state)}
        except Exception:
            await self._release(chat_id, action_id)
            raise

    @_serialized
    async def pull(self, *, chat_id: int, user_id: int, action_id: str,
                   panel_message_id: int | None = None, game_id: str | None = None) -> dict[str, Any]:
        if not await self._claim(chat_id, user_id, action_id, "pull"):
            return {"status": "duplicate_action"}
        try:
            state = await self._load(chat_id)
            if not state or state.get("phase") != "playing":
                raise ValueError("game_not_playing")
            self._check_callback_panel(state, game_id, panel_message_id)
            try:
                if state.get("turn_expires_at") is not None and time.time() >= float(state["turn_expires_at"]):
                    self._expire_state(state)
                    await self._save(chat_id, state)
                    return {
                        "status": "expired",
                        "terminal": True,
                        "state": state,
                        "text": self.result_text(state),
                    }
            except (TypeError, ValueError, OverflowError):
                raise ValueError("invalid_state") from None
            if int(state.get("current_player_id", 0)) != int(user_id):
                raise ValueError("not_turn")
            player_name = html.escape(self._player_name(state, user_id))
            result = RouletteGame().pull_trigger(state, player_name)
            alive = [int(item) for item in state.get("alive", [])]
            if result.get("hit"):
                alive = [item for item in alive if item != int(user_id)]
                state["alive"] = alive
                if len(alive) <= 1:
                    winner_id = alive[0] if alive else None
                    winner_name = self._player_name(state, winner_id) if winner_id else "kimse"
                    state.update(phase="finished", winner_id=winner_id,
                                 turn_expires_at=None,
                                 leaderboard_required=winner_id is not None and self.leaderboard is not None,
                                 economy_required=winner_id is not None and self.economy is not None,
                                 score_pending=winner_id is not None and
                                 (self.leaderboard is not None or self.economy is not None),
                                 panel_dirty=True, panel_retry_at=0,
                                 panel_message_id=int(panel_message_id) if panel_message_id else state.get("panel_message_id"),
                                 result_text=f"{result.get('message', '')}\n\n🏆 Kazanan: <b>{html.escape(winner_name)}</b>")
                    await self._save(chat_id, state)
                    try:
                        state = await self._settle_finished(chat_id, state)
                    except Exception as exc:
                        logger.warning("Roulette reward deferred: %s", type(exc).__name__)
                    return {
                        "status": "finished",
                        "terminal": True,
                        "winner_id": winner_id,
                        "state": state,
                        "text": self.result_text(state),
                    }
                state["current_player_idx"] = state["current_player_idx"] % len(alive)
            else:
                if alive:
                    current_index = alive.index(int(user_id))
                    state["current_player_idx"] = (current_index + 1) % len(alive)
            if alive:
                state["current_player_id"] = alive[state["current_player_idx"]]
                state["turn_expires_at"] = time.time() + int(state.get("turn_timeout_seconds", 180))
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

    @_serialized
    async def cancel(self, *, chat_id: int, user_id: int, moderator: bool = False,
                     game_id: str | None = None, panel_message_id: int | None = None) -> dict[str, Any]:
        state = await self._load(chat_id)
        if not state:
            raise ValueError("game_expired")
        if game_id is not None or panel_message_id is not None:
            self._check_callback_panel(state, game_id, panel_message_id)
        if not moderator and int(state.get("owner_id", 0)) != int(user_id):
            raise ValueError("not_owner")
        if state.get("phase") == "finished":
            raise ValueError("game_not_playing")
        await self._delete(chat_id)
        return {"status": "cancelled", "terminal": True}
