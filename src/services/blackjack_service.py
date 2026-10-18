"""Persistent, owner-scoped Blackjack sessions for Telegram callbacks."""

from __future__ import annotations

import logging
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
        return await getter(int(chat_id), self.state_key(user_id)) if getter else None

    async def _save(self, chat_id: int, user_id: int, state: dict[str, Any]) -> None:
        saver = getattr(self.database, "save_game_state", None)
        if saver:
            await saver(int(chat_id), self.state_key(user_id), state)

    async def _delete(self, chat_id: int, user_id: int) -> None:
        deleter = getattr(self.database, "delete_game_state", None)
        if deleter:
            await deleter(int(chat_id), self.state_key(user_id))

    async def start(self, *, chat_id: int, user_id: int, user_name: str, bet: int, event_id: str) -> dict[str, Any]:
        bet = self.validate_bet(bet)
        if await self._load(chat_id, user_id):
            raise ValueError("active_game")
        if self.economy:
            accepted = await self.economy.debit(
                int(user_id), int(chat_id), bet,
                reason="blackjack_bet", event_id=f"bet:{event_id}",
            )
            if not accepted:
                raise ValueError("insufficient_balance")
        game = self.game_factory()
        state = game.create_game(str(user_name)[:120], int(user_id))
        state["bet"] = bet
        state["base_bet"] = bet
        state["charged_bet"] = bet
        state["event_id"] = str(event_id)[:160]
        if state.get("status") == "blackjack":
            return await self._settle(chat_id, user_id, state, game)
        await self._save(chat_id, user_id, state)
        return self._view(state, game, terminal=False)

    async def action(self, *, chat_id: int, user_id: int, action: str, action_id: str) -> dict[str, Any]:
        action = str(action).casefold()
        if action not in {"hit", "stand", "double"}:
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
            if state.get("status") not in {"playing", "stand"}:
                raise ValueError("game_finished")
            game = self.game_factory()
            if action == "hit":
                game.hit(state)
            elif action == "stand":
                game.stand(state)
            else:
                if len(state.get("player_hand", [])) != 2:
                    raise ValueError("double_only_initial_hand")
                extra_bet = int(state.get("base_bet", state.get("bet", 0)))
                if self.economy and extra_bet:
                    accepted = await self.economy.debit(
                        int(user_id), int(chat_id), extra_bet,
                        reason="blackjack_double",
                        event_id=f"double:{state.get('event_id', user_id)}",
                    )
                    if not accepted:
                        raise ValueError("insufficient_balance")
                game.double_down(state)
                state["charged_bet"] = int(state.get("charged_bet", state.get("base_bet", 0))) + extra_bet
            if state.get("status") in {"bust", "finished", "blackjack"}:
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
        """Cancel the owner's hand and return all stakes charged for it.

        The economy event id is derived from the immutable game event, so a
        retried command cannot credit the same stake twice.
        """
        state = await self._load(chat_id, user_id)
        if not state:
            raise ValueError("game_expired")
        if int(state.get("player_id", 0)) != int(user_id):
            raise ValueError("not_owner")
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
