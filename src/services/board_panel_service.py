"""Durable single-panel delivery and winner-score outbox for legacy boards.

Callers hold ``guard`` across domain mutation, ``save``, and callback validation.
The repository remains the only state store; a renewable action-ledger lease
serializes the read/write sequence across bot instances.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from copy import deepcopy
from typing import ClassVar

from telegram.error import BadRequest, RetryAfter, TelegramError

from src.games.action_ledger import GameActionLedger
from src.observability.metrics import MetricsRegistry
from src.services.chess_ui import chess_keyboard, render_chess_text
from src.services.gomoku_ui import gomoku_keyboard, render_gomoku_text
from src.services.menu_service import tictactoe_surface_keyboard
from src.services.tictactoe_ui import render_tictactoe_text
from src.services.word_game_ui import connect4_keyboard, render_connect4_text

log = logging.getLogger(__name__)


def _retry_seconds(value) -> float:
    return float(value.total_seconds() if hasattr(value, "total_seconds") else value)


class BoardPanelService:
    GAME_KEYS = frozenset({"connect4", "tictactoe", "gomoku", "chess"})
    WIN_SCORES: ClassVar[dict[str, int]] = {"connect4": 50, "tictactoe": 30, "gomoku": 100, "chess": 100}
    _RENDER: ClassVar[dict] = {
        "connect4": (render_connect4_text, connect4_keyboard),
        "tictactoe": (render_tictactoe_text, tictactoe_surface_keyboard),
        "gomoku": (render_gomoku_text, gomoku_keyboard),
        "chess": (render_chess_text, chess_keyboard),
    }

    def __init__(self, repository, leaderboard=None, economy=None, actions=None,
                 metrics=None):
        self.repository = repository
        self.leaderboard = leaderboard
        self.economy = economy
        self.actions = actions or GameActionLedger(repository)
        self.metrics = metrics or MetricsRegistry()
        self._owner: ContextVar[tuple[object, int, str] | None] = ContextVar(
            "board_panel_guard_owner", default=None)

    @staticmethod
    def _key(game_key: str) -> str:
        key = str(game_key)
        if key not in BoardPanelService.GAME_KEYS:
            raise ValueError("board_unknown_game")
        return key

    def _inside(self, chat_id: int, game_key: str) -> bool:
        return self._owner.get() == (asyncio.current_task(), int(chat_id), game_key)

    @asynccontextmanager
    async def guard(self, chat_id: int, game_key: str):
        key = self._key(game_key)
        chat = int(chat_id)
        if self._inside(chat, key):
            yield
            return
        async with self.actions.lease(chat, "board_session_v1", key):
            token = self._owner.set((asyncio.current_task(), chat, key))
            try:
                yield
            finally:
                self._owner.reset(token)

    async def current(self, chat_id: int, game_key: str) -> dict | None:
        key = self._key(game_key)
        state = await self.repository.get_game_state(int(chat_id), key)
        if state and "version" not in state:
            initializer = getattr(self.repository, "initialize_game_state_version_if_missing", None)
            if initializer is None:
                raise RuntimeError("board_cas_unavailable")
            await initializer(int(chat_id), key)
            state = await self.repository.get_game_state(int(chat_id), key)
            if "version" not in state:
                raise RuntimeError("board_cas_unavailable")
        return deepcopy(state) if state else None

    async def _write(self, chat_id: int, game_key: str, state: dict) -> None:
        previous = await self.current(chat_id, game_key)
        if previous is None:
            creator = getattr(self.repository, "create_game_state_if_absent", None)
            if creator is None:
                raise RuntimeError("board_cas_unavailable")
            state["version"] = 0
            if not await creator(int(chat_id), game_key, deepcopy(state)):
                raise RuntimeError("board_state_conflict")
            return
        expected = int(previous["version"])
        if "version" in state and int(state["version"]) != expected:
            raise RuntimeError("board_state_conflict")
        saver = getattr(self.repository, "compare_and_save_game_state", None)
        if saver is None:
            raise RuntimeError("board_cas_unavailable")
        state["version"] = expected + 1
        if not await saver(int(chat_id), game_key, deepcopy(state),
                           expected_version=expected):
            raise RuntimeError("board_state_conflict")

    @staticmethod
    def _won(state: dict, game_key: str) -> int | None:
        if game_key == "chess":
            if state.get("status") != "finished":
                return None
        elif state.get("status") != "won":
            return None
        try:
            winner = int(state.get("winner_id") or 0)
        except (TypeError, ValueError):
            return None
        players = state.get("players") or []
        return winner if winner > 0 and any(
            isinstance(p, dict) and int(p.get("id") or 0) == winner for p in players
        ) else None

    async def save(self, chat_id: int, game_key: str, state: dict) -> dict:
        key = self._key(game_key)
        if not self._inside(chat_id, key):
            async with self.guard(chat_id, key):
                return await self.save(chat_id, key, state)
        if not isinstance(state, dict):
            raise TypeError("board_invalid_state")
        previous = await self.current(chat_id, key)
        fresh = deepcopy(state)
        new_round = bool(previous and str(previous.get("round_id")) != str(fresh.get("round_id")))
        if previous:
            for field in ("prompt_message_id", "thread_id", "board_score_pending",
                          "leaderboard_required", "economy_required", "board_score_round_id"):
                if field not in fresh and field in previous and not (new_round and field in {
                    "board_score_pending", "leaderboard_required", "economy_required", "board_score_round_id"
                }):
                    fresh[field] = previous.get(field)
            if (previous.get("board_score_pending") and
                    previous.get("board_score_round_id") != fresh.get("round_id")):
                raise RuntimeError("board_score_pending")
        if previous and ("version" not in fresh or int(fresh["version"]) != int(previous["version"])):
            raise RuntimeError("board_state_conflict")
        fresh["board_panel_revision"] = int((previous or {}).get("board_panel_revision") or 0) + 1
        fresh["board_panel_dirty"] = True
        fresh["board_panel_retry_at"] = 0
        fresh["board_panel_delivery_unknown"] = bool(
            previous and previous.get("board_panel_delivery_unknown") and
            not int(previous.get("prompt_message_id") or 0))
        if not previous or new_round:
            fresh["leaderboard_required"] = self.leaderboard is not None
            fresh["economy_required"] = self.economy is not None
        if not previous or new_round:
            fresh["board_score_pending"] = False
            fresh["board_score_round_id"] = None
        if fresh.get("status") == "playing":
            old_turn = ((previous or {}).get("turn"), (previous or {}).get("current_player_id"))
            new_turn = (fresh.get("turn"), fresh.get("current_player_id"))
            if key == "gomoku":
                old_engine = (previous or {}).get("engine") or {}
                new_engine = fresh.get("engine") or {}
                old_turn += (old_engine.get("turn_index"), (previous or {}).get("moves"))
                new_turn += (new_engine.get("turn_index"), fresh.get("moves"))
            new_turn_started = (not previous or previous.get("status") != "playing" or
                str(previous.get("round_id")) != str(fresh.get("round_id")) or old_turn != new_turn)
            if new_turn_started:
                fresh["board_turn_waiting_delivery"] = True
                fresh["board_turn_duration"] = max(30, min(3600,
                    int(fresh.get("turn_timeout_seconds") or 180)))
                fresh["turn_expires_at"] = None
            elif previous.get("board_turn_waiting_delivery"):
                fresh["board_turn_waiting_delivery"] = True
                fresh["turn_expires_at"] = None
            else:
                fresh["turn_expires_at"] = previous.get("turn_expires_at")
        winner = self._won(fresh, key)
        if winner and fresh.get("board_score_round_id") != fresh.get("round_id"):
            fresh["board_score_pending"] = True
            fresh["board_score_round_id"] = fresh.get("round_id")
        await self._write(chat_id, key, fresh)
        self.metrics.inc("board_panel_saved_total")
        return deepcopy(fresh)

    async def adopt_callback(self, chat_id: int, game_key: str, message_id: int,
                             round_id: str, thread_id: int | None = None) -> bool:
        key = self._key(game_key)
        if not self._inside(chat_id, key):
            async with self.guard(chat_id, key):
                return await self.adopt_callback(chat_id, key, message_id, round_id, thread_id)
        state = await self.current(chat_id, key)
        if not state or int(state.get("prompt_message_id") or 0):
            return False
        if not round_id or str(state.get("round_id")) != str(round_id):
            return False
        if int(message_id) <= 0 or (state.get("thread_id") is not None and
                                   state.get("thread_id") != thread_id):
            return False
        state["prompt_message_id"] = int(message_id)
        state["board_panel_dirty"] = True
        state["board_panel_retry_at"] = 0
        state["board_panel_delivery_unknown"] = False
        await self._write(chat_id, key, state)
        return True

    async def _ack(self, chat_id: int, game_key: str, round_id: str,
                   revision: int, message_id: int) -> bool:
        state = await self.current(chat_id, game_key)
        if not state or str(state.get("round_id")) != round_id or int(
                state.get("board_panel_revision") or 0) != revision:
            if state and not state.get("board_panel_dirty"):
                state["board_panel_dirty"] = True
                state["board_panel_retry_at"] = 0
                await self._write(chat_id, game_key, state)
            return False
        state["prompt_message_id"] = int(message_id)
        state["board_panel_dirty"] = False
        state["board_panel_retry_at"] = 0
        state["board_panel_delivery_unknown"] = False
        if state.get("board_turn_waiting_delivery"):
            state["turn_expires_at"] = time.time() + int(state.get("board_turn_duration") or 180)
            state["board_turn_waiting_delivery"] = False
        await self._write(chat_id, game_key, state)
        self.metrics.inc("board_panel_delivered_total")
        return True

    async def _retry(self, chat_id: int, game_key: str, *, unknown: bool = False,
                     delay: float = 5) -> None:
        state = await self.current(chat_id, game_key)
        if state:
            state["board_panel_dirty"] = True
            state["board_panel_delivery_unknown"] = bool(unknown)
            state["board_panel_retry_at"] = time.time() + max(1, delay)
            await self._write(chat_id, game_key, state)
            self.metrics.inc("board_panel_retry_total")

    async def publish(self, bot, chat_id: int, game_key: str, force: bool = False) -> bool:
        key = self._key(game_key)
        if not self._inside(chat_id, key):
            async with self.guard(chat_id, key):
                return await self.publish(bot, chat_id, key, force=force)
        state = await self.current(chat_id, key)
        if not state or (not force and not state.get("board_panel_dirty")):
            return False
        if state.get("board_panel_delivery_unknown") and not force:
            return False
        if float(state.get("board_panel_retry_at") or 0) > time.time() and not force:
            return False
        render, keyboard = self._RENDER[key]
        text, markup = render(state), keyboard(state)
        round_id = str(state.get("round_id") or "")
        revision = int(state.get("board_panel_revision") or 0)
        message_id = int(state.get("prompt_message_id") or 0)
        if message_id:
            try:
                await bot.edit_message_text(chat_id=int(chat_id), message_id=message_id,
                    text=text, reply_markup=markup, parse_mode="HTML")
                return await self._ack(chat_id, key, round_id, revision, message_id)
            except BadRequest as exc:
                message = str(exc).lower()
                if "message is not modified" in message:
                    return await self._ack(chat_id, key, round_id, revision, message_id)
                if not ("message to edit not found" in message or
                        "message_id_invalid" in message or "message can't be edited" in message):
                    await self._retry(chat_id, key)
                    log.warning("Board panel edit rejected: %s", type(exc).__name__)
                    return False
                state = await self.current(chat_id, key)
                state["prompt_message_id"] = 0
                await self._write(chat_id, key, state)
            except RetryAfter as exc:
                await self._retry(chat_id, key, delay=_retry_seconds(exc.retry_after))
                return False
            except TelegramError as exc:
                await self._retry(chat_id, key)
                log.warning("Board panel edit failed: %s", type(exc).__name__)
                return False
        # A send can succeed remotely before the process receives its message id.
        # Persist uncertainty first. Only an explicit force request retries such a send.
        state = await self.current(chat_id, key)
        state["board_panel_delivery_unknown"] = True
        await self._write(chat_id, key, state)
        try:
            sent = await bot.send_message(chat_id=int(chat_id), text=text,
                reply_markup=markup, parse_mode="HTML",
                **({"message_thread_id": int(state["thread_id"])}
                   if state.get("thread_id") is not None else {}))
        except RetryAfter as exc:
            await self._retry(chat_id, key, unknown=False,
                              delay=_retry_seconds(exc.retry_after))
            return False
        except TelegramError as exc:
            await self._retry(chat_id, key, unknown=True)
            log.warning("Board panel send outcome unknown: %s", type(exc).__name__)
            return False
        return await self._ack(chat_id, key, round_id, revision, int(sent.message_id))

    async def _settle(self, chat_id: int, game_key: str) -> bool:
        state = await self.current(chat_id, game_key)
        if not state or not state.get("board_score_pending"):
            return False
        winner = self._won(state, game_key)
        if not winner or str(state.get("board_score_round_id")) != str(state.get("round_id")):
            raise RuntimeError("board_score_state_invalid")
        if ((state.get("leaderboard_required") is not False and self.leaderboard is None) or
                (state.get("economy_required") is not False and self.economy is None)):
            raise RuntimeError("board_score_store_unavailable")
        if state.get("leaderboard_required") is not False:
            player = next(p for p in state["players"] if int(p.get("id") or 0) == winner)
            await self.leaderboard.record_score(user_id=winner, chat_id=int(chat_id),
                game_type=game_key, score=self.WIN_SCORES[game_key],
                user_name=str(player.get("name") or "Oyuncu"),
                event_id=f"board:{game_key}:{chat_id}:{state['round_id']}:{winner}")
        if state.get("economy_required") is not False:
            score = self.WIN_SCORES[game_key]
            await self.economy.credit(winner, int(chat_id), max(1, score // 2),
                reason=f"{game_key}_reward",
                event_id=f"coin:board:{game_key}:{chat_id}:{state['round_id']}:{winner}")
        latest = await self.current(chat_id, game_key)
        if (latest and latest.get("board_score_pending") and
                str(latest.get("round_id")) == str(state.get("round_id"))):
            latest["board_score_pending"] = False
            await self._write(chat_id, game_key, latest)
            self.metrics.inc("board_score_settled_total")
            return True
        return False

    async def cancel(self, chat_id: int, game_key: str, user_id: int,
                     moderator: bool = False, thread_id: int | None = None) -> bool:
        key = self._key(game_key)
        if not self._inside(chat_id, key):
            async with self.guard(chat_id, key):
                return await self.cancel(chat_id, key, user_id, moderator, thread_id)
        state = await self.current(chat_id, key)
        if not state or state.get("status") in {"won", "draw", "finished", "cancelled", "expired"}:
            return False
        if state.get("thread_id") is not None and state.get("thread_id") != thread_id:
            return False
        if not moderator and int(state.get("owner_id") or 0) != int(user_id):
            return False
        state.update(status="cancelled", turn_expires_at=None)
        await self.save(chat_id, key, state)
        return True

    async def expire(self, chat_id: int, game_key: str, *, now: float | None = None) -> bool:
        key = self._key(game_key)
        if not self._inside(chat_id, key):
            async with self.guard(chat_id, key):
                return await self.expire(chat_id, key, now=now)
        state = await self.current(chat_id, key)
        if not state or state.get("status") != "playing":
            return False
        raw = state.get("turn_expires_at")
        if raw is None or float(raw) > (time.time() if now is None else float(now)):
            return False
        # Timeout is a closed, scoreless round for the three compact boards.
        # Chess retains its domain result but is also not a completed win.
        if key == "chess":
            from src.games.chess import _expire_if_needed
            if now is None:
                _expire_if_needed(state)
            else:
                # Domain uses wall time. Reproduce its explicit terminal
                # representation for deterministic worker/testing clocks.
                current = str(state.get("turn") or "white")
                opponent = "black" if current == "white" else "white"
                winner = next((int(p.get("id") or 0) for p in state.get("players") or []
                               if p.get("color") == opponent), None)
                state.update(status="expired", termination="timeout",
                    result="0-1" if current == "white" else "1-0",
                    winner_id=winner, turn_expires_at=None, selected_square=None,
                    legal_targets=[], pending_promotion=None, draw_offer_by=None)
        else:
            state.update(status="expired", turn_expires_at=None)
        await self.save(chat_id, key, state)
        return True

    async def process(self, bot, chat_id: int, game_key: str,
                      force: bool = False) -> bool:
        key = self._key(game_key)
        if not self._inside(chat_id, key):
            async with self.guard(chat_id, key):
                return await self.process(bot, chat_id, key, force=force)
        state = await self.current(chat_id, key)
        if not state:
            return False
        changed = False
        changed = await self.expire(chat_id, key) or changed
        state = await self.current(chat_id, key)
        if force or state.get("board_panel_dirty"):
            changed = await self.publish(bot, chat_id, key, force=force) or changed
        state = await self.current(chat_id, key)
        if state.get("board_score_pending"):
            changed = await self._settle(chat_id, key) or changed
        return changed
