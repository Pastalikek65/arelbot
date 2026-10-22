"""Persistent, owner-scoped Blackjack sessions for Telegram callbacks."""

from __future__ import annotations

import logging
import hashlib
import time
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

    async def _debit_applied(self, chat_id: int, user_id: int, event_id: str) -> bool:
        """Distinguish an already charged wager from insufficient funds."""
        receipt = getattr(self.economy, "was_applied", None)
        return bool(await receipt(int(user_id), int(chat_id), event_id)) if receipt else False

    async def _stake_has_economy_receipt(self, chat_id: int, user_id: int,
                                         state: dict[str, Any]) -> bool:
        """Never turn a hand created without a debit into a payout."""
        required = state.get("economy_required")
        if required is False:
            return False
        if required is True:
            if self.economy is None:
                raise RuntimeError("game_reward_store_unavailable")
            return True
        # Older hands did not persist an economy requirement. Only a durable
        # bet receipt proves that such a hand was funded.
        event_id = str(state.get("event_id") or "").strip()
        if not event_id or self.economy is None or not await self._debit_applied(
            chat_id, user_id, f"bet:{event_id}"
        ):
            raise RuntimeError("game_payment_evidence_missing")
        return True

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
        """Serialize a hand through a renewable, crash-expiring owner lease."""
        async with self.actions.lease(chat_id, "blackjack_owner_v2",
                                      f"owner:{int(user_id)}", user_id=user_id):
            yield

    async def start(self, *, chat_id: int, user_id: int, user_name: str, bet: int, event_id: str) -> dict[str, Any]:
        async with self._hand_guard(chat_id, user_id):
            return await self._start(chat_id=chat_id, user_id=user_id, user_name=user_name, bet=bet, event_id=event_id)

    async def _start(self, *, chat_id: int, user_id: int, user_name: str, bet: int, event_id: str) -> dict[str, Any]:
        bet = self.validate_bet(bet)
        existing = await self._load(chat_id, user_id)
        if existing:
            if existing.get("status") == "pending_bet":
                return await self._resume_pending_start(chat_id, user_id, existing)
            if existing.get("status") == "pending_double":
                await self._resume_pending_double(chat_id, user_id, existing)
                existing = await self._load(chat_id, user_id)
            if existing and existing.get("terminal_panel_pending"):
                raise ValueError("result_pending")
            if existing and (existing.get("settlement_pending") or existing.get("refund_pending")):
                try:
                    await self._resume_pending(chat_id, user_id, existing)
                except Exception as exc:
                    logger.warning("Blackjack pending payment deferred: %s", type(exc).__name__)
                    raise ValueError("settlement_pending") from None
                existing = await self._load(chat_id, user_id)
            elif existing and existing.get("status") in {"bust", "finished", "blackjack", "surrendered"}:
                try:
                    await self._settle(chat_id, user_id, existing, self.game_factory())
                except Exception as exc:
                    logger.warning("Blackjack settlement deferred: %s", type(exc).__name__)
                    raise ValueError("settlement_pending") from None
                existing = await self._load(chat_id, user_id)
            if existing:
                raise ValueError("active_game")

        game = self.game_factory()
        state = game.create_game(str(user_name)[:120], int(user_id))
        original_status = str(state.get("status") or "playing")
        state["bet"] = bet
        state["base_bet"] = bet
        state["charged_bet"] = bet
        state["event_id"] = str(event_id)[:160]
        state["round_token"] = hashlib.sha256(str(event_id).encode("utf-8")).hexdigest()[:12]
        state["economy_required"] = self.economy is not None
        state["leaderboard_required"] = self.leaderboard is not None
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
            debit_event = f"bet:{event_id}"
            accepted = await self.economy.debit(
                int(user_id), int(chat_id), bet,
                reason="blackjack_bet", event_id=debit_event,
            )
            if not accepted and not await self._debit_applied(chat_id, user_id, debit_event):
                await self._delete(chat_id, user_id)
                raise ValueError("insufficient_balance")
        state["status"] = str(state.get("pending_game_status") or "playing")
        state["bet_status"] = "charged"
        await self._save(chat_id, user_id, state)
        return await self._finish_started_hand(chat_id, user_id, state, game)

    async def _finish_started_hand(
        self, chat_id: int, user_id: int, state: dict[str, Any], game: BlackjackGame
    ) -> dict[str, Any]:
        if state.get("status") in {"blackjack", "finished", "bust", "surrendered"}:
            return await self._settle(chat_id, user_id, state, game)
        return self._view(state, game, terminal=False)

    async def action(self, *, chat_id: int, user_id: int, action: str, action_id: str,
                     round_token: str | None = None,
                     panel_message_id: int | None = None) -> dict[str, Any]:
        async with self._hand_guard(chat_id, user_id):
            return await self._action(chat_id=chat_id, user_id=user_id, action=action,
                                      action_id=action_id, round_token=round_token,
                                      panel_message_id=panel_message_id)

    async def _action(self, *, chat_id: int, user_id: int, action: str, action_id: str,
                      round_token: str | None, panel_message_id: int | None) -> dict[str, Any]:
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
            if state.get("terminal_panel_pending"):
                raise ValueError("game_finished")
            if int(state.get("player_id", 0)) != int(user_id):
                raise ValueError("not_owner")
            if (round_token is not None or action == "surrender") and (
                not round_token or round_token != state.get("round_token")
            ):
                raise ValueError("stale_game")
            game = self.game_factory()
            if panel_message_id is not None:
                state["panel_message_id"] = int(panel_message_id)
            if state.get("status") in {"bust", "finished", "blackjack", "surrendered"}:
                # A previous settlement may have credited before deletion
                # failed. Resume its persisted outcome, never a new action.
                return await self._settle(chat_id, user_id, state, game)
            if state.get("status") == "pending_double":
                return await self._resume_pending_double(chat_id, user_id, state)
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
                pending = deepcopy(state)
                pending.update(status="pending_double", pending_double_previous=state,
                               pending_double_candidate=candidate)
                await self._save(chat_id, user_id, pending)
                return await self._resume_pending_double(chat_id, user_id, pending)
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
        if state.get("status") == "pending_bet":
            raise ValueError("bet_pending")
        if state.get("status") == "pending_double":
            raise ValueError("bet_pending")
        if state.get("terminal_panel_pending"):
            raise ValueError("result_pending")
        if state.get("refund_pending"):
            return await self._complete_refund(chat_id, user_id, state)
        if state.get("status") in {"bust", "finished", "blackjack", "surrendered"}:
            return await self._settle(chat_id, user_id, state, self.game_factory())
        state.update(status="cancelled", refund_pending=True, settlement_pending=False)
        await self._save(chat_id, user_id, state)
        return await self._complete_refund(chat_id, user_id, state)

    async def _complete_refund(self, chat_id: int, user_id: int,
                               state: dict[str, Any]) -> dict[str, Any]:
        funded = await self._stake_has_economy_receipt(chat_id, user_id, state)
        charged = max(0, int(state.get("charged_bet", state.get("base_bet", state.get("bet", 0)))))
        if funded and charged:
            await self.economy.credit(
                int(user_id), int(chat_id), charged,
                reason="blackjack_cancel",
                event_id=f"cancel:{state.get('event_id', user_id)}",
            )
        await self._delete(chat_id, user_id)
        return {"status": "cancelled", "terminal": True, "refunded": charged if funded else 0}

    async def resume_pending(self, *, chat_id: int, user_id: int) -> dict[str, Any] | None:
        """Finish a persisted payout/refund under the owner-scoped action guard."""
        async with self._hand_guard(chat_id, user_id):
            state = await self._load(chat_id, user_id)
            if not state:
                return None
            return await self._resume_pending(chat_id, user_id, state)

    async def _resume_pending(self, chat_id: int, user_id: int,
                              state: dict[str, Any]) -> dict[str, Any] | None:
        if int(state.get("player_id", 0)) != int(user_id):
            raise ValueError("not_owner")
        if state.get("refund_pending"):
            return await self._complete_refund(chat_id, user_id, state)
        if state.get("settlement_pending"):
            return await self._settle(chat_id, user_id, state, self.game_factory())
        if state.get("status") == "pending_bet":
            return await self._resume_pending_start(chat_id, user_id, state)
        if state.get("status") == "pending_double":
            return await self._resume_pending_double(chat_id, user_id, state)
        return None

    async def claim_terminal_panel(self, *, chat_id: int, user_id: int,
                                   now: float | None = None) -> dict[str, Any] | None:
        """Lease the immutable terminal result for Telegram delivery retry."""
        async with self._hand_guard(chat_id, user_id):
            state = await self._load(chat_id, user_id)
            moment = time.time() if now is None else float(now)
            if (not state or not state.get("terminal_panel_pending") or
                    float(state.get("panel_retry_at") or 0) > moment):
                return None
            state["panel_retry_at"] = moment + 30
            await self._save(chat_id, user_id, state)
            return state

    async def acknowledge_terminal_panel(self, *, chat_id: int, user_id: int,
                                         event_id: str, message_id: int) -> bool:
        async with self._hand_guard(chat_id, user_id):
            state = await self._load(chat_id, user_id)
            if (not state or not state.get("terminal_panel_pending") or
                    str(state.get("event_id")) != str(event_id)):
                return False
            state["panel_message_id"] = int(message_id)
            await self._delete(chat_id, user_id)
            return True

    async def _resume_pending_double(self, chat_id: int, user_id: int,
                                     state: dict[str, Any]) -> dict[str, Any]:
        """Complete one precomputed draw after its debit receipt is durable."""
        previous = state.get("pending_double_previous")
        candidate = state.get("pending_double_candidate")
        if not isinstance(previous, dict) or not isinstance(candidate, dict):
            raise RuntimeError("game_persistence_unavailable")
        funded = await self._stake_has_economy_receipt(chat_id, user_id, previous)
        extra_bet = int(previous.get("base_bet", previous.get("bet", 0)))
        debit_event = f"double:{state.get('event_id', user_id)}"
        if funded and extra_bet:
            accepted = await self.economy.debit(
                int(user_id), int(chat_id), extra_bet,
                reason="blackjack_double", event_id=debit_event,
            )
            if not accepted and not await self._debit_applied(chat_id, user_id, debit_event):
                await self._save(chat_id, user_id, previous)
                raise ValueError("insufficient_balance")
        candidate = deepcopy(candidate)
        candidate["charged_bet"] = int(previous.get("charged_bet", extra_bet)) + extra_bet
        await self._save(chat_id, user_id, candidate)
        return await self._finish_started_hand(chat_id, user_id, candidate, self.game_factory())

    async def _settle(self, chat_id: int, user_id: int, state: dict[str, Any], game: BlackjackGame) -> dict[str, Any]:
        if state.get("terminal_panel_pending"):
            return {"status": "finished", "terminal": True,
                    "text": str(state.get("terminal_text") or "Blackjack sonucu kaydedildi."),
                    "result": state.get("terminal_result"),
                    "winnings": int(state.get("terminal_winnings") or 0), "state": state}
        result = game.get_result(state)
        # Preserve the irrevocable outcome before any credit. On retry the
        # economy's same event id prevents a second refund/payout.
        state.update(settlement_pending=True, refund_pending=False)
        await self._save(chat_id, user_id, state)
        funded = await self._stake_has_economy_receipt(chat_id, user_id, state)
        if state.get("leaderboard_required") and self.leaderboard is None:
            raise RuntimeError("game_reward_store_unavailable")
        bet = int(state.get("bet", 0))
        winnings = int(result.get("winnings", 0))
        credit = max(0, bet + winnings)
        if funded and credit:
            await self.economy.credit(
                int(user_id), int(chat_id), credit,
                reason="blackjack_settlement",
                event_id=f"settle:{state.get('event_id', user_id)}",
            )
        if self.leaderboard and funded:
            await self.leaderboard.record_score(
                user_id=int(user_id), chat_id=int(chat_id), game_type="blackjack",
                score=max(-100, min(100, winnings)),
                user_name=state.get("player_name"),
                event_id=f"score:{state.get('event_id', user_id)}",
            )
        text = game.format_game(state, reveal_dealer=True) + f"\n\n{result.get('message', '')}"
        state.update(settlement_pending=False, terminal_panel_pending=True,
                     panel_retry_at=0, terminal_text=text,
                     terminal_result=result, terminal_winnings=winnings)
        await self._save(chat_id, user_id, state)
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
