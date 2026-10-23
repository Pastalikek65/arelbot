"""Durable UNO service shared by Telegram callbacks and future Mini Apps."""

from __future__ import annotations

import asyncio
import time
import weakref
from typing import Any

from src.games import uno as legacy_rules, uno_upstream
from src.games.uno import UnoError


def _rules(state):
    return uno_upstream if state and state.get("engine") == "maildian" else legacy_rules



class UnoService:
    GAME_TYPE = "uno"

    def __init__(self, database: Any, *, leaderboard_service: Any | None = None, turn_seconds: int = 60, ruleset: str = "maildian", inline_secret: str | bytes | None = None) -> None:
        self.ruleset = ruleset
        self.inline_secret = inline_secret
        self.bot_username = None
        self.database = database
        self.leaderboard = leaderboard_service
        self.turn_seconds = max(15, min(300, int(turn_seconds)))
        self._locks: dict[int, asyncio.Lock] = {}
        self._panel_locks = weakref.WeakValueDictionary()

    def presentation_lock(self, chat_id):
        return self._panel_locks.setdefault(int(chat_id), asyncio.Lock())

    def _lock_for(self, chat_id: int) -> asyncio.Lock:
        return self._locks.setdefault(int(chat_id), asyncio.Lock())

    async def _get(self, chat_id: int) -> dict[str, Any] | None:
        getter = getattr(self.database, "get_game_state", None)
        if getter is None or getattr(self.database, "available", True) is False:
            raise UnoError("UNO_STORAGE_UNAVAILABLE")
        try:
            value = await getter(int(chat_id), self.GAME_TYPE)
        except UnoError:
            raise
        except Exception:
            raise UnoError("UNO_STORAGE_UNAVAILABLE") from None
        return dict(value) if isinstance(value, dict) else None

    async def _save(self, chat_id: int, state: dict[str, Any]) -> None:
        saver = getattr(self.database, "save_game_state", None)
        if saver is None or getattr(self.database, "available", True) is False:
            raise UnoError("UNO_STORAGE_UNAVAILABLE")
        try:
            if self.inline_secret and state.get("engine") == "maildian":
                from src.services.uno_inline_scope import create_query
                state["inline_query"] = create_query(state, self.inline_secret)
            if self.bot_username:
                state["source_url"] = f"https://t.me/{self.bot_username}?start=uno_source"
            await saver(int(chat_id), self.GAME_TYPE, state)
        except UnoError:
            raise
        except Exception:
            raise UnoError("UNO_STORAGE_UNAVAILABLE") from None

    async def _delete(self, chat_id: int) -> None:
        deleter = getattr(self.database, "delete_game_state", None)
        if deleter is None or getattr(self.database, "available", True) is False:
            raise UnoError("UNO_STORAGE_UNAVAILABLE")
        try:
            await deleter(int(chat_id), self.GAME_TYPE)
        except UnoError:
            raise
        except Exception:
            raise UnoError("UNO_STORAGE_UNAVAILABLE") from None

    async def current(self, chat_id: int) -> dict[str, Any] | None:
        async with self._lock_for(chat_id):
            state = await self._get(chat_id)
            if state and _rules(state).validate_state(state):
                state = await self._advance_due(chat_id, state)
                if state.get("score_pending") and await self._record_winner(chat_id, state):
                    state["score_pending"] = False
                    await self._save(chat_id, state)
        if state and _rules(state).validate_state(state):
            return state
        return None

    async def start(self, *, chat_id: int, owner_id: int, owner_name: str, thread_id: int | None = None) -> dict[str, Any]:
        async with self._lock_for(chat_id):
            current = await self._get(chat_id)
            if current and _rules(current).validate_state(current) and current.get("status") in {"lobby", "playing"}:
                raise UnoError("UNO_ACTIVE")
            state = (uno_upstream if self.ruleset == "maildian" else legacy_rules).new_uno_state(
                chat_id=int(chat_id), owner_id=int(owner_id), owner_name=owner_name,
                thread_id=thread_id, turn_seconds=self.turn_seconds,
            )
            await self._save(chat_id, state)
            return state

    @staticmethod
    def _expect(state, expected_revision):
        if expected_revision is not None and int(expected_revision) != int(state.get("revision", 0)):
            raise UnoError("UNO_STALE_ACTION")

    async def _commit(self, chat_id, state, *, human=True):
        state["revision"] = int(state.get("revision", 0)) + 1
        state["panel_dirty"] = True
        if human:
            state["last_activity_at"] = time.time()
            state.update(delivery_retry_at=0, delivery_failures=0)
        await self._save(chat_id, state)
        return state

    async def _advance_due(self, chat_id, state):
        if state.get("status") in {"lobby", "playing"} and time.time() - float(state.get("last_activity_at", time.time())) >= 900:
            state["status"] = "expired"
            state["turn_expires_at"] = None
            return await self._commit(chat_id, state, human=False)
        if _rules(state).turn_expired(state):
            return await self._commit(chat_id, _rules(state).timeout_turn(state), human=False)
        return state

    async def join(self, *, chat_id: int, user_id: int, name: str, expected_revision: int | None = None) -> dict[str, Any]:
        async with self._lock_for(chat_id):
            state = await self._required(chat_id)
            self._expect(state, expected_revision)
            state = _rules(state).join_player(state, user_id=user_id, name=name)
            return await self._commit(chat_id, state)

    async def begin(self, *, chat_id: int, user_id: int, action_id: str, expected_revision: int | None = None) -> dict[str, Any]:
        async with self._lock_for(chat_id):
            state = await self._required(chat_id)
            self._expect(state, expected_revision)
            if int(state.get("owner_id", 0)) != int(user_id):
                raise UnoError("UNO_OWNER_ONLY")
            if state.get("status") == "playing":
                return state
            state = _rules(state).start_round(state)
            state["processed_actions"] = [str(action_id)] if action_id else []
            return await self._commit(chat_id, state)

    async def next_round(self, *, chat_id: int, user_id: int, action_id: str, expected_revision: int | None = None) -> dict[str, Any]:
        async with self._lock_for(chat_id):
            state = await self._required(chat_id)
            self._expect(state, expected_revision)
            if int(state.get("owner_id", 0)) != int(user_id):
                raise UnoError("UNO_OWNER_ONLY")
            if state.get("status") != "finished" or state.get("match_complete"):
                raise UnoError("UNO_NEXT_ROUND_NOT_ALLOWED")
            if state.get("score_pending"):
                raise UnoError("UNO_SCORE_PENDING")
            state = _rules(state).start_round(state)
            state["processed_actions"] = [str(action_id)]
            return await self._commit(chat_id, state)

    async def action(self, *, chat_id: int, user_id: int, action: str, action_id: str, card_id: str | None = None, card_position: int | None = None, color: str | None = None, target_id: int | None = None, expected_revision: int | None = None) -> dict[str, Any]:
        async with self._lock_for(chat_id):
            state = await self._required(chat_id)
            self._expect(state, expected_revision)
            action = str(action).casefold()
            if action == "play":
                state = _rules(state).play_card(state, user_id=user_id, card_id=str(card_id or ""), action_id=action_id)
            elif action == "playpos":
                if int(state.get("current_player_id", 0) or 0) != int(user_id):
                    raise UnoError("UNO_NOT_YOUR_TURN")
                player = next((p for p in state.get("players", []) if int(p.get("user_id", 0)) == int(user_id)), None)
                hand = (player or {}).get("hand", [])
                position = int(card_position or 0)
                if position < 1 or position > len(hand):
                    raise UnoError("UNO_CARD_NOT_IN_HAND")
                state = _rules(state).play_card(state, user_id=user_id, card_id=str(hand[position - 1]["id"]), action_id=action_id)
            elif action == "draw":
                state = _rules(state).draw_card(state, user_id=user_id, action_id=action_id)
            elif action == "skip":
                state = _rules(state).skip_after_draw(state, user_id=user_id, action_id=action_id)
            elif action == "color":
                state = _rules(state).choose_color(state, user_id=user_id, color=str(color or ""), action_id=action_id)
            elif action == "call":
                state = _rules(state).call_uno(state, user_id=user_id, action_id=action_id)
            elif action == "challenge":
                state = _rules(state).challenge_uno(state, user_id=user_id, target_id=int(target_id or 0), action_id=action_id)
            elif action in {"bluff", "call_bluff"} and state.get("engine") == "maildian":
                state = uno_upstream.call_bluff(state, user_id=user_id, action_id=action_id)
            else:
                raise UnoError("UNO_INVALID_ACTION")
            if state.get("status") == "finished":
                state["score_pending"] = True
            await self._commit(chat_id, state)
            if state.get("score_pending") and await self._record_winner(chat_id, state):
                state["score_pending"] = False
                await self._save(chat_id, state)
            return state

    async def cancel(self, *, chat_id: int, user_id: int, moderator: bool = False, expected_revision: int | None = None) -> bool:
        async with self._lock_for(chat_id):
            state = await self._get(chat_id)
            if not state:
                return False
            self._expect(state, expected_revision)
            if not moderator and int(state.get("owner_id", 0)) != int(user_id):
                raise UnoError("UNO_OWNER_ONLY")
            state["status"] = "cancelled"
            await self._save(chat_id, state)
            await self._delete(chat_id)
            return True

    async def view(self, chat_id: int, user_id: int) -> dict[str, Any] | None:
        state = await self.current(chat_id)
        return _rules(state).player_view(state, user_id) if state else None

    async def set_prompt_message(self, *, chat_id: int, message_id: int, expected_revision: int | None = None) -> dict[str, Any]:
        async with self._lock_for(chat_id):
            state = await self._required(chat_id)
            state["prompt_message_id"] = int(message_id)
            state["panel_turn_player_id"] = state.get("current_player_id") if state.get("status") == "playing" else None
            state["panel_dirty"] = expected_revision is not None and int(state.get("revision", 0)) != int(expected_revision)
            await self._save(chat_id, state)
            return state

    async def mark_panel_delivered(self, chat_id: int, revision: int, message_id: int | None = None) -> None:
        async with self._lock_for(chat_id):
            state = await self._get(chat_id)
            if not state or (message_id is not None and int(state.get("prompt_message_id", 0) or 0) != int(message_id)):
                return
            state["panel_dirty"] = int(state.get("revision", 0)) != int(revision)
            if int(state.get("revision", 0)) == int(revision):
                state["panel_turn_player_id"] = state.get("current_player_id") if state.get("status") == "playing" else None
            state.update(delivery_retry_at=0, delivery_failures=0)
            await self._save(chat_id, state)

    async def defer_delivery(self, chat_id):
        async with self._lock_for(chat_id):
            state = await self._get(chat_id)
            if state:
                failures = int(state.get("delivery_failures", 0)) + 1
                state.update(delivery_failures=failures, delivery_retry_at=time.time() + min(300, 5 * 2 ** min(failures, 6)))
                await self._save(chat_id, state)

    async def _required(self, chat_id: int) -> dict[str, Any]:
        state = await self._get(chat_id)
        if not state or not _rules(state).validate_state(state):
            raise UnoError("UNO_NOT_FOUND")
        return await self._advance_due(chat_id, state)

    async def _record_winner(self, chat_id: int, state: dict[str, Any]) -> bool:
        if self.leaderboard is None or not state.get("winner_id"):
            return True
        winner = next((p for p in state.get("players", []) if int(p.get("user_id", 0)) == int(state["winner_id"])), None)
        if winner is None:
            return True
        try:
            await self.leaderboard.record_score(
                user_id=int(winner["user_id"]), chat_id=int(chat_id), game_type=self.GAME_TYPE,
                score=max(0, min(100, int(state.get("round_points", winner.get("score", 0))))),
                user_name=str(winner.get("name") or "oyuncu")[:80],
                event_id=f"uno:{chat_id}:{state.get('round_id')}:{winner['user_id']}",
            )
            return True
        except Exception:
            return False


__all__ = ["UnoService", "UnoError"]
