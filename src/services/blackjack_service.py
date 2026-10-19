"""Persistent, owner-scoped Blackjack sessions for Telegram callbacks."""

from __future__ import annotations

import logging
import hashlib
from contextlib import asynccontextmanager
from copy import deepcopy
from typing import Any, Callable

from src.games.blackjack import BlackjackGame
from src.games.action_ledger import GameActionLedger

logger = logging.getLogger(__name__)


class BlackjackService:
    MAX_BET = 10_000
    MIN_BET = 1

    def __init__(
        self,
        database: Any,
        economy_service: Any | None = None,
        leaderboard_service: Any | None = None,
        action_ledger: GameActionLedger | None = None,
        game_factory: Callable[[], BlackjackGame] = BlackjackGame,
    ) -> None:
        self.database = database
        self.economy = economy_service
        self.leaderboard = leaderboard_service
        self.actions = action_ledger or GameActionLedger(database)
        self.game_factory = game_factory

    @staticmethod
    def state_key(user_id: int) -> str:
        return f"blackjack:{int(user_id)}"

    @classmethod
    def validate_bet(cls, value: int | str) -> int:
        try:
            amount = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid_bet") from exc
        if amount < cls.MIN_BET or amount > cls.MAX_BET:
            raise ValueError("bet_out_of_range")
        return amount

    async def _load(self, chat_id: int, user_id: int) -> dict[str, Any] | None:
        getter = getattr(self.database, "get_game_state", None)
        if getter is None or getattr(self.database, "available", True) is False:
            raise RuntimeError("game_persistence_unavailable")
        try:
            return await getter(int(chat_id), self.state_key(user_id))
        except RuntimeError:
            raise
        except Exception:
            raise RuntimeError("game_persistence_unavailable") from None

    async def _save(self, chat_id: int, user_id: int, state: dict[str, Any]) -> None:
        saver = getattr(self.database, "save_game_state", None)
        if saver is None or getattr(self.database, "available", True) is False:
            raise RuntimeError("game_persistence_unavailable")
        try:
            await saver(int(chat_id), self.state_key(user_id), state)
        except Exception:
            raise RuntimeError("game_persistence_unavailable") from None

    async def _delete(self, chat_id: int, user_id: int) -> None:
        deleter = getattr(self.database, "delete_game_state", None)
        if deleter is None or getattr(self.database, "available", True) is False:
            raise RuntimeError("game_persistence_unavailable")
        try:
            await deleter(int(chat_id), self.state_key(user_id))
        except RuntimeError:
            raise
        except Exception:
            raise RuntimeError("game_persistence_unavailable") from None

    @asynccontextmanager
    async def _hand_guard(self, chat_id: int, user_id: int):
        """Serialize every mutation of this owner's hand through the ledger.

        Production uses the repository's unique action key; test/degraded
        memory fallback retains the ledger's existing scope and limitations.
        A contending callback fails explicitly and may be retried.
        """
        key = f"owner:{int(user_id)}"
        if not await self.actions.claim(chat_id, "blackjack_session", key, user_id=user_id):
            raise ValueError("game_busy")
        try:
            yield
        finally:
            await self.actions.release(chat_id, "blackjack_session", key)

    async def start(self, *, chat_id: int, user_id: int, user_name: str, bet: int, event_id: str) -> dict[str, Any]:
        async with self._hand_guard(chat_id, user_id):
            return await self._start(chat_id=chat_id, user_id=user_id, user_name=user_name, bet=bet, event_id=event_id)

    async def _start(self, *, chat_id: int, user_id: int, user_name: str, bet: int, event_id: str) -> dict[str, Any]:
        bet = self.validate_bet(bet)
        existing = await self._load(chat_id, user_id)
        if existing:
            if existing.get("status") == "pending_bet":
                return await self._resume_pending_start(chat_id, user_id, existing)
            raise ValueError("active_game")

        game = self.game_factory()
        state = game.create_game(str(user_name)[:120], int(user_id))
        original_status = str(state.get("status") or "playing")
        state["bet"] = bet
        state["base_bet"] = bet
        state["charged_bet"] = bet
        state["event_id"] = str(event_id)[:160]
        state["round_token"] = hashlib.sha256(str(event_id).encode("utf-8")).hexdigest()[:12]
        # Persist a pending reservation before touching the economy.  If the
        # process dies after debit but before the final write, the next start
        # resumes this exact event id instead of charging twice or losing the
        # wager.
        state["pending_game_status"] = original_status
        state["bet_status"] = "pending"
        state["status"] = "pending_bet"
        await self._save(chat_id, user_id, state)
        try:
            if self.economy:
                accepted = await self.economy.debit(
                    int(user_id), int(chat_id), bet,
                    reason="blackjack_bet", event_id=f"bet:{state['event_id']}",
                )
                if not accepted:
                    try:
                        await self._delete(chat_id, user_id)
                    except RuntimeError:
                        logger.warning("Could not remove rejected Blackjack reservation", exc_info=True)
                    raise ValueError("insufficient_balance")
            state["status"] = original_status
            state["bet_status"] = "charged"
            await self._save(chat_id, user_id, state)
        except ValueError:
            raise

        return await self._finish_started_hand(chat_id, user_id, state, game)

    async def _resume_pending_start(
        self, chat_id: int, user_id: int, state: dict[str, Any]
    ) -> dict[str, Any]:
        """Complete a bet reservation left by a crash or transient write error."""

        game = self.game_factory()
        event_id = str(state.get("event_id") or "").strip()
        if not event_id:
            raise RuntimeError("game_persistence_unavailable")
        bet = self.validate_bet(state.get("base_bet", state.get("bet", 0)))
        if self.economy:
            accepted = await self.economy.debit(
                int(user_id), int(chat_id), bet,
                reason="blackjack_bet", event_id=f"bet:{event_id}",
            )
            if not accepted:
                raise ValueError("insufficient_balance")
        state["status"] = str(state.get("pending_game_status") or "playing")
        state["bet_status"] = "charged"
        await self._save(chat_id, user_id, state)
        return await self._finish_started_hand(chat_id, user_id, state, game)

    async def _finish_started_hand(
        self, chat_id: int, user_id: int, state: dict[str, Any], game: BlackjackGame
    ) -> dict[str, Any]:
        if state.get("status") in {"blackjack", "finished"}:
            return await self._settle(chat_id, user_id, state, game)
        return self._view(state, game, terminal=False)

    async def action(self, *, chat_id: int, user_id: int, action: str, action_id: str, round_token: str | None = None) -> dict[str, Any]:
        async with self._hand_guard(chat_id, user_id):
            return await self._action(chat_id=chat_id, user_id=user_id, action=action, action_id=action_id, round_token=round_token)

    async def _action(self, *, chat_id: int, user_id: int, action: str, action_id: str, round_token: str | None) -> dict[str, Any]:
        action = str(action).casefold()
        if action not in {"hit", "stand", "double", "surrender"}:
            raise ValueError("invalid_action")
        claimed = await self.actions.claim(
            chat_id, "blackjack", action_id, user_id=user_id,
            payload={"action": action},
        )
        if not claimed:
            raise ValueError("duplicate_action")
        try:
            state = await self._load(chat_id, user_id)
            if not state:
                raise ValueError("game_expired")
            if int(state.get("player_id", 0)) != int(user_id):
                raise ValueError("not_owner")
            if (round_token is not None or action == "surrender") and (
                not round_token or round_token != state.get("round_token")
            ):
                raise ValueError("stale_game")
            game = self.game_factory()
            if state.get("status") in {"bust", "finished", "blackjack", "surrendered"}:
                # A previous settlement may have credited before deletion
                # failed. Resume its persisted outcome, never a new action.
                return await self._settle(chat_id, user_id, state, game)
            if state.get("status") not in {"playing", "stand"}:
                raise ValueError("game_finished")
            if action == "hit":
                game.hit(state)
            elif action == "stand":
                game.stand(state)
            elif action == "surrender":
                game.surrender(state)
            else:
                if len(state.get("player_hand", [])) != 2:
                    raise ValueError("double_only_initial_hand")
                candidate = deepcopy(state)
                game.double_down(candidate)
                extra_bet = int(state.get("base_bet", state.get("bet", 0)))
                if self.economy and extra_bet:
                    accepted = await self.economy.debit(
                        int(user_id), int(chat_id), extra_bet,
                        reason="blackjack_double",
                        event_id=f"double:{state.get('event_id', user_id)}",
                    )
                    if not accepted:
                        raise ValueError("insufficient_balance")
                state = candidate
                state["charged_bet"] = int(state.get("charged_bet", state.get("base_bet", 0))) + extra_bet
            if state.get("status") in {"bust", "finished", "blackjack", "surrendered"}:
                return await self._settle(chat_id, user_id, state, game)
            await self._save(chat_id, user_id, state)
            return self._view(state, game, terminal=False)
        except Exception:
            # A failed callback must be retryable.  Settlement itself is
            # idempotent through its economy event id, so releasing the claim
            # here cannot create a second payout.
            await self.actions.release(chat_id, "blackjack", action_id)
            raise

    async def cancel(self, *, chat_id: int, user_id: int) -> dict[str, Any]:
        async with self._hand_guard(chat_id, user_id):
            return await self._cancel(chat_id=chat_id, user_id=user_id)

    async def _cancel(self, *, chat_id: int, user_id: int) -> dict[str, Any]:
        """Cancel the owner's hand and return all stakes charged for it.

        The economy event id is derived from the immutable game event, so a
        retried command cannot credit the same stake twice.
        """
        state = await self._load(chat_id, user_id)
        if not state:
            raise ValueError("game_expired")
        if int(state.get("player_id", 0)) != int(user_id):
            raise ValueError("not_owner")
        if state.get("status") in {"bust", "finished", "blackjack", "surrendered"}:
            return await self._settle(chat_id, user_id, state, self.game_factory())
        state["status"] = "cancelled"
        await self._save(chat_id, user_id, state)
        charged = max(0, int(state.get("charged_bet", state.get("base_bet", state.get("bet", 0)))))
        if self.economy and charged:
            await self.economy.credit(
                int(user_id), int(chat_id), charged,
                reason="blackjack_cancel",
                event_id=f"cancel:{state.get('event_id', user_id)}",
            )
        await self._delete(chat_id, user_id)
        return {"status": "cancelled", "terminal": True, "refunded": charged}

    async def _settle(self, chat_id: int, user_id: int, state: dict[str, Any], game: BlackjackGame) -> dict[str, Any]:
        result = game.get_result(state)
        # Preserve the irrevocable outcome before any credit. On retry the
        # economy's same event id prevents a second refund/payout.
        await self._save(chat_id, user_id, state)
        bet = int(state.get("bet", 0))
        winnings = int(result.get("winnings", 0))
        credit = max(0, bet + winnings)
        if self.economy and credit:
            await self.economy.credit(
                int(user_id), int(chat_id), credit,
                reason="blackjack_settlement",
                event_id=f"settle:{state.get('event_id', user_id)}",
            )
        if self.leaderboard:
            try:
                await self.leaderboard.record_score(
                    user_id=int(user_id), chat_id=int(chat_id), game_type="blackjack",
                    score=max(-100, min(100, winnings)),
                    user_name=state.get("player_name"),
                    event_id=f"score:{state.get('event_id', user_id)}",
                )
            except Exception:
                # A settlement already happened; telemetry failure must not
                # make the user replay a charged hand.
                logger.warning("Blackjack leaderboard telemetry failed", exc_info=True)
        text = game.format_game(state, reveal_dealer=True) + f"\n\n{result.get('message', '')}"
        await self._delete(chat_id, user_id)
        return {
            "status": "finished",
            "terminal": True,
            "text": text,
            "result": result,
            "winnings": winnings,
            "state": state,
        }

    @staticmethod
    def _view(state: dict[str, Any], game: BlackjackGame, *, terminal: bool) -> dict[str, Any]:
        return {
            "status": state.get("status", "playing"),
            "terminal": terminal,
            "text": game.format_game(state, reveal_dealer=False),
            "state": state,
        }
